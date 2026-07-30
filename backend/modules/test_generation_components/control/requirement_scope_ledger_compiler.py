from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from .model_envelope_call import (
    MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
    EnvelopeCallResult,
    classify_response_termination,
    invoke_model_envelope,
)
from .requirement_scope_ledger import (
    build_requirement_scope_binding_prompt,
    build_requirement_scope_binding_user_input,
    build_requirement_scope_boundary_selection_prompt,
    build_requirement_scope_boundary_selection_user_input,
    build_requirement_scope_membership_prompt,
    build_requirement_scope_membership_user_input,
    fingerprint_requirement_scope_boundary_manifest,
    normalize_requirement_scope_binding_shard,
    normalize_requirement_scope_boundary_selection_model_response,
    normalize_requirement_scope_ledger,
    normalize_requirement_scope_membership_model_response,
    project_requirement_scope_binding_recompile_feedback,
    project_requirement_scope_ledger,
)


ScopeLedgerCompileStatus = Literal[
    "validated",
    "parse_failed",
    "contract_invalid",
    "output_truncated",
    "output_incomplete",
    "capacity_exceeded",
    "transport_exhausted",
    "fatal_model_error",
]
_CandidateStatus = Literal[
    "validated",
    "parse_failed",
    "contract_invalid",
    "output_truncated",
    "output_incomplete",
]

DEFAULT_SCOPE_LEDGER_MAX_TOKENS = 4096
DEFAULT_SCOPE_LEDGER_REQUEST_TIMEOUT_SECONDS = 180.0
MAX_SCOPE_LEDGER_CANDIDATE_ENVELOPES = 2

# 分片只根据实际 JSON 身份长度和输出上限计算，不依赖文档类型或业务词。
SCOPE_BINDING_SCHEMA_BUDGET_UNITS = 96
SCOPE_BINDING_UTF8_BYTES_PER_BUDGET_UNIT = 3
SCOPE_BINDING_BUDGET_NUMERATOR = 2
SCOPE_BINDING_BUDGET_DENOMINATOR = 1


@dataclass(frozen=True)
class RequirementScopeLedgerCompilationResult:
    """A2 编译结果；只有 success=True 的完整 ledger 可以进入 B 阶段。"""

    normalized_ledger: dict[str, Any]
    projection: dict[str, Any]
    diagnostics: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.diagnostics.get("scope_ledger_compile_success") is True

    @property
    def status(self) -> str:
        return str(
            self.diagnostics.get("scope_ledger_compile_status")
            or "contract_invalid"
        )


@dataclass(frozen=True)
class _CandidateEvaluation:
    status: _CandidateStatus
    normalized_payload: dict[str, Any]
    diagnostics: dict[str, Any]
    retry_reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class _ParseableCandidateSnapshot:
    attempt: int
    status: Literal["validated", "contract_invalid"]
    fingerprint: str
    error_codes: tuple[str, ...]


@dataclass(frozen=True)
class _CompilationUnit:
    status: ScopeLedgerCompileStatus
    normalized_payload: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]
    envelope_results: tuple[EnvelopeCallResult, ...]
    fresh_candidate_trigger_codes: tuple[str, ...]
    validated_attempt: int
    last_parseable_candidate: _ParseableCandidateSnapshot | None


@dataclass(frozen=True)
class _BindingShard:
    index: int
    count: int
    target_fact_ids: tuple[str, ...]
    budget_units: int
    fingerprint: str


def _positive_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"{field} 必须是正数")
    return parsed


