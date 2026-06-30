from __future__ import annotations

import re
from typing import Any, Callable, Collection

from ..control.actor_roles import (
    normalize_actor_role as normalize_actor_role_value,
    session_key_for_role as session_key_for_actor_role,
)
from .case_access import case_flat_text, case_priority, case_step_lines, case_text_field
from .streaming_postprocess_utils import _clip_text


def execution_case_text(item: dict[str, Any]) -> str:
    return case_flat_text(
        item,
        ("test_module", "description", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )


def contains_any_token(text: str, tokens: Collection[str]) -> bool:
    return any(token and token.lower() in text for token in tokens)


def token_hit(text: str, tokens: tuple[str, ...]) -> bool:
    haystack = str(text or "").strip().lower()
    if not haystack:
        return False
    for token in tokens:
        needle = str(token or "").strip().lower()
        if not needle:
            continue
        if needle.isascii() and re.search(r"[a-z0-9]", needle):
            if re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack):
                return True
            continue
        if needle in haystack:
            return True
    return False


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


def infer_workflow_stage_kind(text: str) -> str:
    lowered = str(text or "").lower()
    if token_hit(lowered, ("保存", "提交", "确认", "发布")):
        return "commit"
    if token_hit(lowered, ("保存", "提交", "确认", "发布", "save", "submit", "commit", "confirm", "publish")):
        return "commit"
    if token_hit(
        lowered,
        (
            "触发打分",
            "开始打分",
            "自动打分",
            "评分计算",
            "生成评分",
            "给出评分",
            "trigger score",
            "score calculation",
        ),
    ):
        return "commit"
    if token_hit(
        lowered,
        (
            "同步",
            "生效",
            "展示",
            "显示",
            "出现",
            "可见",
            "最新",
            "评分结果",
            "打分结果",
            "综合评分",
            "visible",
            "display",
            "displayed",
            "show",
            "shows",
            "shown",
            "score result",
            "scoring result",
        ),
    ):
        return "downstream_visibility"
    if token_hit(lowered, ("入口", "进入入口")):
        return "entry"
    if token_hit(
        lowered,
        (
            "点击",
            "跳转",
            "学习",
            "查看",
            "打开",
            "进入",
        ),
    ):
        return "consume"
    if token_hit(lowered, ("预览", "检查", "确认前")):
        return "preview"
    if token_hit(
        lowered,
        (
            "新增",
            "创建",
            "添加",
            "选择",
            "设置",
            "配置",
            "编辑",
            "修改",
        ),
    ):
        return "configure"
    if token_hit(lowered, ("进入", "访问", "打开")):
        return "entry"
    if token_hit(lowered, ("完成", "进度", "状态")):
        return "completion_sync"
    if token_hit(lowered, ("同步", "生效", "展示", "显示", "刷新", "最新", "sync", "display", "show", "visible", "effective", "latest", "reflect", "reflects", "reflected", "downstream")):
        return "downstream_visibility"
    if token_hit(lowered, ("入口", "工作流入口", "进入入口", "entry", "workflow entry")):
        return "entry"
    if token_hit(lowered, ("点击", "跳转", "学习", "查看", "打开", "click", "navigate", "learn", "view", "open")):
        return "consume"
    if token_hit(lowered, ("预览", "检查", "确认前", "preview", "review")):
        return "preview"
    if token_hit(lowered, ("新增", "创建", "添加", "选择", "设置", "配置", "编辑", "修改", "create", "add", "select", "set", "configure", "edit", "modify")):
        return "configure"
    if token_hit(lowered, ("进入", "访问", "打开", "enter", "access", "open")):
        return "entry"
    if token_hit(lowered, ("完成", "进度", "状态", "complete", "completion", "progress", "status")):
        return "completion_sync"
    return "unknown"


