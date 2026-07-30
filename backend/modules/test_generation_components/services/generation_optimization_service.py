from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from core.ai.ai_client import get_client_for_user
from core.db.models import LogEntry, Project, TestGeneration
from core.settings.config import settings

from ..coverage.case_quality_gate import summarize_case_quality_gate
from ..coverage.coverage_analyzer import analyze_coverage
from ..postprocess.case_access import case_id as case_access_id
from ..postprocess.case_contract import (
    merge_contract_quality_gate,
    project_persistable_cases,
    summarize_persistable_case_contract,
)
from ..postprocess.json_processing import (
    clean_and_parse_json,
    deduplicate_test_cases,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from ..postprocess.persistence_gate import (
    build_persistence_gate_diagnostic,
    evaluate_persistence_gate,
    summarize_persistence_case_quality_gate,
)
from ..prompting.case_semantic_schema import render_case_semantic_output_contract
from ..control.semantic_contract import validate_case_semantic_contract
from ..postprocess.streaming_execution_plan_ordering import (
    apply_existing_execution_group_ordering,
    assign_presentation_order,
)
from .final_case_parsing import parse_test_cases_payload
from .final_case_quality_ledger_lookup import find_generation_quality_ledger


DEFAULT_OPTIMIZATION_MAX_TOKENS = 2048
DEFAULT_OPTIMIZATION_EXECUTION_MAX_TOKENS = 3072
DEFAULT_OPTIMIZATION_BATCH_ATTEMPTS = 3
DEFAULT_OPTIMIZATION_HTTP_TIMEOUT_SECONDS = 60
MAX_DROP_RATIO = 0.2
ALLOWED_PATCH_KEYS = {"add_cases", "replace_cases", "drop_case_ids", "fix_notes"}
FORBIDDEN_FULL_REWRITE_KEYS = {"cases", "test_cases", "final_cases", "generated_result", "items", "data"}


def get_optimization_ai_client(*, user_id: int, db: Any) -> Any:
    """Keep the external model boundary replaceable in focused tests."""
    return get_client_for_user(user_id, db)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _requirement_contract_from_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    direct = ledger.get("requirement_semantic_contract")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    control = _as_dict(ledger.get("control"))
    nested = control.get("requirement_semantic_contract")
    if isinstance(nested, dict) and nested:
        return dict(nested)
    source_meta = _as_dict(control.get("source_meta"))
    nested = source_meta.get("requirement_semantic_contract")
    return dict(nested) if isinstance(nested, dict) else {}


def _safe_json(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text


def _setting_int_value(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(value), int(maximum)))


def _parse_cases(raw: Any) -> list[dict[str, Any]]:
    parsed = parse_test_cases_payload(raw)
    if parsed:
        return [dict(item) for item in parsed if isinstance(item, dict)]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    normalized = normalize_json_structure(raw)
    if isinstance(normalized, dict):
        for key in ("cases", "test_cases", "generated_result"):
            nested = normalized.get(key)
            if isinstance(nested, list):
                normalized = nested
                break
    return [dict(item) for item in _as_list(normalized) if isinstance(item, dict)]


def _case_id(case: dict[str, Any], index: int) -> str:
    value = case_access_id(case)
    return str(value or f"TC-{index:03d}").strip()


def _case_briefs(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        rows.append(
            {
                "id": _case_id(case, index),
                "description": str(case.get("description") or case.get("name") or ""),
                "test_module": str(case.get("test_module") or case.get("module") or ""),
                "preconditions": case.get("preconditions") if isinstance(case.get("preconditions"), list) else [],
                "steps": case.get("steps") if isinstance(case.get("steps"), list) else [],
                "test_input": str(case.get("test_input") or ""),
                "expected_result": str(case.get("expected_result") or ""),
                "priority": str(case.get("priority") or ""),
                "priority_final": str(case.get("priority_final") or ""),
                "execution_group": str(case.get("execution_group") or ""),
                "execution_sequence": case.get("execution_sequence"),
                "workflow_id": str(case.get("workflow_id") or ""),
                "source_state": str(case.get("source_state") or ""),
                "action": str(case.get("action") or ""),
                "target_state": str(case.get("target_state") or ""),
                "main_chain_stage": str(case.get("main_chain_stage") or ""),
                "main_chain_stage_kind": str(case.get("main_chain_stage_kind") or ""),
            }
        )
    return rows


def _optimization_max_tokens() -> int:
    return _setting_int_value(
        "GENERATION_OPTIMIZATION_MAX_TOKENS",
        DEFAULT_OPTIMIZATION_MAX_TOKENS,
        minimum=512,
        maximum=4096,
    )


def _optimization_execution_max_tokens() -> int:
    return _setting_int_value(
        "GENERATION_OPTIMIZATION_EXECUTION_MAX_TOKENS",
        DEFAULT_OPTIMIZATION_EXECUTION_MAX_TOKENS,
        minimum=2048,
        maximum=8192,
    )


def _optimization_batch_attempts() -> int:
    return _setting_int_value(
        "GENERATION_OPTIMIZATION_BATCH_ATTEMPTS",
        DEFAULT_OPTIMIZATION_BATCH_ATTEMPTS,
        minimum=1,
        maximum=5,
    )


def _optimization_http_timeout_seconds() -> int:
    return _setting_int_value(
        "GENERATION_OPTIMIZATION_HTTP_TIMEOUT_SECONDS",
        DEFAULT_OPTIMIZATION_HTTP_TIMEOUT_SECONDS,
        minimum=15,
        maximum=120,
    )


def _case_id_set(values: Any) -> set[str]:
    output: set[str] = set()
    if isinstance(values, str):
        value = values.strip()
        if value:
            output.add(value)
        return output
    if isinstance(values, dict):
        for key in ("case_id", "caseId", "id", "target_case_id", "duplicate_of_case_id"):
            value = str(values.get(key) or "").strip()
            if value:
                output.add(value)
        return output
    if isinstance(values, list):
        for item in values:
            output.update(_case_id_set(item))
    return output


def _problem_case_ids_from_ledger(ledger: dict[str, Any]) -> set[str]:
    case_ids: set[str] = set()
    for key in (
        "judge_decision_rows",
        "judgeDecisionRows",
        "judge_decision_table_rows",
        "judgeDecisionTableRows",
        "review_decision_rows",
        "reviewDecisionRows",
        "review_decision_table_rows",
        "reviewDecisionTableRows",
    ):
        for row in _as_list(ledger.get(key)):
            if not isinstance(row, dict):
                continue
            status = str(row.get("judge_status") or row.get("status") or "").strip().upper()
            reason = str(row.get("reject_reason") or row.get("dropped_reason") or row.get("reason") or "").strip()
            is_problem = bool(
                status in {"REJECT", "PENDING"}
                or reason
                or row.get("is_semantic_duplicate") is True
                or row.get("row_evidence_incomplete") is True
            )
            if is_problem:
                case_ids.update(_case_id_set(row))
    gate = _as_dict(ledger.get("case_quality_gate"))
    metrics = _as_dict(gate.get("metrics"))
    for key in (
        "low_quality_case_ids",
        "reasoning_leakage_case_ids",
        "role_mismatch_case_ids",
        "persistable_required_field_missing_case_ids",
        "persistable_priority_final_invalid_case_ids",
    ):
        case_ids.update(_case_id_set(metrics.get(key) or gate.get(key)))
    return {item for item in case_ids if item}


def _execution_plan_failure_reasons(ledger: dict[str, Any]) -> list[str]:
    persistence_gate = _as_dict(ledger.get("persistence_gate"))
    execution = _as_dict(persistence_gate.get("execution_plan_validation"))
    reasons = [
        str(item).strip()
        for item in _as_list(execution.get("failure_reasons"))
        if str(item).strip()
    ]
    frontend_error = str(ledger.get("frontend_error") or "").strip()
    if "execution_plan_failed" in frontend_error and not reasons:
        reasons.append("execution_plan_failed")
    return list(dict.fromkeys(reasons))


def _needs_execution_plan_repair(ledger: dict[str, Any]) -> bool:
    persistence_gate = _as_dict(ledger.get("persistence_gate"))
    execution = _as_dict(persistence_gate.get("execution_plan_validation"))
    return bool(
        str(persistence_gate.get("failure_code") or "").strip() == "execution_plan_failed"
        or persistence_gate.get("blocked") is True and execution.get("passed") is False
        or _execution_plan_failure_reasons(ledger)
    )


def _declared_execution_repair_context(ledger: dict[str, Any]) -> dict[str, Any]:
    persistence_gate = _as_dict(ledger.get("persistence_gate"))
    validation = _as_dict(persistence_gate.get("execution_plan_validation"))
    metrics = _as_dict(validation.get("metrics"))
    closure = _as_dict(metrics.get("workflow_closure"))
    contract = _as_dict(closure.get("declared_workflow_contract"))
    source = str(metrics.get("workflow_blueprint_source") or "").strip().lower()
    required_stage_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in _as_list(closure.get("required_stage_ids"))
            if str(item or "").strip()
        )
    )
    declared_steps = [
        dict(item)
        for item in _as_list(contract.get("steps"))
        if isinstance(item, dict)
    ]
    declared_step_ids = {
        str(item.get("id") or "").strip()
        for item in declared_steps
        if str(item.get("id") or "").strip()
    }
    if (
        not _needs_execution_plan_repair(ledger)
        or not contract
        or source in {"", "none", "current_generation_cases"}
        or not required_stage_ids
        or not str(closure.get("initial_state") or "").strip()
        or not _as_list(closure.get("terminal_states"))
        or any(stage_id not in declared_step_ids for stage_id in required_stage_ids)
    ):
        return {}
    return {
        "workflow_blueprint_source": source,
        "declared_workflow_contract": contract,
        "required_stage_ids": required_stage_ids,
        "initial_state": str(closure.get("initial_state") or "").strip(),
        "terminal_states": [
            str(item or "").strip()
            for item in _as_list(closure.get("terminal_states"))
            if str(item or "").strip()
        ],
    }


