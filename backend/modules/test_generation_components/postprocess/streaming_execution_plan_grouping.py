from __future__ import annotations

from typing import Any, Callable

from ..control.actor_roles import (
    normalize_actor_role as normalize_actor_role_value,
    session_key_for_role as session_key_for_actor_role,
)
from .case_access import case_flat_text, case_priority
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
    return normalize_actor_role_value(item.get("role"), fallback_text=execution_case_text(item))


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


def main_chain_state_overrides_for_current_generation(
    selected_by_stage: list[tuple[str, str, dict[str, Any]]],
    *,
    stage_meta_by_key: dict[str, dict[str, Any]] | None,
    signature_fn: Callable[[dict[str, Any]], str],
) -> dict[str, tuple[str, str]]:
    stage_meta = dict(stage_meta_by_key or {})
    overrides: dict[str, tuple[str, str]] = {}
    previous_state = ""
    for index, (stage_key, _stage_label, item) in enumerate(selected_by_stage, start=1):
        signature = signature_fn(item)
        if not signature:
            continue
        step_meta = stage_meta.get(stage_key) or {}
        source_state = str(step_meta.get("state_in") or "").strip()
        target_state = str(step_meta.get("state_out") or "").strip()
        if previous_state:
            source_state = previous_state
        elif not source_state:
            source_state = "initial"
        if not target_state or target_state == source_state:
            target_state = f"derived_selected_state_{index:03d}"
        overrides[signature] = (source_state, target_state)
        previous_state = target_state
    return overrides


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
    fixture_key = "default_logged_in_student"
    fixture_builder = "login_student()"
    cleanup_policy = "reset_session"
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


def is_student_observation_projection(item: dict[str, Any]) -> bool:
    text = execution_case_text(item)
    approval_or_publish = contains_any_token(
        text,
        (
            "review approved",
            "approval passed",
            "published",
            "visible in community",
            "审核通过",
            "已发布",
            "发布",
        ),
    )
    student_surface = contains_any_token(
        text,
        (
            "community",
            "visible",
            "student",
            "作文圈",
            "我的作文",
            "列表可见",
            "可见",
            "同步",
        ),
    )
    return bool(approval_or_publish and student_surface)


LOW_VALUE_MAIN_CHAIN_P0_TOKENS = (
    "remains pending",
    "pending status remains",
    "pending status",
    "48 hours",
    "48h",
    "record limit",
    "records limit",
    "maximum records",
    "drag sort",
    "drag sorted",
    "delete thumbnail",
    "force close",
    "kill app",
    "保持审核中",
    "审核中保持",
    "状态保持",
    "48小时",
)

MAIN_CHAIN_CLOSURE_TOKENS = (
    "submit succeeds",
    "submit success",
    "submitted successfully",
    "generate correction result",
    "correction result is generated",
    "feedback modules",
    "four modules",
    "result details",
    "approval passed",
    "review approved",
    "approved work",
    "first lesson",
    "all courses",
    "locked",
    "paywall",
    "提交成功",
    "生成批改结果",
    "批改结果",
    "四个模块",
    "审核通过",
    "第一课",
    "全部课程",
)

CORE_RESULT_COMPLETE_OUTPUT_TOKENS = (
    "feedback modules",
    "four modules",
    "result details",
    "correction result is generated",
    "generate correction result",
    "四个模块",
    "四部分",
    "结果详情",
    "批改结果页展示",
)

CORE_RESULT_DETAIL_ONLY_TOKENS = (
    "star rating",
    "stars",
    "button disabled",
    "disabled button",
    "0 images",
    "editable title",
    "title body",
    "星星评分",
    "评分展示",
    "按钮不可点",
    "置灰",
    "0张",
    "标题正文",
    "可编辑",
)

CORE_RESULT_OUTPUT_ANCHOR_TOKENS = (
    "feedback modules",
    "four modules",
    "result details",
    "correction result is generated",
    "generate correction result",
)


def is_low_value_main_chain_p0(item: dict[str, Any]) -> bool:
    text = execution_case_text(item)
    low_value_status = contains_any_token(text, LOW_VALUE_MAIN_CHAIN_P0_TOKENS)
    blocking_closure = contains_any_token(text, MAIN_CHAIN_CLOSURE_TOKENS)
    return bool(low_value_status and not blocking_closure)


def is_core_result_output_anchor(item: dict[str, Any]) -> bool:
    text = execution_case_text(item)
    complete_result_output = contains_any_token(text, CORE_RESULT_COMPLETE_OUTPUT_TOKENS)
    detail_only_output = contains_any_token(text, CORE_RESULT_DETAIL_ONLY_TOKENS)
    if detail_only_output and not complete_result_output:
        return False
    return contains_any_token(text, CORE_RESULT_OUTPUT_ANCHOR_TOKENS) or complete_result_output


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
    if contains_any_token(
        text,
        (
            "权限",
            "无权限",
            "越权",
            "授权失败",
            "鉴权",
            "未登录",
            "permission",
            "unauthorized",
            "forbidden",
            "access denied",
            "auth failed",
        ),
    ):
        return "permission"
    if contains_any_token(
        text,
        ("失败", "异常", "超时", "网络", "拒绝", "不通过", "接口", "重试", "failure", "error", "timeout", "retry"),
    ):
        return "exception"
    if contains_any_token(
        text,
        ("空状态", "最多", "最少", "上限", "下限", "格式", "大小", "边界", "无数据", "max", "min", "limit", "boundary"),
    ):
        return "boundary"
    if contains_any_token(
        text,
        ("下载", "入口", "弹窗", "展示", "排序", "筛选", "列表", "详情", "display", "list", "detail", "filter"),
    ):
        return "display"
    return "independent_functional"


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