def _positive_float(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正数") from exc
    if parsed <= 0:
        raise ValueError(f"{field} 必须是正数")
    return parsed


def _canonical_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _binding_item_budget_units(
    fact_id: str,
    *,
    scope_ids: list[str],
) -> int:
    """按 manifest 已知归属估算单条 binding 输出。"""

    identity_bytes = len(str(fact_id).encode("utf-8")) + sum(
        len(item.encode("utf-8")) + 3 for item in scope_ids
    )
    identity_units = (
        identity_bytes + SCOPE_BINDING_UTF8_BYTES_PER_BUDGET_UNIT - 1
    ) // SCOPE_BINDING_UTF8_BYTES_PER_BUDGET_UNIT
    return SCOPE_BINDING_SCHEMA_BUDGET_UNITS + identity_units


def _binding_scope_candidates_by_fact(
    boundary_manifest: dict[str, Any],
) -> tuple[dict[str, list[str]], str]:
    """从冻结 manifest 提取已声明归属；未声明事实使用单归属基线。"""

    candidates: dict[str, set[str]] = {}
    boundary_ids: list[str] = []
    for raw_boundary in boundary_manifest.get("boundaries") or []:
        if not isinstance(raw_boundary, dict):
            continue
        boundary_id = str(raw_boundary.get("boundary_id") or "")
        if not boundary_id:
            continue
        boundary_ids.append(boundary_id)
        parent_boundary_id = str(raw_boundary.get("parent_boundary_id") or "")
        if parent_boundary_id:
            for fact_id in raw_boundary.get("membership_fact_ids") or []:
                normalized_fact_id = str(fact_id or "")
                if normalized_fact_id:
                    candidates.setdefault(normalized_fact_id, set()).add(
                        parent_boundary_id
                    )
        for support in raw_boundary.get("support") or []:
            if not isinstance(support, dict):
                continue
            for fact_id in support.get("fact_ids") or []:
                normalized_fact_id = str(fact_id or "")
                if normalized_fact_id:
                    candidates.setdefault(normalized_fact_id, set()).add(
                        boundary_id
                    )

    # 普通事实的具体 owner 由 binding 阶段判定；只为容量估算保留一个最长 ID。
    default_scope_id = max(
        boundary_ids,
        key=lambda item: (len(item.encode("utf-8")), item),
        default="",
    )
    return (
        {
            fact_id: sorted(scope_ids)
            for fact_id, scope_ids in candidates.items()
        },
        default_scope_id,
    )


def _partition_binding_targets(
    fact_ids: list[str],
    *,
    boundary_manifest: dict[str, Any],
    max_tokens: int,
) -> tuple[list[_BindingShard], int, int]:
    """保持 A1 fact ID 稳定顺序分片，且在调用模型前验证互斥和全集。"""

    scope_candidates_by_fact, default_scope_id = (
        _binding_scope_candidates_by_fact(boundary_manifest)
    )
    shard_budget_units = max(
        1,
        int(max_tokens)
        * SCOPE_BINDING_BUDGET_NUMERATOR
        // SCOPE_BINDING_BUDGET_DENOMINATOR,
    )
    item_costs = []
    minimum_item_costs = []
    for fact_id in fact_ids:
        scope_ids = scope_candidates_by_fact.get(fact_id)
        if scope_ids is None:
            scope_ids = [default_scope_id] if default_scope_id else []
        item_costs.append(
            _binding_item_budget_units(fact_id, scope_ids=scope_ids)
        )
        minimum_item_costs.append(
            _binding_item_budget_units(fact_id, scope_ids=[])
        )
    oversized_fact_count = sum(
        1
        for minimum_item_cost in minimum_item_costs
        if minimum_item_cost > shard_budget_units
    )

    raw_chunks: list[tuple[tuple[str, ...], int]] = []
    current_ids: list[str] = []
    current_budget = 0

    def flush() -> None:
        nonlocal current_ids, current_budget
        if not current_ids:
            return
        raw_chunks.append((tuple(current_ids), current_budget))
        current_ids = []
        current_budget = 0

    for fact_id, item_cost in zip(fact_ids, item_costs, strict=True):
        if item_cost > shard_budget_units:
            # 已知多归属可能让估算超过常规 shard；单独调用后由终止元数据门禁判定。
            flush()
            current_ids.append(fact_id)
            current_budget = item_cost
            flush()
            continue
        if current_ids and current_budget + item_cost > shard_budget_units:
            flush()
        current_ids.append(fact_id)
        current_budget += item_cost
    flush()

    flattened = [fact_id for chunk, _ in raw_chunks for fact_id in chunk]
    if flattened != fact_ids or len(set(flattened)) != len(flattened):
        raise AssertionError("A2 binding 分片必须保持顺序、互斥且完整")

    fact_fingerprint = str(boundary_manifest.get("fact_ledger_fingerprint") or "")
    manifest_fingerprint = str(boundary_manifest.get("fingerprint") or "")
    chunk_count = len(raw_chunks)
    shards = [
        _BindingShard(
            index=index,
            count=chunk_count,
            target_fact_ids=target_ids,
            budget_units=budget_units,
            fingerprint=_canonical_fingerprint(
                {
                    "fact_ledger_fingerprint": fact_fingerprint,
                    "boundary_manifest_fingerprint": manifest_fingerprint,
                    "shard_index": index,
                    "shard_count": chunk_count,
                    "target_fact_ids": list(target_ids),
                }
            ),
        )
        for index, (target_ids, budget_units) in enumerate(raw_chunks, start=1)
    ]
    return shards, shard_budget_units, oversized_fact_count


def _evaluate_json_candidate(
    raw_text: str,
    *,
    phase: Literal["boundary_selection", "membership", "binding"],
    normalizer: Callable[[dict[str, Any]], dict[str, Any]],
) -> _CandidateEvaluation:
    try:
        candidate = json.loads(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        code = f"scope_{phase}_json_parse_failed"
        return _CandidateEvaluation(
            status="parse_failed",
            normalized_payload={},
            diagnostics={
                "parse_error_code": code,
                "parse_error_type": type(exc).__name__,
            },
            retry_reason_codes=(code,),
        )
    if not isinstance(candidate, dict):
        code = f"scope_{phase}_candidate_not_object"
        return _CandidateEvaluation(
            status="parse_failed",
            normalized_payload={},
            diagnostics={
                "parse_error_code": code,
                "parsed_type": type(candidate).__name__,
            },
            retry_reason_codes=(code,),
        )

    normalized = normalizer(candidate)
    normalized_diagnostics = dict(normalized.get("diagnostics") or {})
    error_codes = tuple(
        sorted(
            {
                str(item)
                for item in normalized_diagnostics.get("error_codes") or []
                if str(item).strip()
            }
        )
    )
    if normalized.get("valid") is not True:
        return _CandidateEvaluation(
            status="contract_invalid",
            normalized_payload=normalized,
            diagnostics={
                "contract_error_count": len(normalized.get("errors") or []),
                "contract_error_codes": list(error_codes),
            },
            retry_reason_codes=(
                error_codes or (f"scope_{phase}_contract_invalid",)
            ),
        )
    return _CandidateEvaluation(
        status="validated",
        normalized_payload=normalized,
        diagnostics={
            "payload_fingerprint": str(normalized.get("fingerprint") or ""),
            "contract_error_count": 0,
            "contract_error_codes": [],
        },
        retry_reason_codes=(),
    )


def _attempt_diagnostic(
    *,
    attempt: int,
    candidate_mode: str,
    compilation_mode: str,
    phase: str,
    shard_index: int,
    shard_count: int,
    target_fact_count: int,
    system_prompt: str,
    user_input: str,
    envelope: EnvelopeCallResult,
    evaluation: _CandidateEvaluation | None,
) -> dict[str, Any]:
    status = evaluation.status if evaluation is not None else envelope.status
    item: dict[str, Any] = {
        "attempt": int(attempt),
        "candidate_mode": str(candidate_mode),
        "compilation_mode": str(compilation_mode),
        "phase": str(phase),
        "shard_index": int(shard_index),
        "shard_count": int(shard_count),
        "target_fact_count": int(target_fact_count),
        "system_prompt_chars": len(system_prompt),
        "user_input_chars": len(user_input),
        "request_chars": len(system_prompt) + len(user_input),
        "user_input_fingerprint": hashlib.sha256(
            user_input.encode("utf-8")
        ).hexdigest(),
        "status": status,
        "raw_chars": len(envelope.raw_text),
        "finish_reason": str(
            envelope.response_metadata.get("finish_reason") or ""
        ),
        "model_envelope": envelope.to_diagnostic(),
    }
    try:
        request_payload = json.loads(user_input)
    except (TypeError, ValueError, json.JSONDecodeError):
        request_payload = {}
    source_outline = (
        request_payload.get("frozen_source_outline")
        if isinstance(request_payload, dict)
        else None
    )
    item["source_topology_wire_present"] = isinstance(source_outline, dict)
    if isinstance(source_outline, dict):
        item.update(
            {
                "source_topology_version": str(
                    source_outline.get("outline_version") or ""
                ),
                "source_topology_fingerprint": str(
                    source_outline.get("fingerprint") or ""
                ),
                "source_topology_group_count": int(
                    source_outline.get("group_count") or 0
                ),
                "source_topology_relation_count": int(
                    source_outline.get("relation_count") or 0
                ),
                "source_topology_anchored_fact_count": int(
                    source_outline.get("anchored_fact_count") or 0
                ),
            }
        )
    frozen_selection = (
        request_payload.get("frozen_boundary_selection")
        if isinstance(request_payload, dict)
        else None
    )
    if isinstance(frozen_selection, dict):
        item["boundary_selection_version_wire"] = str(
            frozen_selection.get("selection_version") or ""
        )
        item["boundary_selection_fingerprint_wire"] = str(
            frozen_selection.get("fingerprint") or ""
        )
        item["boundary_selection_count_wire"] = len(
            frozen_selection.get("boundaries") or []
        )
    frozen_manifest = (
        request_payload.get("frozen_boundary_manifest")
        if isinstance(request_payload, dict)
        else None
    )
    if isinstance(frozen_manifest, dict):
        item["boundary_manifest_fingerprint_wire"] = str(
            frozen_manifest.get("fingerprint") or ""
        )
    if evaluation is not None:
        item.update(dict(evaluation.diagnostics))
    return item


def _compile_unit(
    *,
    client: Any,
    phase: Literal["boundary_selection", "membership", "binding"],
    prompt: str,
    user_input_builder: Callable[..., str],
    normalizer: Callable[[dict[str, Any]], dict[str, Any]],
    envelope_prefix: str,
    shard_index: int,
    shard_count: int,
    target_fact_count: int,
    db: Any,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
    recompile_feedback_projector: (
        Callable[[dict[str, Any]], list[dict[str, Any]]] | None
    ) = None,
) -> _CompilationUnit:
    attempts: list[dict[str, Any]] = []
    envelope_results: list[EnvelopeCallResult] = []
    fresh_candidate_trigger_codes: list[str] = []
    final_status: ScopeLedgerCompileStatus = "contract_invalid"
    normalized_payload: dict[str, Any] = {}
    validated_attempt = 0
    last_parseable_candidate: _ParseableCandidateSnapshot | None = None
    recompile_contract_feedback: list[dict[str, Any]] = []

    for attempt in range(1, MAX_SCOPE_LEDGER_CANDIDATE_ENVELOPES + 1):
        if attempt > 1 and not fresh_candidate_trigger_codes:
            break
        candidate_mode = "initial" if attempt == 1 else "fresh_candidate"
        compilation_mode = (
            "initial" if attempt == 1 else "independent_recompile"
        )
        user_input_kwargs: dict[str, Any] = {
            "attempt": attempt,
            "compilation_mode": compilation_mode,
            "recompile_reason_codes": (
                fresh_candidate_trigger_codes if attempt > 1 else []
            ),
        }
        if recompile_feedback_projector is not None:
            user_input_kwargs["recompile_contract_feedback"] = (
                recompile_contract_feedback if attempt > 1 else []
            )
        user_input = user_input_builder(**user_input_kwargs)
        envelope = invoke_model_envelope(
            client=client,
            envelope_id=f"{envelope_prefix}-{candidate_mode}",
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
            attempts.append(
                _attempt_diagnostic(
                    attempt=attempt,
                    candidate_mode=candidate_mode,
                    compilation_mode=compilation_mode,
                    phase=phase,
                    shard_index=shard_index,
                    shard_count=shard_count,
                    target_fact_count=target_fact_count,
                    system_prompt=prompt,
                    user_input=user_input,
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
                normalized_payload={},
                diagnostics={
                    "parse_error_code": f"scope_{phase}_output_truncated",
                },
                retry_reason_codes=(),
            )
        elif response_termination == "incomplete":
            evaluation = _CandidateEvaluation(
                status="output_incomplete",
                normalized_payload={},
                diagnostics={
                    "parse_error_code": f"scope_{phase}_output_incomplete",
                },
                retry_reason_codes=(),
            )
        else:
            evaluation = _evaluate_json_candidate(
                envelope.raw_text,
                phase=phase,
                normalizer=normalizer,
            )
        attempts.append(
            _attempt_diagnostic(
                attempt=attempt,
                candidate_mode=candidate_mode,
                compilation_mode=compilation_mode,
                phase=phase,
                shard_index=shard_index,
                shard_count=shard_count,
                target_fact_count=target_fact_count,
                system_prompt=prompt,
                user_input=user_input,
                envelope=envelope,
                evaluation=evaluation,
            )
        )
        final_status = evaluation.status
        normalized_payload = evaluation.normalized_payload
        if evaluation.status in {"validated", "contract_invalid"}:
            normalized_diagnostics = dict(
                evaluation.normalized_payload.get("diagnostics") or {}
            )
            last_parseable_candidate = _ParseableCandidateSnapshot(
                attempt=attempt,
                status=evaluation.status,
                fingerprint=str(
                    evaluation.normalized_payload.get("fingerprint") or ""
                ),
                error_codes=tuple(
                    str(item)
                    for item in normalized_diagnostics.get("error_codes") or []
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
            if (
                recompile_feedback_projector is not None
                and evaluation.normalized_payload
            ):
                recompile_contract_feedback = recompile_feedback_projector(
                    evaluation.normalized_payload
                )

    return _CompilationUnit(
        status=final_status,
        normalized_payload=normalized_payload,
        attempts=tuple(attempts),
        envelope_results=tuple(envelope_results),
        fresh_candidate_trigger_codes=tuple(fresh_candidate_trigger_codes),
        validated_attempt=validated_attempt,
        last_parseable_candidate=last_parseable_candidate,
    )


def _result(
    *,
    status: ScopeLedgerCompileStatus,
    normalized_ledger: dict[str, Any],
    projection: dict[str, Any],
    attempts: list[dict[str, Any]],
    envelope_results: list[EnvelopeCallResult],
    fact_ledger_version: str,
    fact_ledger_fingerprint: str,
    max_tokens: int,
    request_timeout_seconds: float,
    fresh_candidate_trigger_codes: list[str],
    validated_attempt: int,
    last_parseable_candidate: _ParseableCandidateSnapshot | None,
    boundary_manifest: dict[str, Any],
    binding_shards: list[_BindingShard],
    completed_binding_shards: int,
    failed_binding_shard_index: int,
    binding_shard_summaries: list[dict[str, Any]],
    binding_budget_units: int,
    oversized_binding_fact_count: int,
    global_status: str,
    global_error_codes: list[str],
    boundary_selection: dict[str, Any] | None = None,
) -> RequirementScopeLedgerCompilationResult:
    success = status == "validated"
    published_ledger = normalized_ledger if success else {}
    published_projection = projection if success else {}
    ledger_diagnostics = dict(published_ledger.get("diagnostics") or {})
    boundary_diagnostics = dict(boundary_manifest.get("diagnostics") or {})
    selection = dict(boundary_selection or {})
    selection_diagnostics = dict(selection.get("diagnostics") or {})
    membership_attempts = [
        item for item in attempts if item.get("phase") == "membership"
    ]
    topology_attempt = next(
        (
            item
            for item in attempts
            if item.get("source_topology_wire_present") is True
        ),
        {},
    )
    binding_role_counts: dict[str, int] = {}
    for binding in published_ledger.get("fact_bindings") or []:
        if not isinstance(binding, dict):
            continue
        role = str(binding.get("role") or "")
        if role:
            binding_role_counts[role] = binding_role_counts.get(role, 0) + 1
    candidate_attempt_limit = (
        (2 + len(binding_shards)) * MAX_SCOPE_LEDGER_CANDIDATE_ENVELOPES
    )
    diagnostics = {
        "scope_ledger_compile_status": status,
        "scope_ledger_compile_success": success,
        "scope_ledger_compile_mode": (
            "global_boundary_selection_then_membership_then_binding_shards"
        ),
        "scope_ledger_compile_envelope_count": len(envelope_results),
        "scope_ledger_compile_candidate_attempt_count": len(attempts),
        "scope_ledger_compile_candidate_attempt_limit": candidate_attempt_limit,
        "scope_ledger_compile_physical_call_count": sum(
            item.physical_call_count for item in envelope_results
        ),
        "scope_ledger_compile_provider_call_count": sum(
            item.provider_call_count for item in envelope_results
        ),
        "scope_ledger_compile_cache_hit_count": sum(
            item.cache_hit_count for item in envelope_results
        ),
        "scope_ledger_compile_cache_miss_count": sum(
            item.cache_miss_count for item in envelope_results
        ),
        "scope_ledger_compile_cache_bypass_count": sum(
            item.cache_bypass_count for item in envelope_results
        ),
        "scope_ledger_compile_transport_failure_count": sum(
            item.transport_failure_count for item in envelope_results
        ),
        "scope_ledger_compile_transport_retry_count": sum(
            item.transport_retry_count for item in envelope_results
        ),
        "scope_ledger_compile_transport_replays_per_envelope": (
            MAX_TRANSPORT_REPLAYS_PER_ENVELOPE
        ),
        "scope_ledger_compile_fresh_candidate_used": any(
            item.get("candidate_mode") == "fresh_candidate"
            for item in attempts
        ),
        "scope_ledger_compile_fresh_candidate_trigger_codes": list(
            fresh_candidate_trigger_codes
        ),
        "scope_ledger_compile_validated_attempt": int(validated_attempt),
        "scope_ledger_compile_last_parseable_candidate_attempt": int(
            last_parseable_candidate.attempt
            if last_parseable_candidate is not None
            else 0
        ),
        "scope_ledger_compile_last_parseable_candidate_status": (
            last_parseable_candidate.status
            if last_parseable_candidate is not None
            else ""
        ),
        "scope_ledger_compile_last_parseable_candidate_fingerprint": (
            last_parseable_candidate.fingerprint
            if last_parseable_candidate is not None
            else ""
        ),
        "scope_ledger_compile_last_parseable_candidate_error_codes": (
            list(last_parseable_candidate.error_codes)
            if last_parseable_candidate is not None
            else []
        ),
        "scope_ledger_compile_stop_reason": "" if success else status,
        "scope_ledger_compile_attempts": list(attempts),
        "scope_ledger_compile_global_status": str(global_status),
        "scope_ledger_compile_global_error_codes": list(global_error_codes),
        "scope_ledger_source_topology": {
            "version": str(topology_attempt.get("source_topology_version") or ""),
            "fingerprint": str(
                topology_attempt.get("source_topology_fingerprint") or ""
            ),
            "group_count": int(
                topology_attempt.get("source_topology_group_count") or 0
            ),
            "relation_count": int(
                topology_attempt.get("source_topology_relation_count") or 0
            ),
            "anchored_fact_count": int(
                topology_attempt.get("source_topology_anchored_fact_count") or 0
            ),
        },
        "scope_ledger_fact_ledger_version": str(fact_ledger_version),
        "scope_ledger_fact_ledger_fingerprint": str(fact_ledger_fingerprint),
        "scope_ledger_compile_max_tokens": int(max_tokens),
        "scope_ledger_compile_request_timeout_seconds": float(
            request_timeout_seconds
        ),
        "scope_ledger_boundary_selection_status": (
            "validated" if selection.get("valid") is True else ""
        ),
        "scope_ledger_boundary_selection_fingerprint": str(
            selection.get("fingerprint") or ""
        ),
        "scope_ledger_boundary_selection_count": int(
            selection_diagnostics.get("boundary_count") or 0
        ),
        "scope_ledger_membership_assignment_status": (
            str(membership_attempts[-1].get("status") or "")
            if membership_attempts
            else ""
        ),
        "scope_ledger_membership_assignment_fingerprint": str(
            boundary_diagnostics.get("membership_assignment_fingerprint") or ""
        ),
        "scope_ledger_membership_assignment_count": int(
            boundary_diagnostics.get("membership_assignment_count") or 0
        ),
        "scope_ledger_membership_none_count": int(
            boundary_diagnostics.get("membership_none_count") or 0
        ),
        "scope_ledger_boundary_manifest_status": (
            "validated" if boundary_manifest.get("valid") is True else ""
        ),
        "scope_ledger_boundary_manifest_fingerprint": str(
            boundary_manifest.get("fingerprint") or ""
        ),
        "scope_ledger_boundary_count": int(
            boundary_diagnostics.get("boundary_count")
            or ledger_diagnostics.get("boundary_count")
            or selection_diagnostics.get("boundary_count")
            or 0
        ),
        "scope_ledger_binding_shard_count": len(binding_shards),
        "scope_ledger_binding_shard_budget_units": int(binding_budget_units),
        "scope_ledger_binding_oversized_fact_count": int(
            oversized_binding_fact_count
        ),
        "scope_ledger_binding_completed_shard_count": int(
            completed_binding_shards
        ),
        "scope_ledger_binding_failed_shard_index": int(
            failed_binding_shard_index
        ),
        "scope_ledger_binding_shard_summaries": list(
            binding_shard_summaries
        ),
        "scope_ledger_binding_projected_context_scope_id_count": sum(
            int(
                item.get("projected_non_scope_context_scope_id_count")
                or 0
            )
            for item in binding_shard_summaries
            if isinstance(item, dict)
        ),
        "scope_ledger_fingerprint": (
            str(published_ledger.get("fingerprint") or "") if success else ""
        ),
        "scope_ledger_fact_count": int(
            ledger_diagnostics.get("fact_count")
            or boundary_diagnostics.get("fact_count")
            or 0
        ),
        "scope_ledger_active_scope_count": int(
            ledger_diagnostics.get("active_scope_count")
            or boundary_diagnostics.get("active_scope_count")
            or 0
        ),
        "scope_ledger_external_boundary_count": int(
            ledger_diagnostics.get("external_boundary_count")
            or boundary_diagnostics.get("external_boundary_count")
            or 0
        ),
        "scope_ledger_fact_binding_count": int(
            ledger_diagnostics.get("fact_binding_count") or 0
        ),
        "scope_ledger_binding_role_counts": dict(
            sorted(binding_role_counts.items())
        ),
        "scope_ledger_membership_relation_count": int(
            boundary_diagnostics.get("membership_relation_count") or 0
        ),
        "scope_ledger_explicit_fact_membership_count": int(
            boundary_diagnostics.get("explicit_fact_membership_count") or 0
        ),
        "scope_ledger_boundary_topology_summary": [
            {
                "boundary_id": str(item.get("boundary_id") or ""),
                "parent_boundary_id": str(
                    item.get("parent_boundary_id") or ""
                ),
                "decision": str(item.get("decision") or ""),
                "membership_relation_ids": list(
                    item.get("membership_relation_ids") or []
                ),
                "membership_fact_count": len(
                    item.get("membership_fact_ids") or []
                ),
                "support_fact_count": sum(
                    len(support.get("fact_ids") or [])
                    for support in item.get("support") or []
                    if isinstance(support, dict)
                ),
            }
            for item in boundary_manifest.get("boundaries") or []
            if isinstance(item, dict)
        ],
    }
    return RequirementScopeLedgerCompilationResult(
        normalized_ledger=published_ledger,
        projection=published_projection,
        diagnostics=diagnostics,
    )


def compile_requirement_scope_ledger(
    *,
    client: Any,
    normalized_fact_ledger: dict[str, Any],
    source_evidence_catalog: Any,
    db: Any = None,
    max_tokens: int = DEFAULT_SCOPE_LEDGER_MAX_TOKENS,
    task_type: str = "generation",
    request_timeout_seconds: float = (
        DEFAULT_SCOPE_LEDGER_REQUEST_TIMEOUT_SECONDS
    ),
) -> RequirementScopeLedgerCompilationResult:
    """
    三段式编译 A2：先仅用全局事实冻结职责选择，再分配单一 membership
    证据，最后按 fact ID 互斥分片生成 bindings。任一阶段失败都不发布部分 ledger。
    """

    normalized_max_tokens = _positive_int(max_tokens, field="max_tokens")
    normalized_timeout = _positive_float(
        request_timeout_seconds,
        field="request_timeout_seconds",
    )
    fact_ledger_version = (
        str(normalized_fact_ledger.get("fact_ledger_version") or "")
        if isinstance(normalized_fact_ledger, dict)
        else ""
    )
    fact_ledger_fingerprint = (
        str(normalized_fact_ledger.get("fingerprint") or "")
        if isinstance(normalized_fact_ledger, dict)
        else ""
    )

    # user input builder 在任何模型调用前完成 A1 指纹和冻结字段验证。
    selection_prompt = build_requirement_scope_boundary_selection_prompt()
    build_requirement_scope_boundary_selection_user_input(
        normalized_fact_ledger,
    )
    frozen_facts = list(normalized_fact_ledger.get("evidence_facts") or [])
    # binding 分片沿 A1 冻结来源顺序传播；哈希 fact_id 仅用于身份与集合校验。
    fact_ids = [str(item.get("fact_id") or "") for item in frozen_facts]

    attempts: list[dict[str, Any]] = []
    envelope_results: list[EnvelopeCallResult] = []
    fresh_candidate_trigger_codes: list[str] = []
    last_parseable_candidate: _ParseableCandidateSnapshot | None = None
    boundary_selection: dict[str, Any] = {}
    boundary_manifest: dict[str, Any] = {}
    binding_shards: list[_BindingShard] = []
    binding_shard_summaries: list[dict[str, Any]] = []
    completed_binding_shards = 0
    failed_binding_shard_index = 0
    binding_budget_units = 0
    oversized_binding_fact_count = 0

    selection_unit = _compile_unit(
        client=client,
        phase="boundary_selection",
        prompt=selection_prompt,
        user_input_builder=lambda **kwargs: (
            build_requirement_scope_boundary_selection_user_input(
                normalized_fact_ledger,
                **kwargs,
            )
        ),
        normalizer=lambda candidate: normalize_requirement_scope_boundary_selection_model_response(
            candidate,
            normalized_fact_ledger=normalized_fact_ledger,
        ),
        envelope_prefix=(
            "requirement-scope-boundary-selection-"
            f"{fact_ledger_fingerprint[:12]}"
        ),
        shard_index=0,
        shard_count=0,
        target_fact_count=len(fact_ids),
        db=db,
        max_tokens=normalized_max_tokens,
        task_type=str(task_type or "generation"),
        request_timeout_seconds=normalized_timeout,
    )
    attempts.extend(selection_unit.attempts)
    envelope_results.extend(selection_unit.envelope_results)
    fresh_candidate_trigger_codes.extend(
        selection_unit.fresh_candidate_trigger_codes
    )
    last_parseable_candidate = selection_unit.last_parseable_candidate
    if selection_unit.status != "validated":
        return _result(
            status=selection_unit.status,
            normalized_ledger={},
            projection={},
            attempts=attempts,
            envelope_results=envelope_results,
            fact_ledger_version=fact_ledger_version,
            fact_ledger_fingerprint=fact_ledger_fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            boundary_manifest={},
            binding_shards=[],
            completed_binding_shards=0,
            failed_binding_shard_index=0,
            binding_shard_summaries=[],
            binding_budget_units=0,
            oversized_binding_fact_count=0,
            global_status="boundary_selection_failed",
            global_error_codes=[
                f"scope_boundary_selection_{selection_unit.status}"
            ],
            boundary_selection=selection_unit.normalized_payload,
        )

    boundary_selection = selection_unit.normalized_payload
    selection_fingerprint = str(boundary_selection.get("fingerprint") or "")
    membership_target_count = sum(
        1
        for item in boundary_selection.get("boundaries") or []
        if isinstance(item, dict)
        and str(item.get("parent_boundary_id") or "")
    )
    membership_prompt = build_requirement_scope_membership_prompt()
    build_requirement_scope_membership_user_input(
        normalized_fact_ledger,
        boundary_selection,
        source_evidence_catalog=source_evidence_catalog,
    )
    membership_unit = _compile_unit(
        client=client,
        phase="membership",
        prompt=membership_prompt,
        user_input_builder=lambda **kwargs: (
            build_requirement_scope_membership_user_input(
                normalized_fact_ledger,
                boundary_selection,
                source_evidence_catalog=source_evidence_catalog,
                **kwargs,
            )
        ),
        normalizer=lambda candidate: normalize_requirement_scope_membership_model_response(
            candidate,
            normalized_fact_ledger=normalized_fact_ledger,
            boundary_selection=boundary_selection,
            source_evidence_catalog=source_evidence_catalog,
        ),
        envelope_prefix=(
            "requirement-scope-membership-"
            f"{fact_ledger_fingerprint[:12]}-"
            f"{selection_fingerprint[:12]}"
        ),
        shard_index=0,
        shard_count=0,
        target_fact_count=membership_target_count,
        db=db,
        max_tokens=normalized_max_tokens,
        task_type=str(task_type or "generation"),
        request_timeout_seconds=normalized_timeout,
    )
    attempts.extend(membership_unit.attempts)
    envelope_results.extend(membership_unit.envelope_results)
    for code in membership_unit.fresh_candidate_trigger_codes:
        if code not in fresh_candidate_trigger_codes:
            fresh_candidate_trigger_codes.append(code)
    if membership_unit.last_parseable_candidate is not None:
        last_parseable_candidate = membership_unit.last_parseable_candidate
    if membership_unit.status != "validated":
        return _result(
            status=membership_unit.status,
            normalized_ledger={},
            projection={},
            attempts=attempts,
            envelope_results=envelope_results,
            fact_ledger_version=fact_ledger_version,
            fact_ledger_fingerprint=fact_ledger_fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            boundary_manifest=membership_unit.normalized_payload,
            binding_shards=[],
            completed_binding_shards=0,
            failed_binding_shard_index=0,
            binding_shard_summaries=[],
            binding_budget_units=0,
            oversized_binding_fact_count=0,
            global_status="membership_assignment_failed",
            global_error_codes=[
                f"scope_membership_{membership_unit.status}"
            ],
            boundary_selection=boundary_selection,
        )

    boundary_manifest = membership_unit.normalized_payload
    declared_manifest_fingerprint = str(
        boundary_manifest.get("fingerprint") or ""
    )
    if (
        not declared_manifest_fingerprint
        or fingerprint_requirement_scope_boundary_manifest(boundary_manifest)
        != declared_manifest_fingerprint
    ):
        return _result(
            status="contract_invalid",
            normalized_ledger={},
            projection={},
            attempts=attempts,
            envelope_results=envelope_results,
            fact_ledger_version=fact_ledger_version,
            fact_ledger_fingerprint=fact_ledger_fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            boundary_manifest={},
            binding_shards=[],
            completed_binding_shards=0,
            failed_binding_shard_index=0,
            binding_shard_summaries=[],
            binding_budget_units=0,
            oversized_binding_fact_count=0,
            global_status="boundary_manifest_fingerprint_invalid",
            global_error_codes=["scope_boundary_manifest_fingerprint_invalid"],
            boundary_selection=boundary_selection,
        )

    binding_shards, binding_budget_units, oversized_binding_fact_count = (
        _partition_binding_targets(
            fact_ids,
            boundary_manifest=boundary_manifest,
            max_tokens=normalized_max_tokens,
        )
    )
    if oversized_binding_fact_count > 0:
        error_codes = []
        if oversized_binding_fact_count > 0:
            error_codes.append("scope_binding_item_capacity_exceeded")
        return _result(
            status="capacity_exceeded",
            normalized_ledger={},
            projection={},
            attempts=attempts,
            envelope_results=envelope_results,
            fact_ledger_version=fact_ledger_version,
            fact_ledger_fingerprint=fact_ledger_fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            boundary_manifest=boundary_manifest,
            binding_shards=binding_shards,
            completed_binding_shards=0,
            failed_binding_shard_index=(1 if binding_shards else 0),
            binding_shard_summaries=[],
            binding_budget_units=binding_budget_units,
            oversized_binding_fact_count=oversized_binding_fact_count,
            global_status="binding_capacity_exceeded",
            global_error_codes=error_codes,
            boundary_selection=boundary_selection,
        )

    merged_bindings: list[dict[str, Any]] = []
    binding_prompt = build_requirement_scope_binding_prompt()
    final_validated_attempt = max(
        selection_unit.validated_attempt,
        membership_unit.validated_attempt,
    )
    for shard in binding_shards:
        unit = _compile_unit(
            client=client,
            phase="binding",
            prompt=binding_prompt,
            user_input_builder=lambda *, _shard=shard, **kwargs: (
                build_requirement_scope_binding_user_input(
                    normalized_fact_ledger,
                    boundary_manifest,
                    list(_shard.target_fact_ids),
                    source_evidence_catalog=source_evidence_catalog,
                    **kwargs,
                )
            ),
            normalizer=lambda candidate, _shard=shard: (
                normalize_requirement_scope_binding_shard(
                    candidate,
                    normalized_fact_ledger=normalized_fact_ledger,
                    boundary_manifest=boundary_manifest,
                    target_fact_ids=list(_shard.target_fact_ids),
                    source_evidence_catalog=source_evidence_catalog,
                )
            ),
            envelope_prefix=(
                "requirement-scope-binding-"
                f"{fact_ledger_fingerprint[:12]}-"
                f"{declared_manifest_fingerprint[:12]}-"
                f"{shard.fingerprint[:12]}-"
                f"shard-{shard.index:03d}-of-{shard.count:03d}"
            ),
            shard_index=shard.index,
            shard_count=shard.count,
            target_fact_count=len(shard.target_fact_ids),
            db=db,
            max_tokens=normalized_max_tokens,
            task_type=str(task_type or "generation"),
            request_timeout_seconds=normalized_timeout,
            recompile_feedback_projector=lambda normalized_shard: (
                project_requirement_scope_binding_recompile_feedback(
                    normalized_fact_ledger,
                    normalized_shard,
                )
            ),
        )
        attempts.extend(unit.attempts)
        envelope_results.extend(unit.envelope_results)
        for code in unit.fresh_candidate_trigger_codes:
            if code not in fresh_candidate_trigger_codes:
                fresh_candidate_trigger_codes.append(code)
        if unit.last_parseable_candidate is not None:
            last_parseable_candidate = unit.last_parseable_candidate
        final_validated_attempt = max(
            final_validated_attempt,
            unit.validated_attempt,
        )
        unit_diagnostics = dict(unit.normalized_payload.get("diagnostics") or {})
        summary = {
            "shard_index": shard.index,
            "status": unit.status,
            "target_fact_count": len(shard.target_fact_ids),
            "budget_units": shard.budget_units,
            "target_fingerprint": shard.fingerprint,
            "candidate_attempt_count": len(unit.attempts),
            "envelope_count": len(unit.envelope_results),
            "physical_call_count": sum(
                item.physical_call_count for item in unit.envelope_results
            ),
            "provider_call_count": sum(
                item.provider_call_count for item in unit.envelope_results
            ),
            "cache_hit_count": sum(
                item.cache_hit_count for item in unit.envelope_results
            ),
            "cache_miss_count": sum(
                item.cache_miss_count for item in unit.envelope_results
            ),
            "validated_attempt": unit.validated_attempt,
            "binding_count": int(
                unit_diagnostics.get("fact_binding_count") or 0
            ),
            "projected_non_scope_context_binding_count": int(
                unit_diagnostics.get(
                    "projected_non_scope_context_binding_count"
                )
                or 0
            ),
            "projected_non_scope_context_scope_id_count": int(
                unit_diagnostics.get(
                    "projected_non_scope_context_scope_id_count"
                )
                or 0
            ),
            "payload_fingerprint": (
                str(unit.normalized_payload.get("fingerprint") or "")
                if unit.status == "validated"
                else ""
            ),
        }
        binding_shard_summaries.append(summary)
        if unit.status != "validated":
            failed_binding_shard_index = shard.index
            return _result(
                status=unit.status,
                normalized_ledger={},
                projection={},
                attempts=attempts,
                envelope_results=envelope_results,
                fact_ledger_version=fact_ledger_version,
                fact_ledger_fingerprint=fact_ledger_fingerprint,
                max_tokens=normalized_max_tokens,
                request_timeout_seconds=normalized_timeout,
                fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
                validated_attempt=0,
                last_parseable_candidate=last_parseable_candidate,
                boundary_manifest=boundary_manifest,
                binding_shards=binding_shards,
                completed_binding_shards=completed_binding_shards,
                failed_binding_shard_index=failed_binding_shard_index,
                binding_shard_summaries=binding_shard_summaries,
                binding_budget_units=binding_budget_units,
                oversized_binding_fact_count=oversized_binding_fact_count,
                global_status="binding_shard_failed",
                global_error_codes=[f"scope_binding_{unit.status}"],
                boundary_selection=boundary_selection,
            )
        merged_bindings.extend(
            dict(item)
            for item in unit.normalized_payload.get("fact_bindings") or []
            if isinstance(item, dict)
        )
        completed_binding_shards += 1

    merged_fact_ids = [str(item.get("fact_id") or "") for item in merged_bindings]
    if merged_fact_ids != fact_ids or len(set(merged_fact_ids)) != len(
        merged_fact_ids
    ):
        return _result(
            status="contract_invalid",
            normalized_ledger={},
            projection={},
            attempts=attempts,
            envelope_results=envelope_results,
            fact_ledger_version=fact_ledger_version,
            fact_ledger_fingerprint=fact_ledger_fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            boundary_manifest=boundary_manifest,
            binding_shards=binding_shards,
            completed_binding_shards=completed_binding_shards,
            failed_binding_shard_index=0,
            binding_shard_summaries=binding_shard_summaries,
            binding_budget_units=binding_budget_units,
            oversized_binding_fact_count=oversized_binding_fact_count,
            global_status="binding_union_invalid",
            global_error_codes=["scope_binding_union_invalid"],
            boundary_selection=boundary_selection,
        )

    normalized_ledger = normalize_requirement_scope_ledger(
        {
            "boundaries": list(boundary_manifest.get("boundaries") or []),
            "fact_bindings": sorted(
                merged_bindings,
                key=lambda item: str(item.get("fact_id") or ""),
            ),
        },
        normalized_fact_ledger=normalized_fact_ledger,
        source_evidence_catalog=source_evidence_catalog,
    )
    if normalized_ledger.get("valid") is not True:
        final_error_codes = list(
            (normalized_ledger.get("diagnostics") or {}).get("error_codes")
            or []
        )
        return _result(
            status="contract_invalid",
            normalized_ledger={},
            projection={},
            attempts=attempts,
            envelope_results=envelope_results,
            fact_ledger_version=fact_ledger_version,
            fact_ledger_fingerprint=fact_ledger_fingerprint,
            max_tokens=normalized_max_tokens,
            request_timeout_seconds=normalized_timeout,
            fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
            validated_attempt=0,
            last_parseable_candidate=last_parseable_candidate,
            boundary_manifest=boundary_manifest,
            binding_shards=binding_shards,
            completed_binding_shards=completed_binding_shards,
            failed_binding_shard_index=0,
            binding_shard_summaries=binding_shard_summaries,
            binding_budget_units=binding_budget_units,
            oversized_binding_fact_count=oversized_binding_fact_count,
            global_status="final_closure_invalid",
            global_error_codes=final_error_codes,
            boundary_selection=boundary_selection,
        )

    projection = project_requirement_scope_ledger(normalized_ledger)
    return _result(
        status="validated",
        normalized_ledger=normalized_ledger,
        projection=projection,
        attempts=attempts,
        envelope_results=envelope_results,
        fact_ledger_version=fact_ledger_version,
        fact_ledger_fingerprint=fact_ledger_fingerprint,
        max_tokens=normalized_max_tokens,
        request_timeout_seconds=normalized_timeout,
        fresh_candidate_trigger_codes=fresh_candidate_trigger_codes,
        validated_attempt=final_validated_attempt,
        last_parseable_candidate=last_parseable_candidate,
        boundary_manifest=boundary_manifest,
        binding_shards=binding_shards,
        completed_binding_shards=completed_binding_shards,
        failed_binding_shard_index=0,
        binding_shard_summaries=binding_shard_summaries,
        binding_budget_units=binding_budget_units,
        oversized_binding_fact_count=oversized_binding_fact_count,
        global_status="validated",
        global_error_codes=[],
        boundary_selection=boundary_selection,
    )


__all__ = [
    "DEFAULT_SCOPE_LEDGER_MAX_TOKENS",
    "DEFAULT_SCOPE_LEDGER_REQUEST_TIMEOUT_SECONDS",
    "MAX_SCOPE_LEDGER_CANDIDATE_ENVELOPES",
    "RequirementScopeLedgerCompilationResult",
    "ScopeLedgerCompileStatus",
    "compile_requirement_scope_ledger",
]
