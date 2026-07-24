from __future__ import annotations

from typing import Any, Callable

from ..control.actor_roles import (
    normalize_actor_role as normalize_actor_role_value,
    session_key_for_role as session_key_for_actor_role,
)
from .case_access import case_flat_text, case_priority
from .priority_behavior_semantics import (
    has_generic_blocking_outcome,
    has_generic_non_blocking_behavior,
    has_generic_output_outcome,
    has_structured_blocking_priority_evidence,
    has_structured_core_signal,
    infer_non_main_execution_group,
    is_structured_non_blocking_detail,
    is_structured_output_anchor,
)
from .streaming_execution_plan_stage_inference import contains_any_token
from .streaming_postprocess_utils import _clip_text


def execution_case_text(item: dict[str, Any]) -> str:
    return case_flat_text(
        item,
        ("test_module", "description", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )


def priority_rank(item: dict[str, Any]) -> int:
    priority = case_priority(item)
    return {"P0": 30, "P1": 15, "P2": 0}.get(priority, 0)


def infer_role(item: dict[str, Any]) -> str:
    return normalize_actor_role_value(item.get("role"))


def session_key_for_role(role: str) -> str:
    return session_key_for_actor_role(role)


def empty_execution_plan_summary() -> dict[str, Any]:
    return {
        "applied": False,
        "linear_executable": False,
        "main_chain_case_count": 0,
        "independent_case_count": 0,
        "isolation_case_count": 0,
        "role_switch_count": 0,
        "broken_dependency_count": 0,
        "state_conflict_count": 0,
    }


def infer_data_state(
    item: dict[str, Any],
    *,
    stage_output_state: dict[str, str] | None = None,
    stage_key: str = "",
) -> str:
    state_map = dict(stage_output_state or {})
    if stage_key in state_map:
        return str(state_map[stage_key])
    text = execution_case_text(item)
    if contains_any_token(text, ("失败", "异常", "错误", "超时", "failure", "failed", "error", "timeout")):
        return "failed"
    if contains_any_token(text, ("待处理", "处理中", "待审核", "审核中", "pending", "processing")):
        return "pending"
    if contains_any_token(text, ("完成", "成功", "已生成", "已保存", "生效", "completed", "success", "saved")):
        return "completed"
    if contains_any_token(text, ("变更", "更新", "同步", "流转", "changed", "updated", "synced", "transition")):
        return "changed"
    if contains_any_token(text, ("空状态", "无数据", "暂无")):
        return "empty"
    return "prepared"


def fixture_for_case(item: dict[str, Any], group: str, data_state: str) -> dict[str, str]:
    text = execution_case_text(item)
    if group == "main_smoke":
        fixture_key = "workflow_blueprint_chain_seed"
        fixture_builder = "seed_workflow_blueprint_dataset()"
        cleanup_policy = "cleanup_workflow_blueprint_dataset"
    elif group == "permission":
        if contains_any_token(text, ("麦克风", "语音", "录音", "浏览器权限", "授权")):
            fixture_key = "browser_permission_state"
            fixture_builder = "set_browser_permission(permission='microphone', state='prompt')"
            cleanup_policy = "reset_browser_permissions"
        else:
            fixture_key = "permission_state_dataset"
            fixture_builder = "seed_permission_state_dataset()"
            cleanup_policy = "reset_permission_state_dataset"
    elif group == "exception":
        fixture_key = "fault_injection_case"
        fixture_builder = "enable_fault_injection_for_case()"
        cleanup_policy = "disable_fault_injection"
    elif group == "boundary":
        if data_state == "empty":
            fixture_key = "empty_state_dataset"
            fixture_builder = "seed_empty_state()"
            cleanup_policy = "restore_default_dataset"
        else:
            fixture_key = "boundary_dataset"
            fixture_builder = "seed_boundary_dataset()"
            cleanup_policy = "delete_boundary_dataset"
    elif group == "display":
        fixture_key = "display_ready_dataset"
        fixture_builder = "seed_display_ready_dataset()"
        cleanup_policy = "delete_display_ready_dataset"
    else:
        fixture_key = f"{group}_dataset"
        fixture_builder = f"seed_{group}_dataset()"
        cleanup_policy = f"cleanup_{group}_dataset"
    return {
        "fixture_key": fixture_key,
        "fixture_builder": fixture_builder,
        "cleanup_policy": cleanup_policy,
    }


def is_low_value_main_chain_p0(item: dict[str, Any]) -> bool:
    text = execution_case_text(item)
    if (
        is_structured_output_anchor(item)
        or has_structured_blocking_priority_evidence(item)
        or has_generic_blocking_outcome(text)
    ):
        return False
    if has_structured_core_signal(item) and not is_structured_non_blocking_detail(item):
        return False
    return bool(
        is_structured_non_blocking_detail(item)
        or has_generic_non_blocking_behavior(text)
    )


def is_core_result_output_anchor(item: dict[str, Any]) -> bool:
    text = execution_case_text(item)
    if is_structured_output_anchor(item):
        return True
    if is_structured_non_blocking_detail(item):
        return False
    return has_generic_output_outcome(text)


def default_group_setup_map() -> dict[str, str]:
    return {
        "main_smoke": "seed_workflow_blueprint_dataset()",
        "permission": "seed_permission_state_dataset()",
        "exception": "enable_fault_injection_for_case()",
        "boundary": "seed_boundary_dataset()",
        "independent_functional": "seed_functional_dataset()",
        "display": "seed_display_ready_dataset()",
    }


def default_group_teardown_map() -> dict[str, str]:
    return {
        "main_smoke": "cleanup_workflow_blueprint_dataset()",
        "permission": "reset_permission_state_dataset()",
        "exception": "disable_fault_injection()",
        "boundary": "delete_boundary_dataset()",
        "independent_functional": "cleanup_functional_dataset()",
        "display": "delete_display_ready_dataset()",
    }


def infer_group(item: dict[str, Any], *, in_main_chain: bool) -> str:
    if in_main_chain:
        return "main_smoke"
    text = execution_case_text(item)
    return infer_non_main_execution_group(item, text)


def setup_hint(
    item: dict[str, Any],
    *,
    in_main_chain: bool,
    previous_id: str = "",
    previous_result: str = "",
) -> str:
    if in_main_chain and previous_id:
        return f"依赖 {previous_id} 的执行结果：{_clip_text(previous_result, 120)}"
    preconditions = item.get("preconditions")
    if isinstance(preconditions, list):
        joined = "；".join(str(x).strip() for x in preconditions if str(x).strip())
        if joined:
            return f"独立准备：{_clip_text(joined, 160)}"
    return "独立准备：按本用例前置条件准备账号、数据和页面状态"
