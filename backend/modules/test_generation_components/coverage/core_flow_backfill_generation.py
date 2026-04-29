from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import Any

from core.ai.ai_client import ai_client, get_client_for_user
from core.db.models import LogEntry
from modules.test_generation_components.coverage.core_flow_coverage_contract import (
    CORE_FLOWS,
    audit_core_flow_coverage,
    map_case_to_core_flows,
)
from modules.test_generation_components.postprocess.result_postprocess_priority_semantics import (
    apply_priority_semantics_to_cases,
)

_NON_ASSERTABLE_EXPECTED_PHRASES = (
    "对应状态变化",
    "关键结果可核对",
    "对应内容",
    "匹配的结果",
    "结果内容可校验",
    "正常展示",
    "正常跳转",
    "正常更新",
    "符合预期",
)

_TRUNCATED_TEXT_ENDINGS = (
    "或显",
    "对应内",
    "可校",
    "正常展",
    "跳转至",
    "显示为",
)

_REQUIRED_FIELDS = (
    "description",
    "test_module",
    "steps",
    "expected_result",
    "source_flow_key",
)

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("case_id") or case.get("id") or "").strip()


def _matched_flows(case: dict[str, Any]) -> set[str]:
    mapper_hits = case.get("mapper_hits")
    if isinstance(mapper_hits, dict):
        return {str(key) for key in mapper_hits.keys() if str(key).strip()}
    return set()


def _coverage_priority_sort_key(case: dict[str, Any], flow_order: dict[str, int]) -> tuple[int, int, str]:
    source_flow = str(case.get("source_flow_key") or "")
    priority = str(case.get("priority_final") or case.get("priority") or "P2").strip().upper()
    return (
        int(flow_order.get(source_flow, 9999)),
        int(_PRIORITY_RANK.get(priority, 9)),
        _case_id(case),
    )


def _normalize_steps(steps: Any) -> list[str]:
    if not isinstance(steps, list):
        return []
    output: list[str] = []
    for raw in steps:
        text = str(raw or "").strip()
        if not text:
            continue
        text = re.sub(r"^\s*(?:step\s*)?\d+\s*[\.\):、-]*\s*", "", text, flags=re.IGNORECASE)
        text = text.strip()
        if text:
            output.append(text)
    return [f"{idx}. {item}" for idx, item in enumerate(output, start=1)]


def _normalize_preconditions(value: Any, module: str) -> list[str]:
    if not isinstance(value, list):
        return [f"已登录并可访问模块：{module or '目标模块'}"]
    items = [str(item).strip() for item in value if str(item).strip()]
    if items:
        return items
    return [f"已登录并可访问模块：{module or '目标模块'}"]


