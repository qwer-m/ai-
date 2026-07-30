from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from .model_envelope_call import (
    MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
    EnvelopeCallResult,
    classify_response_termination,
    invoke_model_envelope,
)
from .requirement_graph_stage_contract import (
    RequirementGraphStageContractError,
    assemble_requirement_graph_stage_response,
    build_requirement_graph_stage_prompt,
    build_requirement_graph_stage_user_input,
    validate_requirement_graph_stage_projection,
)
from .semantic_retry_topology_guard import (
    SemanticRetryTopologyGuard,
    compile_semantic_retry_repair_targets,
)


GraphStageCompileStatus = Literal[
    "validated",
    "parse_failed",
    "response_contract_invalid",
    "contract_invalid",
    "projection_invalid",
    "output_truncated",
    "output_incomplete",
    "targeted_repair_unavailable",
    "retry_topology_drift_blocked",
    "candidate_evaluator_failed",
    "candidate_evaluator_contract_invalid",
    "recompile_resolver_failed",
    "transport_exhausted",
    "fatal_model_error",
]
CandidateEvaluator = Callable[[dict[str, Any]], dict[str, Any]]
IndependentRecompileCodeResolver = Callable[[dict[str, Any]], list[str]]

DEFAULT_GRAPH_STAGE_MAX_TOKENS = 8192
DEFAULT_GRAPH_STAGE_REQUEST_TIMEOUT_SECONDS = 240.0
DEFAULT_GRAPH_STAGE_PARTITION_FACT_THRESHOLD = 160
MAX_GRAPH_STAGE_CANDIDATE_ENVELOPES = 3
MAX_GRAPH_STAGE_TARGETED_REPAIRS = 1
MAX_GRAPH_STAGE_INDEPENDENT_RECOMPILES = 1

_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
_GRAPH_STAGE_CONTRACT_CODE_PREFIX = "graph_stage_"
_RETRY_FEEDBACK_KEYS = (
    "semantic_graph_rejections",
    "workflow_consistency_rejections",
    "typed_state_rejections",
    "workflow_rejection_reasons",
)
_COMPACT_FAILURE_DIAGNOSTIC_KEYS = (
    "evaluation_status",
    "workflow_declaration_status",
    "workflow_rejection_count",
    "workflow_rejection_codes",
    "semantic_graph_rejection_count",
    "semantic_graph_rejection_codes",
    "workflow_consistency_rejection_count",
    "workflow_consistency_rejection_codes",
    "typed_state_rejection_count",
    "typed_state_rejection_codes",
    "workflow_topology_status",
    "workflow_topology_error_count",
    "workflow_topology_error_codes",
    "projection_error_count",
    "projection_error_codes",
    "raw_workflow_candidate_count",
    "normalized_workflow_count",
    "rejected_workflow_count",
    "verified_functional_module_count",
)
_ATTEMPT_EVALUATION_DIAGNOSTIC_KEYS = (
    *_COMPACT_FAILURE_DIAGNOSTIC_KEYS,
    "parse_error_code",
    "parse_error_type",
    "candidate_evaluator_error_code",
    "candidate_evaluator_error_type",
    "candidate_evaluator_result_type",
    "normalized_semantic_contract_missing",
    "frozen_fact_contract_match",
    "frozen_fact_count",
    "evaluated_fact_count",
    "missing_frozen_fact_ids",
    "extra_evaluated_fact_ids",
    "changed_frozen_fact_ids",
    "evaluation_valid",
    "projection_valid",
    "retry_feedback_count",
    "repair_target_count",
    "repair_target_codes",
    "forbidden_topology_change_count",
    "independent_recompile_codes",
    "recompile_resolver_error_code",
    "recompile_resolver_error_type",
)
_TOPOLOGY_GUARD_DIAGNOSTIC_KEYS = (
    "applicable",
    "allowed",
    "decision",
    "anchor_created",
    "topology_changed",
    "parseable_candidate_count",
    "topology_diff_count",
    "allowed_diff_count",
    "blocked_diff_count",
    "diff_diagnostics_truncated",
)


@dataclass(frozen=True)
class RequirementGraphStageCompilationResult:
    """阶段 B 编译结果；只有 success 为真时才可进入发布路径。"""

    assembled_candidate: dict[str, Any]
    evaluation: dict[str, Any]
    diagnostics: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.diagnostics.get("semantic_compile_success") is True

    @property
    def status(self) -> str:
        return str(
            self.diagnostics.get("semantic_compile_status")
            or "contract_invalid"
        )


@dataclass(frozen=True)
class _CandidateEvaluation:
    status: GraphStageCompileStatus
    assembled_candidate: dict[str, Any]
    evaluation: dict[str, Any]
    normalized_semantic_contract: dict[str, Any]
    projection: dict[str, Any]
    retry_feedback: tuple[Any, ...]
    repair_targets: tuple[dict[str, Any], ...]
    forbidden_topology_changes: tuple[dict[str, Any], ...]
    independent_recompile_codes: tuple[str, ...]
    diagnostics: dict[str, Any]


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


