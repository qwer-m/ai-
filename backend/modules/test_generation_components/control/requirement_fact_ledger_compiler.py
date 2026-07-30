from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from modules.orchestration.background_task_governance import (
    iter_governed_threadpool_map,
)

from .ai_runtime_isolation import AIRuntimeIsolationGuard
from .model_envelope_call import (
    MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
    EnvelopeCallResult,
    classify_response_termination,
    invoke_model_envelope,
)
from .requirement_fact_ledger import (
    build_requirement_fact_ledger_prompt,
    build_requirement_fact_ledger_user_input,
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_model_response,
    normalize_requirement_fact_ledger,
    normalize_source_evidence_catalog,
    project_requirement_source_dispositions,
    validate_requirement_fact_ledger_fingerprints,
)


FactLedgerCompileStatus = Literal[
    "validated",
    "parse_failed",
    "contract_invalid",
    "output_truncated",
    "output_incomplete",
    "transport_exhausted",
    "fatal_model_error",
]

DEFAULT_FACT_LEDGER_MAX_TOKENS = 8192
DEFAULT_FACT_LEDGER_REQUEST_TIMEOUT_SECONDS = 180.0
MAX_FACT_LEDGER_CANDIDATE_ENVELOPES = 3
MAX_FACT_LEDGER_CHUNK_WORKERS = 2

# 分片预算不依赖文档类型或业务词。每条来源按最多两个常见原子事实计入
# FACT JSON、anchor 和处置声明的固定开销。预算估算保持每来源两个事实的
# 保守密度，同时允许模型输出额度承载多个来源组，减少固定网络往返。
# 真实输出超过该密度时仍由模型输出上限失败关闭，不通过拆结构组来掩盖。
FACT_LEDGER_ATOMIC_FACT_SCHEMA_BUDGET_UNITS = 128
FACT_LEDGER_FACTS_PER_SOURCE_HEADROOM = 2
FACT_LEDGER_SOURCE_SCHEMA_BUDGET_UNITS = (
    FACT_LEDGER_ATOMIC_FACT_SCHEMA_BUDGET_UNITS
    * FACT_LEDGER_FACTS_PER_SOURCE_HEADROOM
)
FACT_LEDGER_UTF8_BYTES_PER_BUDGET_UNIT = 3
FACT_LEDGER_CHUNK_BUDGET_NUMERATOR = 9
FACT_LEDGER_CHUNK_BUDGET_DENOMINATOR = 5


@dataclass(frozen=True)
class RequirementFactLedgerCompilationResult:
    """A1 编译结果；只有 success=True 的 ledger 可以进入 A2。"""

    normalized_ledger: dict[str, Any]
    diagnostics: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.diagnostics.get("fact_ledger_compile_success") is True

    @property
    def status(self) -> str:
        return str(
            self.diagnostics.get("fact_ledger_compile_status")
            or "contract_invalid"
        )

    @property
    def raw_declarations(self) -> dict[str, Any]:
        value = self.normalized_ledger.get("raw_declarations")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def evidence_facts(self) -> list[dict[str, Any]]:
        value = self.normalized_ledger.get("evidence_facts")
        return list(value) if isinstance(value, list) else []


@dataclass(frozen=True)
class _CandidateEvaluation:
    status: Literal[
        "validated",
        "parse_failed",
        "contract_invalid",
        "output_truncated",
        "output_incomplete",
    ]
    normalized_ledger: dict[str, Any]
    diagnostics: dict[str, Any]
    retry_reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class _ParseableCandidateSnapshot:
    attempt: int
    status: Literal["validated", "contract_invalid"]
    normalized_ledger: dict[str, Any]
    fingerprint: str
    error_codes: tuple[str, ...]


@dataclass(frozen=True)
class _CatalogChunk:
    index: int
    items: tuple[dict[str, Any], ...]
    budget_units: int
    fingerprint: str


@dataclass(frozen=True)
class _CatalogCompilation:
    status: FactLedgerCompileStatus
    normalized_ledger: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]
    envelope_results: tuple[EnvelopeCallResult, ...]
    fresh_candidate_trigger_codes: tuple[str, ...]
    validated_attempt: int
    last_parseable_candidate: _ParseableCandidateSnapshot | None


def _positive_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return parsed