def _looks_truncated_text(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    trimmed = re.sub(r"[。！？?!?]+$", "", normalized).strip()
    if not trimmed:
        return False
    return any(trimmed.endswith(suffix) for suffix in _TRUNCATED_TEXT_ENDINGS)


def _is_non_assertable_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    return any(phrase in normalized for phrase in _NON_ASSERTABLE_EXPECTED_PHRASES)


def _case_exact_signature(case: dict[str, Any]) -> str:
    steps = "\n".join(_normalize_steps(case.get("steps")))
    payload = {
        "description": str(case.get("description") or "").strip(),
        "test_module": str(case.get("test_module") or "").strip(),
        "steps": steps,
        "test_input": str(case.get("test_input") or "").strip(),
        "expected_result": str(case.get("expected_result") or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _safe_json_array(raw_text: str) -> list[dict[str, Any]] | None:
    text = str(raw_text or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    candidates: list[str] = [text]
    left = text.find("[")
    right = text.rfind("]")
    if left >= 0 and right > left:
        candidates.append(text[left : right + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            try:
                parsed = ast.literal_eval(candidate)
            except Exception:
                continue
        if isinstance(parsed, list):
            if all(isinstance(item, dict) for item in parsed):
                return [item for item in parsed if isinstance(item, dict)]
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("cases"), list):
            case_rows = parsed.get("cases") or []
            if all(isinstance(item, dict) for item in case_rows):
                return [item for item in case_rows if isinstance(item, dict)]
            continue
    return None


def _normalize_case_structure_light(case: dict[str, Any], index: int) -> dict[str, Any] | None:
    normalized = dict(case or {})
    description = str(normalized.get("description") or "").strip()
    module = str(normalized.get("test_module") or "").strip()
    steps = _normalize_steps(normalized.get("steps"))
    expected_result = str(normalized.get("expected_result") or "").strip()

    if not description or not module or not steps:
        return None

    source_flow_key = str(normalized.get("source_flow_key") or "").strip()
    source_flow_name = str(normalized.get("source_flow_name") or "").strip()

    model_priority = str(normalized.get("model_priority") or normalized.get("priority") or "").strip().upper()
    if model_priority not in {"P0", "P1", "P2"}:
        model_priority = str(normalized.get("suggested_priority") or "").strip().upper()
    if model_priority not in {"P0", "P1", "P2"}:
        model_priority = "P2"

    normalized_case_id = f"BF-{index:03d}"
    normalized["case_id"] = normalized_case_id
    normalized["id"] = normalized_case_id
    normalized["description"] = description
    normalized["test_module"] = module
    normalized["steps"] = steps
    normalized["preconditions"] = _normalize_preconditions(normalized.get("preconditions"), module)
    normalized["test_input"] = str(normalized.get("test_input") or "").strip() or description[:80]
    normalized["expected_result"] = expected_result
    normalized["priority"] = model_priority
    normalized["model_priority"] = model_priority
    normalized["source_flow_key"] = source_flow_key
    normalized["source_flow_name"] = source_flow_name
    normalized["backfill_generated"] = True

    if _looks_truncated_text(expected_result):
        normalized["expected_result_quality"] = "truncated"
        normalized["expected_result_quality_reason"] = "truncated_suffix_detected"
        normalized["truncated_text_detected"] = True
    elif _is_non_assertable_expected_result(expected_result):
        normalized["expected_result_quality"] = "non_assertable"
        normalized["expected_result_quality_reason"] = "template_or_weak_assertion"
        normalized["truncated_text_detected"] = False
    else:
        normalized["expected_result_quality"] = "assertable"
        normalized["expected_result_quality_reason"] = "contains_concrete_assertion"
        normalized["truncated_text_detected"] = False

    return normalized


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
                "id": str(case.get("id") or case.get("case_id") or ""),
                "description": str(case.get("description") or "")[:120],
                "test_module": str(case.get("test_module") or "")[:80],
                "expected_result": str(case.get("expected_result") or "")[:160],
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


def _priority_and_flow_sort_key(case: dict[str, Any], flow_order: dict[str, int]) -> tuple[int, int, str]:
    flow_key = str(case.get("source_flow_key") or "")
    flow_idx = int(flow_order.get(flow_key, 9999))
    priority = str(case.get("priority_final") or case.get("priority") or "P2").strip().upper()
    priority_rank = int(_PRIORITY_RANK.get(priority, 9))
    case_id = str(case.get("case_id") or case.get("id") or "")
    return (flow_idx, priority_rank, case_id)


def select_merged_preview_cases(
    existing_cases: list[dict[str, Any]],
    accepted_backfill_cases: list[dict[str, Any]],
    required_flow_keys: list[str],
    max_cases: int = 18,
    min_cases: int = 12,
) -> dict[str, Any]:
    """
    Coverage-first merged preview selection:
    1) Keep one representative accepted backfill per required flow (prefer source_flow_key exact + mapper hit).
    2) Add primary existing cases up to max_cases.
    3) Fill remaining capacity with extra accepted backfills.
    4) If over limit, trim primary before trimming coverage backfills.
    """
    max_cases = max(1, int(max_cases or 18))
    min_cases = max(1, int(min_cases or 12))
    existing_items = [dict(item) for item in (existing_cases or []) if isinstance(item, dict)]
    accepted_items = [dict(item) for item in (accepted_backfill_cases or []) if isinstance(item, dict)]
    flow_order = {str(flow_key): idx for idx, flow_key in enumerate(required_flow_keys or []) if str(flow_key).strip()}

    selected_backfill: list[dict[str, Any]] = []
    selected_backfill_ids: set[str] = set()

    def _add_backfill(case: dict[str, Any]) -> bool:
        cid = _case_id(case)
        if not cid or cid in selected_backfill_ids:
            return False
        selected_backfill.append(case)
        selected_backfill_ids.add(cid)
        return True

    # Stage 1A: strict pick by source_flow_key + matched_core_flows contains same flow
    for flow_key in required_flow_keys or []:
        flow_key = str(flow_key or "").strip()
        if not flow_key:
            continue
        exact = [
            case
            for case in accepted_items
            if str(case.get("source_flow_key") or "").strip() == flow_key and flow_key in _matched_flows(case)
        ]
        if exact:
            exact.sort(key=lambda case: _coverage_priority_sort_key(case, flow_order))
            _add_backfill(exact[0])
            continue
        # Stage 1B fallback: any accepted case whose mapper hit covers required flow
        fallback = [case for case in accepted_items if flow_key in _matched_flows(case)]
        if fallback:
            fallback.sort(key=lambda case: _coverage_priority_sort_key(case, flow_order))
            _add_backfill(fallback[0])

    # Stage 2: add primary existing cases first (coverage-first means required backfills already locked)
    remaining_capacity = max(0, max_cases - len(selected_backfill))
    retained_primary = existing_items[:remaining_capacity]
    remaining_capacity = max(0, remaining_capacity - len(retained_primary))

    # Stage 3: add other accepted backfills if room remains
    extra_backfills = [case for case in accepted_items if _case_id(case) not in selected_backfill_ids]
    extra_backfills.sort(key=lambda case: _coverage_priority_sort_key(case, flow_order))
    retained_extra_backfills = extra_backfills[:remaining_capacity]
    merged_backfills = list(selected_backfill) + list(retained_extra_backfills)

    merged_cases = merged_backfills + list(retained_primary)

    # Keep hard max bound
    merged_cases = merged_cases[:max_cases]
    merged_case_ids = {_case_id(case) for case in merged_cases}
    retained_backfill_case_ids = [_case_id(case) for case in merged_cases if _case_id(case).startswith("BF-")]

    primary_ids = [_case_id(case) for case in existing_items]
    retained_primary_case_ids = [cid for cid in primary_ids if cid in merged_case_ids]
    trimmed_primary_case_ids = [cid for cid in primary_ids if cid and cid not in merged_case_ids]

    accepted_backfill_ids = [_case_id(case) for case in accepted_items if _case_id(case)]
    dropped_backfill_due_to_limit_case_ids = [
        cid for cid in accepted_backfill_ids if cid not in set(retained_backfill_case_ids)
    ]

    # Ensure coverage backfills are not dropped when capacity allows
    return {
        "merged_preview_cases": merged_cases,
        "accepted_for_preview_count": int(len(retained_backfill_case_ids)),
        "primary_retained_count": int(len(retained_primary_case_ids)),
        "primary_trimmed_count": int(len(trimmed_primary_case_ids)),
        "backfill_retained_count": int(len(retained_backfill_case_ids)),
        "backfill_trimmed_count": int(len(dropped_backfill_due_to_limit_case_ids)),
        "coverage_first_selection_applied": True,
        "trimmed_primary_case_ids": trimmed_primary_case_ids,
        "retained_backfill_case_ids": retained_backfill_case_ids,
        "dropped_backfill_due_to_limit_case_ids": dropped_backfill_due_to_limit_case_ids,
        "selection_target_min": int(min_cases),
        "selection_target_max": int(max_cases),
    }


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


def summarize_case_quality_gate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    case_items = [item for item in (cases or []) if isinstance(item, dict)]
    priority_final_invalid_case_ids: list[str] = []
    non_assertable_case_ids: list[str] = []
    truncated_case_ids: list[str] = []
    priority_final_null_count = 0
    non_assertable_expected_result_count = 0
    truncated_text_count = 0

    for index, case_item in enumerate(case_items, start=1):
        case_id = _case_id(case_item) or f"ROW-{int(index):03d}"
        priority_final_value = str(case_item.get("priority_final") or "").strip().upper()
        if priority_final_value not in {"P0", "P1", "P2"}:
            priority_final_null_count += 1
            priority_final_invalid_case_ids.append(case_id)

        expected_result_text = str(case_item.get("expected_result") or "").strip()
        expected_result_quality = str(case_item.get("expected_result_quality") or "").strip().lower()
        quality_reason = str(case_item.get("expected_result_quality_reason") or "").strip().lower()
        truncated_flag = bool(case_item.get("truncated_text_detected"))

        phrase_hit = any(phrase in expected_result_text for phrase in _NON_ASSERTABLE_EXPECTED_PHRASES)
        non_assertable_hit = bool(
            expected_result_quality == "non_assertable"
            or quality_reason in {"no_concrete_assertion", "template_or_weak_assertion"}
            or phrase_hit
        )
        if non_assertable_hit:
            non_assertable_expected_result_count += 1
            non_assertable_case_ids.append(case_id)

        expected_result_trimmed = expected_result_text.rstrip("。！？?!? ")
        truncated_suffix_hit = any(expected_result_trimmed.endswith(suffix) for suffix in _TRUNCATED_TEXT_ENDINGS)
        truncated_hit = bool(
            expected_result_quality == "truncated"
            or truncated_flag
            or truncated_suffix_hit
        )
        if truncated_hit:
            truncated_text_count += 1
            truncated_case_ids.append(case_id)

    failed_checks: list[str] = []
    if priority_final_null_count > 0:
        failed_checks.append(f"priority_final_null_count={int(priority_final_null_count)}")
    if non_assertable_expected_result_count > 0:
        failed_checks.append(f"non_assertable_expected_result_count={int(non_assertable_expected_result_count)}")
    if truncated_text_count > 0:
        failed_checks.append(f"truncated_text_count={int(truncated_text_count)}")

    return {
        "passed": not bool(failed_checks),
        "failed_checks": failed_checks,
        "priority_final_null_count": int(priority_final_null_count),
        "invalid_priority_final_count": int(len(priority_final_invalid_case_ids)),
        "invalid_priority_final_case_ids": list(priority_final_invalid_case_ids),
        "non_assertable_expected_result_count": int(non_assertable_expected_result_count),
        "truncated_text_count": int(truncated_text_count),
        "non_assertable_case_ids": list(non_assertable_case_ids),
        "truncated_case_ids": list(truncated_case_ids),
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

    generated_cases: list[dict[str, Any]] = []
    for idx, raw_case in enumerate(parsed_candidates, start=1):
        normalized = _normalize_case_structure_light(raw_case, idx)
        if normalized is None:
            generated_cases.append(
                {
                    "case_id": f"BF-{idx:03d}",
                    "raw_case": dict(raw_case),
                    "source_flow_key": str(raw_case.get("source_flow_key") or ""),
                    "source_flow_name": str(raw_case.get("source_flow_name") or ""),
                    "backfill_generated": True,
                    "missing_required_fields": True,
                }
            )
            continue
        if not normalized.get("source_flow_name"):
            flow_key = str(normalized.get("source_flow_key") or "")
            if flow_key in flow_name_map:
                normalized["source_flow_name"] = flow_name_map[flow_key]
        generated_cases.append(normalized)

    result["generated_backfill_candidate_cases"] = generated_cases
    result["raw_backfill_response"] = raw_response

    priority_resolved_cases = _apply_priority_semantics(generated_cases)

    existing_signatures = {_case_exact_signature(item) for item in existing_case_items}

    accepted_cases: list[dict[str, Any]] = []
    rejected_cases: list[dict[str, Any]] = []

    for case in priority_resolved_cases:
        candidate = dict(case)
        source_flow_key = str(candidate.get("source_flow_key") or "").strip()

        missing_required = any(
            (key == "steps" and not isinstance(candidate.get("steps"), list))
            or (key != "steps" and not str(candidate.get(key) or "").strip())
            for key in _REQUIRED_FIELDS
        )
        if missing_required:
            candidate["rejection_reason"] = "missing_required_fields"
            rejected_cases.append(candidate)
            continue

        expected_result = str(candidate.get("expected_result") or "").strip()
        if not expected_result:
            candidate["rejection_reason"] = "missing_required_fields"
            rejected_cases.append(candidate)
            continue

        priority_final = str(candidate.get("priority_final") or "").strip().upper()
        if priority_final not in {"P0", "P1", "P2"}:
            candidate["rejection_reason"] = "invalid_priority_final"
            rejected_cases.append(candidate)
            continue

        if bool(candidate.get("truncated_text_detected")) or _looks_truncated_text(expected_result):
            candidate["rejection_reason"] = "truncated_expected_result"
            rejected_cases.append(candidate)
            continue

        expected_quality = str(candidate.get("expected_result_quality") or "").strip().lower()
        if expected_quality == "non_assertable" or _is_non_assertable_expected_result(expected_result):
            candidate["rejection_reason"] = "non_assertable_expected_result"
            rejected_cases.append(candidate)
            continue

        if any(phrase in expected_result for phrase in _NON_ASSERTABLE_EXPECTED_PHRASES):
            candidate["rejection_reason"] = "non_assertable_expected_result"
            rejected_cases.append(candidate)
            continue

        if _case_exact_signature(candidate) in existing_signatures:
            candidate["rejection_reason"] = "duplicate_with_existing_case"
            rejected_cases.append(candidate)
            continue

        mapper_hits = map_case_to_core_flows(candidate)
        candidate["mapper_hits"] = dict(mapper_hits)
        candidate["matched_core_flows"] = sorted(list(mapper_hits.keys()))
        if not mapper_hits:
            candidate["rejection_reason"] = "source_flow_not_matched_by_mapper"
            rejected_cases.append(candidate)
            continue

        accepted_cases.append(candidate)

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


__all__ = [
    "generate_core_flow_backfill_candidates",
    "select_merged_preview_cases",
    "summarize_case_quality_gate",
]
