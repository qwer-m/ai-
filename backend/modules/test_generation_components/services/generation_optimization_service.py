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
from ..postprocess.streaming_execution_plan_ordering import (
    apply_existing_execution_group_ordering,
    assign_presentation_order,
)
from .final_case_parsing import parse_test_cases_payload
from .final_case_quality_ledger_lookup import find_generation_quality_ledger


MAX_REQUIREMENT_CHARS = 6000
MAX_CASE_BRIEF_COUNT = 36
MAX_CASE_BRIEF_CHARS = 12000
MAX_LEDGER_CHARS = 7000
DEFAULT_OPTIMIZATION_BATCH_CASE_COUNT = 12
DEFAULT_OPTIMIZATION_EXECUTION_CASE_COUNT = 8
DEFAULT_OPTIMIZATION_BATCH_CASE_BRIEF_CHARS = 5000
DEFAULT_OPTIMIZATION_BATCH_LEDGER_CHARS = 4000
DEFAULT_OPTIMIZATION_BATCH_REQUIREMENT_CHARS = 4500
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


def _safe_json(value: Any, *, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:limit]


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
    for index, case in enumerate(cases[:MAX_CASE_BRIEF_COUNT], start=1):
        rows.append(
            {
                "id": _case_id(case, index),
                "description": str(case.get("description") or case.get("name") or "")[:220],
                "test_module": str(case.get("test_module") or case.get("module") or "")[:120],
                "steps": case.get("steps") if isinstance(case.get("steps"), list) else [],
                "expected_result": str(case.get("expected_result") or "")[:260],
                "priority": str(case.get("priority_final") or case.get("priority") or "")[:20],
            }
        )
    return rows


def _execution_case_briefs(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases[:MAX_CASE_BRIEF_COUNT], start=1):
        rows.append(
            {
                "id": _case_id(case, index),
                "description": str(case.get("description") or case.get("name") or "")[:160],
                "test_module": str(case.get("test_module") or case.get("module") or "")[:100],
                "priority": str(case.get("priority_final") or case.get("priority") or "")[:20],
                "execution_group": str(case.get("execution_group") or "")[:80],
                "execution_sequence": case.get("execution_sequence"),
                "workflow_id": str(case.get("workflow_id") or "")[:120],
                "source_state": str(case.get("source_state") or "")[:120],
                "action": str(case.get("action") or "")[:160],
                "target_state": str(case.get("target_state") or "")[:120],
                "main_chain_stage_kind": str(case.get("main_chain_stage_kind") or "")[:80],
                "suggested_stage_kind": str(case.get("_suggested_main_chain_stage_kind") or "")[:80],
            }
        )
    return rows