def infer_workflow_phase(text: str) -> int:
    lowered = str(text or "").lower()
    if token_hit(
        lowered,
        (
            "保存",
            "提交",
            "确认",
            "发布",
            "下架",
            "删除",
            "save",
            "submit",
            "commit",
            "confirm",
            "publish",
            "delete",
            "触发打分",
            "开始打分",
            "自动打分",
            "评分计算",
            "生成评分",
            "给出评分",
            "trigger score",
            "score calculation",
        ),
    ):
        return 60
    if token_hit(
        lowered,
        (
            "同步",
            "展示",
            "显示",
            "刷新",
            "生效",
            "评分结果",
            "打分结果",
            "综合评分",
            "sync",
            "display",
            "displayed",
            "show",
            "shows",
            "shown",
            "effective",
            "visible",
            "reflect",
            "reflects",
            "reflected",
            "downstream",
            "score result",
            "scoring result",
        ),
    ):
        return 70
    if contains_any_token(lowered, ("打开", "进入", "访问", "入口", "open", "enter", "entry")):
        return 10
    if contains_any_token(lowered, ("新增", "创建", "添加", "选择", "选课", "设置", "配置", "准备", "create", "add", "select", "set", "prepare", "prepared", "ready")):
        return 20
    if contains_any_token(lowered, ("编辑", "修改", "调整", "update", "edit", "modify")):
        return 30
    if contains_any_token(lowered, ("预览", "检查", "确认前", "preview", "review")):
        return 50
    if token_hit(
        lowered,
        (
            "保存",
            "提交",
            "确认",
            "发布",
            "下架",
            "删除",
            "save",
            "submit",
            "commit",
            "confirm",
            "publish",
            "delete",
            "触发打分",
            "开始打分",
            "自动打分",
            "评分计算",
            "生成评分",
            "给出评分",
            "trigger score",
            "score calculation",
        ),
    ):
        return 60
    if token_hit(lowered, ("同步", "展示", "显示", "刷新", "生效", "sync", "display", "displayed", "show", "shows", "shown", "effective", "visible", "reflect", "reflects", "reflected", "downstream")):
        return 70
    if contains_any_token(lowered, ("点击", "跳转", "学习", "查看", "click", "navigate", "learn", "view")):
        return 80
    return 90


def main_chain_closure_status(
    selected: list[tuple[str, str, dict[str, Any]]],
    *,
    stage_meta_by_key: dict[str, dict[str, Any]],
    source: str,
) -> tuple[bool, str, list[str]]:
    stage_kinds: list[str] = []
    for stage_key, stage_label, item in selected:
        meta = stage_meta_by_key.get(stage_key) or {}
        text = " ".join(
            [
                execution_case_text(item),
                str(stage_label or ""),
                str(meta.get("label") or ""),
                str(meta.get("action") or ""),
                str(meta.get("assertion") or ""),
                str(meta.get("state_out") or ""),
                str(meta.get("state_out") or "").replace("_", " "),
            ]
        )
        explicit_stage_kind = str(meta.get("stage_kind") or "").strip().lower()
        stage_kinds.append(explicit_stage_kind or infer_workflow_stage_kind(text))
    if len(stage_kinds) < 2:
        return False, "main_chain_too_short", stage_kinds
    has_commit = "commit" in stage_kinds
    first_commit_index = stage_kinds.index("commit") if has_commit else -1
    has_post_commit_downstream = bool(
        has_commit
        and any(
            kind in {"downstream_visibility", "consume", "completion_sync"}
            for kind in stage_kinds[first_commit_index + 1:]
        )
    )
    has_configure = any(kind in {"entry", "configure", "preview"} for kind in stage_kinds)
    has_pre_commit_consume = bool(
        first_commit_index > 0
        and any(kind == "consume" for kind in stage_kinds[:first_commit_index])
    )
    if not has_commit:
        return False, "missing_commit_success_step", stage_kinds
    if not has_post_commit_downstream:
        return False, "missing_downstream_visibility_or_consume_step", stage_kinds
    if source == "current_generation_cases" and not (has_configure or has_pre_commit_consume):
        return False, "missing_configure_or_entry_step", stage_kinds
    return True, "", stage_kinds