def _focused_case_briefs(cases: list[dict[str, Any]], ledger: dict[str, Any]) -> list[dict[str, Any]]:
    _ = ledger
    # Optimization 也必须从全局候选集求解，不能只看问题样本或前若干条。
    return _case_briefs([case for case in cases if isinstance(case, dict)])


def _compact_ledger_for_prompt(ledger: dict[str, Any]) -> dict[str, Any]:
    gate = _as_dict(ledger.get("case_quality_gate"))
    gate_metrics = _as_dict(gate.get("metrics"))
    review = _as_dict(ledger.get("review"))
    judge = _as_dict(ledger.get("judge"))
    persistence_gate = _as_dict(ledger.get("persistence_gate"))
    generation_summary = _as_dict(ledger.get("generation_summary"))
    return {
        "quality_score": ledger.get("quality_score"),
        "quality_score_grade": ledger.get("quality_score_grade"),
        "case_quality_gate": {
            "passed": gate.get("passed"),
            "failure_reasons": gate.get("failure_reasons") or gate.get("failed_checks"),
            "metrics": {
                key: gate_metrics.get(key)
                for key in (
                    "final_count",
                    "min_acceptable_final",
                    "quality_score",
                    "quality_score_grade",
                    "judge_rejected_count",
                    "semantic_duplicate_reject_count",
                    "filtered_semantic_duplicate_reject_count",
                    "final_scenario_duplicate_case_count",
                    "final_flow_misordered_count",
                    "quantity_shortfall_advisory",
                )
                if key in gate_metrics
            },
        },
        "generation_summary": {
            key: generation_summary.get(key)
            for key in (
                "final_count",
                "min_acceptable_final",
                "underfill_reason",
                "underfill_root_cause",
                "underfill_level",
            )
            if key in generation_summary
        },
        "review": {
            key: review.get(key)
            for key in (
                "candidate_total",
                "retained_total",
                "final_count",
                "final_scenario_duplicate_case_count",
                "final_flow_misordered_count",
            )
            if key in review
        },
        "judge": {
            key: judge.get(key)
            for key in (
                "pass_count",
                "reject_count",
                "pending_count",
                "reason_clusters",
                "confirmed_pass_out_count",
                "rejected_out_count",
                "pending_out_count",
            )
            if key in judge
        },
        "quality_remediation": ledger.get("quality_remediation"),
        "persistence_gate": {
            "passed": persistence_gate.get("passed"),
            "blocked": persistence_gate.get("blocked"),
            "failure_code": persistence_gate.get("failure_code"),
            "execution_plan_validation": _as_dict(persistence_gate.get("execution_plan_validation")),
        },
        "problem_case_ids": sorted(_problem_case_ids_from_ledger(ledger))[:30],
    }