def _chunk_case_briefs(case_briefs: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    if not case_briefs:
        return [[]]
    size = max(1, int(batch_size or DEFAULT_OPTIMIZATION_BATCH_CASE_COUNT))
    return [case_briefs[index : index + size] for index in range(0, len(case_briefs), size)]


_MAIN_CHAIN_STAGE_ORDER = {
    "entry": 0,
    "configure": 1,
    "preview": 2,
    "commit": 3,
    "downstream_visibility": 4,
    "consume": 5,
    "completion_sync": 5,
}


def _normalize_stage_kind(value: Any, fallback_index: int) -> str:
    stage = str(value or "").strip().lower()
    if stage in _MAIN_CHAIN_STAGE_ORDER:
        return stage
    fallback = ("entry", "configure", "preview", "commit", "downstream_visibility", "completion_sync")
    return fallback[min(max(0, int(fallback_index)), len(fallback) - 1)]


def _normalize_execution_repair_main_chain(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    main_cases: list[dict[str, Any]] = []
    side_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        item = dict(case)
        if str(item.get("execution_group") or "").strip().lower() == "main_smoke":
            main_cases.append(item)
        else:
            side_cases.append(item)

    if not main_cases:
        return [dict(item) for item in cases]

    def sequence_value(item: dict[str, Any], fallback: int) -> int:
        try:
            value = int(item.get("execution_sequence") or 0)
        except Exception:
            value = 0
        return value if value > 0 else fallback

    main_cases = [
        dict(item)
        for _rank, _sequence, _index, item in sorted(
            (
                (
                    _MAIN_CHAIN_STAGE_ORDER.get(str(item.get("main_chain_stage_kind") or "").strip().lower(), 99),
                    sequence_value(item, index),
                    index,
                    item,
                )
                for index, item in enumerate(main_cases, start=1)
            ),
            key=lambda row: (row[0], row[1], row[2]),
        )
    ]

    workflow_id = next(
        (
            str(item.get("workflow_id") or "").strip()
            for item in main_cases
            if str(item.get("workflow_id") or "").strip()
        ),
        "optimized_main_flow",
    )
    state_pairs = [
        ("initial", "entry_done"),
        ("entry_done", "configure_done"),
        ("configure_done", "preview_done"),
        ("preview_done", "commit_done"),
        ("commit_done", "downstream_visibility_done"),
        ("downstream_visibility_done", "completion_sync_done"),
    ]
    normalized_main: list[dict[str, Any]] = []
    for index, item in enumerate(main_cases, start=1):
        stage_kind = _normalize_stage_kind(item.get("main_chain_stage_kind"), index - 1)
        source_state, target_state = state_pairs[min(index - 1, len(state_pairs) - 1)]
        updated = dict(item)
        updated["workflow_id"] = workflow_id
        updated["source_state"] = source_state
        updated["target_state"] = target_state
        updated["path_type"] = "positive"
        updated["blocking"] = False
        updated["destructive"] = False
        updated["can_advance_main_flow"] = True
        updated["execution_group"] = "main_smoke"
        updated["main_chain_stage_kind"] = stage_kind
        updated["main_chain_step"] = int(index)
        role = str(updated.get("role") or "").strip().lower()
        if role in {"", "user"}:
            updated["role"] = "business_user"
            updated["session_key"] = "business_user_session"
        elif not str(updated.get("session_key") or "").strip():
            updated["session_key"] = f"{role}_session"
        normalized_main.append(updated)

    ordered = [*normalized_main, *side_cases]
    output: list[dict[str, Any]] = []
    for index, item in enumerate(ordered, start=1):
        updated = dict(item)
        updated["id"] = f"TC-{index:03d}"
        updated["execution_sequence"] = int(index)
        output.append(updated)
    return output


def _optimization_prompt_limits() -> dict[str, int]:
    return {
        "batch_case_count": _setting_int_value(
            "GENERATION_OPTIMIZATION_BATCH_CASE_COUNT",
            DEFAULT_OPTIMIZATION_BATCH_CASE_COUNT,
            minimum=4,
            maximum=MAX_CASE_BRIEF_COUNT,
        ),
        "requirement_chars": _setting_int_value(
            "GENERATION_OPTIMIZATION_REQUIREMENT_CHARS",
            DEFAULT_OPTIMIZATION_BATCH_REQUIREMENT_CHARS,
            minimum=1200,
            maximum=MAX_REQUIREMENT_CHARS,
        ),
        "case_brief_chars": _setting_int_value(
            "GENERATION_OPTIMIZATION_CASE_BRIEF_CHARS",
            DEFAULT_OPTIMIZATION_BATCH_CASE_BRIEF_CHARS,
            minimum=2000,
            maximum=MAX_CASE_BRIEF_CHARS,
        ),
        "ledger_chars": _setting_int_value(
            "GENERATION_OPTIMIZATION_LEDGER_CHARS",
            DEFAULT_OPTIMIZATION_BATCH_LEDGER_CHARS,
            minimum=1200,
            maximum=MAX_LEDGER_CHARS,
        ),
    }


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


def _optimization_execution_case_count() -> int:
    return _setting_int_value(
        "GENERATION_OPTIMIZATION_EXECUTION_CASE_COUNT",
        DEFAULT_OPTIMIZATION_EXECUTION_CASE_COUNT,
        minimum=6,
        maximum=16,
    )


def _retry_prompt_limits(base_limits: dict[str, int], attempt_index: int) -> dict[str, int]:
    divisor = 1 if attempt_index <= 1 else 2 ** (attempt_index - 1)
    return {
        "requirement_chars": max(1200, int(base_limits["requirement_chars"]) // divisor),
        "case_brief_chars": max(1200, int(base_limits["case_brief_chars"]) // divisor),
        "ledger_chars": max(700, int(base_limits["ledger_chars"]) // divisor),
    }


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


def _execution_repair_case_briefs(cases: list[dict[str, Any]], ledger: dict[str, Any]) -> list[dict[str, Any]]:
    limit = _optimization_execution_case_count()
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_modules: set[str] = set()

    def add_case(
        case: dict[str, Any],
        index: int,
        *,
        allow_same_module: bool = True,
        suggested_stage_kind: str = "",
    ) -> None:
        if len(selected) >= limit:
            return
        cid = _case_id(case, index)
        if cid in selected_ids:
            return
        module_key = str(case.get("test_module") or case.get("module") or "").strip()
        if module_key and module_key in selected_modules and not allow_same_module:
            return
        selected_ids.add(cid)
        if module_key:
            selected_modules.add(module_key)
        row = dict(case)
        if suggested_stage_kind:
            row["_suggested_main_chain_stage_kind"] = suggested_stage_kind
        selected.append(row)

    # 中文注释：显式执行契约优先于正文词面猜测，避免领域词命中挤占主链样本。
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            continue
        execution_group = str(case.get("execution_group") or "").strip().lower()
        explicit_stage = str(
            case.get("main_chain_stage_kind")
            or case.get("main_chain_stage")
            or case.get("stage_kind")
            or ""
        ).strip()
        actor = str(case.get("actor") or case.get("role") or "").strip()
        action = str(case.get("action") or "").strip()
        state_in = str(case.get("state_in") or case.get("source_state") or "").strip()
        state_out = str(case.get("state_out") or case.get("target_state") or "").strip()
        has_explicit_contract = bool(
            execution_group == "main_smoke"
            and explicit_stage
            and actor
            and action
            and state_in
            and state_out
        )
        if has_explicit_contract:
            add_case(
                case,
                index,
                suggested_stage_kind=explicit_stage,
            )

    stage_profiles = [
        ("entry", ("入口", "进入", "首页", "导航", "分区", "列表", "entry", "home", "navigate")),
        ("configure", ("编辑", "填写", "输入", "选择", "上传", "配置", "compose", "configure", "input", "upload")),
        ("preview", ("详情", "预览", "展示", "查看", "列表", "图片", "内容", "detail", "preview", "display", "view")),
        ("commit", ("提交", "发布", "保存", "确认", "删除", "审核", "commit", "submit", "publish", "save")),
        ("downstream_visibility", ("消息", "通知", "可见", "展示", "列表", "回复", "message", "visible", "notification", "reply")),
        ("completion_sync", ("同步", "完成", "状态", "更新", "闭环", "跳转", "落地", "sync", "complete", "status", "done")),
    ]

    def case_text(case: dict[str, Any]) -> str:
        parts = [
            str(case.get(key) or "")
            for key in ("description", "test_module", "test_input", "expected_result", "action")
        ]
        steps = case.get("steps")
        if isinstance(steps, list):
            parts.extend(str(item or "") for item in steps[:3])
        return " ".join(parts)

    for stage_kind, tokens in stage_profiles:
        best: tuple[int, int, dict[str, Any]] | None = None
        for index, case in enumerate(cases, start=1):
            if not isinstance(case, dict):
                continue
            if _case_id(case, index) in selected_ids:
                continue
            text = case_text(case).lower()
            score = sum(1 for token in tokens if str(token).lower() in text)
            priority = str(case.get("priority_final") or case.get("priority") or "").strip().upper()
            if priority == "P0":
                score += 1
            if score <= 0:
                continue
            candidate = (score, -index, case)
            if best is None or candidate > best:
                best = candidate
        if best is not None:
            add_case(best[2], abs(best[1]), allow_same_module=True, suggested_stage_kind=stage_kind)

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            continue
        priority = str(case.get("priority_final") or case.get("priority") or "").strip().upper()
        if priority == "P0":
            add_case(case, index, allow_same_module=False)

    stage_tokens = (
        "入口",
        "进入",
        "发布",
        "提交",
        "保存",
        "评论",
        "回复",
        "消息",
        "展示",
        "可见",
        "同步",
        "完成",
        "详情",
        "审核",
        "entry",
        "submit",
        "save",
        "commit",
        "message",
        "visible",
        "detail",
    )
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            continue
        text = " ".join(
            str(case.get(key) or "")
            for key in ("description", "test_module", "test_input", "expected_result", "action")
        ).lower()
        if any(token.lower() in text for token in stage_tokens):
            add_case(case, index, allow_same_module=False)

    for index, case in enumerate(cases, start=1):
        if isinstance(case, dict):
            add_case(case, index, allow_same_module=False)
    for index, case in enumerate(cases, start=1):
        if isinstance(case, dict):
            add_case(case, index)

    return _execution_case_briefs(selected)


def _focused_case_briefs(cases: list[dict[str, Any]], ledger: dict[str, Any]) -> list[dict[str, Any]]:
    if _needs_execution_plan_repair(ledger):
        return _execution_repair_case_briefs(cases, ledger)

    problem_ids = _problem_case_ids_from_ledger(ledger)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add_case(case: dict[str, Any], index: int) -> None:
        if len(selected) >= MAX_CASE_BRIEF_COUNT:
            return
        cid = _case_id(case, index)
        if cid in selected_ids:
            return
        selected_ids.add(cid)
        selected.append(case)

    for index, case in enumerate(cases, start=1):
        if _case_id(case, index) in problem_ids:
            add_case(case, index)
    for index, case in enumerate(cases, start=1):
        priority = str(case.get("priority_final") or case.get("priority") or "").strip().upper()
        execution_group = str(case.get("execution_group") or "").strip().lower()
        if priority == "P0" or execution_group == "main_smoke":
            add_case(case, index)
    for index, case in enumerate(cases, start=1):
        add_case(case, index)
    return _case_briefs(selected)


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
                "final_floor_recovery_applied",
                "final_floor_recovery_reason",
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
    requirement_chars: int = MAX_REQUIREMENT_CHARS,
    case_brief_chars: int = MAX_CASE_BRIEF_CHARS,
    ledger_chars: int = MAX_LEDGER_CHARS,
) -> str:
    focused_ledger = _compact_ledger_for_prompt(ledger)
    focused_cases = case_briefs if case_briefs is not None else _focused_case_briefs(cases, ledger)
    execution_repair = _needs_execution_plan_repair(ledger)
    focus_lines = []
    if execution_repair:
        focus_lines.extend(
            [
                "Primary repair focus: execution_plan_failed. Do not perform broad quality rewriting in this call.",
                "Repair only the minimal executable main chain required for persistence: at least six P0 main_smoke cases with connected source_state -> target_state transitions.",
                "The main chain must include entry/configure-or-compose/preview-or-detail/commit/downstream_visibility-or-message/consume-or-completion_sync stages.",
                "Use path_type=\"positive\" for every main_smoke repair case; do not use happy.",
                "Use blocking=false and destructive=false for positive main flow cases.",
                "Each current case brief may include suggested_stage_kind. Prefer that stage only when the case text supports it.",
                "If visible cases cannot semantically support a required stage, use add_cases for that stage instead of forcing an unrelated case_id.",
                "Use replace_cases to add missing execution fields to visible cases; use add_cases only when the visible cases cannot close the chain.",
                "For execution repair, replacement cases must include only changed execution metadata and priority fields.",
                "Do not include description, test_module, preconditions, steps, test_input, or expected_result in execution repair replacement cases.",
                "Prefer six replace_cases for selected existing case ids. Keep add_cases empty unless fewer than six visible cases can form the chain.",
                "Keep the JSON compact; no markdown, no explanation outside the JSON object.",
            ]
        )
    return "\n".join(
        [
            "You are a test-case quality repair agent. Use only the requirement, current cases, and diagnostics below.",
            f"This is optimization batch {int(batch_index)}/{int(batch_total)}. Only repair cases visible in this batch; do not reference unseen case ids.",
            *focus_lines,
            "Return one JSON object with exactly these top-level keys: add_cases, replace_cases, drop_case_ids, fix_notes.",
            f"add_cases must contain at most {int(max_new_cases)} cases for this batch.",
            "replace_cases items must be shaped as {\"case_id\":\"existing id\",\"case\":{...}}.",
            "drop_case_ids may only remove obviously duplicate or invalid existing cases.",
            "fix_notes must be an array of short strings.",
            "Do not rewrite the whole case set. Focus on missing rules, assertable expected_result, semantic duplicate reduction, and valid coverage backfill.",
            "If final_count is below min_acceptable_final, add only non-duplicate cases that cover missing behavior; do not add filler cases.",
            "Every added case must include id, description, test_module, preconditions, steps, test_input, expected_result, priority, priority_final.",
            "Replacement cases may include only the fields to change; case_id identifies the existing case.",
            "If persistence_gate.execution_plan_validation failed, repair the executable flow fields too: execution_group, execution_sequence, workflow_id, source_state, action, target_state, path_type, blocking, destructive, can_advance_main_flow, role, session_key, main_chain_stage_kind.",
            "For executable main flow, provide enough P0 main_smoke cases and close a configure/preview/commit -> downstream_visibility/consume/completion_sync chain.",
            "",
            "[Requirement]",
            requirement[: int(requirement_chars or MAX_REQUIREMENT_CHARS)],
            "",
            "[Current case brief]",
            _safe_json(focused_cases, limit=int(case_brief_chars or MAX_CASE_BRIEF_CHARS)),
            "",
            "[Quality diagnostics]",
            _safe_json(focused_ledger, limit=int(ledger_chars or MAX_LEDGER_CHARS)),
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
    limits: dict[str, int],
) -> str:
    return _build_prompt(
        requirement=requirement_text or "",
        cases=source_cases,
        ledger=ledger,
        max_new_cases=max_new_cases,
        case_briefs=case_briefs,
        batch_index=batch_index,
        batch_total=batch_total,
        requirement_chars=int(limits["requirement_chars"]),
        case_brief_chars=int(limits["case_brief_chars"]),
        ledger_chars=int(limits["ledger_chars"]),
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
    base_prompt_limits: dict[str, int],
    max_tokens: int,
    db: Any,
    prompt_batches: list[dict[str, Any]],
    depth: int = 0,
) -> tuple[str, dict[str, Any]]:
    attempts = _optimization_batch_attempts()
    last_payload: dict[str, Any] = {}
    for attempt_index in range(1, attempts + 1):
        limits = _retry_prompt_limits(base_prompt_limits, attempt_index + depth)
        prompt = _build_batch_prompt(
            requirement_text=requirement_text,
            source_cases=source_cases,
            ledger=ledger,
            max_new_cases=max_new_cases,
            case_briefs=case_briefs,
            batch_index=batch_index,
            batch_total=batch_total,
            limits=limits,
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
                "depth": int(depth),
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
                "depth": int(depth),
            }
            continue
        if _is_error_response(raw):
            last_payload = {
                "message": str(raw or "").strip(),
                "prompt_batch_index": int(batch_index),
                "prompt_batch_count": int(batch_total),
                "attempt": int(attempt_index),
                "depth": int(depth),
            }
            continue

        patch_status, patch = parse_optimization_patch(raw)
        if patch_status == "ok":
            patch.setdefault("fix_notes", [])
            patch["fix_notes"] = list(_as_list(patch.get("fix_notes"))) + [
                f"batch={batch_index},attempt={attempt_index},depth={depth}"
            ]
            return "ok", patch
        last_payload = {
            "message": "optimization_patch_invalid",
            "prompt_batch_index": int(batch_index),
            "prompt_batch_count": int(batch_total),
            "attempt": int(attempt_index),
            "depth": int(depth),
            **patch,
        }

    if len(case_briefs) > 1 and depth < 4:
        midpoint = max(1, len(case_briefs) // 2)
        left_status, left_patch = _collect_patch_for_case_batch(
            client=client,
            requirement_text=requirement_text,
            source_cases=source_cases,
            ledger=ledger,
            case_briefs=case_briefs[:midpoint],
            batch_index=batch_index,
            batch_total=batch_total,
            max_new_cases=max(1, max_new_cases // 2),
            base_prompt_limits=base_prompt_limits,
            max_tokens=max_tokens,
            db=db,
            prompt_batches=prompt_batches,
            depth=depth + 1,
        )
        right_status, right_patch = _collect_patch_for_case_batch(
            client=client,
            requirement_text=requirement_text,
            source_cases=source_cases,
            ledger=ledger,
            case_briefs=case_briefs[midpoint:],
            batch_index=batch_index,
            batch_total=batch_total,
            max_new_cases=max(1, max_new_cases - max(1, max_new_cases // 2)),
            base_prompt_limits=base_prompt_limits,
            max_tokens=max_tokens,
            db=db,
            prompt_batches=prompt_batches,
            depth=depth + 1,
        )
        if left_status == "ok" and right_status == "ok":
            return "ok", _merge_optimization_patches([left_patch, right_patch])
        if left_status != "ok":
            return left_status, left_patch
        return right_status, right_patch

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


def parse_optimization_patch(raw_response: Any) -> tuple[str, dict[str, Any]]:
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
    if errors:
        return "error", {"schema_errors": errors}

    return (
        "ok",
        {
            "add_cases": [dict(item) for item in _as_list(parsed.get("add_cases")) if isinstance(item, dict)],
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
    active_added_main_smoke_count = 0
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
        replacements[target_id] = merged

    add_cases = [dict(item) for item in _as_list(patch.get("add_cases")) if isinstance(item, dict)][:max_new_cases]
    active_added_main_smoke_count = sum(
        1 for item in add_cases if str(item.get("execution_group") or "").strip().lower() == "main_smoke"
    )
    should_demote_stale_main_smoke = bool(
        execution_repair and len(active_main_smoke_ids) + active_added_main_smoke_count >= 6
    )

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
    if execution_repair:
        normalized = _normalize_execution_repair_main_chain(normalized)
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
        prompt_limits = _optimization_prompt_limits()
        execution_repair = _needs_execution_plan_repair(ledger)
        if execution_repair:
            max_new_cases = min(max_new_cases, 4)
            prompt_limits = dict(prompt_limits)
            prompt_limits["batch_case_count"] = max(1, _optimization_execution_case_count())
            prompt_limits["requirement_chars"] = min(int(prompt_limits["requirement_chars"]), 1600)
            prompt_limits["case_brief_chars"] = min(int(prompt_limits["case_brief_chars"]), 2200)
            prompt_limits["ledger_chars"] = min(int(prompt_limits["ledger_chars"]), 1200)
        focused_case_briefs = _focused_case_briefs(source_cases, ledger)
        case_batches = _chunk_case_briefs(
            focused_case_briefs,
            int(prompt_limits["batch_case_count"]),
        )
        batch_total = max(1, len(case_batches))
        batch_new_case_budget = max(1, min(max_new_cases, (max_new_cases + batch_total - 1) // batch_total))
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
                base_prompt_limits=prompt_limits,
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
            workflow_blueprints=[],
            execution_plan={"workflow_blueprint_source": "current_generation_cases"},
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
                "prompt_batch_size": int(prompt_limits["batch_case_count"]),
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
