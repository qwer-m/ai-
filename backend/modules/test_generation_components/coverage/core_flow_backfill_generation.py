from __future__ import annotations

import json
from collections import Counter
from typing import Any

from core.ai.ai_client import ai_client, get_client_for_user
from core.db.models import LogEntry
from .case_quality_gate import summarize_case_quality_gate as _summarize_case_quality_gate
from .core_flow_backfill_candidates import (
    NON_ASSERTABLE_EXPECTED_PHRASES as _NON_ASSERTABLE_EXPECTED_PHRASES,
    accept_backfill_candidate_cases as _accept_backfill_candidate_cases,
    normalize_backfill_candidate_cases as _normalize_backfill_candidate_cases,
    normalize_case_structure_light as _normalize_case_structure_light,
    safe_json_array as _safe_json_array,
)
from .core_flow_coverage_contract import (
    CORE_FLOWS,
    audit_core_flow_coverage,
)
from .core_flow_backfill_selection import select_merged_preview_cases
from ..postprocess.result_postprocess_priority_semantics import (
    apply_priority_semantics_to_cases,
)
from ..postprocess.case_access import (
    case_id as case_access_id,
    case_text_field,
)

def _resolve_llm_client(*, llm_client: Any, db: Any, backfill_plan: dict[str, Any]) -> Any:
    if llm_client is not None:
        return llm_client
    user_id = int(backfill_plan.get("user_id") or backfill_plan.get("generation_user_id") or 0)
    if user_id > 0 and db is not None:
        try:
            return get_client_for_user(user_id, db)
        except Exception:
            pass
    return ai_client


def _build_prompt(
    *,
    requirement_context: str,
    existing_cases: list[dict[str, Any]],
    backfill_items: list[dict[str, Any]],
    max_candidates: int,
) -> str:
    forbidden_phrases = "\n".join([f"- {phrase}" for phrase in _NON_ASSERTABLE_EXPECTED_PHRASES])

    existing_snapshot: list[dict[str, str]] = []
    for case in existing_cases[:24]:
        existing_snapshot.append(
            {
                "id": case_access_id(case),
                "description": case_text_field(case, "description")[:120],
                "test_module": case_text_field(case, "test_module")[:80],
                "expected_result": case_text_field(case, "expected_result")[:160],
            }
        )

    plan_payload = [
        {
            "flow_key": str(item.get("flow_key") or ""),
            "flow_name": str(item.get("flow_name") or ""),
            "required_focus": str(item.get("required_focus") or ""),
            "suggested_priority": str(item.get("suggested_priority") or "P1"),
            "must_include_assertions": list(item.get("must_include_assertions") or []),
        }
        for item in backfill_items
    ]

    output_schema = {
        "case_id": "BF-001",
        "description": "",
        "test_module": "",
        "preconditions": [""],
        "steps": [""],
        "test_input": "",
        "expected_result": "",
        "priority": "P0/P1/P2",
        "model_priority": "P0/P1/P2",
        "source_flow_key": "",
        "source_flow_name": "",
        "backfill_generated": True,
    }

    return (
        "你是测试用例补全器。请严格输出 JSON array，且只输出 JSON。\n"
        "任务：根据 missing core flow 生成 backfill candidate cases。\n"
        f"最多输出 {int(max_candidates)} 条，每个 flow_key 最多 1 条，不要重复 existing_cases。\n"
        "每条用例必须只围绕 source_flow_key 对应 flow，不要混合多个 flow。\n"
        "expected_result 必须可断言，必须包含具体可验证结果。\n"
        "禁止在 expected_result 中出现以下短语：\n"
        f"{forbidden_phrases}\n"
        "可用的断言示例：页面文案等于xxx、按钮不可点击、列表数量等于N、接口字段status=xxx、不创建学习任务记录、跳转目标为xxx。\n"
        "输出每条 case 必须包含字段：case_id, description, test_module, preconditions, steps, test_input, expected_result, priority, model_priority, source_flow_key, source_flow_name, backfill_generated。\n"
        "backfill_generated 必须为 true。\n\n"
        f"Requirement Context:\n{requirement_context}\n\n"
        f"Missing Core Flow Plan:\n{json.dumps(plan_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Existing Cases Snapshot (do not duplicate):\n{json.dumps(existing_snapshot, ensure_ascii=False, indent=2)}\n\n"
        f"Output Schema Example:\n{json.dumps(output_schema, ensure_ascii=False, indent=2)}\n"
    )