def _positive_float(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正数") from exc
    if parsed <= 0:
        raise ValueError(f"{field} 必须是正数")
    return parsed


def _catalog_item_budget_units(item: dict[str, Any]) -> int:
    """使用与文档类型无关的可重现预算估算单条来源的编译开销。"""

    encoded = json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_units = (
        len(encoded) + FACT_LEDGER_UTF8_BYTES_PER_BUDGET_UNIT - 1
    ) // FACT_LEDGER_UTF8_BYTES_PER_BUDGET_UNIT
    return FACT_LEDGER_SOURCE_SCHEMA_BUDGET_UNITS + content_units


def _partition_source_evidence_catalog(
    catalog_items: list[dict[str, Any]],
    *,
    max_tokens: int,
) -> tuple[list[_CatalogChunk], int, int]:
    """保持目录顺序切成互斥分片，且不拆分来源 partition group。"""

    chunk_budget_units = max(
        1,
        int(max_tokens)
        * FACT_LEDGER_CHUNK_BUDGET_NUMERATOR
        // FACT_LEDGER_CHUNK_BUDGET_DENOMINATOR,
    )
    partition_groups: list[list[dict[str, Any]]] = []
    partition_group_ids: list[str] = []
    for item in catalog_items:
        group_id = str(item.get("partition_group_id") or item.get("ref") or "")
        if not partition_groups or partition_group_ids[-1] != group_id:
            partition_groups.append([])
            partition_group_ids.append(group_id)
        partition_groups[-1].append(item)
    group_costs = [
        sum(_catalog_item_budget_units(item) for item in group)
        for group in partition_groups
    ]
    chunks: list[_CatalogChunk] = []
    current_items: list[dict[str, Any]] = []
    current_budget_units = 0

    def flush() -> None:
        nonlocal current_items, current_budget_units
        if not current_items:
            return
        copied_items = tuple(copy.deepcopy(current_items))
        chunks.append(
            _CatalogChunk(
                index=len(chunks) + 1,
                items=copied_items,
                budget_units=current_budget_units,
                fingerprint=fingerprint_source_evidence_catalog(
                    list(copied_items)
                ),
            )
        )
        current_items = []
        current_budget_units = 0

    for group, group_budget_units in zip(
        partition_groups,
        group_costs,
        strict=True,
    ):
        if (
            current_items
            and current_budget_units + group_budget_units > chunk_budget_units
        ):
            flush()
        current_items.extend(copy.deepcopy(group))
        current_budget_units += group_budget_units
    flush()

    flattened_refs = [
        str(item.get("ref") or "")
        for chunk in chunks
        for item in chunk.items
    ]
    expected_refs = [str(item.get("ref") or "") for item in catalog_items]
    if flattened_refs != expected_refs or len(set(flattened_refs)) != len(
        flattened_refs
    ):
        raise AssertionError("A1 目录分片必须保持顺序、互斥且完整")
    oversized_partition_group_count = sum(
        1 for group_cost in group_costs if group_cost > chunk_budget_units
    )
    return chunks, chunk_budget_units, oversized_partition_group_count


def _build_catalog_chunk_user_input(
    *,
    global_source_evidence_catalog: list[dict[str, Any]],
    global_source_catalog_fingerprint: str,
    local_source_evidence_catalog: list[dict[str, Any]],
    local_source_catalog_fingerprint: str,
    target_evidence_refs: list[str],
    attempt: int,
    compilation_mode: str,
    recompile_reason_codes: list[str],
) -> str:
    """
    构建只携带当前分片目录的模型输入。

    局部目录仅负责缩小模型上下文；全局来源顺序与目录指纹继续作为整次
    A1 编译身份传播，候选校验和最终合并仍由调用方持有全局目录完成。
    """

    local_input = build_requirement_fact_ledger_user_input(
        local_source_evidence_catalog,
        source_catalog_fingerprint=local_source_catalog_fingerprint,
        target_evidence_refs=target_evidence_refs,
        attempt=attempt,
        compilation_mode=compilation_mode,
        recompile_reason_codes=recompile_reason_codes,
    )
    payload = json.loads(local_input)
    global_order_by_ref = {
        str(item.get("ref") or ""): index
        for index, item in enumerate(global_source_evidence_catalog)
    }
    local_refs = {
        str(item.get("ref") or "")
        for item in local_source_evidence_catalog
        if str(item.get("ref") or "")
    }
    wire_items = [
        *list(payload.get("context_source_evidence_catalog") or []),
        *list(payload.get("target_source_evidence_catalog") or []),
    ]
    wire_refs = {
        str(item.get("ref") or "")
        for item in wire_items
        if isinstance(item, dict) and str(item.get("ref") or "")
    }
    if wire_refs != local_refs:
        raise AssertionError("A1 分片模型目录必须与当前局部分片严格一致")
    for item in wire_items:
        if not isinstance(item, dict):
            continue
        evidence_ref = str(item.get("ref") or "")
        if evidence_ref not in global_order_by_ref:
            raise AssertionError("A1 分片模型目录包含全局目录外来源")
        item["source_order"] = global_order_by_ref[evidence_ref]

    payload["compilation_scope"] = (
        "whole_catalog"
        if local_refs == set(global_order_by_ref)
        else "catalog_shard"
    )
    payload["source_catalog_fingerprint"] = str(
        global_source_catalog_fingerprint
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _stable_global_fact_identity(
    fact: dict[str, Any],
    *,
    catalog_ref_order: dict[str, int],
) -> dict[str, Any]:
    return {
        "fact_kind": str(fact.get("fact_kind") or ""),
        "statement": str(fact.get("statement") or ""),
        "requirement_level": str(fact.get("requirement_level") or ""),
        "priority": str(fact.get("priority") or ""),
        "testability": str(fact.get("testability") or ""),
        "evidence": sorted(
            {str(item) for item in fact.get("evidence") or [] if str(item)},
            key=lambda item: (catalog_ref_order.get(item, 10**9), item),
        ),
    }


def _stable_global_fact_id(identity: dict[str, Any]) -> str:
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"FACT_A1_{digest[:24].upper()}"


def _merge_chunk_raw_declarations(
    chunk_ledgers: list[dict[str, Any]],
    *,
    source_evidence_catalog: list[dict[str, str]],
) -> tuple[dict[str, Any], tuple[str, ...], int]:
    """
    重映射模型局部 ID，并从唯一的事实所有权/引用图投影最终处置账本。
    同一事实的置信度取较低值；若摘要碰撞对应不同事实则失败关闭。
    """

    merged_facts_by_id: dict[str, dict[str, Any]] = {}
    identity_by_global_id: dict[str, dict[str, Any]] = {}
    provisional_disposition_by_ref: dict[str, dict[str, Any]] = {}
    error_codes: set[str] = set()
    collapsed_duplicate_count = 0
    catalog_refs = [
        str(item.get("ref") or "") for item in source_evidence_catalog
    ]
    catalog_ref_order = {
        evidence_ref: index for index, evidence_ref in enumerate(catalog_refs)
    }

    for ledger in chunk_ledgers:
        raw_declarations = ledger.get("raw_declarations")
        if not isinstance(raw_declarations, dict):
            error_codes.add("fact_ledger_chunk_raw_declarations_missing")
            continue
        facts = raw_declarations.get("evidence_facts")
        dispositions = raw_declarations.get("source_evidence_dispositions")
        if not isinstance(facts, list) or not isinstance(dispositions, list):
            error_codes.add("fact_ledger_chunk_raw_declarations_invalid")
            continue

        for raw_fact in facts:
            if not isinstance(raw_fact, dict):
                error_codes.add("fact_ledger_chunk_fact_invalid")
                continue
            local_fact_id = str(raw_fact.get("fact_id") or "")
            identity = _stable_global_fact_identity(
                raw_fact,
                catalog_ref_order=catalog_ref_order,
            )
            global_fact_id = _stable_global_fact_id(identity)
            if not local_fact_id:
                error_codes.add("fact_ledger_chunk_fact_id_missing")
                continue

            existing_identity = identity_by_global_id.get(global_fact_id)
            if existing_identity is not None and existing_identity != identity:
                error_codes.add("fact_ledger_global_fact_id_hash_collision")
                continue
            remapped = copy.deepcopy(raw_fact)
            remapped["fact_id"] = global_fact_id
            remapped["evidence"] = list(identity["evidence"])
            existing_fact = merged_facts_by_id.get(global_fact_id)
            if existing_fact is None:
                identity_by_global_id[global_fact_id] = copy.deepcopy(identity)
                merged_facts_by_id[global_fact_id] = remapped
            else:
                existing_anchor = str(
                    existing_fact.get("anchor_evidence_ref") or ""
                )
                candidate_anchor = str(
                    remapped.get("anchor_evidence_ref") or ""
                )
                existing_fact["anchor_evidence_ref"] = min(
                    (existing_anchor, candidate_anchor),
                    key=lambda item: (
                        catalog_ref_order.get(item, 10**9),
                        item,
                    ),
                )
                collapsed_duplicate_count += 1
                existing_fact["confidence"] = min(
                    float(existing_fact.get("confidence") or 0.0),
                    float(remapped.get("confidence") or 0.0),
                )

        for raw_disposition in dispositions:
            if not isinstance(raw_disposition, dict):
                error_codes.add("fact_ledger_chunk_disposition_invalid")
                continue
            evidence_ref = str(raw_disposition.get("evidence_ref") or "")
            if evidence_ref in provisional_disposition_by_ref:
                error_codes.add("fact_ledger_target_disposition_duplicate")
                continue
            provisional_disposition_by_ref[evidence_ref] = {
                "evidence_ref": evidence_ref,
                "disposition": str(
                    raw_disposition.get("disposition") or ""
                ),
            }

    if set(provisional_disposition_by_ref) != set(catalog_refs):
        error_codes.add("fact_ledger_target_disposition_manifest_mismatch")

    fact_ids_by_ref: dict[str, set[str]] = {
        evidence_ref: set() for evidence_ref in catalog_refs
    }
    anchored_fact_ids_by_ref: dict[str, set[str]] = {
        evidence_ref: set() for evidence_ref in catalog_refs
    }
    for fact_id, fact in merged_facts_by_id.items():
        for evidence_ref in fact.get("evidence") or []:
            if str(evidence_ref) not in fact_ids_by_ref:
                error_codes.add("fact_ledger_merged_fact_evidence_unknown")
                continue
            fact_ids_by_ref[str(evidence_ref)].add(fact_id)
        anchor_evidence_ref = str(
            fact.get("anchor_evidence_ref") or ""
        )
        if anchor_evidence_ref not in anchored_fact_ids_by_ref:
            error_codes.add("fact_ledger_merged_fact_anchor_unknown")
        else:
            anchored_fact_ids_by_ref[anchor_evidence_ref].add(fact_id)

    owner_refs = {
        evidence_ref
        for evidence_ref, fact_ids in anchored_fact_ids_by_ref.items()
        if fact_ids
    }
    cited_refs = {
        evidence_ref
        for evidence_ref, fact_ids in fact_ids_by_ref.items()
        if fact_ids
    }
    merged_dispositions = project_requirement_source_dispositions(
        catalog_refs,
        owner_refs=owner_refs,
        cited_refs=cited_refs,
        unreferenced_disposition="non_requirement",
    )
    merged_disposition_by_ref = {
        item["evidence_ref"]: item["disposition"]
        for item in merged_dispositions
    }
    for evidence_ref in catalog_refs:
        anchored_fact_ids = sorted(
            anchored_fact_ids_by_ref.get(evidence_ref) or []
        )
        provisional_disposition = (
            provisional_disposition_by_ref.get(evidence_ref) or {}
        )
        declared_disposition = str(
            provisional_disposition.get("disposition") or ""
        )
        if anchored_fact_ids:
            if declared_disposition != "fact_backed":
                error_codes.add(
                    "fact_ledger_anchored_disposition_mismatch"
                )
        else:
            if declared_disposition not in {"context_only", "fact_backed"}:
                error_codes.add(
                    "fact_ledger_non_owner_projection_input_invalid"
                )
        if not merged_disposition_by_ref.get(evidence_ref):
            error_codes.add("fact_ledger_disposition_projection_missing")

    return (
        {
            "evidence_facts": sorted(
                merged_facts_by_id.values(),
                # 全局 fact_id 是内容哈希，只用于稳定身份；输出顺序应沿来源目录传播，
                # 供后续职责分片保留相邻需求的语义局部性。
                key=lambda item: (
                    catalog_ref_order.get(
                        str(item.get("anchor_evidence_ref") or ""),
                        min(
                            (
                                catalog_ref_order.get(str(ref), 10**9)
                                for ref in item.get("evidence") or []
                            ),
                            default=10**9,
                        ),
                    ),
                    str(item.get("fact_id") or ""),
                ),
            ),
            "source_evidence_dispositions": sorted(
                merged_dispositions,
                key=lambda item: str(item.get("evidence_ref") or ""),
            ),
        },
        tuple(sorted(error_codes)),
        collapsed_duplicate_count,
    )


def _evaluate_candidate(
    raw_text: str,
    *,
    source_evidence_catalog: Any,
    source_catalog_fingerprint: str,
    target_evidence_refs: list[str],
    shard_mode: bool,
) -> _CandidateEvaluation:
    try:
        candidate = json.loads(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _CandidateEvaluation(
            status="parse_failed",
            normalized_ledger={},
            diagnostics={
                "parse_error_code": "fact_ledger_json_parse_failed",
                "parse_error_type": type(exc).__name__,
            },
            retry_reason_codes=("fact_ledger_json_parse_failed",),
        )
    if not isinstance(candidate, dict):
        return _CandidateEvaluation(
            status="parse_failed",
            normalized_ledger={},
            diagnostics={
                "parse_error_code": "fact_ledger_candidate_not_object",
                "parsed_type": type(candidate).__name__,
            },
            retry_reason_codes=("fact_ledger_candidate_not_object",),
        )

    normalized = normalize_requirement_fact_model_response(
        candidate,
        source_evidence_catalog=source_evidence_catalog,
        source_catalog_fingerprint=source_catalog_fingerprint,
        target_evidence_refs=target_evidence_refs,
        shard_mode=shard_mode,
    )
    ledger_diagnostics = dict(normalized.get("diagnostics") or {})
    error_codes = tuple(
        sorted(
            {
                str(item)
                for item in ledger_diagnostics.get("error_codes") or []
                if str(item).strip()
            }
        )
    )
    if normalized.get("valid") is not True:
        return _CandidateEvaluation(
            status="contract_invalid",
            normalized_ledger=normalized,
            diagnostics={
                "contract_error_count": len(normalized.get("errors") or []),
                "contract_error_codes": list(error_codes),
                "fact_ledger_diagnostics": ledger_diagnostics,
            },
            retry_reason_codes=(
                error_codes or ("fact_ledger_contract_invalid",)
            ),
        )
    return _CandidateEvaluation(
        status="validated",
        normalized_ledger=normalized,
        diagnostics={
            "fact_ledger_fingerprint": str(
                normalized.get("fingerprint") or ""
            ),
            "fact_ledger_diagnostics": ledger_diagnostics,
        },
        retry_reason_codes=(),
    )


def _attempt_diagnostic(
    *,
    attempt: int,
    candidate_mode: str,
    compilation_mode: str,
    chunk_index: int,
    chunk_count: int,
    chunk_source_evidence_count: int,
    envelope: EnvelopeCallResult,
    evaluation: _CandidateEvaluation | None,
) -> dict[str, Any]:
    status = evaluation.status if evaluation is not None else envelope.status
    item: dict[str, Any] = {
        "attempt": int(attempt),
        "candidate_mode": str(candidate_mode),
        "compilation_mode": str(compilation_mode),
        "chunk_index": int(chunk_index),
        "chunk_count": int(chunk_count),
        "chunk_source_evidence_count": int(chunk_source_evidence_count),
        "status": status,
        "raw_chars": len(envelope.raw_text),
        "model_envelope": envelope.to_diagnostic(),
    }
    if evaluation is not None:
        item.update(dict(evaluation.diagnostics))
    return item


def _compile_catalog_chunk(
    *,
    client: Any,
    source_evidence_catalog: list[dict[str, str]],
    source_catalog_fingerprint: str,
    local_source_evidence_catalog: list[dict[str, str]],
    target_evidence_refs: list[str],
    target_fingerprint: str,
    chunk_index: int,
    chunk_count: int,
    db: Any,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
) -> _CatalogCompilation:
    prompt = build_requirement_fact_ledger_prompt()
    attempts: list[dict[str, Any]] = []
    envelope_results: list[EnvelopeCallResult] = []
    fresh_candidate_trigger_codes: list[str] = []
    normalized_ledger: dict[str, Any] = {}
    final_status: FactLedgerCompileStatus = "contract_invalid"
    validated_attempt = 0
    last_parseable_candidate: _ParseableCandidateSnapshot | None = None

    for attempt in range(1, MAX_FACT_LEDGER_CANDIDATE_ENVELOPES + 1):
        if attempt > 1 and not fresh_candidate_trigger_codes:
            break
        candidate_mode = "initial" if attempt == 1 else "fresh_candidate"
        compilation_mode = (
            "initial" if attempt == 1 else "independent_recompile"
        )
        user_input = _build_catalog_chunk_user_input(
            global_source_evidence_catalog=source_evidence_catalog,
            global_source_catalog_fingerprint=source_catalog_fingerprint,
            local_source_evidence_catalog=local_source_evidence_catalog,
            local_source_catalog_fingerprint=target_fingerprint,
            target_evidence_refs=target_evidence_refs,
            attempt=attempt,
            compilation_mode=compilation_mode,
            recompile_reason_codes=(
                fresh_candidate_trigger_codes if attempt > 1 else []
            ),
        )
        chunk_suffix = (
            ""
            if chunk_count == 1
            else f"-chunk-{chunk_index:03d}-of-{chunk_count:03d}"
        )
        envelope = invoke_model_envelope(
            client=client,
            envelope_id=(
                "requirement-fact-ledger-"
                f"{source_catalog_fingerprint[:12]}-"
                f"{target_fingerprint[:12]}{chunk_suffix}-"
                f"{candidate_mode}"
            ),
            user_input=user_input,
            system_prompt=prompt,
            db=db,
            max_tokens=max_tokens,
            task_type=str(task_type or "generation"),
            request_timeout_seconds=request_timeout_seconds,
            max_transport_replays=MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
        )
        envelope_results.append(envelope)

        if envelope.status != "response":
            final_status = envelope.status
            normalized_ledger = (
                last_parseable_candidate.normalized_ledger
                if last_parseable_candidate is not None
                else {}
            )
            attempts.append(
                _attempt_diagnostic(
                    attempt=attempt,
                    candidate_mode=candidate_mode,
                    compilation_mode=compilation_mode,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                    chunk_source_evidence_count=len(target_evidence_refs),
                    envelope=envelope,
                    evaluation=None,
                )
            )
            break

        response_termination = classify_response_termination(
            envelope.response_metadata
        )
        if response_termination == "truncated":
            evaluation = _CandidateEvaluation(
                status="output_truncated",
                normalized_ledger={},
                diagnostics={
                    "parse_error_code": "fact_ledger_output_truncated",
                },
                retry_reason_codes=(),
            )
        elif response_termination == "incomplete":
            evaluation = _CandidateEvaluation(
                status="output_incomplete",
                normalized_ledger={},
                diagnostics={
                    "parse_error_code": "fact_ledger_output_incomplete",
                },
                retry_reason_codes=(),
            )
        else:
            evaluation = _evaluate_candidate(
                envelope.raw_text,
                source_evidence_catalog=source_evidence_catalog,
                source_catalog_fingerprint=source_catalog_fingerprint,
                target_evidence_refs=target_evidence_refs,
                shard_mode=chunk_count > 1,
            )
        attempts.append(
            _attempt_diagnostic(
                attempt=attempt,
                candidate_mode=candidate_mode,
                compilation_mode=compilation_mode,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                chunk_source_evidence_count=len(target_evidence_refs),
                envelope=envelope,
                evaluation=evaluation,
            )
        )
        final_status = evaluation.status
        normalized_ledger = evaluation.normalized_ledger
        if evaluation.status in {"validated", "contract_invalid"}:
            ledger_diagnostics = dict(
                evaluation.normalized_ledger.get("diagnostics") or {}
            )
            last_parseable_candidate = _ParseableCandidateSnapshot(
                attempt=attempt,
                status=evaluation.status,
                normalized_ledger=evaluation.normalized_ledger,
                fingerprint=str(
                    evaluation.normalized_ledger.get("fingerprint") or ""
                ),
                error_codes=tuple(
                    str(item)
                    for item in ledger_diagnostics.get("error_codes") or []
                    if str(item).strip()
                ),
            )
        if evaluation.status == "validated":
            validated_attempt = attempt
            break
        if attempt == 1:
            fresh_candidate_trigger_codes = list(
                evaluation.retry_reason_codes
            )

    return _CatalogCompilation(
        status=final_status,
        normalized_ledger=normalized_ledger,
        attempts=tuple(attempts),
        envelope_results=tuple(envelope_results),
        fresh_candidate_trigger_codes=tuple(fresh_candidate_trigger_codes),
        validated_attempt=validated_attempt,
        last_parseable_candidate=last_parseable_candidate,
    )


def _result(
    *,
    status: FactLedgerCompileStatus,
    normalized_ledger: dict[str, Any],
    attempts: list[dict[str, Any]],
    envelope_results: list[EnvelopeCallResult],
    source_catalog_fingerprint: str,
    max_tokens: int,
    request_timeout_seconds: float,
    fresh_candidate_trigger_codes: list[str],
    validated_attempt: int,
    last_parseable_candidate: _ParseableCandidateSnapshot | None,
    candidate_attempt_limit: int = MAX_FACT_LEDGER_CANDIDATE_ENVELOPES,
    diagnostic_summary: dict[str, Any] | None = None,
) -> RequirementFactLedgerCompilationResult:
    success = status == "validated"
    ledger_diagnostics = dict(normalized_ledger.get("diagnostics") or {})
    diagnostics = {
        "fact_ledger_compile_status": status,
        "fact_ledger_compile_success": success,
        "fact_ledger_compile_envelope_count": len(envelope_results),
        "fact_ledger_compile_candidate_attempt_count": len(attempts),
        "fact_ledger_compile_candidate_attempt_limit": int(
            candidate_attempt_limit
        ),
        "fact_ledger_compile_physical_call_count": sum(
            item.physical_call_count for item in envelope_results
        ),
        "fact_ledger_compile_provider_call_count": sum(
            item.provider_call_count for item in envelope_results
        ),
        "fact_ledger_compile_cache_hit_count": sum(
            item.cache_hit_count for item in envelope_results
        ),
        "fact_ledger_compile_cache_miss_count": sum(
            item.cache_miss_count for item in envelope_results
        ),
        "fact_ledger_compile_cache_bypass_count": sum(
            item.cache_bypass_count for item in envelope_results
        ),
        "fact_ledger_compile_transport_failure_count": sum(
            item.transport_failure_count for item in envelope_results
        ),
        "fact_ledger_compile_transport_retry_count": sum(
            item.transport_retry_count for item in envelope_results
        ),
        "fact_ledger_compile_transport_replays_per_envelope": (
            MAX_TRANSPORT_REPLAYS_PER_ENVELOPE
        ),
        "fact_ledger_compile_fresh_candidate_used": any(
            item.get("candidate_mode") == "fresh_candidate"
            for item in attempts
        ),
        "fact_ledger_compile_fresh_candidate_trigger_codes": list(
            fresh_candidate_trigger_codes
        ),
        "fact_ledger_compile_validated_attempt": int(validated_attempt),
        "fact_ledger_compile_last_parseable_candidate_attempt": int(
            last_parseable_candidate.attempt
            if last_parseable_candidate is not None
            else 0
        ),
        "fact_ledger_compile_last_parseable_candidate_status": (
            last_parseable_candidate.status
            if last_parseable_candidate is not None
            else ""
        ),
        "fact_ledger_compile_last_parseable_candidate_fingerprint": (
            last_parseable_candidate.fingerprint
            if last_parseable_candidate is not None
            else ""
        ),
        "fact_ledger_compile_last_parseable_candidate_error_codes": (
            list(last_parseable_candidate.error_codes)
            if last_parseable_candidate is not None
            else []
        ),
        "fact_ledger_compile_stop_reason": "" if success else status,
        "fact_ledger_compile_attempts": list(attempts),
        "fact_ledger_source_catalog_fingerprint": str(
            source_catalog_fingerprint
        ),
        "fact_ledger_compile_max_tokens": int(max_tokens),
        "fact_ledger_compile_request_timeout_seconds": float(
            request_timeout_seconds
        ),
        "fact_ledger_fingerprint": (
            str(normalized_ledger.get("fingerprint") or "")
            if success
            else ""
        ),
        "fact_ledger_raw_declarations_fingerprint": (
            str(normalized_ledger.get("raw_declarations_fingerprint") or "")
            if success
            else ""
        ),
        "fact_ledger_evidence_facts_fingerprint": (
            str(normalized_ledger.get("evidence_facts_fingerprint") or "")
            if success
            else ""
        ),
        "fact_ledger_fact_count": int(
            ledger_diagnostics.get("fact_count") or 0
        ),
        "fact_ledger_source_evidence_count": int(
            ledger_diagnostics.get("source_evidence_count") or 0
        ),
        "fact_ledger_source_disposition_count": int(
            ledger_diagnostics.get("source_disposition_count") or 0
        ),
    }
    diagnostics.update(dict(diagnostic_summary or {}))
    return RequirementFactLedgerCompilationResult(
        normalized_ledger=normalized_ledger,
        diagnostics=diagnostics,
    )


def compile_requirement_atomic_fact_ledger(
    *,
    client: Any,
    source_evidence_catalog: Any,
    source_catalog_fingerprint: str | None = None,
    db: Any = None,
    max_tokens: int = DEFAULT_FACT_LEDGER_MAX_TOKENS,
    task_type: str = "generation",
    request_timeout_seconds: float = (
        DEFAULT_FACT_LEDGER_REQUEST_TIMEOUT_SECONDS
    ),
    worker_runtime_factory: Callable[[], Any] | None = None,
) -> RequirementFactLedgerCompilationResult:
    """
    编译独立 A1 原子事实 ledger。

    每个不可变 envelope 最多原样重放一次；只有 JSON 或契约失败才启动
    一次不携带旧候选的 independent fresh compile。
    """

    normalized_max_tokens = _positive_int(max_tokens, field="max_tokens")
    normalized_timeout = _positive_float(
        request_timeout_seconds,
        field="request_timeout_seconds",
    )
    actual_fingerprint = fingerprint_source_evidence_catalog(
        source_evidence_catalog
    )
    declared_fingerprint = str(source_catalog_fingerprint or "").strip()
    if declared_fingerprint and declared_fingerprint != actual_fingerprint:
        raise ValueError("source_catalog_fingerprint 与来源目录不一致")
    fingerprint = actual_fingerprint

    catalog = normalize_source_evidence_catalog(source_evidence_catalog)
    catalog_items = list(catalog.get("items") or [])
    chunks, chunk_budget_units, oversized_partition_group_count = (
        _partition_source_evidence_catalog(
            catalog_items,
            max_tokens=normalized_max_tokens,
        )
    )
    chunk_count = len(chunks)
    catalog_budget_units = sum(
        _catalog_item_budget_units(item) for item in catalog_items
    )
    partition_group_count = sum(
        1
        for index, item in enumerate(catalog_items)
        if index == 0
        or str(item.get("partition_group_id") or item.get("ref") or "")
        != str(
            catalog_items[index - 1].get("partition_group_id")
            or catalog_items[index - 1].get("ref")
            or ""
        )
    )
    candidate_attempt_limit = (
        chunk_count * MAX_FACT_LEDGER_CANDIDATE_ENVELOPES
    )
    last_parseable_candidate: _ParseableCandidateSnapshot | None = None

    common_summary: dict[str, Any] = {
        "fact_ledger_compile_chunked": chunk_count > 1,
        "fact_ledger_compile_chunk_count": chunk_count,
        "fact_ledger_compile_chunk_budget_units": chunk_budget_units,
        "fact_ledger_compile_catalog_budget_units": catalog_budget_units,
        "fact_ledger_compile_partition_group_count": partition_group_count,
        "fact_ledger_compile_wire_source_evidence_count_total": sum(
            len(chunk.items) for chunk in chunks
        ),
        "fact_ledger_compile_wire_source_evidence_count_max": max(
            (len(chunk.items) for chunk in chunks),
            default=0,
        ),
        "fact_ledger_compile_oversized_partition_group_count": (
            oversized_partition_group_count
        ),
        "fact_ledger_compile_completed_chunk_count": 0,
        "fact_ledger_compile_failed_chunk_index": 0,
        "fact_ledger_compile_chunk_summaries": [],
        "fact_ledger_compile_global_status": "not_started",
        "fact_ledger_compile_global_error_codes": [],
        "fact_ledger_compile_collapsed_duplicate_fact_count": 0,
        "fact_ledger_source_evidence_count": len(catalog_items),
    }
    attempts: list[dict[str, Any]] = []
    envelope_results: list[EnvelopeCallResult] = []
    fresh_candidate_trigger_codes: list[str] = []
    chunk_ledgers: list[dict[str, Any]] = []
    chunk_summaries: list[dict[str, Any]] = []
    validated_attempt = 0

    def _compile_with_runtime(
        *,
        chunk: _CatalogChunk,
        execution_client: Any,
        execution_db: Any,
    ) -> tuple[int, _CatalogCompilation]:
        target_refs = [str(item.get("ref") or "") for item in chunk.items]
        return chunk.index, _compile_catalog_chunk(
            client=execution_client,
            source_evidence_catalog=catalog_items,
            source_catalog_fingerprint=fingerprint,
            local_source_evidence_catalog=list(chunk.items),
            target_evidence_refs=target_refs,
            target_fingerprint=chunk.fingerprint,
            chunk_index=chunk.index,
            chunk_count=chunk_count,
            db=execution_db,
            max_tokens=normalized_max_tokens,
            task_type=str(task_type or "generation"),
            request_timeout_seconds=normalized_timeout,
        )

    chunk_compilations: dict[int, _CatalogCompilation] = {}
    parallel_chunks_enabled = bool(
        worker_runtime_factory is not None and chunk_count > 1
    )
    common_summary.update(
        {
            "fact_ledger_compile_parallel_chunks_enabled": (
                parallel_chunks_enabled
            ),
            "fact_ledger_compile_chunk_max_workers": (
                MAX_FACT_LEDGER_CHUNK_WORKERS
                if parallel_chunks_enabled
                else 1
            ),
        }
    )
    if parallel_chunks_enabled:
        runtime_isolation = AIRuntimeIsolationGuard(
            parent_client=client,
            parent_db=db,
            error_message=(
                "A1 fact ledger 分片 worker 禁止共享 provider、AIClient 或 DB Session"
            ),
        )

        def _compile_with_isolated_runtime(
            chunk: _CatalogChunk,
        ) -> tuple[int, _CatalogCompilation]:
            assert worker_runtime_factory is not None
            with worker_runtime_factory() as (worker_client, worker_db):
                runtime_isolation.claim(
                    client=worker_client,
                    db=worker_db,
                )
                return _compile_with_runtime(
                    chunk=chunk,
                    execution_client=worker_client,
                    execution_db=worker_db,
                )

        governed_updates = iter_governed_threadpool_map(
            profile_key="test_generation_fact_ledger_shard_threadpool",
            items=chunks,
            worker=_compile_with_isolated_runtime,
            max_workers=MAX_FACT_LEDGER_CHUNK_WORKERS,
            thread_name_prefix="fact-ledger-shard",
            heartbeat_interval_seconds=0,
        )
        for governed_update in governed_updates:
            governed_result = governed_update.item_result
            if governed_result is None:
                continue
            if governed_result.exception is not None:
                # 隔离冲突或分片执行异常必须立即关闭本轮，避免继续等待未启动分片。
                raise governed_result.exception
            chunk_index, compilation = governed_result.result
            chunk_compilations[int(chunk_index)] = compilation
    else:
        for chunk in chunks:
            chunk_index, compilation = _compile_with_runtime(
                chunk=chunk,
                execution_client=client,
                execution_db=db,
            )
            chunk_compilations[int(chunk_index)] = compilation

    # 并发完成顺序不可影响 A1 账本：只按原 chunk index 归并并执行 fail-close。
    for chunk in chunks:
        compilation = chunk_compilations[chunk.index]
        target_refs = [str(item.get("ref") or "") for item in chunk.items]
        if chunk_count == 1:
            last_parseable_candidate = compilation.last_parseable_candidate
        attempts.extend(compilation.attempts)
        envelope_results.extend(compilation.envelope_results)
        for code in compilation.fresh_candidate_trigger_codes:
            if code not in fresh_candidate_trigger_codes:
                fresh_candidate_trigger_codes.append(code)
        chunk_diagnostics = dict(
            compilation.normalized_ledger.get("diagnostics") or {}
        )
        chunk_summary = {
            "chunk_index": chunk.index,
            "status": compilation.status,
            "target_source_evidence_count": len(target_refs),
            "model_source_evidence_count": len(chunk.items),
            "global_source_evidence_count": len(catalog_items),
            "budget_units": chunk.budget_units,
            "target_fingerprint": chunk.fingerprint,
            "candidate_attempt_count": len(compilation.attempts),
            "envelope_count": len(compilation.envelope_results),
            "physical_call_count": sum(
                item.physical_call_count
                for item in compilation.envelope_results
            ),
            "provider_call_count": sum(
                item.provider_call_count
                for item in compilation.envelope_results
            ),
            "cache_hit_count": sum(
                item.cache_hit_count for item in compilation.envelope_results
            ),
            "cache_miss_count": sum(
                item.cache_miss_count for item in compilation.envelope_results
            ),
            "validated_attempt": compilation.validated_attempt,
            "ledger_fingerprint": (
                str(compilation.normalized_ledger.get("fingerprint") or "")
                if compilation.status == "validated"
                else ""
            ),
            "fact_count": int(chunk_diagnostics.get("fact_count") or 0),
            "source_disposition_count": int(
                chunk_diagnostics.get("source_disposition_count") or 0
            ),
        }
        chunk_summaries.append(chunk_summary)
        common_summary["fact_ledger_compile_chunk_summaries"] = list(
            chunk_summaries
        )

        manifest_valid = bool(
            compilation.status == "validated"
            and chunk_diagnostics.get("source_evidence_count")
            == len(catalog_items)
            and chunk_diagnostics.get("target_source_evidence_count")
            == len(target_refs)
            and chunk_diagnostics.get("source_disposition_count")
            == len(target_refs)
            and validate_requirement_fact_ledger_fingerprints(
                compilation.normalized_ledger
            ).get("valid")
            is True
        )
        if compilation.status != "validated" or not manifest_valid:
            failed_status: FactLedgerCompileStatus = (
                compilation.status
                if compilation.status != "validated"
                else "contract_invalid"
            )
            common_summary.update(
                {
                    "fact_ledger_compile_completed_chunk_count": len(
                        chunk_ledgers
                    ),
                    "fact_ledger_compile_failed_chunk_index": chunk.index,
                    "fact_ledger_compile_global_status": (
                        "chunk_failed"
                        if compilation.status != "validated"
                        else "chunk_manifest_invalid"
                    ),
                    "fact_ledger_compile_global_error_codes": (
                        []
                        if compilation.status != "validated"
                        else ["fact_ledger_chunk_manifest_invalid"]
                    ),
                }
            )
            return _result(
                status=failed_status,
                normalized_ledger={},
                attempts=attempts,
                envelope_results=envelope_results,
                source_catalog_fingerprint=fingerprint,
                max_tokens=normalized_max_tokens,
                request_timeout_seconds=normalized_timeout,
                fresh_candidate_trigger_codes=(
                    fresh_candidate_trigger_codes
                ),
                validated_attempt=0,
                last_parseable_candidate=last_parseable_candidate,
                candidate_attempt_limit=candidate_attempt_limit,
                diagnostic_summary=common_summary,
            )
        chunk_ledgers.append(compilation.normalized_ledger)
        validated_attempt = max(
            validated_attempt,
            compilation.validated_attempt,
        )

    merged_candidate, merge_error_codes, collapsed_duplicate_count = (
        _merge_chunk_raw_declarations(
            chunk_ledgers,
            source_evidence_catalog=catalog_items,
        )
    )
    common_summary.update(
        {
            "fact_ledger_compile_completed_chunk_count": len(chunk_ledgers),
            "fact_ledger_compile_collapsed_duplicate_fact_count": (
                collapsed_duplicate_count
            ),
        }
    )
    if merge_error_codes:
        common_summary.update(
            {
                "fact_ledger_compile_global_status": "merge_invalid",
                "fact_ledger_compile_global_error_codes": list(
                    merge_error_codes
                ),
            }
        )
        return _result(
            status="contract_invalid",
            normalized_ledger={},
            attempts=attempts,
            envelope_results=envelope_results,
            source_catalog_fingerprint=fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            candidate_attempt_limit=candidate_attempt_limit,
            diagnostic_summary=common_summary,
        )

    normalized_ledger = normalize_requirement_fact_ledger(
        merged_candidate,
        source_evidence_catalog=catalog_items,
        source_catalog_fingerprint=fingerprint,
    )
    global_error_codes = list(
        (normalized_ledger.get("diagnostics") or {}).get("error_codes") or []
    )
    fingerprint_validation = validate_requirement_fact_ledger_fingerprints(
        normalized_ledger
    )
    if normalized_ledger.get("valid") is not True:
        common_summary.update(
            {
                "fact_ledger_compile_global_status": "contract_invalid",
                "fact_ledger_compile_global_error_codes": global_error_codes,
            }
        )
        return _result(
            status="contract_invalid",
            normalized_ledger={},
            attempts=attempts,
            envelope_results=envelope_results,
            source_catalog_fingerprint=fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            candidate_attempt_limit=candidate_attempt_limit,
            diagnostic_summary=common_summary,
        )
    if fingerprint_validation.get("valid") is not True:
        common_summary.update(
            {
                "fact_ledger_compile_global_status": "fingerprint_invalid",
                "fact_ledger_compile_global_error_codes": list(
                    fingerprint_validation.get("error_codes") or []
                ),
            }
        )
        return _result(
            status="contract_invalid",
            normalized_ledger={},
            attempts=attempts,
            envelope_results=envelope_results,
            source_catalog_fingerprint=fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            candidate_attempt_limit=candidate_attempt_limit,
            diagnostic_summary=common_summary,
        )

    common_summary["fact_ledger_compile_global_status"] = "validated"
    return _result(
        status="validated",
        normalized_ledger=normalized_ledger,
        attempts=attempts,
        envelope_results=envelope_results,
        source_catalog_fingerprint=fingerprint,
        max_tokens=normalized_max_tokens,
        request_timeout_seconds=normalized_timeout,
        fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
        validated_attempt=validated_attempt,
        last_parseable_candidate=last_parseable_candidate,
        candidate_attempt_limit=candidate_attempt_limit,
        diagnostic_summary=common_summary,
    )


__all__ = [
    "DEFAULT_FACT_LEDGER_MAX_TOKENS",
    "DEFAULT_FACT_LEDGER_REQUEST_TIMEOUT_SECONDS",
    "FactLedgerCompileStatus",
    "MAX_FACT_LEDGER_CANDIDATE_ENVELOPES",
    "MAX_FACT_LEDGER_CHUNK_WORKERS",
    "RequirementFactLedgerCompilationResult",
    "compile_requirement_atomic_fact_ledger",
]