def _action_ids(ledger: dict[str, Any]) -> list[str]:
    remediation = _as_dict(ledger.get("quality_remediation"))
    return [
        str(item.get("action_id") or "").strip()
        for item in _as_list(remediation.get("actions"))
        if isinstance(item, dict) and str(item.get("action_id") or "").strip()
    ]


def _optimization_needed(ledger: dict[str, Any]) -> bool:
    if not ledger:
        return True
    persistence_gate = _as_dict(ledger.get("persistence_gate"))
    if persistence_gate.get("passed") is False or persistence_gate.get("blocked") is True:
        return True
    if str(persistence_gate.get("failure_code") or "").strip():
        return True
    gate = _as_dict(ledger.get("case_quality_gate"))
    if gate.get("passed") is False and gate.get("blocked") is not True:
        return True
    grade = str(ledger.get("quality_score_grade") or "").strip().lower()
    if grade in {"critical", "low"}:
        return True
    try:
        score = int(ledger.get("quality_score") or 0)
        if score and score <= 60:
            return True
    except Exception:
        pass
    return bool(_action_ids(ledger))


def _build_prompt(
    *,
    requirement: str,
    cases: list[dict[str, Any]],
    ledger: dict[str, Any],
    max_new_cases: int,
    case_briefs: list[dict[str, Any]] | None = None,
    batch_index: int = 1,
    batch_total: int = 1,
    requirement_contract: dict[str, Any] | None = None,
) -> str:
    focused_ledger = _compact_ledger_for_prompt(ledger)
    focused_cases = case_briefs if case_briefs is not None else _focused_case_briefs(cases, ledger)
    repair_context = _declared_execution_repair_context(ledger)
    execution_repair = bool(repair_context)
    focus_lines = []
    declared_contract_lines: list[str] = []
    added_case_contract = render_case_semantic_output_contract(
        case_subject="Every add_cases item"
    )
    verified_requirement_contract = dict(
        requirement_contract or _requirement_contract_from_ledger(ledger)
    )
    add_case_contract_rule = (
        "add_cases may contain new cases, and every _semantic reference must exist in the verified requirement contract."
        if verified_requirement_contract
        else "No verified requirement semantic contract is available. add_cases MUST be an empty array; do not invent semantic IDs."
    )
    if execution_repair:
        focus_lines.extend(
            [
                "Primary repair focus: execution_plan_failed. Do not perform broad quality rewriting in this call.",
                "Repair only the stages listed in declared_workflow_contract.required_stage_ids.",
                "Set main_chain_stage to the exact declared stage id and preserve each declared action, actor, state_in, state_out, critical, and blocking value.",
                "Do not infer an extra stage, transition, priority floor, or replacement workflow from current case text.",
                "Use P0 only when the declared stage is critical or blocking; otherwise retain the case's ordinary semantic priority.",
                "If a visible case does not support a required stage, leave it unchanged instead of forcing a match.",
                "Use replace_cases to add missing execution fields to visible cases; use add_cases only when the visible cases cannot close the chain.",
                "For execution repair, replacement cases must include only changed execution metadata and priority fields.",
                "Do not include description, test_module, preconditions, steps, test_input, or expected_result in execution repair replacement cases.",
                "Keep the JSON compact; no markdown, no explanation outside the JSON object.",
            ]
        )
        declared_contract_lines = [
            "",
            "[Declared workflow repair contract]",
            _safe_json(repair_context.get("declared_workflow_contract")),
        ]
    return "\n".join(
        [
            "You are a test-case quality repair agent. Use only the requirement, current cases, and diagnostics below.",
            f"This is global optimization pass {int(batch_index)}/{int(batch_total)}. Evaluate every current case together.",
            *focus_lines,
            "Return one JSON object with exactly these top-level keys: add_cases, replace_cases, drop_case_ids, fix_notes.",
            f"add_cases must contain at most {int(max_new_cases)} cases for this batch.",
            "replace_cases items must be shaped as {\"case_id\":\"existing id\",\"case\":{...}}.",
            "drop_case_ids may only remove exact duplicates or cases that violate the public hard contract.",
            "fix_notes must be an array of short strings.",
            "Do not rewrite the whole case set. Focus on missing rules, assertable expected_result, semantic duplicate reduction, and valid coverage backfill.",
            "If final_count is below min_acceptable_final, add only non-duplicate cases that cover missing behavior; do not add filler cases.",
            "Every added case must include id, description, test_module, preconditions, steps, test_input, expected_result, priority, priority_final.",
            add_case_contract_rule,
            added_case_contract,
            "Replacement cases may include only the fields to change; case_id identifies the existing case.",
            "If a declared workflow repair contract is present, repair these fields only as declared: execution_group, execution_sequence, workflow_id, source_state, action, target_state, path_type, blocking, destructive, can_advance_main_flow, role, session_key, main_chain_stage, main_chain_stage_kind.",
            *declared_contract_lines,
            "",
            "[Verified requirement semantic contract]",
            _safe_json(verified_requirement_contract) if verified_requirement_contract else "UNAVAILABLE",
            "",
            "[Requirement]",
            requirement,
            "",
            "[Current case brief]",
            _safe_json(focused_cases),
            "",
            "[Quality diagnostics]",
            _safe_json(focused_ledger),
        ]
    )


