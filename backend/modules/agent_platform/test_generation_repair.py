from __future__ import annotations

from typing import Any

from .output_repair import OutputRepairStrategy


GENERATION_REPAIR_STRATEGY = "testing.generation_repair"
PLANNING_REPAIR_STRATEGY = "testing.planning_route_repair"
REVIEW_REPAIR_STRATEGY = "testing.final_review_repair"


def generation_repair_targets(
    item_input: dict[str, Any], candidate: dict[str, Any], details: dict[str, Any],
) -> dict[str, Any]:
    """用结构化覆盖差异定位允许修改的槽位，保留其他用例的完整内容。"""

    contract = item_input.get("case_fact_contract")
    cases = candidate.get("test_cases")
    if not isinstance(contract, dict) or not isinstance(cases, list):
        return {}
    slots = contract.get("coverage_slots")
    if not isinstance(slots, list) or len(slots) != len(cases):
        return {}
    missing_facts = set(details.get("missing_fact_ids") or [])
    missing_designs = set(details.get("missing_test_design_item_ids") or [])
    invalid_designs = set(details.get("invalid_test_design_item_ids") or [])
    changed_case_ids = set(details.get("case_ids") or [])
    if not (missing_facts or missing_designs or invalid_designs or changed_case_ids):
        return {}
    if any(not isinstance(case, dict) for case in cases):
        return {}
    targets = []
    protected_ids = []
    protected_indexes = []
    for index, slot in enumerate(slots):
        case_id = slot["case_id"]
        fact_ids = [value for value in slot["required_fact_ids"] if value in missing_facts]
        design_ids = [value for value in slot["required_test_design_item_ids"] if value in missing_designs]
        invalid_case_designs = invalid_designs.intersection(
            cases[index].get("test_design_item_ids") or []
        )
        if fact_ids or design_ids or invalid_case_designs or case_id in changed_case_ids:
            targets.append({
                "case_id": case_id,
                "test_cases_array_index": index,
                "missing_fact_ids": fact_ids,
                "missing_test_design_item_ids": design_ids,
            })
        else:
            protected_ids.append(case_id)
            protected_indexes.append(index)
    if not targets or not protected_indexes:
        return {}
    return {
        "repair_targets": targets,
        "protected_case_ids": protected_ids,
        "protected_collections": [{"field": "test_cases", "protected_indexes": protected_indexes}],
        "instruction": (
            "repair_targets 已按事实覆盖契约定位到允许修改的用例槽位。"
            "只能修改这些槽位；protected_case_ids 对应的用例必须逐字段保持不变。"
        ),
    }


def planning_repair_targets(
    item_input: dict[str, Any], candidate: dict[str, Any], details: dict[str, Any],
) -> dict[str, Any]:
    """规划校验直接提交出错 scope，保护规则按编号恢复而不依赖数组顺序。"""

    scopes = item_input.get("scopes")
    routes = candidate.get("routes")
    if not isinstance(scopes, list) or not isinstance(routes, list):
        return {}
    target_ids = set(details.get("scope_ids") or [])
    known_ids = {scope["scope_id"] for scope in scopes}
    candidate_ids = [route.get("scope_id") for route in routes if isinstance(route, dict)]
    if set(candidate_ids) != known_ids or len(candidate_ids) != len(known_ids):
        return {}
    if not target_ids or not target_ids < known_ids:
        return {}
    protected_ids = [scope["scope_id"] for scope in scopes if scope["scope_id"] not in target_ids]
    return {
        "route_repair_targets": [{"scope_id": scope["scope_id"]} for scope in scopes if scope["scope_id"] in target_ids],
        "protected_scope_ids": protected_ids,
        "protected_collections": [{"field": "routes", "identity_key": "scope_id", "protected_ids": protected_ids}],
        "instruction": (
            "route_repair_targets 已按路由校验差异定位到允许修改的 scope。"
            "只能修正这些 scope 的 assignments；protected_scope_ids 对应的 scope 必须保持不变。"
        ),
    }


def generation_repair_feedback(item_input: dict[str, Any], details: dict[str, Any]) -> str | None:
    """将已知业务差异与原始事实、设计意图关联，供模型执行修复。"""

    instructions = []
    requirements = [str(value).strip() for value in item_input.get("repair_requirements") or [] if str(value).strip()]
    if requirements:
        instructions.append("本次输出必须落实以下终审要求：" + "；".join(requirements))
    missing_design_ids = set(details.get("missing_test_design_item_ids") or [])
    design_items = list(dict(item_input.get("plan") or {}).get("test_design_items") or item_input.get("test_design_items") or [])
    missing_designs = [item for item in design_items if item.get("test_design_item_id") in missing_design_ids]
    if missing_designs:
        design_details = "；".join(f"{item['test_design_item_id']}={str(item.get('coverage_intent') or '').strip()}" for item in missing_designs)
        instructions.append("本次修复必须在语义匹配的用例中实际落实并引用以下测试设计项，不能只移动或删除其他已覆盖编号：" + design_details)
    missing_fact_ids = set(details.get("missing_fact_ids") or [])
    missing_facts = [fact for fact in item_input.get("authoritative_facts") or [] if fact.get("fact_id") in missing_fact_ids]
    if missing_facts:
        fact_details = "；".join(f"{fact['fact_id']}={str(fact.get('assertion') or '').strip()}" for fact in missing_facts)
        instructions.append(
            "本次修复必须覆盖且不能删除以下有效事实；如果需要改写原承载字段，"
            "必须把事实迁移到目标用例集合中语义匹配的用例、前置条件或步骤，并保留事实绑定：" + fact_details
        )
    return "\n".join(instructions) or None


def no_repair_targets(
    item_input: dict[str, Any], candidate: dict[str, Any], details: dict[str, Any],
) -> dict[str, Any]:
    return {}


def planning_repair_feedback(item_input: dict[str, Any], details: dict[str, Any]) -> str | None:
    scope_ids = details.get("scope_ids") or []
    return "本次必须修正的规划范围：" + "、".join(scope_ids) if scope_ids else None


GENERATION_OUTPUT_REPAIR = OutputRepairStrategy(generation_repair_targets, generation_repair_feedback)
PLANNING_OUTPUT_REPAIR = OutputRepairStrategy(planning_repair_targets, planning_repair_feedback)
REVIEW_OUTPUT_REPAIR = OutputRepairStrategy(no_repair_targets, generation_repair_feedback)