def _build_default_result() -> dict[str, Any]:
    empty_audit = audit_core_flow_coverage([])
    return {
        "backfill_generation_mode": "llm",
        "generated_backfill_candidate_cases": [],
        "raw_backfill_response": "",
        "generation_errors": [],
        "accepted_backfill_cases": [],
        "rejected_backfill_cases": [],
        "merged_preview_cases": [],
        "quality_metrics": {},
        "coverage_before": empty_audit,
        "coverage_after_candidates": empty_audit,
        "coverage_after_merged_preview": empty_audit,
        "newly_covered_flows": [],
        "still_missing_core_flows": list(empty_audit.get("missing_core_flows") or []),
        "accepted_for_preview_count": 0,
        "primary_retained_count": 0,
        "primary_trimmed_count": 0,
        "backfill_retained_count": 0,
        "backfill_trimmed_count": 0,
        "coverage_first_selection_applied": False,
        "trimmed_primary_case_ids": [],
        "retained_backfill_case_ids": [],
        "dropped_backfill_due_to_limit_case_ids": [],
        "backfill_not_applied": True,
        "dry_run": True,
    }


def _persist_generation_dry_run_log(
    *,
    db: Any,
    backfill_plan: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if db is None:
        return
    try:
        project_id = backfill_plan.get("project_id")
        user_id = backfill_plan.get("user_id")
        coverage_before = result.get("coverage_before") or {}
        coverage_after = result.get("coverage_after_merged_preview") or {}
        rejected = [item for item in (result.get("rejected_backfill_cases") or []) if isinstance(item, dict)]
        rejection_reasons = Counter(str(item.get("rejection_reason") or "unknown") for item in rejected)
        payload = {
            "kind": "core_flow_backfill_generation_dry_run",
            "dry_run": True,
            "backfill_not_applied": True,
            "primary_case_count": int(backfill_plan.get("primary_case_count") or 0),
            "planned_backfill_count": int(backfill_plan.get("planned_backfill_count") or 0),
            "generated_backfill_candidate_count": int(len(result.get("generated_backfill_candidate_cases") or [])),
            "accepted_backfill_candidate_count": int(len(result.get("accepted_backfill_cases") or [])),
            "rejected_backfill_candidate_count": int(len(rejected)),
            "merged_preview_case_count": int(len(result.get("merged_preview_cases") or [])),
            "coverage_before": {
                "covered": int(coverage_before.get("core_flow_covered_count") or 0),
                "required": int(coverage_before.get("core_flow_required_count") or len(CORE_FLOWS)),
            },
            "coverage_after": {
                "covered": int(coverage_after.get("core_flow_covered_count") or 0),
                "required": int(coverage_after.get("core_flow_required_count") or len(CORE_FLOWS)),
            },
            "newly_covered_flows": list(result.get("newly_covered_flows") or []),
            "still_missing_core_flows": list(result.get("still_missing_core_flows") or []),
            "quality_metrics": dict(result.get("quality_metrics") or {}),
            "rejection_reasons": dict(rejection_reasons),
        }
        db.add(
            LogEntry(
                project_id=int(project_id) if project_id is not None else None,
                user_id=int(user_id) if user_id is not None else None,
                log_type="system",
                message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def generate_core_flow_backfill_candidates(
    requirement_context: str,
    existing_cases: list[dict],
    backfill_plan: dict,
    db=None,
    llm_client=None,
    max_candidates: int = 12,
    preview_min_total: int = 12,
    preview_max_total: int = 18,
) -> dict:
    result = _build_default_result()

    existing_case_items = [dict(item) for item in (existing_cases or []) if isinstance(item, dict)]
    result["coverage_before"] = audit_core_flow_coverage(existing_case_items)

    plan_items = [item for item in (backfill_plan.get("backfill_plan") or []) if isinstance(item, dict)]
    if max_candidates > 0:
        plan_items = plan_items[: int(max_candidates)]

    if not plan_items:
        result["merged_preview_cases"] = list(existing_case_items)
        result["coverage_after_candidates"] = audit_core_flow_coverage([])
        result["coverage_after_merged_preview"] = audit_core_flow_coverage(result["merged_preview_cases"])
        result["still_missing_core_flows"] = list(
            (result["coverage_after_merged_preview"] or {}).get("missing_core_flows") or []
        )
        _persist_generation_dry_run_log(db=db, backfill_plan=backfill_plan, result=result)
        return result

    client = _resolve_llm_client(llm_client=llm_client, db=db, backfill_plan=backfill_plan or {})
    prompt = _build_prompt(
        requirement_context=requirement_context,
        existing_cases=existing_case_items,
        backfill_items=plan_items,
        max_candidates=max_candidates,
    )

    raw_response = ""
    parse_error = False
    try:
        token_limit = int(getattr(client, "max_tokens", 2000) or 2000)
        token_limit = min(max(1200, token_limit), 6000)
        raw_response = str(
            client.generate_response(
                str(requirement_context or ""),
                prompt,
                db=db,
                task_type="generation",
                max_tokens=token_limit,
            )
            or ""
        )
        parsed_candidates = _safe_json_array(raw_response)
        if parsed_candidates is None:
            parse_error = True
            parsed_candidates = []
    except Exception as exc:
        parse_error = True
        parsed_candidates = []
        raw_response = f"{raw_response}\n{str(exc)}".strip()
        result["generation_errors"].append({"reason": "exception", "message": str(exc)})

    if parse_error:
        result["generation_errors"].append({"reason": "invalid_json"})

    flow_name_map = {
        str(item.get("flow_key") or ""): str(item.get("flow_name") or "")
        for item in plan_items
    }

    generated_cases = _normalize_backfill_candidate_cases(parsed_candidates, flow_name_map)

    result["generated_backfill_candidate_cases"] = generated_cases
    result["raw_backfill_response"] = raw_response

    priority_resolved_cases = _apply_priority_semantics(generated_cases)

    acceptance_result = _accept_backfill_candidate_cases(priority_resolved_cases, existing_case_items)
    accepted_cases = acceptance_result["accepted_cases"]
    rejected_cases = acceptance_result["rejected_cases"]

    result["accepted_backfill_cases"] = accepted_cases
    result["rejected_backfill_cases"] = rejected_cases

    preview_min_total = max(1, int(preview_min_total or 12))
    preview_max_total = max(preview_min_total, int(preview_max_total or 18))
    required_flow_keys = [str(item.get("flow_key") or "").strip() for item in plan_items if str(item.get("flow_key") or "").strip()]
    selection_result = select_merged_preview_cases(
        existing_cases=existing_case_items,
        accepted_backfill_cases=accepted_cases,
        required_flow_keys=required_flow_keys,
        max_cases=preview_max_total,
        min_cases=preview_min_total,
    )
    accepted_for_preview_count = int(selection_result.get("accepted_for_preview_count") or 0)
    merged_preview_cases = [dict(item) for item in (selection_result.get("merged_preview_cases") or []) if isinstance(item, dict)]
    result["merged_preview_cases"] = merged_preview_cases

    coverage_after_candidates = audit_core_flow_coverage(accepted_cases)
    coverage_after_preview = audit_core_flow_coverage(merged_preview_cases)
    result["coverage_after_candidates"] = coverage_after_candidates
    result["coverage_after_merged_preview"] = coverage_after_preview

    before_covered = {
        str(flow_id)
        for flow_id, detail in (result["coverage_before"].get("coverage_detail") or {}).items()
        if isinstance(detail, dict) and bool(detail.get("covered"))
    }
    after_covered = {
        str(flow_id)
        for flow_id, detail in (coverage_after_preview.get("coverage_detail") or {}).items()
        if isinstance(detail, dict) and bool(detail.get("covered"))
    }
    newly_covered = sorted(after_covered - before_covered)
    result["newly_covered_flows"] = newly_covered
    result["still_missing_core_flows"] = list(coverage_after_preview.get("missing_core_flows") or [])

    rejected_reason_counter = Counter(str(item.get("rejection_reason") or "unknown") for item in rejected_cases)
    priority_counter = Counter(
        str(item.get("priority_final") or "").strip().upper() if str(item.get("priority_final") or "").strip().upper() in {"P0", "P1", "P2"} else "null"
        for item in accepted_cases
    )
    quality_counter = Counter(str(item.get("expected_result_quality") or "").strip().lower() or "unknown" for item in generated_cases)

    result["accepted_for_preview_count"] = accepted_for_preview_count
    result["primary_retained_count"] = int(selection_result.get("primary_retained_count") or 0)
    result["primary_trimmed_count"] = int(selection_result.get("primary_trimmed_count") or 0)
    result["backfill_retained_count"] = int(selection_result.get("backfill_retained_count") or 0)
    result["backfill_trimmed_count"] = int(selection_result.get("backfill_trimmed_count") or 0)
    result["coverage_first_selection_applied"] = bool(selection_result.get("coverage_first_selection_applied"))
    result["trimmed_primary_case_ids"] = list(selection_result.get("trimmed_primary_case_ids") or [])
    result["retained_backfill_case_ids"] = list(selection_result.get("retained_backfill_case_ids") or [])
    result["dropped_backfill_due_to_limit_case_ids"] = list(selection_result.get("dropped_backfill_due_to_limit_case_ids") or [])

    result["quality_metrics"] = {
        "generated_backfill_candidate_count": int(len(generated_cases)),
        "accepted_backfill_candidate_count": int(len(accepted_cases)),
        "rejected_backfill_candidate_count": int(len(rejected_cases)),
        "accepted_for_preview_count": accepted_for_preview_count,
        "primary_retained_count": int(selection_result.get("primary_retained_count") or 0),
        "primary_trimmed_count": int(selection_result.get("primary_trimmed_count") or 0),
        "backfill_retained_count": int(selection_result.get("backfill_retained_count") or 0),
        "backfill_trimmed_count": int(selection_result.get("backfill_trimmed_count") or 0),
        "coverage_first_selection_applied": bool(selection_result.get("coverage_first_selection_applied")),
        "trimmed_primary_case_ids": list(selection_result.get("trimmed_primary_case_ids") or []),
        "retained_backfill_case_ids": list(selection_result.get("retained_backfill_case_ids") or []),
        "dropped_backfill_due_to_limit_case_ids": list(selection_result.get("dropped_backfill_due_to_limit_case_ids") or []),
        "merged_preview_target_range": {
            "min": int(preview_min_total),
            "max": int(preview_max_total),
            "actual": int(len(merged_preview_cases)),
        },
        "priority_final_breakdown": dict(priority_counter),
        "expected_result_quality_breakdown": dict(quality_counter),
        "rejection_reasons": dict(rejected_reason_counter),
    }

    _persist_generation_dry_run_log(db=db, backfill_plan=backfill_plan, result=result)
    return result


def _apply_priority_semantics(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return apply_priority_semantics_to_cases([dict(item) for item in cases if isinstance(item, dict)], attach_debug=False)


def summarize_case_quality_gate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return _summarize_case_quality_gate(cases)


__all__ = [
    "generate_core_flow_backfill_candidates",
    "select_merged_preview_cases",
    "summarize_case_quality_gate",
]
