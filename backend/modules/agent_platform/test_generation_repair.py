from __future__ import annotations

from typing import Any

from .output_repair import OutputRepairStrategy


GENERATION_REPAIR_STRATEGY = "testing.generation_repair"
PLANNING_REPAIR_STRATEGY = "testing.planning_route_repair"
REVIEW_REPAIR_STRATEGY = "testing.final_review_repair"


def generation_repair_targets(
    item_input: dict[str, Any], candidate: dict[str, Any], details: dict[str, Any],
) -> dict[str, Any]:
    """只定位候选真实绑定中的错误；全批缺口交由 Agent 按语义分配。"""

    contract = item_input.get("case_fact_contract")
    cases = candidate.get("test_cases")
    if not isinstance(contract, dict) or not isinstance(cases, list):
        return {}
    missing_facts = list(details.get("missing_fact_ids") or [])
    missing_designs = list(details.get("missing_test_design_item_ids") or [])
    invalid_facts = set(details.get("invalid_fact_ids") or [])
    if not (missing_facts or missing_designs or invalid_facts):
        return {}
    case_ids = contract.get("target_case_ids")
    available_facts = set(contract["available_fact_ids"])
    targets = []
    candidate_facts = {}
    # 编号只对应已经存在的候选数组，不给尚未出现的事实推测目标用例。
    if isinstance(case_ids, list) and len(case_ids) == len(cases) and all(isinstance(case, dict) for case in cases):
        for case_id, case in zip(case_ids, cases, strict=True):
            references = []
            for index, condition in enumerate(case.get("preconditions") or []):
                if isinstance(condition, dict):
                    references.append((f"preconditions[{index}]", condition.get("fact_ids")))
            if isinstance(case.get("test_input"), dict):
                references.append(("test_input", case["test_input"].get("fact_ids")))
            for index, step in enumerate(case.get("steps") or []):
                if isinstance(step, dict) and isinstance(step.get("fact_bindings"), dict):
                    references.extend((f"steps[{index}].{field}", step["fact_bindings"].get(field))
                                      for field in ("action", "expected"))
            bound = set()
            issues = []
            for path, raw_ids in references:
                if not isinstance(raw_ids, list) or any(not isinstance(value, str) for value in raw_ids):
                    continue
                bound.update(raw_ids)
                invalid = sorted(set(raw_ids) & invalid_facts)
                if invalid:
                    issues.append({"field_path": path, "invalid_fact_ids": invalid})
            candidate_facts[case_id] = sorted(bound & available_facts)
            if issues:
                targets.append({"case_id": case_id, "field_issues": issues})
    # 设计覆盖按完整支持集合验收；反馈必须展示同一集合的差额，不能只报设计编号。
    # 按每条真实候选分别计算，不把跨用例引用的并集误判成同一用例已完整承接。
    routes = contract["fact_design_item_ids"]
    design_support_gaps = []
    for design_id in missing_designs:
        support_ids = {fact_id for fact_id, design_ids in routes.items() if design_id in design_ids}
        design_support_gaps.append({
            "test_design_item_id": design_id,
            "support_fact_ids": sorted(support_ids),
            "missing_fact_ids_by_case": {
                case_id: sorted(support_ids - set(fact_ids))
                for case_id, fact_ids in candidate_facts.items()
            },
        })
    return {
        "repair_targets": targets,
        "batch_coverage_gaps": {"missing_fact_ids": missing_facts,
                                "missing_test_design_item_ids": missing_designs,
                                "design_support_gaps": design_support_gaps},
        "candidate_fact_ids_by_case": candidate_facts,
        "instruction": (
            "repair_targets 只指出候选真实绑定中已经定位的非法引用，不能当成事实分配计划。"
            "batch_coverage_gaps 是全批缺口，尚未指定承载用例；必须依据真实条件、角色和生命周期选择修正位置。"
            "design_support_gaps 列出未覆盖意图的完整支持集合及各候选用例缺少的引用；"
            "即使 missing_fact_ids 为空，同一条用例仍须语义连贯地承接该意图的全部支持事实。"
            "差额不是指定补绑位置，不能只挂编号；须核对对应业务内容，无法合理承接时报告实际范围问题。"
            "candidate_fact_ids_by_case 是当前候选已引用的本批事实，并非语义验收结果；"
            "修正时核对并保留其中正确的内容，不能用本轮缺失集合替换全部候选而制造新的遗漏。"
            "必须修正这些缺口，同时依据原始事实复核完整用例；"
            "其他内容发现错误也应修正，最终全部用例仍须通过完整校验。"
        ),
    }


def planning_repair_targets(
    item_input: dict[str, Any], candidate: dict[str, Any], details: dict[str, Any],
) -> dict[str, Any]:
    """用原始范围编号定位已知路由错误，不为其余范围授予可信状态。"""

    scopes = item_input.get("scopes")
    routes = candidate.get("routes")
    if not isinstance(scopes, list) or not isinstance(routes, list):
        return {}
    target_ids = set(details.get("scope_ids") or [])
    known_ids = {scope["scope_id"] for scope in scopes}
    candidate_ids = [route.get("scope_id") for route in routes if isinstance(route, dict)]
    if set(candidate_ids) != known_ids or len(candidate_ids) != len(known_ids):
        return {}
    if not target_ids or not target_ids <= known_ids:
        return {}
    return {
        "route_repair_targets": [{"scope_id": scope["scope_id"]} for scope in scopes if scope["scope_id"] in target_ids],
        "instruction": (
            "route_repair_targets 是当前已发现路由错误的 scope，并非候选中全部问题。"
            "必须修正这些 scope，并依据原始 facts 和 business_modules 复核所有范围；"
            "其他范围发现错误也应修正，最终完整路由仍须通过校验。"
        ),
    }


def generation_repair_feedback(item_input: dict[str, Any], details: dict[str, Any]) -> str | None:
    """将已知业务差异与原始事实、设计意图关联，供模型执行修复。"""

    instructions = []
    invalid_fact_ids = list(details.get("invalid_fact_ids") or [])
    if invalid_fact_ids:
        instructions.append(
            "以下编号不属于当前批次，不能继续引用或猜测相近编号：" + "、".join(invalid_fact_ids)
            + "。只能从当前 authoritative_facts 选择直接支持字段的事实；没有依据时改写业务内容。"
        )
    requirements = [str(value).strip() for value in item_input.get("repair_requirements") or [] if str(value).strip()]
    if requirements:
        instructions.append("本次输出必须落实以下终审要求：" + "；".join(requirements))
    missing_design_ids = set(details.get("missing_test_design_item_ids") or [])
    design_items = list(dict(item_input.get("plan") or {}).get("test_design_items") or item_input.get("test_design_items") or [])
    missing_designs = [item for item in design_items if item.get("test_design_item_id") in missing_design_ids]
    if missing_designs:
        design_details = "；".join(f"{item['test_design_item_id']}={str(item.get('coverage_intent') or '').strip()}" for item in missing_designs)
        instructions.append("本次修复必须在语义匹配的用例中落实以下测试设计覆盖意图，按输出契约保留事实引用，不能只移动或删除其他已覆盖编号：" + design_details)
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
