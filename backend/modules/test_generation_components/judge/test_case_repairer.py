from __future__ import annotations

from copy import deepcopy
from typing import Any

from .judge_types import JudgeBatchResult, JudgeResult, JudgeStatus, RepairActionType


def _build_core_flow_patch_case(ctx: dict[str, Any]) -> dict[str, Any]:
    _ = ctx
    return {
        "id": "AUTO-FLOW-001",
        "description": "验证完整学习路径闭环：学课文 -> 词组消消乐 -> 选词填空 -> 返回首页",
        "test_module": "学习路径-核心工作流",
        "preconditions": ["用户已进入课文学习详情页"],
        "steps": [
            "完成学课文阶段并点击下一步",
            "进入词组消消乐并完成当前任务",
            "点击下一步进入选词填空",
            "完成选词填空并执行完成动作",
        ],
        "test_input": "正常完整学习路径",
        "expected_result": "流程按学课文→词组消消乐→选词填空顺序流转，完成后返回首页。",
        "priority": "P1",
    }


def _build_reuse_risk_patch_case(ctx: dict[str, Any]) -> dict[str, Any]:
    _ = ctx
    return {
        "id": "AUTO-REUSE-001",
        "description": "验证复用模块接入后不会串原模块逻辑或错误返回到旧链路",
        "test_module": "复用模块适配风险",
        "preconditions": ["用户已进入复用后的学习路径页面"],
        "steps": [
            "进入词组消消乐页面",
            "观察页面标题、按钮文案及返回路径",
            "完成当前步骤并继续下一步",
            "检查是否返回当前学习链路而非旧模块链路",
        ],
        "test_input": "正常进入复用页面",
        "expected_result": "页面名称、按钮行为、完成后跳转均符合当前学课文链路，不残留旧模块逻辑。",
        "priority": "P1",
    }


def repair_case(
    judge_result: JudgeResult,
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
    strategy: str = "rule_first_llm_fallback",
) -> JudgeResult:
    _ = strategy
    _ = control_state
    _ = requirement_semantics_context

    result = deepcopy(judge_result)
    if result.status != JudgeStatus.REPAIRABLE:
        return result

    for action in result.suggested_actions:
        if action.action_type == RepairActionType.APPEND_CORE_FLOW_CASE:
            result.after_case = _build_core_flow_patch_case(action.payload)
            result.repaired = True
            result.repaired_pass = True
            continue
        if action.action_type == RepairActionType.APPEND_REUSE_RISK_CASE:
            result.after_case = _build_reuse_risk_patch_case(action.payload)
            result.repaired = True
            result.repaired_pass = True
            continue
        if action.action_type in {RepairActionType.ISOLATE_PENDING, RepairActionType.DROP_CASE}:
            result.repaired = False
            result.repaired_pass = False

    return result


def repair_cases(
    judged: JudgeBatchResult,
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
    strategy: str = "rule_first_llm_fallback",
) -> JudgeBatchResult:
    repaired_cases: list[JudgeResult] = []
    for item in judged.cases:
        repaired_cases.append(
            repair_case(
                judge_result=item,
                requirement_semantics_context=requirement_semantics_context,
                control_state=control_state,
                strategy=strategy,
            )
        )

    appended_case_count = sum(
        1
        for item in repaired_cases
        if item.status == JudgeStatus.REPAIRABLE and item.repaired_pass and isinstance(item.after_case, dict) and item.after_case
    )
    repaired_case_count = sum(1 for item in repaired_cases if bool(item.repaired))

    return JudgeBatchResult(
        cases=repaired_cases,
        core_flow_covered=judged.core_flow_covered,
        reuse_risk_covered=judged.reuse_risk_covered,
        pass_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.PASS),
        repairable_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.REPAIRABLE),
        reject_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.REJECT),
        pending_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.PENDING),
        appended_case_count=int(appended_case_count),
        repaired_case_count=int(repaired_case_count),
        notes=list(judged.notes or []),
    )