def selected_stage_state_conflicts(
    selected: list[tuple[str, str, dict[str, Any]]],
    *,
    stage_meta_by_key: dict[str, dict[str, Any]],
    case_id_fn: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    previous_stage_key = ""
    previous_case_id = ""
    previous_target_state = ""
    resolve_case_id = case_id_fn or (lambda item: str(item.get("id") or ""))
    for stage_key, _stage_label, item in selected:
        step_meta = stage_meta_by_key.get(stage_key) or {}
        source_state = str(step_meta.get("state_in") or "").strip()
        target_state = str(step_meta.get("state_out") or "").strip()
        case_id = resolve_case_id(item)
        if previous_target_state and source_state and previous_target_state != source_state:
            conflicts.append(
                {
                    "prev_stage_key": previous_stage_key,
                    "curr_stage_key": str(stage_key),
                    "prev_case_id": previous_case_id,
                    "curr_case_id": case_id,
                    "prev_target_state": previous_target_state,
                    "curr_source_state": source_state,
                    "reason": "state_not_connected",
                }
            )
        previous_stage_key = str(stage_key)
        previous_case_id = case_id
        previous_target_state = target_state
    return conflicts


class MainChainExclusionRecorder:
    def __init__(
        self,
        records: list[dict[str, str]],
        *,
        signature_fn: Callable[[dict[str, Any]], str],
        case_id_fn: Callable[[dict[str, Any]], str],
        description_fn: Callable[[dict[str, Any]], str],
    ) -> None:
        self._records = records
        self._signature_fn = signature_fn
        self._case_id_fn = case_id_fn
        self._description_fn = description_fn

    def __call__(self, item: dict[str, Any], reason: str, *, stage_key: str = "") -> None:
        if not reason:
            return
        signature = self._signature_fn(item)
        if any(
            entry.get("signature") == signature and entry.get("reason") == reason
            for entry in self._records
        ):
            return
        self._records.append(
            {
                "case_id": _clip_text(self._case_id_fn(item), 40),
                "description": _clip_text(self._description_fn(item), 160),
                "stage_key": _clip_text(stage_key, 80),
                "reason": str(reason),
                "signature": signature,
            }
        )


_DEFAULT_MAIN_CHAIN_EXCLUSION_TOKEN_TUPLES: dict[str, tuple[str, ...]] = {
    "analytics_tokens": (
        "埋点",
        "上报",
        "曝光",
        "停留时间",
        "pv",
        "uv",
        "tracking",
        "analytics",
        "event",
    ),
    "destructive_action_tokens": (
        "删除",
        "下架",
        "撤销",
        "作废",
        "取消发布",
        "delete",
        "remove",
        "unpublish",
        "archive",
        "deactivate",
    ),
    "blocking_negative_tokens": (
        "失败",
        "异常",
        "超时",
        "错误",
        "拒绝",
        "不通过",
        "不可点击",
        "不可操作",
        "置灰",
        "阻止",
        "无法",
        "不能",
        "不允许",
        "不进入",
        "不生成",
        "不保存",
        "failure",
        "failed",
        "timeout",
        "error",
        "invalid",
        "blocked",
        "cannot",
        "not allowed",
        "not saved",
    ),
    "boundary_capacity_tokens": (
        "边界",
        "上限",
        "下限",
        "最多",
        "最少",
        "容量不足",
        "学不完",
        "课程设置过少",
        "时间冲突",
        "冲突",
        "boundary",
        "limit",
        "capacity",
        "conflict",
        "too few",
        "too many",
    ),
    "display_only_tokens": (
        "文案",
        "样式",
        "布局",
        "标题",
        "排序",
        "筛选",
        "列表",
        "卡片",
        "弹窗",
        "copy",
        "style",
        "layout",
        "title",
        "sorting",
        "filter",
        "list",
        "card",
        "popup",
    ),
    "downstream_visibility_tokens": (
        "新增",
        "新计划",
        "同步",
        "生效",
        "最新",
        "进度更新",
        "状态同步",
        "new",
        "created",
        "sync",
        "synced",
        "visible",
        "effective",
        "latest",
        "updated",
    ),
}


def default_main_chain_exclusion_token_sets() -> dict[str, set[str]]:
    return {
        name: set(tokens)
        for name, tokens in _DEFAULT_MAIN_CHAIN_EXCLUSION_TOKEN_TUPLES.items()
    }


def workflow_transition_for_case(
    item: dict[str, Any],
    *,
    step_meta: dict[str, Any] | None = None,
    stage_label: str = "",
    workflow_blueprints_present: bool = False,
    destructive_action_tokens: Collection[str] = (),
    blocking_negative_tokens: Collection[str] = (),
    boundary_capacity_tokens: Collection[str] = (),
    analytics_tokens: Collection[str] = (),
) -> dict[str, Any]:
    meta = dict(step_meta or {})
    text = " ".join(
        [
            execution_case_text(item),
            str(stage_label or ""),
            str(meta.get("label") or ""),
            str(meta.get("action") or ""),
            str(meta.get("assertion") or ""),
            str(meta.get("state_out") or ""),
            str(meta.get("state_out") or "").replace("_", " "),
        ]
    )
    destructive = bool(contains_any_token(text, destructive_action_tokens))
    blocking = bool(
        contains_any_token(text, blocking_negative_tokens)
        or contains_any_token(text, boundary_capacity_tokens)
        or contains_any_token(text, analytics_tokens)
    )
    stage_kind = str(meta.get("stage_kind") or "").strip().lower() or infer_workflow_stage_kind(text)
    source_state = str(meta.get("state_in") or "").strip()
    target_state = str(meta.get("state_out") or "").strip()
    if not source_state:
        phase = infer_workflow_phase(text)
        source_state = {
            10: "entry_ready",
            20: "workflow_started",
            30: "workflow_edit_ready",
            50: "workflow_configured",
            60: "workflow_ready_to_commit",
            70: "committed",
            80: "downstream_visible",
        }.get(phase, "prepared")
    if not target_state:
        target_state = {
            "entry": "workflow_entered",
            "configure": "workflow_configured",
            "preview": "workflow_preview_ready",
            "commit": "workflow_committed",
            "downstream_visibility": "downstream_visible",
            "consume": "workflow_consumed",
            "completion_sync": "completion_synced",
        }.get(stage_kind, "prepared")
    path_type = "positive" if not (blocking or destructive) else "negative"
    can_advance = bool(path_type == "positive" and stage_kind != "unknown")
    workflow_id = str(meta.get("workflow_id") or meta.get("blueprint_id") or "").strip()
    transition_confidence = 0.9 if workflow_blueprints_present else 0.35
    return {
        "workflow_id": workflow_id,
        "source_state": source_state,
        "action": _clip_text(
            meta.get("action") or stage_label or item.get("description"),
            160,
            strip=True,
        ),
        "target_state": target_state,
        "path_type": path_type,
        "blocking": bool(blocking),
        "destructive": bool(destructive),
        "can_advance_main_flow": bool(can_advance),
        "state_transition_confidence": float(transition_confidence),
        "stage_kind": stage_kind,
    }


def main_chain_exclusion_reason(
    item: dict[str, Any],
    *,
    step_meta: dict[str, Any] | None = None,
    stage_label: str = "",
    workflow_blueprints_present: bool = False,
    analytics_tokens: Collection[str] = (),
    destructive_action_tokens: Collection[str] = (),
    boundary_capacity_tokens: Collection[str] = (),
    blocking_negative_tokens: Collection[str] = (),
    display_only_tokens: Collection[str] = (),
    downstream_visibility_tokens: Collection[str] = (),
    reasoning_leakage_fn: Callable[[dict[str, Any]], bool] | None = None,
    semantic_alignment_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    action_support_conflict_fn: Callable[[dict[str, Any]], str] | None = None,
) -> str:
    text = execution_case_text(item)
    if not text:
        return "empty_text"
    if reasoning_leakage_fn is not None and reasoning_leakage_fn(item):
        return "reasoning_leakage"
    if contains_any_token(text, analytics_tokens):
        return "analytics"
    if contains_any_token(text, destructive_action_tokens):
        return "destructive_action"
    if contains_any_token(text, boundary_capacity_tokens):
        return "boundary_capacity"
    if contains_any_token(text, blocking_negative_tokens):
        return "blocking_negative"
    if is_display_only_workflow_text(
        text,
        display_only_tokens=display_only_tokens,
        downstream_visibility_tokens=downstream_visibility_tokens,
    ):
        return "display_only"
    meta = dict(step_meta or {})
    transition = workflow_transition_for_case(
        item,
        step_meta=meta,
        stage_label=stage_label,
        workflow_blueprints_present=workflow_blueprints_present,
        destructive_action_tokens=destructive_action_tokens,
        blocking_negative_tokens=blocking_negative_tokens,
        boundary_capacity_tokens=boundary_capacity_tokens,
        analytics_tokens=analytics_tokens,
    )
    if not bool(transition.get("can_advance_main_flow")):
        return "non_advancing_transition"
    semantic_probe = dict(item)
    semantic_probe["execution_group"] = "main_smoke"
    semantic_probe["main_chain_stage_kind"] = str(transition.get("stage_kind") or "").strip()
    semantic_probe["main_chain_stage_label"] = str(meta.get("label") or stage_label or "").strip()
    semantic_probe["action"] = str(transition.get("action") or "").strip()
    if semantic_alignment_fn is not None:
        semantic_conflicts = semantic_alignment_fn([semantic_probe])
        if semantic_conflicts:
            return str(semantic_conflicts[0].get("reason") or "main_chain_semantic_conflict")
    if action_support_conflict_fn is not None:
        action_support_reason = action_support_conflict_fn(semantic_probe)
        if action_support_reason:
            return action_support_reason
    return ""


def is_display_only_workflow_text(
    text: str,
    *,
    display_only_tokens: Collection[str],
    downstream_visibility_tokens: Collection[str],
) -> bool:
    if not contains_any_token(text, display_only_tokens):
        return False
    if contains_any_token(text, downstream_visibility_tokens):
        return False
    workflow_action_tokens = (
        "新增",
        "创建",
        "添加",
        "选择",
        "设置",
        "预览",
        "保存",
        "提交",
        "确认",
        "跳转",
        "进入",
        "create",
        "add",
        "select",
        "set",
        "preview",
        "save",
        "submit",
        "confirm",
        "click",
        "open",
        "view",
        "learn",
        "navigate",
        "enter",
    )
    return not contains_any_token(text, workflow_action_tokens)


def is_internal_state_text(value: str) -> bool:
    return bool(re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\b", str(value or "").strip().lower()))


def public_contract_module_label(step_meta: dict[str, Any], label: str) -> str:
    for raw in (
        step_meta.get("module"),
        step_meta.get("domain"),
        step_meta.get("feature"),
        step_meta.get("blueprint_name"),
    ):
        value = str(raw or "").strip()
        if value and not is_internal_state_text(value):
            return _clip_text(value, 80)
    if contains_any_token(str(label or "").lower(), ("学生", "学员", "student")):
        return "学生端主链路"
    return "业务主链路"


CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX_BY_STAGE = {
    "entry": "完成，目标入口页面可执行后续操作",
    "configure": "完成，已选配置在页面中保留并可进入下一步",
    "preview": "完成，预览内容展示当前配置结果",
    "commit": "完成，保存结果展示成功状态",
    "downstream_visibility": "完成，下游页面展示最新业务结果",
    "consume": "完成，目标页面打开并展示可操作内容",
    "completion_sync": "完成，进度状态更新",
}
DEFAULT_CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX = (
    "完成，业务状态已更新并可继续执行下一步"
)


def contract_materialized_expected_result(label: str, stage_kind: str) -> str:
    stage = str(stage_kind or "").strip().lower()
    suffix = CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX_BY_STAGE.get(
        stage,
        DEFAULT_CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX,
    )
    return f"{label}{suffix}"


def materialize_workflow_contract_case(stage_key: str, step_meta: dict[str, Any]) -> dict[str, Any] | None:
    label = str(step_meta.get("label") or stage_key).strip()
    if not label or is_internal_state_text(label):
        return None
    action = str(step_meta.get("action") or label).strip()
    if not action or is_internal_state_text(action):
        action = label
    test_steps = step_meta.get("test_steps") if isinstance(step_meta.get("test_steps"), list) else []
    public_steps = [
        str(step).strip()
        for step in test_steps
        if str(step).strip() and not is_internal_state_text(str(step))
    ]
    if not public_steps:
        public_steps = [action]
    stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
    assertion = str(step_meta.get("assertion") or step_meta.get("expected_result") or "").strip()
    expected_result = (
        assertion
        if assertion and not is_internal_state_text(assertion)
        else contract_materialized_expected_result(label, stage_kind)
    )
    return {
        "id": f"TC-CONTRACT-{stage_key.upper().replace(':', '-').replace(' ', '-')[:40]}",
        "description": label,
        "test_module": public_contract_module_label(step_meta, label),
        "preconditions": [f"已具备执行“{label}”的前置业务状态"],
        "steps": public_steps,
        "test_input": action,
        "expected_result": expected_result,
        "priority": "P0" if bool(step_meta.get("main_path_step", True)) else "P1",
        "role": normalize_actor_role_value(step_meta.get("actor"), fallback_text=label),
        "workflow_contract_materialized_case": True,
    }


def workflow_bridge_case(
    stage_key: str,
    *,
    stage_meta_by_key: dict[str, dict[str, Any]],
    main_chain_stages: list[tuple[str, str, tuple[tuple[str, ...], ...]]],
    selected_stage_keys: set[str],
    available_stage_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    step_meta = stage_meta_by_key.get(stage_key) or {}
    if not step_meta or not bool(step_meta.get("allow_bridge")):
        return None
    stage_order = [key for key, _label, _patterns in main_chain_stages]
    try:
        stage_index = stage_order.index(stage_key)
    except ValueError:
        return None
    available = available_stage_keys if available_stage_keys is not None else selected_stage_keys
    if stage_index > 0 and stage_order[stage_index - 1] not in available:
        return None
    label = str(step_meta.get("label") or step_meta.get("action") or stage_key).strip()
    assertion = str(step_meta.get("assertion") or step_meta.get("expected_result") or step_meta.get("state_out") or "").strip()
    test_steps = step_meta.get("test_steps") if isinstance(step_meta.get("test_steps"), list) else []
    return {
        "id": f"TC-BRIDGE-{stage_key.upper().replace(':', '-').replace(' ', '-')[:40]}",
        "description": label or stage_key,
        "test_module": str(step_meta.get("module") or step_meta.get("blueprint_name") or "workflow_blueprint"),
        "preconditions": [str(step_meta.get("state_in") or "previous workflow state")],
        "steps": test_steps or [str(step_meta.get("action") or label or stage_key)],
        "test_input": str(step_meta.get("input") or step_meta.get("state_in") or "workflow state"),
        "expected_result": assertion or f"workflow state reaches {stage_key}",
        "priority": "P0" if bool(step_meta.get("main_path_step", True)) else "P1",
        "role": normalize_actor_role_value(step_meta.get("actor"), fallback_text=label),
        "generated_bridge_case": True,
        "workflow_blueprint_bridge": True,
    }


DERIVED_WORKFLOW_ACTION_TOKENS = (
    "新增",
    "创建",
    "添加",
    "选择",
    "设置",
    "编辑",
    "修改",
    "准备",
    "准备好",
    "预览",
    "保存",
    "提交",
    "提交成功",
    "提交后",
    "确认",
    "发布",
    "下架",
    "删除",
    "同步",
    "生效",
    "进入",
    "进入页面",
    "入口",
    "跳转",
    "点击",
    "学习",
    "查看",
    "打开",
    "create",
    "add",
    "select",
    "set",
    "edit",
    "preview",
    "save",
    "submit",
    "commit",
    "committed",
    "confirm",
    "sync",
    "navigate",
    "click",
    "view",
    "learn",
    "open",
    "entry",
    "prepare",
    "prepared",
    "reflect",
    "reflects",
    "downstream",
    "触发打分",
    "开始打分",
    "自动打分",
    "评分计算",
    "生成评分",
    "给出评分",
    "trigger score",
    "score calculation",
)

DERIVED_WORKFLOW_STATE_TOKENS = (
    "成功",
    "完成",
    "正确",
    "一致",
    "保存",
    "已保存",
    "保存成功",
    "加入",
    "回到",
    "跳转",
    "更新",
    "展示",
    "显示",
    "进入",
    "准备好",
    "准备完成",
    "生效",
    "已生效",
    "success",
    "completed",
    "successfully",
    "updated",
    "saved",
    "visible",
    "ready",
    "prepared",
    "reflected",
    "shown",
    "评分结果",
    "打分结果",
    "综合评分",
    "score result",
    "scoring result",
)

DERIVED_WORKFLOW_BOUNDARY_TOKENS = (
    "边界",
    "上限",
    "下限",
    "空状态",
    "无数据",
    "boundary",
    "limit",
    "empty",
)

DERIVED_WORKFLOW_DISPLAY_ONLY_PENALTY_TOKENS = (
    "按钮展示",
    "文案",
    "样式",
    "排序",
    "筛选",
    "列表",
    "display only",
)


def derived_workflow_candidate_buckets(
    cases_for_plan: list[dict[str, Any]],
    *,
    exclusion_reason_fn: Callable[[dict[str, Any]], str],
    record_exclusion_fn: Callable[[dict[str, Any], str], None],
) -> tuple[list[tuple[int, int, int, dict[str, Any]]], list[tuple[int, int, int, dict[str, Any]]]]:
    primary_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    fallback_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, item in enumerate(cases_for_plan):
        text = execution_case_text(item)
        exclusion_reason = exclusion_reason_fn(item)
        if exclusion_reason:
            record_exclusion_fn(item, exclusion_reason)
            continue
        action_score = sum(1 for token in DERIVED_WORKFLOW_ACTION_TOKENS if token.lower() in text)
        state_score = sum(1 for token in DERIVED_WORKFLOW_STATE_TOKENS if token.lower() in text)
        if action_score <= 0 or state_score <= 0:
            continue
        score = priority_rank(item) + action_score * 10 + state_score * 5
        if contains_any_token(text, DERIVED_WORKFLOW_BOUNDARY_TOKENS):
            score -= 20
        if contains_any_token(text, DERIVED_WORKFLOW_DISPLAY_ONLY_PENALTY_TOKENS):
            score -= 10
        if score < 15:
            continue
        bucket = primary_candidates if priority_rank(item) > 0 else fallback_candidates
        bucket.append((score, infer_workflow_phase(text), index, item))
    return primary_candidates, fallback_candidates


def select_derived_workflow_candidates(
    primary_candidates: list[tuple[int, int, int, dict[str, Any]]],
    fallback_candidates: list[tuple[int, int, int, dict[str, Any]]],
    *,
    limit: int = 10,
) -> list[tuple[int, int, int, dict[str, Any]]]:
    scored = list(primary_candidates if len(primary_candidates) >= 2 else fallback_candidates)
    if primary_candidates and fallback_candidates:
        scored.extend(fallback_candidates)
    if len(scored) < 2:
        return []
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    return sorted(scored[: max(1, int(limit or 0))], key=lambda row: (row[1], row[2]))


def derived_workflow_steps_from_selected(
    selected: list[tuple[int, int, int, dict[str, Any]]],
    *,
    case_id_fn: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], str]:
    steps: list[dict[str, Any]] = []
    previous_state = "initial"
    for step_index, (_score, _phase, _index, item) in enumerate(selected, start=1):
        description = (
            case_text_field(item, "description")
            or case_text_field(item, "test_module")
            or f"workflow step {step_index}"
        )
        module = case_text_field(item, "test_module")
        expected = case_text_field(item, "expected_result")
        step_texts = case_step_lines(item)
        first_step = next((step for step in step_texts if step), "")
        state_out = f"derived_state_{step_index:03d}"
        stage_kind = infer_workflow_stage_kind(execution_case_text(item))
        match_keywords = [
            _clip_text(value, 120)
            for value in (description, module, expected, first_step)
            if str(value or "").strip()
        ]
        steps.append(
            {
                "id": f"derived_step_{step_index:03d}",
                "label": _clip_text(description, 160),
                "module": _clip_text(module, 80),
                "actor": normalize_actor_role_value("", fallback_text=execution_case_text(item)),
                "action": _clip_text(description, 160),
                "state_in": previous_state,
                "state_out": state_out,
                "stage_kind": stage_kind,
                "assertion": expected[:240],
                "test_steps": step_texts,
                "match_keywords": list(dict.fromkeys(match_keywords))[:6],
                "source_case_id": case_id_fn(item),
                "main_path_step": True,
                "allow_bridge": False,
            }
        )
        previous_state = state_out
    return steps, previous_state


def derived_workflow_selected_for_closure(
    steps: list[dict[str, Any]],
    selected: list[tuple[int, int, int, dict[str, Any]]],
    *,
    case_id_fn: Callable[[dict[str, Any]], str],
) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (
            str(step.get("id") or ""),
            str(step.get("label") or ""),
            next(
                (
                    item
                    for _score, _phase, _index, item in selected
                    if case_id_fn(item) == str(step.get("source_case_id") or "")
                ),
                {},
            ),
        )
        for step in steps
    ]


def derive_workflow_blueprint_from_current_cases(
    cases_for_plan: list[dict[str, Any]],
    *,
    exclusion_reason_fn: Callable[[dict[str, Any]], str],
    record_exclusion_fn: Callable[[dict[str, Any], str], None],
    case_id_fn: Callable[[dict[str, Any]], str],
    stage_meta_by_key: dict[str, dict[str, Any]],
    closure_status_fn: Callable[..., tuple[bool, str, list[str]]],
    closure_source: str = "current_generation_cases",
) -> dict[str, Any]:
    primary_candidates, fallback_candidates = derived_workflow_candidate_buckets(
        cases_for_plan,
        exclusion_reason_fn=exclusion_reason_fn,
        record_exclusion_fn=record_exclusion_fn,
    )
    debug: dict[str, Any] = {
        "candidate_total": int(len(cases_for_plan)),
        "action_state_candidate_count": int(len(primary_candidates) + len(fallback_candidates)),
        "primary_candidate_count": int(len(primary_candidates)),
        "fallback_candidate_count": int(len(fallback_candidates)),
        "selected_candidate_count": 0,
        "closure_reason": "",
    }
    selected = select_derived_workflow_candidates(primary_candidates, fallback_candidates)
    if len(selected) < 2:
        debug["closure_reason"] = "insufficient_action_state_candidates"
        return {
            "blueprint": None,
            "debug": debug,
            "incomplete_reason": "",
            "steps": [],
            "terminal_state": "",
            "stage_kinds": [],
        }
    debug["selected_candidate_count"] = int(len(selected))
    steps, previous_state = derived_workflow_steps_from_selected(
        selected,
        case_id_fn=case_id_fn,
    )
    if len(steps) < 2:
        return {
            "blueprint": None,
            "debug": debug,
            "incomplete_reason": "",
            "steps": steps,
            "terminal_state": previous_state,
            "stage_kinds": [],
        }
    selected_for_closure = derived_workflow_selected_for_closure(
        steps,
        selected,
        case_id_fn=case_id_fn,
    )
    ok, reason, stage_kinds = closure_status_fn(
        selected_for_closure,
        stage_meta_by_key=stage_meta_by_key,
        source=closure_source,
    )
    if not ok:
        debug["closure_reason"] = str(reason or "")
        return {
            "blueprint": None,
            "debug": debug,
            "incomplete_reason": str(reason or ""),
            "steps": steps,
            "terminal_state": previous_state,
            "stage_kinds": list(stage_kinds),
        }
    return {
        "blueprint": {
            "id": "derived_current_generation_workflow",
            "name": "current generation derived workflow",
            "source": "current_generation_cases",
            "steps": steps,
            "terminal_state": previous_state,
        },
        "debug": debug,
        "incomplete_reason": "",
        "steps": steps,
        "terminal_state": previous_state,
        "stage_kinds": list(stage_kinds),
    }


def workflow_blueprint_source_label(
    workflow_blueprints: list[dict[str, Any]],
    plan_workflow_blueprints: list[dict[str, Any]],
) -> str:
    if workflow_blueprints:
        for blueprint in workflow_blueprints:
            repository_source = str(blueprint.get("repository_source") or blueprint.get("source") or "").strip()
            source_type = str(blueprint.get("source_type") or "").strip()
            if repository_source == "current_requirement_blueprint" or source_type == "current_requirement_extracted":
                return "current_requirement_blueprint"
        return "feedback_control_state"
    if plan_workflow_blueprints:
        return "current_generation_cases"
    return "none"


def stage_match_patterns(step: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    raw_keywords: list[str] = []
    for key in ("match_keywords", "keywords", "aliases"):
        value = step.get(key)
        if isinstance(value, list):
            raw_keywords.extend(str(item).strip() for item in value if str(item).strip())
    if bool(step.get("allow_bridge")) and raw_keywords:
        return tuple((keyword.lower(),) for keyword in raw_keywords if str(keyword or "").strip())
    for key in ("label", "action", "module", "assertion", "state_in", "state_out"):
        value = str(step.get(key) or "").strip()
        if value:
            raw_keywords.append(value)
    patterns: list[tuple[str, ...]] = []
    for keyword in raw_keywords:
        compact = str(keyword or "").strip().lower()
        if compact:
            patterns.append((compact,))
    return tuple(patterns)


def pattern_match_score(text: str, patterns: tuple[tuple[str, ...], ...]) -> int:
    normalized_text = str(text or "")
    best = 0
    for pattern in patterns:
        tokens = [str(token or "").strip().lower() for token in pattern if str(token or "").strip()]
        if not tokens:
            continue
        if all(token in normalized_text for token in tokens):
            best = max(best, sum(len(token) for token in tokens))
            continue
        if len(tokens) == 1:
            parts = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", tokens[0])
            if parts and all(part in normalized_text for part in parts[:6]):
                best = max(best, sum(len(part) for part in parts[:6]))
    return best


def main_chain_stages_from_blueprints(
    plan_workflow_blueprints: list[dict[str, Any]],
) -> tuple[
    list[tuple[str, str, tuple[tuple[str, ...], ...]]],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    stages: list[tuple[str, str, tuple[tuple[str, ...], ...]]] = []
    workflow_stage_meta_by_key: dict[str, dict[str, Any]] = {}
    workflow_stage_output_state: dict[str, str] = {}
    for blueprint_index, blueprint in enumerate(plan_workflow_blueprints[:3], start=1):
        steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
        if len(steps) < 2:
            continue
        for step_index, step in enumerate(steps[:12], start=1):
            stage_key = str(step.get("id") or f"bp{blueprint_index}_step_{step_index:03d}").strip()
            stage_label = str(
                step.get("label")
                or step.get("action")
                or step.get("description")
                or stage_key
            ).strip()
            patterns = stage_match_patterns(step)
            if not stage_key or not stage_label or not patterns:
                continue
            stage_text = " ".join(
                str(step.get(key) or "")
                for key in ("label", "action", "description", "module", "assertion", "state_out")
            )
            workflow_stage_meta_by_key[stage_key] = {
                **step,
                "actor": normalize_actor_role_value(
                    step.get("actor") or step.get("role"),
                    fallback_text=stage_text,
                ),
                "source_actor_role": str(
                    step.get("source_actor_role") or step.get("actor") or step.get("role") or ""
                ).strip(),
                "blueprint_id": str(blueprint.get("id") or f"blueprint_{blueprint_index}"),
                "blueprint_name": str(blueprint.get("name") or blueprint.get("title") or "workflow_blueprint"),
                "step_index": int(step_index),
            }
            state_out = str(step.get("state_out") or "").strip()
            if state_out:
                workflow_stage_output_state[stage_key] = state_out
            stages.append((stage_key, stage_label, patterns))
        if stages:
            break
    return stages, workflow_stage_meta_by_key, workflow_stage_output_state