def _merge_optimization_patches(patches: list[dict[str, Any]]) -> dict[str, Any]:
    add_cases: list[dict[str, Any]] = []
    replace_cases: list[dict[str, Any]] = []
    drop_case_ids: list[str] = []
    fix_notes: list[str] = []
    for patch in patches:
        add_cases.extend([dict(item) for item in _as_list(patch.get("add_cases")) if isinstance(item, dict)])
        replace_cases.extend([dict(item) for item in _as_list(patch.get("replace_cases")) if isinstance(item, dict)])
        drop_case_ids.extend(
            str(item).strip()
            for item in _as_list(patch.get("drop_case_ids"))
            if str(item).strip()
        )
        fix_notes.extend(
            str(item).strip()
            for item in _as_list(patch.get("fix_notes"))
            if str(item).strip()
        )
    return {
        "add_cases": add_cases,
        "replace_cases": replace_cases,
        "drop_case_ids": list(dict.fromkeys(drop_case_ids)),
        "fix_notes": list(dict.fromkeys(fix_notes)),
    }


@contextmanager
def _temporary_provider_timeout(client: Any, timeout_seconds: int):
    with _temporary_provider_attrs(client, {"request_timeout_seconds": int(timeout_seconds)}):
        yield


@contextmanager
def _temporary_provider_attrs(client: Any, attrs: dict[str, Any]):
    provider = getattr(client, "provider", None)
    if provider is None:
        yield
        return
    marker = object()
    previous = {name: getattr(provider, name, marker) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(provider, name, value)
        yield
    finally:
        for name, old_value in previous.items():
            if old_value is marker:
                try:
                    delattr(provider, name)
                except Exception:
                    pass
            else:
                setattr(provider, name, old_value)


def _is_timeout_response(raw: Any) -> bool:
    text = str(raw or "").strip().lower()
    return "timed out" in text or "timeout" in text or "read operation timed out" in text


def _is_error_response(raw: Any) -> bool:
    return str(raw or "").lstrip().startswith(("Error", "Exception"))


def _is_reasoning_only_empty_response(raw: Any, metadata: dict[str, Any]) -> bool:
    text = str(raw or "").strip()
    try:
        reasoning_len = int(metadata.get("reasoning_len") or 0)
    except Exception:
        reasoning_len = 0
    try:
        content_len = int(metadata.get("content_len") or 0)
    except Exception:
        content_len = 0
    return bool(
        text.startswith("Error: Empty response from model")
        and int(metadata.get("http_status") or 0) == 200
        and content_len <= 0
        and reasoning_len > 0
    )


def _collect_optimization_response(
    client: Any,
    *,
    requirement_text: str,
    prompt: str,
    db: Any,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Optimization expects a compact patch, so keep the model call bounded."""
    timeout_seconds = _optimization_http_timeout_seconds()
    provider_attrs = {
        "request_timeout_seconds": int(timeout_seconds),
        "disable_json_response_format": True,
        "disable_json_reasoning_effort": True,
    }
    with _temporary_provider_attrs(client, provider_attrs):
        raw = client.generate_response(
            requirement_text or "",
            prompt,
            db=None,
            task_type="generation",
            max_tokens=max_tokens,
        )
    metadata = dict(getattr(client, "last_response_metadata", {}) or {})
    if _is_reasoning_only_empty_response(raw, metadata):
        retry_max_tokens = max(int(max_tokens or 0), min(2048, _optimization_max_tokens()))
        with _temporary_provider_attrs(
            client,
            {
                "request_timeout_seconds": int(timeout_seconds),
                "disable_json_response_format": True,
                "disable_json_reasoning_effort": True,
            },
        ):
            retry_raw = client.generate_response(
                requirement_text or "",
                prompt,
                db=None,
                task_type="generation",
                max_tokens=retry_max_tokens,
            )
        retry_metadata = dict(getattr(client, "last_response_metadata", {}) or {})
        return str(retry_raw or ""), {
            "call_mode": "response_json_compat_retry",
            "timeout_seconds": int(timeout_seconds),
            "metadata": retry_metadata,
            "first_metadata": metadata,
            "first_response_chars": int(len(str(raw or ""))),
            "retry_reason": "reasoning_only_empty_response",
            "retry_max_tokens": int(retry_max_tokens),
            "cache_bypassed": True,
        }

    return str(raw or ""), {
        "call_mode": "response_json_compat",
        "timeout_seconds": int(timeout_seconds),
        "metadata": metadata,
        "cache_bypassed": True,
    }


def _build_batch_prompt(
    *,
    requirement_text: str,
    source_cases: list[dict[str, Any]],
    ledger: dict[str, Any],
    max_new_cases: int,
    case_briefs: list[dict[str, Any]],
    batch_index: int,
    batch_total: int,
    requirement_contract: dict[str, Any],
) -> str:
    return _build_prompt(
        requirement=requirement_text or "",
        cases=source_cases,
        ledger=ledger,
        max_new_cases=max_new_cases,
        case_briefs=case_briefs,
        batch_index=batch_index,
        batch_total=batch_total,
        requirement_contract=requirement_contract,
    )


def _collect_patch_for_case_batch(
    *,
    client: Any,
    requirement_text: str,
    source_cases: list[dict[str, Any]],
    ledger: dict[str, Any],
    case_briefs: list[dict[str, Any]],
    batch_index: int,
    batch_total: int,
    max_new_cases: int,
    requirement_contract: dict[str, Any],
    max_tokens: int,
    db: Any,
    prompt_batches: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    attempts = _optimization_batch_attempts()
    last_payload: dict[str, Any] = {}
    for attempt_index in range(1, attempts + 1):
        prompt = _build_batch_prompt(
            requirement_text=requirement_text,
            source_cases=source_cases,
            ledger=ledger,
            max_new_cases=max_new_cases,
            case_briefs=case_briefs,
            batch_index=batch_index,
            batch_total=batch_total,
            requirement_contract=requirement_contract,
        )
        raw, call_meta = _collect_optimization_response(
            client,
            requirement_text=requirement_text,
            prompt=prompt,
            db=db,
            max_tokens=max_tokens,
        )
        provider_meta = _as_dict(call_meta.get("metadata"))
        prompt_batches.append(
            {
                "batch_index": int(batch_index),
                "batch_total": int(batch_total),
                "attempt": int(attempt_index),
                "case_count": int(len(case_briefs)),
                "prompt_chars": int(len(prompt)),
                "call_mode": str(call_meta.get("call_mode") or ""),
                "response_chars": int(len(str(raw or ""))),
                "timeout": bool(_is_timeout_response(raw)),
                "retry_reason": str(call_meta.get("retry_reason") or ""),
                "provider_finish_reason": str(provider_meta.get("finish_reason") or ""),
                "provider_content_len": int(provider_meta.get("content_len") or 0),
                "provider_reasoning_len": int(provider_meta.get("reasoning_len") or 0),
                "cache_bypassed": bool(call_meta.get("cache_bypassed")),
            }
        )
        if _is_timeout_response(raw):
            last_payload = {
                "message": "optimization_model_timeout",
                "raw_message": str(raw or "").strip()[:300],
                "prompt_case_count": int(len(case_briefs)),
                "prompt_chars": int(len(prompt)),
                "prompt_batch_index": int(batch_index),
                "prompt_batch_count": int(batch_total),
                "attempt": int(attempt_index),
            }
            continue
        if _is_error_response(raw):
            last_payload = {
                "message": str(raw or "").strip(),
                "prompt_batch_index": int(batch_index),
                "prompt_batch_count": int(batch_total),
                "attempt": int(attempt_index),
            }
            continue

        patch_status, patch = parse_optimization_patch(
            raw,
            requirement_contract=requirement_contract,
        )
        if patch_status == "ok":
            patch.setdefault("fix_notes", [])
            patch["fix_notes"] = list(_as_list(patch.get("fix_notes"))) + [
                f"global_pass={batch_index},attempt={attempt_index}"
            ]
            return "ok", patch
        last_payload = {
            "message": "optimization_patch_invalid",
            "prompt_batch_index": int(batch_index),
            "prompt_batch_count": int(batch_total),
            "attempt": int(attempt_index),
            **patch,
        }

    if str(last_payload.get("message") or "") == "optimization_model_timeout":
        return "model_timeout", last_payload
    if str(last_payload.get("message") or "") == "optimization_patch_invalid":
        return "patch_invalid", last_payload
    return "model_error", last_payload or {"message": "optimization_model_error"}


def _resolve_min_acceptable_final(ledger: dict[str, Any], source_count: int) -> int:
    candidates: list[int] = []
    gate = _as_dict(ledger.get("case_quality_gate"))
    metrics = _as_dict(gate.get("metrics"))
    generation_summary = _as_dict(ledger.get("generation_summary"))
    for value in (
        metrics.get("min_acceptable_final"),
        gate.get("min_acceptable_final"),
        generation_summary.get("min_acceptable_final"),
    ):
        try:
            parsed = int(value or 0)
        except Exception:
            parsed = 0
        if parsed > 0:
            candidates.append(parsed)
    if candidates:
        return max(1, min(max(candidates), int(source_count or 0) + 60))
    return max(1, int(round(float(source_count or 0) * 0.8)))


def parse_optimization_patch(
    raw_response: Any,
    *,
    requirement_contract: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    text = str(raw_response or "").strip()
    if not text or text.startswith("Error:") or text.startswith("Exception"):
        return "error", {"schema_errors": ["empty_or_error_response"]}
    parsed = clean_and_parse_json(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("patch"), dict) and len(parsed) == 1:
        parsed = dict(parsed.get("patch") or {})
    if not isinstance(parsed, dict):
        return "error", {"schema_errors": ["patch_must_be_json_object"]}

    errors: list[str] = []
    forbidden = sorted(key for key in parsed if key in FORBIDDEN_FULL_REWRITE_KEYS)
    if forbidden:
        errors.append("full_rewrite_keys_forbidden:" + ",".join(forbidden))
    unknown = sorted(key for key in parsed if key not in ALLOWED_PATCH_KEYS)
    if unknown:
        errors.append("unknown_patch_keys:" + ",".join(unknown))
    if isinstance(parsed.get("fix_notes"), str):
        parsed["fix_notes"] = [str(parsed.get("fix_notes") or "").strip()]
    for key in ("add_cases", "replace_cases", "drop_case_ids", "fix_notes"):
        if key in parsed and not isinstance(parsed.get(key), list):
            errors.append(f"{key}_must_be_list")
    normalized_add_cases: list[dict[str, Any]] = []
    if isinstance(parsed.get("add_cases"), list):
        if parsed.get("add_cases") and not requirement_contract:
            errors.append("add_cases:verified_requirement_contract_missing")
        for index, raw_case in enumerate(parsed.get("add_cases") or [], start=1):
            if not isinstance(raw_case, dict):
                errors.append(f"add_cases[{index}]_must_be_object")
                continue
            case = dict(raw_case)
            case_text = "\n".join(
                [
                    str(case.get("description") or ""),
                    str(case.get("test_module") or ""),
                    *[str(item or "") for item in (case.get("preconditions") or [])],
                    *[str(item or "") for item in (case.get("steps") or [])],
                    str(case.get("test_input") or ""),
                    str(case.get("expected_result") or ""),
                ]
            )
            validation = validate_case_semantic_contract(
                case.get("_semantic"),
                case_text=case_text,
                case_test_module=str(case.get("test_module") or "").strip(),
                requirement_contract=requirement_contract,
            )
            if not validation.get("valid"):
                reasons = ",".join(validation.get("rejection_reasons") or ["unknown"])
                errors.append(f"add_cases[{index}]_semantic_contract_invalid:{reasons}")
                continue
            case["_semantic"] = dict(validation.get("semantic") or {})
            normalized_add_cases.append(case)
    if errors:
        return "error", {"schema_errors": errors}

    return (
        "ok",
        {
            "add_cases": normalized_add_cases,
            "replace_cases": [dict(item) for item in _as_list(parsed.get("replace_cases")) if isinstance(item, dict)],
            "drop_case_ids": [str(item).strip() for item in _as_list(parsed.get("drop_case_ids")) if str(item).strip()],
            "fix_notes": [str(item).strip() for item in _as_list(parsed.get("fix_notes")) if str(item).strip()],
        },
    )


def _replacement_case(item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    case_id = str(item.get("case_id") or item.get("id") or "").strip()
    nested = item.get("case")
    if isinstance(nested, dict) and case_id:
        return case_id, dict(nested)
    if case_id and any(key in item for key in ("description", "expected_result", "steps")):
        case = dict(item)
        case.pop("case_id", None)
        return case_id, case
    return None


def apply_optimization_patch(
    original_cases: list[dict[str, Any]],
    patch: dict[str, Any],
    *,
    max_new_cases: int = 30,
    execution_repair: bool = False,
    execution_repair_contract: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    source_cases = [dict(item) for item in original_cases if isinstance(item, dict)]
    source_by_id = {_case_id(case, index): dict(case) for index, case in enumerate(source_cases, start=1)}
    if len(source_by_id) != len(source_cases):
        return "patch_invalid", [], {"duplicate_original_case_ids": True}

    max_drop = int(len(source_by_id) * MAX_DROP_RATIO)
    drop_ids = list(dict.fromkeys(str(item).strip() for item in _as_list(patch.get("drop_case_ids")) if str(item).strip()))
    patch_summary: dict[str, Any] = {
        "add_count": int(len(_as_list(patch.get("add_cases")))),
        "replace_count": int(len(_as_list(patch.get("replace_cases")))),
        "drop_count": int(len(drop_ids)),
        "allowed_drop_count": int(max_drop),
        "fix_notes": list(_as_list(patch.get("fix_notes"))),
    }
    active_main_smoke_ids: set[str] = set()
    active_required_stage_ids: set[str] = set()
    required_stage_ids = {
        str(item or "").strip()
        for item in _as_list(_as_dict(execution_repair_contract).get("required_stage_ids"))
        if str(item or "").strip()
    }
    missing_drop_ids = [case_id for case_id in drop_ids if case_id not in source_by_id]
    if missing_drop_ids:
        patch_summary["missing_drop_case_ids"] = missing_drop_ids
        return "patch_invalid", [], patch_summary
    if len(drop_ids) > max_drop:
        return "drop_ratio_exceeded", [], patch_summary

    replacements: dict[str, dict[str, Any]] = {}
    for item in _as_list(patch.get("replace_cases")):
        if not isinstance(item, dict):
            continue
        resolved = _replacement_case(item)
        if not resolved:
            patch_summary.setdefault("invalid_replace_items", []).append(item)
            return "patch_invalid", [], patch_summary
        target_id, replacement = resolved
        if target_id not in source_by_id:
            patch_summary.setdefault("missing_replace_case_ids", []).append(target_id)
            return "patch_invalid", [], patch_summary
        replacement_id = str(case_access_id(replacement) or target_id).strip()
        if replacement_id and replacement_id != target_id:
            patch_summary.setdefault("replace_id_mismatch", []).append(
                {"case_id": target_id, "replacement_id": replacement_id}
            )
            return "patch_invalid", [], patch_summary
        merged = dict(source_by_id[target_id])
        merged.update(dict(replacement))
        merged["id"] = target_id
        if str(merged.get("execution_group") or "").strip().lower() == "main_smoke":
            active_main_smoke_ids.add(target_id)
            stage_id = str(merged.get("main_chain_stage") or "").strip()
            if stage_id in required_stage_ids:
                active_required_stage_ids.add(stage_id)
        replacements[target_id] = merged

    add_cases = [dict(item) for item in _as_list(patch.get("add_cases")) if isinstance(item, dict)][:max_new_cases]
    for item in add_cases:
        if str(item.get("execution_group") or "").strip().lower() != "main_smoke":
            continue
        stage_id = str(item.get("main_chain_stage") or "").strip()
        if stage_id in required_stage_ids:
            active_required_stage_ids.add(stage_id)
    should_demote_stale_main_smoke = bool(
        execution_repair
        and required_stage_ids
        and required_stage_ids.issubset(active_required_stage_ids)
    )
    if execution_repair:
        patch_summary["declared_required_stage_ids"] = sorted(required_stage_ids)
        patch_summary["active_required_stage_ids"] = sorted(active_required_stage_ids)

    def demote_stale_main_smoke(case: dict[str, Any]) -> dict[str, Any]:
        updated = dict(case)
        updated["execution_group"] = "independent_functional"
        for key in (
            "workflow_id",
            "source_state",
            "action",
            "target_state",
            "path_type",
            "blocking",
            "destructive",
            "can_advance_main_flow",
            "state_transition_confidence",
            "main_chain_stage",
            "main_chain_stage_label",
            "main_chain_stage_kind",
            "main_chain_step",
            "chain_id",
            "depends_on",
        ):
            updated.pop(key, None)
        return updated

    result: list[dict[str, Any]] = []
    dropped = set(drop_ids)
    for index, case in enumerate(source_cases, start=1):
        current_id = _case_id(case, index)
        if current_id in dropped:
            continue
        merged_case = dict(replacements.get(current_id) or case)
        if (
            should_demote_stale_main_smoke
            and current_id not in active_main_smoke_ids
            and str(merged_case.get("execution_group") or "").strip().lower() == "main_smoke"
        ):
            merged_case = demote_stale_main_smoke(merged_case)
            patch_summary["demoted_stale_main_smoke_count"] = int(
                patch_summary.get("demoted_stale_main_smoke_count") or 0
            ) + 1
        result.append(merged_case)

    result.extend(add_cases)
    normalized = reorder_cases_by_closed_loop(
        deduplicate_test_cases(result),
        start_id=1,
        renumber_ids=True,
    )
    normalized = assign_presentation_order(
        normalized,
        presentation_ordered_cases=normalized,
    )
    normalized = apply_existing_execution_group_ordering(
        normalized,
        start_id=1,
        renumber_ids=True,
    )
    patch_summary["result_count"] = int(len(normalized))
    return "ok", normalized, patch_summary


def _quality_gate_for_cases(
    cases: list[dict[str, Any]],
    *,
    requirement_text: str,
    min_acceptable_final: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    projected = project_persistable_cases(cases)
    case_quality_gate = summarize_case_quality_gate(projected)
    case_quality_gate = merge_contract_quality_gate(
        case_quality_gate,
        summarize_persistable_case_contract(projected),
    )
    coverage = analyze_coverage(requirement_text or "", projected)
    generation_summary = {
        "final_count": int(len(projected)),
        "min_acceptable_final": int(min_acceptable_final),
        "underfill_reason": "",
        "underfill_root_cause": "",
    }
    case_quality_gate = summarize_persistence_case_quality_gate(
        case_quality_gate,
        generation_summary=generation_summary,
        review_decision_summary={},
        judge_summary={},
        settings=settings,
    )
    return projected, case_quality_gate, coverage


def _add_diag(db: Any, *, project_id: int | None, user_id: int | None, payload: dict[str, Any]) -> None:
    if not db or project_id is None:
        return
    db.add(
        LogEntry(
            project_id=int(project_id),
            user_id=user_id,
            log_type="system",
            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
        )
    )


def _ledger_from_preview_diagnostics(diagnostics: Any) -> dict[str, Any]:
    if isinstance(diagnostics, list):
        ledger: dict[str, Any] = {}
        for event in diagnostics:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "").strip()
            if kind == "generation_quality_ledger":
                ledger.update(dict(event))
            elif kind == "case_quality_gate":
                ledger["case_quality_gate"] = dict(event)
            elif kind == "persistence_gate":
                ledger["persistence_gate"] = dict(event)
            elif kind == "coverage_check":
                ledger["coverage"] = event.get("data") if isinstance(event.get("data"), dict) else dict(event)
            elif kind == "review_decision_summary":
                ledger["review"] = dict(event)
            elif kind == "judge_summary":
                ledger["judge"] = dict(event)
        return ledger

    data = _as_dict(diagnostics)
    ledger = dict(_as_dict(data.get("generation_quality_ledger") or data.get("generationQualityLedger")))
    preview_contract = data.get("requirement_semantic_contract") or data.get(
        "requirementSemanticContract"
    )
    if isinstance(preview_contract, dict) and preview_contract:
        ledger["requirement_semantic_contract"] = dict(preview_contract)
    pairs = (
        ("case_quality_gate", ("case_quality_gate", "caseQualityGate")),
        ("persistence_gate", ("persistence_gate", "persistenceGate")),
        ("coverage", ("coverage",)),
        ("review", ("review", "reviewDecisionSummary")),
        ("judge", ("judge", "judgeSummary")),
        ("generation_summary", ("generation_summary", "generationSummary")),
    )
    for target_key, source_keys in pairs:
        if target_key in ledger:
            continue
        for source_key in source_keys:
            value = data.get(source_key)
            if isinstance(value, dict):
                ledger[target_key] = dict(value)
                break
    if data.get("error"):
        ledger["frontend_error"] = str(data.get("error") or "")
    return ledger


class GenerationOptimizationService:
    """Apply an explicit model repair patch and persist it as a new generation."""

    __test__ = False

    def __init__(self, db: Any):
        self.db = db

    def _owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == int(project_id), Project.user_id == int(user_id))
            .first()
        )

    def optimize_generation(
        self,
        *,
        generation_id: int,
        user_id: int,
        apply: bool = True,
        max_new_cases: int = 30,
    ) -> tuple[str, dict[str, Any]]:
        entry = self.db.query(TestGeneration).filter(TestGeneration.id == int(generation_id)).first()
        if not entry:
            return "not_found", {}
        if entry.project_id is not None:
            if not self._owned_project(project_id=int(entry.project_id), user_id=user_id):
                return "not_found", {}
        elif entry.user_id != user_id:
            return "not_found", {}

        source_cases = project_persistable_cases(_parse_cases(entry.generated_result or ""))
        if not source_cases:
            return "invalid_source", {"message": "source_generation_has_no_cases"}

        ledger = find_generation_quality_ledger(db=self.db, log_entry_model=LogEntry, entry=entry)
        return self._optimize_cases(
            source_cases=source_cases,
            requirement_text=entry.requirement_text or "",
            ledger=ledger,
            user_id=user_id,
            project_id=entry.project_id,
            source_generation_id=int(entry.id),
            apply=apply,
            max_new_cases=max_new_cases,
        )

    def optimize_preview_generation(
        self,
        *,
        project_id: int,
        user_id: int,
        requirement_text: str,
        cases: Any,
        diagnostics: Any = None,
        apply: bool = True,
        max_new_cases: int = 30,
    ) -> tuple[str, dict[str, Any]]:
        if not self._owned_project(project_id=int(project_id), user_id=user_id):
            return "project_not_found", {}
        source_cases = project_persistable_cases(_parse_cases(cases))
        if not source_cases:
            return "invalid_source", {"message": "preview_generation_has_no_cases"}
        ledger = _ledger_from_preview_diagnostics(diagnostics)
        return self._optimize_cases(
            source_cases=source_cases,
            requirement_text=requirement_text or "",
            ledger=ledger,
            user_id=user_id,
            project_id=int(project_id),
            source_generation_id=None,
            apply=apply,
            max_new_cases=max_new_cases,
        )

    def _optimize_cases(
        self,
        *,
        source_cases: list[dict[str, Any]],
        requirement_text: str,
        ledger: dict[str, Any],
        user_id: int,
        project_id: int | None,
        source_generation_id: int | None,
        apply: bool,
        max_new_cases: int,
    ) -> tuple[str, dict[str, Any]]:
        source_id = int(source_generation_id) if source_generation_id is not None else None
        if not _optimization_needed(ledger):
            return "no_optimization_needed", {
                "status": "no_optimization_needed",
                "source_generation_id": source_id,
                "generation_id": source_id,
                "cases": source_cases,
                "diagnostics": [],
            }

        max_new_cases = max(1, min(int(max_new_cases or 30), 60))
        requirement_contract = _requirement_contract_from_ledger(ledger)
        repair_context = _declared_execution_repair_context(ledger)
        execution_repair = bool(repair_context)
        focused_case_briefs = _focused_case_briefs(source_cases, ledger)
        case_batches = [focused_case_briefs]
        batch_total = 1
        batch_new_case_budget = max_new_cases
        optimization_max_tokens = _optimization_execution_max_tokens() if execution_repair else _optimization_max_tokens()
        client = get_optimization_ai_client(user_id=user_id, db=self.db)
        patches: list[dict[str, Any]] = []
        prompt_batches: list[dict[str, Any]] = []
        for batch_index, case_briefs in enumerate(case_batches, start=1):
            patch_status, patch = _collect_patch_for_case_batch(
                client=client,
                requirement_text=requirement_text or "",
                source_cases=source_cases,
                ledger=ledger,
                case_briefs=case_briefs,
                batch_index=batch_index,
                batch_total=batch_total,
                max_new_cases=batch_new_case_budget,
                requirement_contract=requirement_contract,
                max_tokens=optimization_max_tokens,
                db=self.db,
                prompt_batches=prompt_batches,
            )
            if patch_status != "ok":
                patch["prompt_batches"] = prompt_batches
                return patch_status, patch
            patches.append(patch)
        patch = _merge_optimization_patches(patches)

        apply_status, merged_cases, merge_summary = apply_optimization_patch(
            source_cases,
            patch,
            max_new_cases=max_new_cases,
            execution_repair=execution_repair,
            execution_repair_contract=_as_dict(
                repair_context.get("declared_workflow_contract")
            ),
        )
        if apply_status != "ok":
            return apply_status, {
                "message": "optimization_patch_failed_safety_guard",
                "patch_summary": merge_summary,
            }

        min_acceptable_final = _resolve_min_acceptable_final(ledger, len(source_cases))
        projected_cases, case_quality_gate, coverage = _quality_gate_for_cases(
            merged_cases,
            requirement_text=requirement_text or "",
            min_acceptable_final=min_acceptable_final,
        )
        persistence_gate = evaluate_persistence_gate(
            projected_cases,
            workflow_blueprints=[
                dict(item)
                for item in (requirement_contract.get("workflow_blueprints") or [])
                if isinstance(item, dict)
            ] or (
                [_as_dict(repair_context.get("declared_workflow_contract"))]
                if repair_context
                else []
            ),
            execution_plan={
                "workflow_blueprint_source": str(
                    (
                        "requirement_semantic_contract"
                        if requirement_contract
                        else repair_context.get("workflow_blueprint_source")
                    )
                    or "none"
                )
            },
            generation_mode="optimization",
            quality_gate=case_quality_gate,
            settings=settings,
        )
        persistence_gate_diag = build_persistence_gate_diagnostic(persistence_gate)

        new_generation_id: int | None = None
        persisted = False
        final_cases = persistence_gate.get("cases") if isinstance(persistence_gate.get("cases"), list) else projected_cases
        try:
            if apply and bool(persistence_gate.get("passed")):
                new_entry = TestGeneration(
                    requirement_text=requirement_text or "",
                    generated_result=json.dumps(final_cases, ensure_ascii=False),
                    project_id=project_id,
                    user_id=user_id,
                )
                self.db.add(new_entry)
                self.db.flush()
                new_generation_id = int(new_entry.id or 0) or None
                persisted = bool(new_generation_id)

            optimization_diag = {
                "kind": "generation_optimization",
                "status": "persisted" if persisted else "blocked",
                "source_generation_id": source_id,
                "generation_id": new_generation_id,
                "action_ids": _action_ids(ledger),
                "before_count": int(len(source_cases)),
                "after_count": int(len(final_cases)),
                "added_count": int(merge_summary.get("add_count") or 0),
                "replaced_count": int(merge_summary.get("replace_count") or 0),
                "dropped_count": int(merge_summary.get("drop_count") or 0),
                "persisted": persisted,
                "batch_count": int(batch_total),
                "prompt_case_count": int(len(focused_case_briefs)),
                "global_candidate_count": int(len(focused_case_briefs)),
                "requirement_contract_available": bool(requirement_contract),
                "optimization_max_tokens": int(optimization_max_tokens),
                "execution_repair_mode": bool(execution_repair),
                "prompt_batches": prompt_batches,
                **merge_summary,
            }
            case_quality_diag = {
                "kind": "case_quality_gate",
                "mode": "optimization",
                "generation_id": new_generation_id,
                "source_generation_id": source_id,
                "passed": bool(case_quality_gate.get("passed")),
                "blocked": bool(persistence_gate.get("blocked")),
                "failure_reasons": list(case_quality_gate.get("failed_checks") or []),
                "metrics": dict(case_quality_gate.get("metrics") or {}),
            }
            persistence_gate_diag.update(
                {
                    "generation_id": new_generation_id,
                    "source_generation_id": source_id,
                    "project_id": int(project_id or 0) if project_id is not None else None,
                }
            )
            diagnostics = [
                optimization_diag,
                case_quality_diag,
                persistence_gate_diag,
                {
                    "kind": "coverage_check",
                    "data": coverage,
                    "generation_id": new_generation_id,
                    "source_generation_id": source_id,
                },
            ]
            for payload in diagnostics:
                _add_diag(self.db, project_id=project_id, user_id=user_id, payload=payload)
            self.db.commit()
        except Exception as exc:
            if hasattr(self.db, "rollback"):
                self.db.rollback()
            return "persistence_failed", {
                "status": "persistence_failed",
                "source_generation_id": source_id,
                "generation_id": None,
                "cases": [],
                "message": f"{type(exc).__name__}: {exc}",
            }

        if apply and not bool(persistence_gate.get("passed")):
            return "quality_gate_failed", {
                "status": "quality_gate_failed",
                "source_generation_id": source_id,
                "generation_id": None,
                "cases": [],
                "diagnostics": diagnostics,
                "case_quality_gate": case_quality_diag,
                "persistence_gate": persistence_gate_diag,
                "optimization_summary": optimization_diag,
                "message": "optimized_cases_failed_persistence_gate",
            }

        return "ok", {
            "status": "optimized",
            "source_generation_id": source_id,
            "generation_id": new_generation_id or source_id,
            "cases": final_cases,
            "diagnostics": diagnostics,
            "case_quality_gate": case_quality_diag,
            "persistence_gate": persistence_gate_diag,
            "optimization_summary": optimization_diag,
            "fix_notes": patch.get("fix_notes") or [],
        }


__all__ = [
    "GenerationOptimizationService",
    "apply_optimization_patch",
    "get_optimization_ai_client",
    "parse_optimization_patch",
]