def _reason_codes(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise TypeError("independent_recompile_code_resolver 必须返回字符串列表")
    output: list[str] = []
    for raw_item in value[:32]:
        item = str(raw_item or "").strip().lower()[:120]
        if item and _REASON_CODE_PATTERN.fullmatch(item) and item not in output:
            output.append(item)
    return output


def _diagnostic_code(value: Any) -> str:
    """只允许稳定机器码进入紧凑诊断，避免把候选文本带入日志。"""

    code = str(value or "").strip().lower()[:120]
    return code if _REASON_CODE_PATTERN.fullmatch(code) else ""


def _diagnostic_codes(value: Any, *, limit: int = 32) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for raw_item in value:
        code = _diagnostic_code(raw_item)
        if code and code not in output:
            output.append(code)
        if len(output) >= max(1, int(limit)):
            break
    return sorted(output)


def _rejection_codes(value: Any, *, limit: int = 32) -> list[str]:
    """仅提取拒绝项的稳定机器码；不携带路径、ID 或原始文本。"""

    if not isinstance(value, list):
        return []
    output: list[str] = []
    for raw_item in value:
        raw_code = (
            raw_item.get("code") or raw_item.get("reason")
            if isinstance(raw_item, dict)
            else raw_item
        )
        code = _diagnostic_code(raw_code)
        if not code:
            # workflow 规范化器会使用 `workflow_N:code` 表示定位，
            # 紧凑诊断只保留冒号后的稳定 code。
            suffix = str(raw_code or "").rsplit(":", 1)[-1]
            code = _diagnostic_code(suffix)
        if code and code not in output:
            output.append(code)
        if len(output) >= max(1, int(limit)):
            break
    return sorted(output)


def _semantic_graph_diagnostics(evaluation: dict[str, Any]) -> dict[str, Any]:
    semantic_contract = evaluation.get("semantic_contract")
    semantic_contract = (
        dict(semantic_contract) if isinstance(semantic_contract, dict) else {}
    )
    graph_validation = semantic_contract.get("semantic_graph_validation")
    graph_validation = (
        dict(graph_validation) if isinstance(graph_validation, dict) else {}
    )
    normalization = evaluation.get("normalization_diagnostics")
    normalization = dict(normalization) if isinstance(normalization, dict) else {}
    # 主链返回完整语义契约；可复用调用方也可直接返回 graph
    # normalizer 契约。只选择确实包含拓扑摘要的已有诊断。
    candidates = (
        graph_validation.get("diagnostics"),
        semantic_contract.get("diagnostics"),
        normalization.get("semantic_graph_diagnostics"),
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if any(
            key in candidate
            for key in (
                "workflow_topology_status",
                "workflow_topology_error_codes",
            )
        ):
            return dict(candidate)
    return {}


def _compact_evaluation_diagnostics(
    evaluation: dict[str, Any],
    *,
    projection_codes: list[str],
) -> dict[str, Any]:
    """仅提炼已有的状态、机器码和计数，不保存原始拒绝对象。"""

    output: dict[str, Any] = {}
    evaluation_status = _diagnostic_code(evaluation.get("status"))
    if evaluation_status:
        output["evaluation_status"] = evaluation_status
    declaration_status = _diagnostic_code(
        evaluation.get("workflow_declaration_status")
    )
    if declaration_status:
        output["workflow_declaration_status"] = declaration_status

    graph_diagnostics = _semantic_graph_diagnostics(evaluation)
    topology_status = _diagnostic_code(
        graph_diagnostics.get("workflow_topology_status")
    )
    if topology_status:
        output["workflow_topology_status"] = topology_status
    topology_codes = _diagnostic_codes(
        graph_diagnostics.get("workflow_topology_error_codes")
    )
    if topology_codes:
        output["workflow_topology_error_codes"] = topology_codes
    try:
        topology_error_count = int(
            graph_diagnostics.get("workflow_topology_error_count") or 0
        )
    except (TypeError, ValueError):
        topology_error_count = 0
    if topology_error_count > 0:
        output["workflow_topology_error_count"] = topology_error_count

    graph_error_codes = _diagnostic_codes(
        graph_diagnostics.get("error_codes")
    )
    if graph_error_codes:
        output["semantic_graph_rejection_codes"] = graph_error_codes
    try:
        graph_error_count = int(graph_diagnostics.get("error_count") or 0)
    except (TypeError, ValueError):
        graph_error_count = 0
    if graph_error_count > 0:
        output["semantic_graph_rejection_count"] = graph_error_count

    normalization = evaluation.get("normalization_diagnostics")
    normalization = dict(normalization) if isinstance(normalization, dict) else {}
    for source_key, count_key, codes_key in (
        (
            "workflow_rejection_reasons",
            "workflow_rejection_count",
            "workflow_rejection_codes",
        ),
        (
            "semantic_graph_rejections",
            "semantic_graph_rejection_count",
            "semantic_graph_rejection_codes",
        ),
        (
            "typed_state_rejections",
            "typed_state_rejection_count",
            "typed_state_rejection_codes",
        ),
    ):
        rejection_items = normalization.get(source_key)
        if not isinstance(rejection_items, list) or not rejection_items:
            continue
        output[count_key] = len(rejection_items)
        codes = _rejection_codes(rejection_items)
        if codes:
            output[codes_key] = codes

    consistency_rejections = normalization.get(
        "workflow_consistency_rejections"
    )
    if isinstance(consistency_rejections, list) and consistency_rejections:
        rejection_codes = _rejection_codes(consistency_rejections)
        output["workflow_consistency_rejection_count"] = len(
            consistency_rejections
        )
        if rejection_codes:
            output["workflow_consistency_rejection_codes"] = rejection_codes

    normalized_projection_codes = _diagnostic_codes(projection_codes)
    if normalized_projection_codes:
        output["projection_error_codes"] = normalized_projection_codes
        output["projection_error_count"] = len(normalized_projection_codes)
    for count_key in (
        "raw_workflow_candidate_count",
        "normalized_workflow_count",
        "rejected_workflow_count",
        "verified_functional_module_count",
    ):
        if count_key not in normalization:
            continue
        try:
            count = int(normalization.get(count_key) or 0)
        except (TypeError, ValueError):
            continue
        output[count_key] = max(0, count)
    return output


def _latest_compact_failure_diagnostics(
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """只把最后一份可识别候选的 code/count 提升到阶段诊断。"""

    for attempt in reversed(attempts):
        compact = {
            key: copy.deepcopy(attempt[key])
            for key in _COMPACT_FAILURE_DIAGNOSTIC_KEYS
            if key in attempt
        }
        if compact:
            compact["failure_diagnostic_attempt"] = int(
                attempt.get("attempt") or 0
            )
            compact["failure_diagnostic_attempt_status"] = str(
                attempt.get("status") or ""
            )
            return compact
    return {}


def _safe_attempt_evaluation_diagnostics(
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """尝试诊断仅允许稳定状态、机器码和计数通过边界。"""

    output = {
        key: copy.deepcopy(diagnostics[key])
        for key in _ATTEMPT_EVALUATION_DIAGNOSTIC_KEYS
        if key in diagnostics
    }
    response_contract_error = diagnostics.get("response_contract_error")
    response_contract_error = (
        dict(response_contract_error)
        if isinstance(response_contract_error, dict)
        else {}
    )
    response_contract_error_code = _diagnostic_code(
        response_contract_error.get("code")
    )
    if response_contract_error_code:
        output["response_contract_error_code"] = response_contract_error_code
    response_contract_error_details = response_contract_error.get("details")
    response_contract_error_details = (
        dict(response_contract_error_details)
        if isinstance(response_contract_error_details, dict)
        else {}
    )
    response_contract_error_field = _diagnostic_code(
        response_contract_error_details.get("field")
    )
    if response_contract_error_field:
        output["response_contract_error_field"] = response_contract_error_field
    return output


def _compact_topology_guard_diagnostics(
    topology_result: dict[str, Any],
) -> dict[str, Any]:
    """拓扑守卫诊断不携带路径、指纹或差异对象。"""

    output: dict[str, Any] = {}
    for key in _TOPOLOGY_GUARD_DIAGNOSTIC_KEYS:
        if key not in topology_result:
            continue
        value = topology_result[key]
        if key == "decision":
            value = _diagnostic_code(value)
            if not value:
                continue
        elif key.endswith("_count"):
            try:
                value = max(0, int(value or 0))
            except (TypeError, ValueError):
                continue
        else:
            value = bool(value)
        output[key] = value
    return output


def _feedback_items(evaluation: dict[str, Any]) -> list[Any]:
    explicit = evaluation.get("retry_feedback")
    if isinstance(explicit, list):
        candidates = list(explicit)
    else:
        normalization = evaluation.get("normalization_diagnostics")
        normalization = (
            dict(normalization) if isinstance(normalization, dict) else {}
        )
        candidates = []
        for key in _RETRY_FEEDBACK_KEYS:
            value = normalization.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif value not in (None, ""):
                candidates.append(value)

    status = str(evaluation.get("status") or "").strip()
    if status:
        candidates.append(status[:160])

    output: list[Any] = []
    markers: set[str] = set()
    for item in candidates:
        if not isinstance(item, (dict, list, str)):
            continue
        copied = copy.deepcopy(item)
        try:
            marker = json.dumps(
                copied,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            marker = str(copied)
        if marker in markers:
            continue
        markers.add(marker)
        output.append(copied)
        if len(output) >= 32:
            break
    return output


def _forbidden_topology_changes(
    evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    value = evaluation.get("forbidden_topology_changes")
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise TypeError("forbidden_topology_changes 必须是对象列表")
    return [copy.deepcopy(item) for item in value[:32]]


def _candidate_evaluation_failure(
    *,
    status: GraphStageCompileStatus,
    assembled_candidate: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> _CandidateEvaluation:
    return _CandidateEvaluation(
        status=status,
        assembled_candidate=copy.deepcopy(assembled_candidate),
        evaluation=copy.deepcopy(evaluation or {}),
        normalized_semantic_contract={},
        projection={},
        retry_feedback=(),
        repair_targets=(),
        forbidden_topology_changes=(),
        independent_recompile_codes=(),
        diagnostics=dict(diagnostics or {}),
    )


def _evaluate_candidate(
    raw_text: str,
    *,
    normalized_scope_ledger: dict[str, Any],
    candidate_evaluator: CandidateEvaluator,
    independent_recompile_code_resolver: IndependentRecompileCodeResolver,
) -> _CandidateEvaluation:
    try:
        response = json.loads(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _candidate_evaluation_failure(
            status="parse_failed",
            assembled_candidate={},
            diagnostics={
                "parse_error_code": "graph_stage_json_parse_failed",
                "parse_error_type": type(exc).__name__,
                "independent_recompile_codes": [
                    "graph_stage_json_parse_failed"
                ],
            },
        )

    try:
        assembled = assemble_requirement_graph_stage_response(
            response,
            normalized_scope_ledger=normalized_scope_ledger,
        )
    except RequirementGraphStageContractError as exc:
        # scope ledger 已在请求构造前校验；这里仅把模型响应协议错误交给 fresh compile。
        if not exc.code.startswith(_GRAPH_STAGE_CONTRACT_CODE_PREFIX):
            raise
        return _candidate_evaluation_failure(
            status="response_contract_invalid",
            assembled_candidate={},
            diagnostics={
                "response_contract_error": exc.to_diagnostic(),
                "independent_recompile_codes": [exc.code],
            },
        )

    try:
        evaluated = candidate_evaluator(copy.deepcopy(assembled))
    except Exception as exc:  # noqa: BLE001 - 调用方校验器边界必须失败关闭
        return _candidate_evaluation_failure(
            status="candidate_evaluator_failed",
            assembled_candidate=assembled,
            diagnostics={
                "candidate_evaluator_error_code": "candidate_evaluator_failed",
                "candidate_evaluator_error_type": type(exc).__name__,
            },
        )
    if not isinstance(evaluated, dict):
        return _candidate_evaluation_failure(
            status="candidate_evaluator_contract_invalid",
            assembled_candidate=assembled,
            diagnostics={
                "candidate_evaluator_result_type": type(evaluated).__name__,
            },
        )

    evaluation = copy.deepcopy(evaluated)
    normalized_semantic_contract = evaluation.get("semantic_contract")
    if not isinstance(normalized_semantic_contract, dict):
        return _candidate_evaluation_failure(
            status="candidate_evaluator_contract_invalid",
            assembled_candidate=assembled,
            evaluation=evaluation,
            diagnostics={"normalized_semantic_contract_missing": True},
        )
    normalized_semantic_contract = copy.deepcopy(normalized_semantic_contract)

    # evaluator 只能规范化图与工作流，无权改写 A1/A2 已冻结事实。这里使用完整对象
    # 等值校验，避免未知字段或已知字段改写被仅覆盖白名单字段的指纹忽略。
    frozen_facts = normalized_scope_ledger.get("evidence_facts")
    evaluated_facts = normalized_semantic_contract.get("evidence_facts")
    if (
        not isinstance(frozen_facts, list)
        or not isinstance(evaluated_facts, list)
        or evaluated_facts != frozen_facts
    ):
        frozen_by_id = {
            str(item.get("fact_id") or ""): item
            for item in frozen_facts or []
            if isinstance(item, dict) and str(item.get("fact_id") or "")
        }
        evaluated_by_id = {
            str(item.get("fact_id") or ""): item
            for item in evaluated_facts or []
            if isinstance(item, dict) and str(item.get("fact_id") or "")
        }
        return _candidate_evaluation_failure(
            status="candidate_evaluator_contract_invalid",
            assembled_candidate=assembled,
            diagnostics={
                "frozen_fact_contract_match": False,
                "frozen_fact_count": (
                    len(frozen_facts) if isinstance(frozen_facts, list) else 0
                ),
                "evaluated_fact_count": (
                    len(evaluated_facts)
                    if isinstance(evaluated_facts, list)
                    else 0
                ),
                "missing_frozen_fact_ids": sorted(
                    set(frozen_by_id) - set(evaluated_by_id)
                )[:16],
                "extra_evaluated_fact_ids": sorted(
                    set(evaluated_by_id) - set(frozen_by_id)
                )[:16],
                "changed_frozen_fact_ids": sorted(
                    fact_id
                    for fact_id in set(frozen_by_id) & set(evaluated_by_id)
                    if frozen_by_id[fact_id] != evaluated_by_id[fact_id]
                )[:16],
            },
        )

    # 投影只能检查调用方完成规范化后的语义契约，不能检查模型原始边方向。
    projection = validate_requirement_graph_stage_projection(
        normalized_semantic_contract,
        normalized_scope_ledger=normalized_scope_ledger,
    )
    projection_codes = _diagnostic_codes(projection.get("error_codes"))
    compact_diagnostics = _compact_evaluation_diagnostics(
        evaluation,
        projection_codes=projection_codes,
    )
    if projection.get("valid") is not True:
        return _CandidateEvaluation(
            status="projection_invalid",
            assembled_candidate=copy.deepcopy(assembled),
            evaluation=evaluation,
            normalized_semantic_contract=normalized_semantic_contract,
            projection=copy.deepcopy(projection),
            retry_feedback=(),
            repair_targets=(),
            forbidden_topology_changes=(),
            independent_recompile_codes=tuple(
                projection_codes or ["scope_projection_invalid"]
            ),
            diagnostics={
                "evaluation_valid": evaluation.get("valid") is True,
                "evaluation_status": str(evaluation.get("status") or ""),
                "projection_valid": False,
                "projection_error_count": len(projection.get("errors") or []),
                "independent_recompile_codes": (
                    projection_codes or ["scope_projection_invalid"]
                ),
                **compact_diagnostics,
            },
        )

    try:
        resolved_codes = _reason_codes(
            independent_recompile_code_resolver(copy.deepcopy(evaluation))
        )
    except Exception as exc:  # noqa: BLE001 - 调用方分类器边界必须失败关闭
        return _candidate_evaluation_failure(
            status="recompile_resolver_failed",
            assembled_candidate=assembled,
            evaluation=evaluation,
            diagnostics={
                "recompile_resolver_error_code": "recompile_resolver_failed",
                "recompile_resolver_error_type": type(exc).__name__,
            },
        )

    feedback = _feedback_items(evaluation)
    repair_targets = compile_semantic_retry_repair_targets(feedback, limit=32)
    # evidence_facts 属于 A1 冻结数据，阶段 B 不得获得修改权限。
    repair_targets = [
        item
        for item in repair_targets
        if not str(item.get("path") or "").startswith("$.evidence_facts")
    ]
    try:
        forbidden_changes = _forbidden_topology_changes(evaluation)
    except TypeError as exc:
        return _candidate_evaluation_failure(
            status="candidate_evaluator_contract_invalid",
            assembled_candidate=assembled,
            evaluation=evaluation,
            diagnostics={
                "candidate_evaluator_error_code": (
                    "candidate_evaluator_contract_invalid"
                ),
                "candidate_evaluator_error_type": type(exc).__name__,
            },
        )

    valid = evaluation.get("valid") is True and not resolved_codes
    status: GraphStageCompileStatus = "validated" if valid else "contract_invalid"
    return _CandidateEvaluation(
        status=status,
        assembled_candidate=copy.deepcopy(assembled),
        evaluation=evaluation,
        normalized_semantic_contract=normalized_semantic_contract,
        projection=copy.deepcopy(projection),
        retry_feedback=tuple(feedback),
        repair_targets=tuple(copy.deepcopy(repair_targets)),
        forbidden_topology_changes=tuple(copy.deepcopy(forbidden_changes)),
        independent_recompile_codes=tuple(resolved_codes),
        diagnostics={
            "evaluation_valid": evaluation.get("valid") is True,
            "evaluation_status": str(evaluation.get("status") or ""),
            "projection_valid": True,
            "projection_error_count": 0,
            "retry_feedback_count": len(feedback),
            "repair_target_count": len(repair_targets),
            "repair_target_codes": list(
                dict.fromkeys(
                    str(item.get("code") or "")
                    for item in repair_targets
                    if str(item.get("code") or "")
                )
            ),
            "forbidden_topology_change_count": len(forbidden_changes),
            "independent_recompile_codes": resolved_codes,
            **compact_diagnostics,
        },
    )


def _attempt_diagnostic(
    *,
    attempt: int,
    candidate_mode: str,
    compilation_mode: str,
    envelope: EnvelopeCallResult,
    evaluation: _CandidateEvaluation | None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "attempt": int(attempt),
        "candidate_mode": str(candidate_mode),
        "compilation_mode": str(compilation_mode),
        "status": evaluation.status if evaluation is not None else envelope.status,
        "raw_chars": len(envelope.raw_text),
        "model_envelope": envelope.to_diagnostic(),
    }
    if evaluation is not None:
        item.update(
            _safe_attempt_evaluation_diagnostics(evaluation.diagnostics)
        )
    return item


def _build_result(
    *,
    status: GraphStageCompileStatus,
    assembled_candidate: dict[str, Any],
    evaluation: dict[str, Any],
    attempts: list[dict[str, Any]],
    envelopes: list[EnvelopeCallResult],
    max_tokens: int,
    request_timeout_seconds: float,
    validated_attempt: int,
    targeted_repair_used: bool,
    targeted_repair_attempt: int,
    targeted_repair_outcome: str,
    independent_recompile_used: bool,
    independent_recompile_attempt: int,
    independent_recompile_trigger_codes: list[str],
    independent_recompile_outcome: str,
    ledger_fingerprint: str,
) -> RequirementGraphStageCompilationResult:
    success = status == "validated"
    published_candidate = assembled_candidate if success else {}
    published_evaluation = evaluation if success else {}
    diagnostics = {
        "semantic_compile_status": status,
        "semantic_compile_success": success,
        "semantic_compile_envelope_count": len(envelopes),
        "semantic_compile_attempt_count": len(attempts),
        "semantic_compile_candidate_attempt_count": len(attempts),
        "semantic_compile_candidate_attempt_limit": (
            MAX_GRAPH_STAGE_CANDIDATE_ENVELOPES
        ),
        "semantic_compile_physical_call_count": sum(
            item.physical_call_count for item in envelopes
        ),
        "semantic_compile_provider_call_count": sum(
            item.provider_call_count for item in envelopes
        ),
        "semantic_compile_cache_hit_count": sum(
            item.cache_hit_count for item in envelopes
        ),
        "semantic_compile_cache_miss_count": sum(
            item.cache_miss_count for item in envelopes
        ),
        "semantic_compile_cache_bypass_count": sum(
            item.cache_bypass_count for item in envelopes
        ),
        "semantic_compile_transport_failure_count": sum(
            item.transport_failure_count for item in envelopes
        ),
        "semantic_compile_transport_retry_count": sum(
            item.transport_retry_count for item in envelopes
        ),
        "semantic_compile_timeout_count": sum(
            int(attempt.timed_out)
            for envelope in envelopes
            for attempt in envelope.attempts
        ),
        "semantic_compile_transport_replays_per_envelope": (
            MAX_TRANSPORT_REPLAYS_PER_ENVELOPE
        ),
        "semantic_compile_retry_used": len(attempts) > 1,
        "semantic_compile_targeted_repair_limit": (
            MAX_GRAPH_STAGE_TARGETED_REPAIRS
        ),
        "semantic_compile_targeted_repair_used": targeted_repair_used,
        "semantic_compile_targeted_repair_attempt": int(
            targeted_repair_attempt
        ),
        "semantic_compile_targeted_repair_outcome": targeted_repair_outcome,
        "semantic_compile_independent_recompile_limit": (
            MAX_GRAPH_STAGE_INDEPENDENT_RECOMPILES
        ),
        "semantic_compile_independent_recompile_used": (
            independent_recompile_used
        ),
        "semantic_compile_independent_recompile_attempt": int(
            independent_recompile_attempt
        ),
        "semantic_compile_independent_recompile_trigger_codes": list(
            independent_recompile_trigger_codes
        ),
        "semantic_compile_independent_recompile_outcome": (
            independent_recompile_outcome
        ),
        "semantic_compile_validated_attempt": int(validated_attempt),
        "semantic_compile_stop_reason": "" if success else status,
        "semantic_compile_attempts": copy.deepcopy(attempts),
        "semantic_compile_max_tokens": int(max_tokens),
        "semantic_compile_request_timeout_seconds": float(
            request_timeout_seconds
        ),
        "semantic_compile_scope_ledger_fingerprint": ledger_fingerprint,
    }
    if not success:
        diagnostics.update(_latest_compact_failure_diagnostics(attempts))
    return RequirementGraphStageCompilationResult(
        assembled_candidate=copy.deepcopy(published_candidate),
        evaluation=copy.deepcopy(published_evaluation),
        diagnostics=diagnostics,
    )


def _compile_partitioned_graph_stage(
    *,
    client: Any,
    normalized_scope_ledger: dict[str, Any],
    candidate_evaluator: CandidateEvaluator,
    independent_recompile_code_resolver: IndependentRecompileCodeResolver,
    db: Any,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
    isolated_ai_runtime_factory: Callable[[], Any] | None,
) -> RequirementGraphStageCompilationResult:
    """编排大规模 Graph 分阶段编译，并在合并后复用原有最终门禁。"""

    from .requirement_graph_partition_compiler import (
        compile_partitioned_requirement_graph_response,
    )

    frozen_ledger = copy.deepcopy(normalized_scope_ledger)
    ledger_fingerprint = str(frozen_ledger.get("fingerprint") or "")
    partition_result = compile_partitioned_requirement_graph_response(
        client=client,
        normalized_scope_ledger=frozen_ledger,
        db=db,
        max_tokens=max_tokens,
        task_type=task_type,
        request_timeout_seconds=request_timeout_seconds,
        worker_runtime_factory=isolated_ai_runtime_factory,
    )
    envelopes = list(partition_result.envelopes)
    phase_attempts = [
        copy.deepcopy(item) for item in partition_result.phase_attempts
    ]
    final_evaluation: _CandidateEvaluation | None = None
    final_status: GraphStageCompileStatus = "contract_invalid"
    assembled_candidate: dict[str, Any] = {}
    evaluation: dict[str, Any] = {}
    final_gate_exception_type = ""
    final_gate_exception_message = ""
    if partition_result.success:
        try:
            final_evaluation = _evaluate_candidate(
                json.dumps(
                    partition_result.response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                normalized_scope_ledger=frozen_ledger,
                candidate_evaluator=candidate_evaluator,
                independent_recompile_code_resolver=(
                    independent_recompile_code_resolver
                ),
            )
            final_status = final_evaluation.status
            if final_evaluation.assembled_candidate:
                assembled_candidate = copy.deepcopy(
                    final_evaluation.assembled_candidate
                )
            if final_evaluation.evaluation:
                evaluation = copy.deepcopy(final_evaluation.evaluation)
            final_attempt = {
                "phase": "final_gate",
                "shard_id": "FINAL",
                "attempt": 1,
                "candidate_mode": "partitioned_merge",
                "compilation_mode": "partitioned",
                "status": final_status,
                "input_chars": 0,
                "raw_chars": len(
                    json.dumps(partition_result.response, ensure_ascii=False)
                ),
            }
            final_attempt.update(
                _safe_attempt_evaluation_diagnostics(
                    final_evaluation.diagnostics
                )
            )
            phase_attempts.append(final_attempt)
        except Exception as exc:  # noqa: BLE001 - final gate 必须返回可持久化诊断
            final_status = "contract_invalid"
            final_gate_exception_type = type(exc).__name__
            final_gate_exception_message = str(exc)[:300]
            phase_attempts.append(
                {
                    "phase": "final_gate",
                    "shard_id": "FINAL",
                    "attempt": 1,
                    "candidate_mode": "partitioned_merge",
                    "compilation_mode": "partitioned",
                    "status": "contract_invalid",
                    "error_code": "graph_partition_final_gate_exception",
                    "error_type": final_gate_exception_type,
                    "error_message": final_gate_exception_message,
                    "input_chars": 0,
                    "raw_chars": len(
                        json.dumps(partition_result.response, ensure_ascii=False)
                    ),
                }
            )
    else:
        candidate_status = str(partition_result.status or "contract_invalid")
        final_status = (
            candidate_status
            if candidate_status
            in {
                "parse_failed",
                "contract_invalid",
                "output_truncated",
                "output_incomplete",
                "transport_exhausted",
                "fatal_model_error",
            }
            else "contract_invalid"
        )

    success = final_status == "validated"
    diagnostics = {
        "semantic_compile_mode": "partitioned",
        "semantic_compile_status": final_status,
        "semantic_compile_success": success,
        "semantic_compile_envelope_count": len(envelopes),
        "semantic_compile_attempt_count": len(phase_attempts),
        "semantic_compile_candidate_attempt_count": (
            1 if partition_result.success else 0
        ),
        "semantic_compile_candidate_attempt_limit": 1,
        "semantic_compile_physical_call_count": sum(
            item.physical_call_count for item in envelopes
        ),
        "semantic_compile_provider_call_count": sum(
            item.provider_call_count for item in envelopes
        ),
        "semantic_compile_cache_hit_count": sum(
            item.cache_hit_count for item in envelopes
        ),
        "semantic_compile_cache_miss_count": sum(
            item.cache_miss_count for item in envelopes
        ),
        "semantic_compile_cache_bypass_count": sum(
            item.cache_bypass_count for item in envelopes
        ),
        "semantic_compile_transport_failure_count": sum(
            item.transport_failure_count for item in envelopes
        ),
        "semantic_compile_transport_retry_count": sum(
            item.transport_retry_count for item in envelopes
        ),
        "semantic_compile_timeout_count": sum(
            int(attempt.timed_out)
            for envelope in envelopes
            for attempt in envelope.attempts
        ),
        "semantic_compile_transport_replays_per_envelope": (
            MAX_TRANSPORT_REPLAYS_PER_ENVELOPE
        ),
        "semantic_compile_retry_used": any(
            int(item.get("attempt") or 0) > 1 for item in phase_attempts
        ),
        "semantic_compile_targeted_repair_limit": 0,
        "semantic_compile_targeted_repair_used": False,
        "semantic_compile_targeted_repair_attempt": 0,
        "semantic_compile_targeted_repair_outcome": "not_applicable",
        "semantic_compile_independent_recompile_limit": 0,
        "semantic_compile_independent_recompile_used": False,
        "semantic_compile_independent_recompile_attempt": 0,
        "semantic_compile_independent_recompile_trigger_codes": [],
        "semantic_compile_independent_recompile_outcome": "not_applicable",
        "semantic_compile_validated_attempt": 1 if success else 0,
        "semantic_compile_stop_reason": "" if success else final_status,
        "semantic_compile_attempts": phase_attempts,
        "semantic_compile_max_tokens": int(max_tokens),
        "semantic_compile_request_timeout_seconds": float(
            request_timeout_seconds
        ),
        "semantic_compile_scope_ledger_fingerprint": ledger_fingerprint,
        "semantic_compile_final_gate_error_code": (
            "graph_partition_final_gate_exception"
            if final_gate_exception_type
            else ""
        ),
        "semantic_compile_final_gate_error_type": final_gate_exception_type,
        "semantic_compile_final_gate_error_message": final_gate_exception_message,
        **copy.deepcopy(partition_result.diagnostics),
    }
    if not success:
        diagnostics.update(_latest_compact_failure_diagnostics(phase_attempts))
    return RequirementGraphStageCompilationResult(
        assembled_candidate=(
            copy.deepcopy(assembled_candidate) if success else {}
        ),
        evaluation=copy.deepcopy(evaluation) if success else {},
        diagnostics=diagnostics,
    )


def compile_requirement_graph_stage(
    *,
    client: Any,
    normalized_scope_ledger: dict[str, Any],
    candidate_evaluator: CandidateEvaluator,
    independent_recompile_code_resolver: IndependentRecompileCodeResolver,
    db: Any = None,
    max_tokens: int = DEFAULT_GRAPH_STAGE_MAX_TOKENS,
    task_type: str = "generation",
    request_timeout_seconds: float = (
        DEFAULT_GRAPH_STAGE_REQUEST_TIMEOUT_SECONDS
    ),
    isolated_ai_runtime_factory: Callable[[], Any] | None = None,
) -> RequirementGraphStageCompilationResult:
    """
    编译独立阶段 B graph/workflow。

    JSON、严格响应、scope projection 或调用方判定的不可局部修复结构错误
    最多触发一次独立新鲜候选；普通契约错误最多触发一次定向修复。
    """

    if not callable(candidate_evaluator):
        raise TypeError("candidate_evaluator 必须可调用")
    if not callable(independent_recompile_code_resolver):
        raise TypeError("independent_recompile_code_resolver 必须可调用")
    normalized_max_tokens = _positive_int(max_tokens, field="max_tokens")
    normalized_timeout = _positive_float(
        request_timeout_seconds,
        field="request_timeout_seconds",
    )
    if not isinstance(normalized_scope_ledger, dict):
        raise TypeError("normalized_scope_ledger 必须是对象")
    frozen_ledger = copy.deepcopy(normalized_scope_ledger)
    ledger_fingerprint = str(frozen_ledger.get("fingerprint") or "")

    fact_count = len(frozen_ledger.get("evidence_facts") or [])
    if fact_count > DEFAULT_GRAPH_STAGE_PARTITION_FACT_THRESHOLD:
        return _compile_partitioned_graph_stage(
            client=client,
            normalized_scope_ledger=frozen_ledger,
            candidate_evaluator=candidate_evaluator,
            independent_recompile_code_resolver=(
                independent_recompile_code_resolver
            ),
            db=db,
            max_tokens=normalized_max_tokens,
            task_type=str(task_type or "generation"),
            request_timeout_seconds=normalized_timeout,
            isolated_ai_runtime_factory=(
                isolated_ai_runtime_factory
            ),
        )

    prompt = build_requirement_graph_stage_prompt()
    attempts: list[dict[str, Any]] = []
    envelopes: list[EnvelopeCallResult] = []
    topology_guard = SemanticRetryTopologyGuard()
    final_status: GraphStageCompileStatus = "contract_invalid"
    assembled_candidate: dict[str, Any] = {}
    evaluation: dict[str, Any] = {}
    validated_attempt = 0

    next_mode = "initial"
    targeted_repair_used = False
    targeted_repair_attempt = 0
    targeted_repair_outcome = "not_used"
    independent_recompile_used = False
    independent_recompile_attempt = 0
    independent_recompile_trigger_codes: list[str] = []
    independent_recompile_outcome = "not_used"

    previous_candidate: dict[str, Any] | None = None
    retry_feedback: list[Any] = []
    repair_targets: list[dict[str, Any]] = []
    forbidden_topology_changes: list[dict[str, Any]] = []

    while next_mode and len(attempts) < MAX_GRAPH_STAGE_CANDIDATE_ENVELOPES:
        attempt = len(attempts) + 1
        compilation_mode = next_mode
        candidate_mode = {
            "initial": "initial",
            "targeted_repair": "targeted_repair",
            "independent_recompile": "fresh_candidate",
        }[compilation_mode]

        user_input = build_requirement_graph_stage_user_input(
            frozen_ledger,
            attempt=attempt,
            compilation_mode=compilation_mode,
            retry_feedback=(
                retry_feedback if compilation_mode == "targeted_repair" else None
            ),
            previous_candidate=(
                previous_candidate
                if compilation_mode == "targeted_repair"
                else None
            ),
            repair_targets=(
                repair_targets if compilation_mode == "targeted_repair" else None
            ),
            forbidden_topology_changes=(
                forbidden_topology_changes
                if compilation_mode == "targeted_repair"
                else None
            ),
            recompile_reason_codes=(
                independent_recompile_trigger_codes
                if compilation_mode == "independent_recompile"
                else None
            ),
        )
        envelope = invoke_model_envelope(
            client=client,
            envelope_id=(
                "requirement-graph-stage-"
                f"{ledger_fingerprint[:12]}-{candidate_mode}"
            ),
            user_input=user_input,
            system_prompt=prompt,
            db=db,
            max_tokens=normalized_max_tokens,
            task_type=str(task_type or "generation"),
            request_timeout_seconds=normalized_timeout,
            max_transport_replays=MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
        )
        envelopes.append(envelope)
        if envelope.status != "response":
            final_status = envelope.status
            attempts.append(
                _attempt_diagnostic(
                    attempt=attempt,
                    candidate_mode=candidate_mode,
                    compilation_mode=compilation_mode,
                    envelope=envelope,
                    evaluation=None,
                )
            )
            if compilation_mode == "targeted_repair":
                targeted_repair_outcome = envelope.status
            elif compilation_mode == "independent_recompile":
                independent_recompile_outcome = envelope.status
            break

        response_termination = classify_response_termination(
            envelope.response_metadata
        )
        if response_termination in {"truncated", "incomplete"}:
            final_status = (
                "output_truncated"
                if response_termination == "truncated"
                else "output_incomplete"
            )
            attempt_diagnostic = _attempt_diagnostic(
                attempt=attempt,
                candidate_mode=candidate_mode,
                compilation_mode=compilation_mode,
                envelope=envelope,
                evaluation=None,
            )
            attempt_diagnostic["status"] = final_status
            attempt_diagnostic["response_termination"] = response_termination
            attempts.append(attempt_diagnostic)
            assembled_candidate = {}
            evaluation = {}
            if compilation_mode == "targeted_repair":
                targeted_repair_outcome = final_status
            elif compilation_mode == "independent_recompile":
                independent_recompile_outcome = final_status
            break

        candidate_result = _evaluate_candidate(
            envelope.raw_text,
            normalized_scope_ledger=frozen_ledger,
            candidate_evaluator=candidate_evaluator,
            independent_recompile_code_resolver=(
                independent_recompile_code_resolver
            ),
        )
        attempt_diagnostic = _attempt_diagnostic(
            attempt=attempt,
            candidate_mode=candidate_mode,
            compilation_mode=compilation_mode,
            envelope=envelope,
            evaluation=candidate_result,
        )
        attempts.append(attempt_diagnostic)
        final_status = candidate_result.status
        if candidate_result.assembled_candidate:
            assembled_candidate = copy.deepcopy(
                candidate_result.assembled_candidate
            )
        if candidate_result.evaluation:
            evaluation = copy.deepcopy(candidate_result.evaluation)

        if (
            compilation_mode == "targeted_repair"
            and candidate_result.assembled_candidate
            and candidate_result.status != "projection_invalid"
        ):
            topology_result = topology_guard.evaluate(
                candidate_result.assembled_candidate,
                validation_feedback=retry_feedback,
            )
            attempt_diagnostic["retry_topology_guard"] = (
                _compact_topology_guard_diagnostics(topology_result)
            )
            if topology_result.get("allowed") is not True:
                final_status = "retry_topology_drift_blocked"
                attempt_diagnostic["status"] = final_status
                targeted_repair_outcome = final_status
                # 不做 merge 或旧值保留；越权候选不能进入后续 fresh 路径。
                break

        if candidate_result.status == "validated":
            final_status = "validated"
            validated_attempt = attempt
            if compilation_mode == "targeted_repair":
                targeted_repair_outcome = "validated"
            elif compilation_mode == "independent_recompile":
                independent_recompile_outcome = "validated"
            break

        if candidate_result.assembled_candidate:
            topology_guard.advance_working_candidate(
                candidate_result.assembled_candidate
            )

        fresh_codes = list(candidate_result.independent_recompile_codes)
        needs_fresh = candidate_result.status in {
            "parse_failed",
            "response_contract_invalid",
            "projection_invalid",
        } or bool(fresh_codes)
        if needs_fresh and not independent_recompile_used:
            independent_recompile_used = True
            independent_recompile_attempt = attempt + 1
            independent_recompile_trigger_codes = list(
                fresh_codes
                or candidate_result.diagnostics.get(
                    "independent_recompile_codes"
                )
                or [candidate_result.status]
            )
            independent_recompile_outcome = "scheduled"
            attempt_diagnostic["independent_recompile_scheduled"] = True
            attempt_diagnostic["independent_recompile_trigger_codes"] = list(
                independent_recompile_trigger_codes
            )
            if compilation_mode == "targeted_repair":
                targeted_repair_outcome = candidate_result.status
            next_mode = "independent_recompile"
            previous_candidate = None
            retry_feedback = []
            repair_targets = []
            forbidden_topology_changes = []
            # independent 是新的候选族，后续若做局部修复必须以 fresh 候选为锚。
            topology_guard = SemanticRetryTopologyGuard()
            continue

        if (
            compilation_mode in {"initial", "independent_recompile"}
            and not targeted_repair_used
            and not fresh_codes
        ):
            if candidate_result.status != "contract_invalid":
                break
            if not candidate_result.repair_targets:
                final_status = "targeted_repair_unavailable"
                attempt_diagnostic["status"] = final_status
                break
            targeted_repair_used = True
            targeted_repair_attempt = attempt + 1
            targeted_repair_outcome = "scheduled"
            attempt_diagnostic["targeted_repair_scheduled"] = True
            if compilation_mode == "independent_recompile":
                independent_recompile_outcome = "repairable_invalid"
            previous_candidate = copy.deepcopy(
                candidate_result.assembled_candidate
            )
            retry_feedback = list(copy.deepcopy(candidate_result.retry_feedback))
            repair_targets = list(copy.deepcopy(candidate_result.repair_targets))
            forbidden_topology_changes = list(
                copy.deepcopy(candidate_result.forbidden_topology_changes)
            )
            next_mode = "targeted_repair"
            continue

        if compilation_mode == "targeted_repair":
            targeted_repair_outcome = candidate_result.status
        elif compilation_mode == "independent_recompile":
            independent_recompile_outcome = candidate_result.status
        break

    if independent_recompile_used and independent_recompile_outcome == "scheduled":
        independent_recompile_outcome = "not_completed"
    if targeted_repair_used and targeted_repair_outcome == "scheduled":
        targeted_repair_outcome = "not_completed"

    return _build_result(
        status=final_status,
        assembled_candidate=assembled_candidate,
        evaluation=evaluation,
        attempts=attempts,
        envelopes=envelopes,
        max_tokens=normalized_max_tokens,
        request_timeout_seconds=normalized_timeout,
        validated_attempt=validated_attempt,
        targeted_repair_used=targeted_repair_used,
        targeted_repair_attempt=targeted_repair_attempt,
        targeted_repair_outcome=targeted_repair_outcome,
        independent_recompile_used=independent_recompile_used,
        independent_recompile_attempt=independent_recompile_attempt,
        independent_recompile_trigger_codes=(
            independent_recompile_trigger_codes
        ),
        independent_recompile_outcome=independent_recompile_outcome,
        ledger_fingerprint=ledger_fingerprint,
    )


__all__ = [
    "CandidateEvaluator",
    "DEFAULT_GRAPH_STAGE_MAX_TOKENS",
    "DEFAULT_GRAPH_STAGE_REQUEST_TIMEOUT_SECONDS",
    "GraphStageCompileStatus",
    "IndependentRecompileCodeResolver",
    "MAX_GRAPH_STAGE_CANDIDATE_ENVELOPES",
    "MAX_GRAPH_STAGE_INDEPENDENT_RECOMPILES",
    "MAX_GRAPH_STAGE_TARGETED_REPAIRS",
    "RequirementGraphStageCompilationResult",
    "compile_requirement_graph_stage",
]
