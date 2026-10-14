from __future__ import annotations

from copy import deepcopy
from typing import Any, TYPE_CHECKING

from .output_repair import OutputValidationError
from .test_generation_facts import bound_fact_ids, required_text, test_input_text, text_identity
from .test_generation_repair import (
    GENERATION_OUTPUT_REPAIR,
    GENERATION_REPAIR_STRATEGY,
    PLANNING_OUTPUT_REPAIR,
    PLANNING_REPAIR_STRATEGY,
    REVIEW_OUTPUT_REPAIR,
    REVIEW_REPAIR_STRATEGY,
)
from .test_generation_batching import (
    postprocess_generation_batch_item,
    prepare_execution_chain_context,
    select_execution_chain,
    validate_execution_chain,
)
from .test_generation_semantics import (
    merge_source_semantics,
    postprocess_authority_reconciliation_item,
    postprocess_source_semantics_item,
    prepare_source_semantics,
)
from .test_generation_review import (
    postprocess_final_review_batch_item,
    postprocess_final_review_repair_item,
    postprocess_global_final_review_output,
)

from .test_generation_planning import (
    submit_source_semantics,
    submit_generation_batch,
    submit_scenario_design_guidance,
    submit_business_plan,
    resolve_requirement_evidence,
    validate_business_plan_output,
    prepare_business_plan_batches,
    validate_business_plan_draft_output,
    postprocess_planning_scope_routing_item,
    postprocess_planning_route_repair_item,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


def validate_scenario_design_guidance(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验场景拆分只引用当前批次的真实事实和测试设计项。"""

    input_payload = dict(arguments.get("input_payload") or {})
    output = arguments.get("output")
    if not isinstance(output, dict):
        raise OutputValidationError("场景拆分专业 Agent 输出必须是对象")
    case_budget = int(input_payload.get("case_budget") or 0)
    recommended_case_count = int(output.get("recommended_case_count") or 0)
    scenario_groups = [
        dict(item)
        for item in list(output.get("scenario_groups") or [])
        if isinstance(item, dict)
    ]
    if case_budget < 1:
        raise OutputValidationError(
            "场景拆分输入缺少有效的用例额度，请重新传入原始批次的 case_budget",
            details={"invalid_input_field": "case_budget", "case_budget": case_budget},
        )
    if recommended_case_count > case_budget:
        raise OutputValidationError(
            "场景拆分建议数量超过当前批次用例额度: "
            f"recommended={recommended_case_count}, case_budget={case_budget}",
            details={"recommended_case_count": recommended_case_count, "case_budget": case_budget},
        )
    if len(scenario_groups) > case_budget:
        raise OutputValidationError(
            "场景拆分分组数量超过当前批次用例额度: "
            f"groups={len(scenario_groups)}, case_budget={case_budget}",
            details={"scenario_group_count": len(scenario_groups), "case_budget": case_budget},
        )

    allowed_fact_ids = {
        str(fact.get("fact_id") or "").strip()
        for fact in list(input_payload.get("authoritative_facts") or [])
        if isinstance(fact, dict) and str(fact.get("fact_id") or "").strip()
    }
    plan = dict(input_payload.get("plan") or {})
    allowed_design_item_ids = {
        str(item.get("test_design_item_id") or "").strip()
        for item in list(plan.get("test_design_items") or [])
        if isinstance(item, dict) and str(item.get("test_design_item_id") or "").strip()
    }
    scenario_keys: set[str] = set()
    for group in scenario_groups:
        scenario_key = str(group.get("scenario_key") or "").strip()
        if scenario_key in scenario_keys:
            raise OutputValidationError(
                f"场景拆分编号重复: {scenario_key}", details={"duplicate_scenario_key": scenario_key},
            )
        scenario_keys.add(scenario_key)
        referenced_fact_ids = {
            str(fact_id)
            for field in (
                "precondition_fact_ids",
                "action_fact_ids",
                "expected_fact_ids",
            )
            for fact_id in list(group.get(field) or [])
        }
        unknown_fact_ids = sorted(referenced_fact_ids - allowed_fact_ids)
        if unknown_fact_ids:
            raise OutputValidationError(
                "场景拆分引用了当前批次之外的事实: "
                + ", ".join(unknown_fact_ids),
                details={"scenario_key": scenario_key, "unknown_fact_ids": unknown_fact_ids},
            )
        design_item_ids = {
            str(item_id) for item_id in list(group.get("test_design_item_ids") or [])
        }
        unknown_design_item_ids = sorted(design_item_ids - allowed_design_item_ids)
        if unknown_design_item_ids:
            raise OutputValidationError(
                "场景拆分引用了当前批次之外的测试设计项: "
                + ", ".join(unknown_design_item_ids),
                details={"scenario_key": scenario_key, "unknown_test_design_item_ids": unknown_design_item_ids},
            )
    return dict(output)


def build_generation_audit_summary(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """用确定性事实绑定检查代替重新输出全部用例的合成模型。"""

    authoritative_facts = [dict(item) for item in list(arguments.get("authoritative_facts") or [])]
    generation = dict(arguments.get("generation") or {})
    generation_inputs = list(arguments.get("generation_inputs") or [])
    case_budget = int(arguments.get("case_budget") or 0)
    test_cases = [dict(item) for item in list(generation.get("test_cases") or [])]
    bindings = [dict(item) for item in list(generation.get("case_fact_bindings") or [])]
    effective_fact_ids = {
        required_text(fact.get("fact_id"), "fact_id")
        for fact in authoritative_facts
        if str(fact.get("status") or "") == "effective"
    }
    covered_fact_ids = set().union(*(bound_fact_ids(binding) for binding in bindings))
    invalid_fact_ids = sorted(covered_fact_ids - effective_fact_ids)
    uncovered_fact_ids = sorted(effective_fact_ids - covered_fact_ids)
    case_ids = [str(item.get("case_id") or "") for item in test_cases]
    duplicate_case_ids = sorted(
        {case_id for case_id in case_ids if case_id and case_ids.count(case_id) > 1}
    )
    expected_design_item_ids = {
        str(design_item_id)
        for raw_input in generation_inputs
        if isinstance(raw_input, dict)
        for design_item_id in list(
            dict(raw_input.get("case_fact_contract") or {}).get(
                "required_test_design_item_ids"
            )
            or []
        )
    }
    covered_design_item_ids = {
        str(design_item_id)
        for test_case in test_cases
        for design_item_id in list(test_case.get("test_design_item_ids") or [])
    }
    uncovered_design_item_ids = sorted(
        expected_design_item_ids - covered_design_item_ids
    )
    invalid_design_item_ids = sorted(
        covered_design_item_ids - expected_design_item_ids
    )
    differences: list[str] = []
    if len(test_cases) != case_budget:
        differences.append(f"用例数量不符: target={case_budget}, actual={len(test_cases)}")
    if len(bindings) != len(test_cases):
        differences.append("用例与事实绑定数量不一致")
    if uncovered_fact_ids:
        differences.append(f"存在未覆盖事实: {uncovered_fact_ids[:20]}")
    if invalid_fact_ids:
        differences.append(f"存在无效事实引用: {invalid_fact_ids[:20]}")
    if duplicate_case_ids:
        differences.append(f"存在重复 case_id: {duplicate_case_ids[:20]}")
    if uncovered_design_item_ids:
        differences.append(f"存在未覆盖测试设计项: {uncovered_design_item_ids[:20]}")
    if invalid_design_item_ids:
        differences.append(f"存在无效测试设计项引用: {invalid_design_item_ids[:20]}")
    approved = not differences
    result = {
        "approved": approved,
        "case_count": len(test_cases),
        "effective_fact_count": len(effective_fact_ids),
        "covered_fact_count": len(covered_fact_ids & effective_fact_ids),
        "uncovered_fact_ids": uncovered_fact_ids,
        "invalid_fact_ids": invalid_fact_ids,
        "duplicate_case_ids": duplicate_case_ids,
        "test_design_item_count": len(expected_design_item_ids),
        "covered_test_design_item_count": len(
            covered_design_item_ids & expected_design_item_ids
        ),
        "uncovered_test_design_item_ids": uncovered_design_item_ids,
        "invalid_test_design_item_ids": invalid_design_item_ids,
        "summary": (
            f"确定性审计{'通过' if approved else '未通过'}：{len(test_cases)} 条用例，"
            f"覆盖 {len(covered_fact_ids & effective_fact_ids)}/{len(effective_fact_ids)} 个有效事实，"
            f"覆盖 {len(covered_design_item_ids & expected_design_item_ids)}/"
            f"{len(expected_design_item_ids)} 个测试设计项。"
        ),
        "differences": differences,
    }
    context.artifacts["test_generation_audit"] = result
    return result


def approve_synthesized_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """只允许通过主智能体终审的合成用例进入确定性校验。"""

    generation = dict(arguments.get("generation") or {})
    audit_summary = dict(arguments.get("audit_summary") or {})
    final_review = dict(arguments.get("final_review") or {})
    case_budget = int(arguments.get("case_budget") or 0)
    if final_review.get("phase") != "final_review":
        raise ValueError("主智能体终审阶段标识无效")
    if audit_summary.get("approved") is not True:
        raise ValueError(
            "确定性用例审计未通过: "
            + "；".join(str(item) for item in list(audit_summary.get("differences") or [])[:5])
        )
    if final_review.get("approved") is not True:
        differences = [str(item) for item in list(final_review.get("differences") or [])]
        detail = "；".join(differences[:5]) or str(final_review.get("summary") or "未通过")
        raise ValueError(f"主智能体终审未通过: {detail}")
    test_cases = list(generation.get("test_cases") or [])
    case_fact_bindings = list(generation.get("case_fact_bindings") or [])
    if case_budget < 1 or len(test_cases) != case_budget:
        raise ValueError(
            f"生成结果数量与任务目标不一致: target={case_budget}, actual={len(test_cases)}"
        )
    if len(case_fact_bindings) != len(test_cases):
        raise ValueError("生成结果的用例与事实绑定数量不一致")
    context.artifacts["test_generation_final_review"] = final_review
    return {
        "test_cases": test_cases,
        "case_fact_bindings": case_fact_bindings,
        "final_review": final_review,
    }


def validate_generated_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """执行与模型无关的用例契约、数量和重复性校验。"""

    required_text(arguments.get("requirement"), "真实需求")
    case_budget = int(arguments.get("case_budget") or 0)
    raw_cases = arguments.get("test_cases")
    if case_budget < 1:
        raise ValueError("用例预算必须大于 0")
    if not isinstance(raw_cases, list):
        raise ValueError("test_cases 必须是数组")
    if not raw_cases:
        raise ValueError("事实对齐后没有可用测试用例")
    if len(raw_cases) != case_budget:
        raise ValueError(
            f"测试用例数量未达到精确目标: target={case_budget}, actual={len(raw_cases)}"
        )

    normalized_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cases: set[tuple[str, str]] = set()
    priority_counts = {"P0": 0, "P1": 0, "P2": 0}
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"第 {index} 条用例不是对象")
        case_id = required_text(raw_case.get("case_id"), f"第 {index} 条 case_id")
        title = required_text(raw_case.get("title"), f"第 {index} 条 title")
        module = required_text(raw_case.get("module"), f"第 {index} 条 module")
        test_input = test_input_text(raw_case.get("test_input"))
        priority = str(raw_case.get("priority") or "").strip().upper()
        if priority not in priority_counts:
            raise ValueError(f"第 {index} 条 priority 只能是 P0、P1 或 P2")

        case_identity = (text_identity(module), text_identity(title))
        if case_id in seen_ids:
            raise ValueError(f"用例编号重复: {case_id}")
        if case_identity in seen_cases:
            raise ValueError(f"用例语义重复: {module}/{title}")
        seen_ids.add(case_id)
        seen_cases.add(case_identity)

        raw_steps = raw_case.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"第 {index} 条用例至少需要一个测试步骤")
        steps: list[dict[str, str]] = []
        for step_index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise ValueError(f"第 {index} 条用例的第 {step_index} 个步骤不是对象")
            steps.append(
                {
                    "action": required_text(
                        raw_step.get("action"),
                        f"第 {index} 条用例的第 {step_index} 个操作",
                    ),
                    "expected": required_text(
                        raw_step.get("expected"),
                        f"第 {index} 条用例的第 {step_index} 个预期",
                    ),
                }
            )

        preconditions = raw_case.get("preconditions")
        tags = raw_case.get("tags")
        test_design_item_ids = raw_case.get("test_design_item_ids")
        if (
            not isinstance(preconditions, list)
            or not isinstance(tags, list)
            or not isinstance(test_design_item_ids, list)
        ):
            raise ValueError(
                f"第 {index} 条用例的 preconditions、tags 和 test_design_item_ids 必须是数组"
            )
        normalized_cases.append(
            {
                "case_id": case_id,
                "title": title,
                "module": module,
                "priority": priority,
                "preconditions": [
                    required_text(item, f"第 {index} 条用例前置条件")
                    for item in preconditions
                ],
                "test_input": test_input,
                "steps": steps,
                "tags": [
                    required_text(item, f"第 {index} 条用例标签")
                    for item in tags
                ],
                "test_design_item_ids": list(
                    dict.fromkeys(
                        required_text(item, f"第 {index} 条用例测试设计项")
                        for item in test_design_item_ids
                    )
                ),
            }
        )
        priority_counts[priority] += 1

    return {
        "status": "passed",
        "validated_count": len(normalized_cases),
        "priority_counts": priority_counts,
        "test_cases": normalized_cases,
    }


def persist_generated_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把校验通过的测试用例固化为 Agent Run 产物。"""

    requirement = required_text(arguments.get("requirement"), "真实需求")
    cases = arguments.get("test_cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("没有可持久化的测试用例")
    case_fact_bindings = arguments.get("case_fact_bindings")
    if not isinstance(case_fact_bindings, list) or len(case_fact_bindings) != len(cases):
        raise ValueError("持久化用例与事实绑定数量不一致")
    execution_plan = dict(arguments.get("execution_plan") or {})
    if not execution_plan:
        raise ValueError("没有通过校验的执行主链")
    final_review = dict(arguments.get("final_review") or {})
    if final_review.get("phase") != "final_review":
        raise ValueError("持久化前缺少独立终审结果")
    if final_review.get("approved") is not True:
        raise ValueError("独立终审未通过，禁止持久化测试用例")
    artifact = {
        "project_id": context.project_id,
        "run_id": context.run_id,
        "requirement": requirement,
        "evidence": {
            "source": dict(arguments.get("evidence_source") or {}),
            # 将压缩决策与最终用例一起落盘，便于后续历史 RAG 和评测追溯。
            "context_compression": deepcopy(
                context.artifacts.get("context_compression") or {}
            ),
        },
        "case_count": len(cases),
        "target_count": int(context.run_input.get("case_budget") or len(cases)),
        "target_met": len(cases) == int(context.run_input.get("case_budget") or len(cases)),
        "test_cases": cases,
        "case_fact_bindings": case_fact_bindings,
        "execution_plan": execution_plan,
        "final_review": final_review,
    }
    context.artifacts["test_generation"] = artifact
    return {
        "status": "completed",
        "summary": f"已持久化 {len(cases)} 条测试用例",
        "persisted_artifact_key": "test_generation",
        "final_review": final_review,
    }


def register_test_generation_tools(registry: ToolRegistry) -> None:
    from . import test_generation_agent as generation
    from .test_generation_escalation import report_generation_scope_problem

    for name in (
        "read_planning_evidence", "reinspect_planning_source", "revise_planning_draft",
        "rebuild_business_plan", "route_planning_scopes", "review_planning_route_gaps",
        "accept_generation_plan", "reconcile_generation_authority", "generate_cases",
        "review_generated_cases", "revise_generation_layout", "inspect_generation_scope",
        "read_review_evidence", "repair_review_batch", "recheck_review_batch", "review_all_cases", "finish_generation",
    ):
        registry.register(f"testing.generation.{name}", getattr(generation, name), state_only=True)
    for handler in (generation.initialize_generation_state, generation.observe_generation_state,
                    generation.validate_generation_resolution):
        registry.register(f"testing.{handler.__name__}", handler)
    registry.register("testing.report_generation_scope_problem", report_generation_scope_problem, parallel_safe=True)
    registry.register_repair_strategy(GENERATION_REPAIR_STRATEGY, GENERATION_OUTPUT_REPAIR)
    registry.register_repair_strategy(PLANNING_REPAIR_STRATEGY, PLANNING_OUTPUT_REPAIR)
    registry.register_repair_strategy(REVIEW_REPAIR_STRATEGY, REVIEW_OUTPUT_REPAIR)
    registry.register(
        "testing.submit_business_plan",
        submit_business_plan,
        parallel_safe=True,
    )
    registry.register(
        "testing.submit_source_semantics",
        submit_source_semantics,
        parallel_safe=True,
    )
    registry.register(
        "testing.submit_generation_batch",
        submit_generation_batch,
        parallel_safe=True,
    )
    registry.register(
        "testing.submit_scenario_design_guidance",
        submit_scenario_design_guidance,
        parallel_safe=True,
    )
    registry.register("testing.resolve_requirement_evidence", resolve_requirement_evidence)
    registry.register(
        "testing.validate_business_plan_output",
        validate_business_plan_output,
    )
    registry.register(
        "testing.validate_business_plan_draft_output",
        validate_business_plan_draft_output,
    )
    registry.register(
        "testing.validate_scenario_design_guidance",
        validate_scenario_design_guidance,
    )
    registry.register("testing.prepare_source_semantics", prepare_source_semantics)
    registry.register(
        "testing.postprocess_source_semantics_item",
        postprocess_source_semantics_item,
    )
    registry.register(
        "testing.postprocess_authority_reconciliation_item",
        postprocess_authority_reconciliation_item,
    )
    registry.register("testing.merge_source_semantics", merge_source_semantics)
    registry.register(
        "testing.prepare_business_plan_batches",
        prepare_business_plan_batches,
    )
    registry.register(
        "testing.postprocess_planning_scope_routing_item",
        postprocess_planning_scope_routing_item,
    )
    registry.register(
        "testing.postprocess_planning_route_repair_item",
        postprocess_planning_route_repair_item,
    )
    registry.register(
        "testing.postprocess_generation_batch_item",
        postprocess_generation_batch_item,
    )
    registry.register(
        "testing.postprocess_final_review_batch_item",
        postprocess_final_review_batch_item,
    )
    registry.register(
        "testing.postprocess_final_review_repair_item",
        postprocess_final_review_repair_item,
    )
    registry.register(
        "testing.postprocess_global_final_review_output",
        postprocess_global_final_review_output,
    )
    registry.register(
        "testing.approve_synthesized_test_cases",
        approve_synthesized_test_cases,
    )
    registry.register("testing.validate_test_cases", validate_generated_test_cases)
    registry.register("testing.prepare_execution_chain", prepare_execution_chain_context)
    registry.register("testing.select_execution_chain", select_execution_chain)
    registry.register("testing.validate_execution_chain", validate_execution_chain)
    registry.register("testing.persist_test_cases", persist_generated_test_cases)
