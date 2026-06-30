from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..control.actor_roles import CANONICAL_ROLE_SESSION_KEYS
from ..control.workflow_blueprint_repository import (
    is_trusted_workflow_contract,
)
from .case_access import (
    case_flat_text,
    case_id as case_access_id,
    case_text_field,
)
from .streaming_execution_plan_ordering import execution_group_order_rank


_STATE_FIELD_NAMES = (
    "workflow_id",
    "source_state",
    "action",
    "target_state",
    "path_type",
    "blocking",
    "destructive",
    "can_advance_main_flow",
    "state_transition_confidence",
)
_ROLE_SESSION_KEYS = dict(CANONICAL_ROLE_SESSION_KEYS)
_COMMIT_ACTION_TOKENS = (
    "保存",
    "提交",
    "发布",
    "确认",
    "触发打分",
    "开始打分",
    "自动打分",
    "评分计算",
    "生成评分",
    "给出评分",
    "save",
    "submit",
    "publish",
    "commit",
    "confirm",
    "trigger score",
    "score calculation",
)


def _is_current_requirement_workflow_blueprint(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    repository_source = _text(payload.get("repository_source") or payload.get("source")).lower()
    source_type = _text(payload.get("source_type")).lower()
    return bool(
        repository_source == "current_requirement_blueprint"
        or source_type == "current_requirement_extracted"
    )
_DOWNSTREAM_VISIBILITY_TOKENS = (
    "同步",
    "生效",
    "展示",
    "显示",
    "评分结果",
    "打分结果",
    "综合评分",
    "visible",
    "display",
    "displayed",
    "sync",
    "show",
    "shows",
    "shown",
    "reflect",
    "score result",
    "scoring result",
)
_COMPLETION_SYNC_TOKENS = (
    "完成",
    "进度",
    "状态",
    "complete",
    "completion",
    "progress",
    "status",
)
_CONSUME_TOKENS = (
    "进入",
    "打开",
    "查看",
    "学习",
    "enter",
    "open",
    "view",
    "learn",
    "consume",
)
_CONFIGURE_TOKENS = (
    "配置",
    "设置",
    "选择",
    "编辑",
    "configure",
    "set",
    "select",
    "edit",
)
_RESET_OR_ABORT_TOKENS = (
    "退出",
    "重新进入",
    "重复进入",
    "不保留",
    "清空",
    "空白",
    "初始状态",
    "exit",
    "reset",
    "clear",
    "not retained",
    "not retain",
)
_RESUME_STATE_ONLY_TOKENS = (
    "未完成",
    "保留历史",
    "保留对话",
    "恢复上次",
    "resume",
    "resume state",
    "re-enter",
    "reentry",
    "return later",
    "retained history",
    "retained dialog",
)
_CONDITIONAL_VISIBILITY_TOKENS = (
    "仅在",
    "只有",
    "仅当",
    "条件",
    "阈值",
    "正确率",
    "超过",
    "不足",
    "低于",
    "高于",
    "大于",
    "小于",
    "only when",
    "only if",
    "threshold",
    "condition",
    "greater than",
    "less than",
)
_PASSIVE_VISIBILITY_SURFACE_TOKENS = (
    "按钮",
    "入口",
    "标识",
    "状态",
    "显示",
    "展示",
    "出现",
    "可见",
    "置灰",
    "button",
    "entry",
    "status",
    "visible",
    "display",
    "appears",
    "shown",
    "disabled",
)
_CONFIGURE_ACTION_REQUIRED_TOKENS = (
    "选择",
    "设置",
    "配置",
    "编辑",
    "修改",
    "新增",
    "添加",
    "选课",
    "选时间",
    "下一步",
    "select",
    "set",
    "configure",
    "edit",
    "modify",
    "add",
    "choose",
    "next",
)
_PASSIVE_LIST_STATUS_TOKENS = (
    "已有计划",
    "列表",
    "排序",
    "升序",
    "降序",
    "标记",
    "状态标记",
    "已完成",
    "进行中",
    "existing plan",
    "list",
    "sort",
    "sorted",
    "status label",
)
_PREVIEW_REQUIRED_TOKENS = (
    "预览",
    "预览确认",
    "确认页",
    "检查",
    "preview",
    "review",
)
_COMMIT_REQUIRED_TOKENS = (
    "保存",
    "提交",
    "确认",
    "完成创建",
    "创建成功",
    "触发打分",
    "开始打分",
    "自动打分",
    "评分计算",
    "生成评分",
    "给出评分",
    "save",
    "submit",
    "commit",
    "confirm",
    "created",
    "trigger score",
    "score calculation",
)
_DOWNSTREAM_PROPAGATION_TOKENS = (
    "同步",
    "生效",
    "最新",
    "新计划",
    "新增",
    "创建",
    "保存",
    "一致",
    "书房端",
    "学生端",
    "评分结果",
    "打分结果",
    "综合评分",
    "sync",
    "synced",
    "effective",
    "latest",
    "new plan",
    "created",
    "saved",
    "consistent",
    "visible",
    "display",
    "displayed",
    "show",
    "shows",
    "shown",
    "reflect",
    "reflected",
    "score result",
    "scoring result",
)
_CONSUME_REQUIRED_TOKENS = (
    "跳转",
    "进入",
    "打开",
    "学习",
    "点击",
    "查看",
    "navigate",
    "enter",
    "open",
    "learn",
    "click",
    "view",
    "consume",
)
_COMPLETION_REQUIRED_TOKENS = (
    "完成",
    "进度",
    "状态同步",
    "进度更新",
    "更新",
    "complete",
    "completion",
    "progress",
    "status sync",
    "updated",
)
_COMPLETION_STRONG_TOKENS = (
    "状态同步",
    "进度更新",
    "完成后",
    "同步",
    "更新",
    "completion sync",
    "progress updated",
    "synced",
    "updated",
)
_REPORT_HISTORY_ONLY_TOKENS = (
    "报告",
    "历史记录",
    "历史课程",
    "report",
    "history",
)
_MANAGEMENT_SURFACE_TOKENS = (
    "督导",
    "老师",
    "教师",
    "学员信息表格",
    "课程管理",
    "课堂管理",
    "supervisor",
    "teacher",
    "student info table",
    "course management",
)
_INTERNAL_PLACEHOLDER_PATTERN = re.compile(r"\b[a-z]+(?:_[a-z0-9]+){2,}\b")


@dataclass(frozen=True)
class ExecutionPlanValidationPolicy:
    min_main_smoke_count: int = 6
    min_p0_count: int = 6
    min_state_field_coverage: float = 0.8
    max_workflow_id_missing_rate: float = 0.2
    reject_untrusted_blueprint_source: bool = True
    allow_candidate_blueprint_without_contract: bool = True


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token_hit(text: str, tokens: tuple[str, ...]) -> bool:
    haystack = _text(text).lower()
    if not haystack:
        return False
    for token in tokens:
        needle = _text(token).lower()
        if not needle:
            continue
        if needle.isascii() and re.search(r"[a-z0-9]", needle):
            if re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack):
                return True
            continue
        if needle in haystack:
            return True
    return False


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _state_value(case: dict[str, Any], field: str) -> Any:
    value = case.get(field)
    if value not in (None, ""):
        return value
    return _transition_payload(case).get(field)


def _case_order(case: dict[str, Any], fallback: int) -> tuple[int, int]:
    try:
        main_step = int(case.get("main_chain_step") or 0)
    except (TypeError, ValueError):
        main_step = 0
    try:
        sequence = int(case.get("execution_sequence") or fallback)
    except (TypeError, ValueError):
        sequence = fallback
    return (main_step if main_step > 0 else 100000 + sequence, sequence)


def _stage_kind(case: dict[str, Any]) -> str:
    explicit = _text(_state_value(case, "stage_kind") or case.get("main_chain_stage_kind")).lower()
    if explicit:
        return explicit
    action_text = _text(_state_value(case, "action")).lower()
    target_state_text = _text(_state_value(case, "target_state")).lower()
    description_text = case_text_field(case, "description").lower()
    text = " ".join(
        [
            action_text,
            description_text,
            case_text_field(case, "expected_result"),
            target_state_text,
        ]
    ).lower()
    action_target_description = " ".join([action_text, target_state_text, description_text])
    if _token_hit(action_target_description, _COMMIT_ACTION_TOKENS):
        return "commit"
    if _token_hit(text, _DOWNSTREAM_VISIBILITY_TOKENS):
        return "downstream_visibility"
    if _token_hit(text, _COMPLETION_SYNC_TOKENS):
        return "completion_sync"
    if _token_hit(text, _CONSUME_TOKENS):
        return "consume"
    if _token_hit(text, _CONFIGURE_TOKENS):
        return "configure"
    return "unknown"


def _case_semantic_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=("test_module", "description", "test_input", "expected_result", "preconditions", "steps"),
        separator=" ",
        lower=True,
    )


_ACTION_SUPPORT_SPLIT_RE = re.compile(r"[的了着和与及并或且在从到于后前时中上下里内为把将对、，。；：:（）()\[\]\s]+")
_ACTION_SUPPORT_GENERIC_TOKENS = {
    "button",
    "click",
    "current",
    "page",
    "user",
    "view",
    "页面",
    "按钮",
    "点击",
    "操作",
    "用户",
    "当前",
    "对应",
    "进行",
    "所有",
}


def _action_support_tokens(value: Any) -> list[str]:
    text = _text(value).lower()
    if not text:
        return []
    raw_tokens: list[str] = []
    raw_tokens.extend(re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text))
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for piece in _ACTION_SUPPORT_SPLIT_RE.split(sequence):
            if len(piece) < 2:
                continue
            if len(piece) <= 6:
                raw_tokens.append(piece)
            for index in range(len(piece) - 1):
                raw_tokens.append(piece[index : index + 2])

    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        normalized = token.strip().lower()
        if len(normalized) < 2 or normalized in _ACTION_SUPPORT_GENERIC_TOKENS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _action_token_in_text(text: str, token: str) -> bool:
    if token.isascii() and re.search(r"[a-z0-9]", token):
        if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", text):
            return True
        words = re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text)
        variants = {token}
        if token.endswith("ed") and len(token) > 4:
            variants.add(token[:-1])
            variants.add(token[:-2])
        if token.endswith("s") and len(token) > 4:
            variants.add(token[:-1])
        for word in words:
            if word in variants:
                return True
            if len(word) >= 7 and len(token) >= 7 and (word.startswith(token[:5]) or token.startswith(word[:5])):
                return True
        return False
    return token in text


def main_chain_action_support_conflict_reason(case: dict[str, Any]) -> str:
    """Return a conflict reason when workflow action metadata is not supported by public case text."""
    action = _text(_state_value(case, "action"))
    label = _text(case.get("main_chain_stage_label"))
    action_tokens = _action_support_tokens(action)
    label_tokens = _action_support_tokens(label)
    if len(action_tokens) < 2 and len(label_tokens) < 2:
        return ""

    expected_tokens = list(dict.fromkeys([*action_tokens, *label_tokens]))
    if len(expected_tokens) < 2:
        return ""

    text = _case_semantic_text(case)
    matched = [token for token in expected_tokens if _action_token_in_text(text, token)]
    required = 1
    if len(expected_tokens) >= 5:
        required = 3
    elif len(expected_tokens) >= 3:
        required = 2
    if len(matched) < required or (len(matched) / max(1, len(expected_tokens))) < 0.3:
        return "stage_action_not_supported_by_case_text"
    return ""


def _has_any_text(text: str, tokens: tuple[str, ...]) -> bool:
    return any(_text(token).lower() in text for token in tokens if _text(token))


def _add_semantic_conflict(
    conflicts: list[dict[str, Any]],
    *,
    case: dict[str, Any],
    reason: str,
    stage_kind: str,
) -> None:
    conflicts.append(
        {
            "case_id": case_access_id(case) or "main-smoke-case",
            "reason": reason,
            "stage_kind": stage_kind,
            "description": case_text_field(case, "description")[:160],
        }
    )


def materialize_final_case_state_fields(cases: Any) -> Any:
    """Promote the workflow transition contract into persisted final-case fields."""
    if not isinstance(cases, list):
        return cases
    normalized: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        transition = _transition_payload(case)
        for field in _STATE_FIELD_NAMES:
            value = case.get(field)
            if value in (None, "") and transition.get(field) not in (None, ""):
                case[field] = transition[field]
        normalized.append(case)
    return normalized


def _main_smoke_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        dict(item)
        for item in cases
        if isinstance(item, dict) and _text(item.get("execution_group")).lower() == "main_smoke"
    ]
    return [
        item
        for _, item in sorted(
            enumerate(selected, start=1),
            key=lambda row: _case_order(row[1], row[0]),
        )
    ]


def validate_execution_group_order(cases: Any) -> list[dict[str, Any]]:
    """Validate that the final JSON array itself follows execution-plan order."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    conflicts: list[dict[str, Any]] = []
    seen_side_suite = False
    previous_side_rank = -1
    previous_side_group = ""

    for index, case in enumerate(final_cases, start=1):
        group = _text(case.get("execution_group")).strip().lower()
        if not group:
            continue
        case_id = case_access_id(case) or f"case-{index}"
        sequence_raw = case.get("execution_sequence")
        if sequence_raw not in (None, ""):
            try:
                sequence_value = int(sequence_raw)
            except Exception:
                sequence_value = 0
            if sequence_value != index:
                conflicts.append(
                    {
                        "case_id": case_id,
                        "index": int(index),
                        "execution_sequence": int(sequence_value),
                        "execution_group": group,
                        "reason": "execution_sequence_mismatch",
                    }
                )
        if group == "main_smoke":
            if seen_side_suite:
                conflicts.append(
                    {
                        "case_id": case_id,
                        "index": int(index),
                        "execution_group": group,
                        "reason": "main_smoke_after_independent_suite",
                    }
                )
            continue

        seen_side_suite = True
        rank = execution_group_order_rank(group)
        if previous_side_rank >= 0 and rank < previous_side_rank:
            conflicts.append(
                {
                    "case_id": case_id,
                    "index": int(index),
                    "execution_group": group,
                    "previous_execution_group": previous_side_group,
                    "rank": int(rank),
                    "previous_rank": int(previous_side_rank),
                    "reason": "side_suite_rank_decreased",
                }
            )
        previous_side_rank = rank
        previous_side_group = group

    return conflicts


def validate_main_smoke_state_chain(cases: Any) -> list[dict[str, Any]]:
    """Validate state continuity and session safety inside the ordered main chain."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(final_cases)
    conflicts: list[dict[str, Any]] = []

    for index, case in enumerate(main_cases):
        case_id = case_access_id(case) or f"main-smoke-{index + 1}"
        source_state = _text(_state_value(case, "source_state"))
        target_state = _text(_state_value(case, "target_state"))
        if not source_state or not target_state:
            conflicts.append(
                {
                    "case_id": case_id,
                    "reason": "missing_state_transition_fields",
                    "source_state": source_state,
                    "target_state": target_state,
                }
            )
        if bool(_state_value(case, "blocking")):
            conflicts.append({"case_id": case_id, "reason": "blocking_case_in_main_smoke"})
        if bool(_state_value(case, "destructive")) and index < len(main_cases) - 1:
            conflicts.append({"case_id": case_id, "reason": "destructive_case_before_terminal"})
        if _state_value(case, "can_advance_main_flow") is not True:
            conflicts.append({"case_id": case_id, "reason": "non_advancing_case_in_main_smoke"})

        role = _text(case.get("role")).lower()
        session_key = _text(case.get("session_key"))
        if not role or not session_key:
            conflicts.append(
                {
                    "case_id": case_id,
                    "reason": "missing_role_session_fields",
                    "role": role,
                    "session_key": session_key,
                }
            )
        expected_session = _ROLE_SESSION_KEYS.get(role)
        if expected_session and session_key and session_key != expected_session:
            conflicts.append(
                {
                    "case_id": case_id,
                    "reason": "role_session_mismatch",
                    "role": role,
                    "session_key": session_key,
                    "expected_session_key": expected_session,
                }
            )

        if index <= 0:
            continue
        previous = main_cases[index - 1]
        previous_id = _text(previous.get("id")) or f"main-smoke-{index}"
        previous_target = _text(_state_value(previous, "target_state"))
        if previous_target and source_state and previous_target != source_state:
            conflicts.append(
                {
                    "prev_case_id": previous_id,
                    "curr_case_id": case_id,
                    "prev_target_state": previous_target,
                    "curr_source_state": source_state,
                    "reason": "state_not_connected",
                }
            )
        previous_role = _text(previous.get("role")).lower()
        previous_session = _text(previous.get("session_key"))
        if previous_role and role and previous_role != role and previous_session and previous_session == session_key:
            conflicts.append(
                {
                    "prev_case_id": previous_id,
                    "curr_case_id": case_id,
                    "session_key": session_key,
                    "reason": "role_switch_reuses_same_session",
                }
            )
    return conflicts


def validate_main_smoke_semantic_alignment(cases: Any) -> list[dict[str, Any]]:
    """Validate that user-facing case text supports the assigned main-chain stage."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(final_cases)
    conflicts: list[dict[str, Any]] = []

    for case in main_cases:
        stage_kind = _stage_kind(case)
        text = _case_semantic_text(case)
        if not text:
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="empty_main_smoke_case_text",
                stage_kind=stage_kind,
            )
            continue

        if bool(case.get("generated_bridge_case")) or bool(case.get("workflow_blueprint_bridge")):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="generated_bridge_case_in_final_main_smoke",
                stage_kind=stage_kind,
            )

        placeholder_hits = [hit for hit in _INTERNAL_PLACEHOLDER_PATTERN.findall(text) if not hit.startswith("tc_")]
        if placeholder_hits and len(" ".join(placeholder_hits)) >= 18:
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="internal_placeholder_text_in_final_main_smoke",
                stage_kind=stage_kind,
            )

        if _has_any_text(text, _RESET_OR_ABORT_TOKENS):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="reset_or_abort_case_in_main_smoke",
                stage_kind=stage_kind,
            )
        if _has_any_text(text, _RESUME_STATE_ONLY_TOKENS):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="resume_state_case_in_main_smoke",
                stage_kind=stage_kind,
            )
        if _has_any_text(text, _CONDITIONAL_VISIBILITY_TOKENS) and _has_any_text(
            text,
            _PASSIVE_VISIBILITY_SURFACE_TOKENS,
        ):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="conditional_visibility_case_in_main_smoke",
                stage_kind=stage_kind,
            )

        role = _text(case.get("role")).lower()
        if role == "student" and _has_any_text(text, _MANAGEMENT_SURFACE_TOKENS):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="student_role_with_management_surface_text",
                stage_kind=stage_kind,
            )

        action_support_reason = main_chain_action_support_conflict_reason(case)
        if action_support_reason:
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason=action_support_reason,
                stage_kind=stage_kind,
            )

        if stage_kind == "configure":
            has_configure_action = _has_any_text(text, _CONFIGURE_ACTION_REQUIRED_TOKENS)
            if not has_configure_action:
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_configure_action",
                    stage_kind=stage_kind,
                )
            if _has_any_text(text, _PASSIVE_LIST_STATUS_TOKENS) and not has_configure_action:
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="passive_list_status_case_used_as_configure",
                    stage_kind=stage_kind,
                )
        elif stage_kind == "preview":
            if not _has_any_text(text, _PREVIEW_REQUIRED_TOKENS):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_preview_action",
                    stage_kind=stage_kind,
                )
        elif stage_kind == "commit":
            if not _has_any_text(text, _COMMIT_REQUIRED_TOKENS):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_commit_action",
                    stage_kind=stage_kind,
                )
        elif stage_kind == "downstream_visibility":
            if not _has_any_text(text, _DOWNSTREAM_PROPAGATION_TOKENS):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_downstream_propagation",
                    stage_kind=stage_kind,
                )
        elif stage_kind == "consume":
            if not _has_any_text(text, _CONSUME_REQUIRED_TOKENS):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_consume_action",
                    stage_kind=stage_kind,
                )
        elif stage_kind == "completion_sync":
            if not _has_any_text(text, _COMPLETION_REQUIRED_TOKENS):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_completion_sync",
                    stage_kind=stage_kind,
                )
            if _has_any_text(text, _REPORT_HISTORY_ONLY_TOKENS) and not _has_any_text(
                text, _COMPLETION_STRONG_TOKENS
            ):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="report_history_case_not_completion_sync",
                    stage_kind=stage_kind,
                )

    return conflicts


def _closure_metrics(main_cases: list[dict[str, Any]]) -> dict[str, Any]:
    stage_kinds = [_stage_kind(item) for item in main_cases]
    commit_indexes = [index for index, kind in enumerate(stage_kinds) if kind == "commit"]
    downstream_indexes = [
        index
        for index, kind in enumerate(stage_kinds)
        if kind in {"downstream_visibility", "consume", "completion_sync"}
    ]
    closed_loop = bool(
        commit_indexes
        and downstream_indexes
        and any(downstream_index > commit_index for commit_index in commit_indexes for downstream_index in downstream_indexes)
    )
    return {
        "main_chain_stage_kinds": stage_kinds,
        "commit_step_count": int(len(commit_indexes)),
        "downstream_or_completion_step_count": int(len(downstream_indexes)),
        "commit_downstream_completion_closed": bool(closed_loop),
    }


def validate_execution_plan(
    final_cases: Any,
    *,
    workflow_blueprints: list[dict[str, Any]] | None = None,
    execution_plan: dict[str, Any] | None = None,
    generation_mode: str = "",
    policy: ExecutionPlanValidationPolicy | None = None,
) -> dict[str, Any]:
    """Return deterministic execution-plan validation diagnostics for persistence."""
    resolved_policy = policy or ExecutionPlanValidationPolicy()
    normalized = materialize_final_case_state_fields(final_cases)
    cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(cases)
    p0_count = sum(1 for item in cases if _text(item.get("priority")).upper() == "P0")
    state_fields = ("source_state", "target_state", "path_type", "blocking", "destructive", "can_advance_main_flow")
    state_field_slots = len(main_cases) * len(state_fields)
    populated_state_fields = sum(
        1
        for item in main_cases
        for field in state_fields
        if _state_value(item, field) not in (None, "")
    )
    workflow_id_missing_count = sum(1 for item in main_cases if not _text(_state_value(item, "workflow_id")))
    state_conflicts = validate_main_smoke_state_chain(cases)
    semantic_conflicts = validate_main_smoke_semantic_alignment(cases)
    order_conflicts = validate_execution_group_order(cases)
    closure = _closure_metrics(main_cases)
    resolved_execution_plan = dict(execution_plan or {})
    blueprint_source = _text(resolved_execution_plan.get("workflow_blueprint_source")).lower()
    resolved_blueprints = [dict(item) for item in (workflow_blueprints or []) if isinstance(item, dict)]
    trusted_workflow_contracts = [
        item for item in resolved_blueprints if is_trusted_workflow_contract(item)
    ]
    current_requirement_blueprints = [
        item for item in resolved_blueprints if _is_current_requirement_workflow_blueprint(item)
    ]
    blueprint_count = len(resolved_blueprints)
    trusted_workflow_contract_count = len(trusted_workflow_contracts)
    current_requirement_blueprint_count = len(current_requirement_blueprints)
    state_field_coverage = _ratio(populated_state_fields, state_field_slots)
    workflow_id_missing_rate = _ratio(workflow_id_missing_count, len(main_cases))

    failure_reasons: list[str] = []
    if len(main_cases) < int(resolved_policy.min_main_smoke_count):
        failure_reasons.append("main_smoke_count_below_threshold")
    if p0_count < int(resolved_policy.min_p0_count):
        failure_reasons.append("p0_count_below_threshold")
    if state_field_coverage < float(resolved_policy.min_state_field_coverage):
        failure_reasons.append("state_field_coverage_below_threshold")
    if workflow_id_missing_rate > float(resolved_policy.max_workflow_id_missing_rate):
        failure_reasons.append("workflow_id_missing_rate_above_threshold")
    if state_conflicts:
        failure_reasons.append("state_chain_conflict")
    if semantic_conflicts:
        failure_reasons.append("main_smoke_semantic_conflict")
    if order_conflicts:
        failure_reasons.append("execution_group_order_conflict")
    if not bool(closure.get("commit_downstream_completion_closed")):
        failure_reasons.append("commit_downstream_completion_missing")
    candidate_blueprint_without_contract = bool(
        trusted_workflow_contract_count <= 0
        and blueprint_count <= 0
        and blueprint_source == "current_generation_cases"
        and resolved_policy.allow_candidate_blueprint_without_contract
    )
    current_requirement_blueprint_allowed = bool(
        current_requirement_blueprint_count > 0
        and blueprint_source == "current_requirement_blueprint"
    )
    if (
        trusted_workflow_contract_count <= 0
        and not current_requirement_blueprint_allowed
        and not candidate_blueprint_without_contract
    ):
        failure_reasons.append("workflow_contract_missing")
    if (
        resolved_policy.reject_untrusted_blueprint_source
        and blueprint_source == "current_generation_cases"
        and not candidate_blueprint_without_contract
    ):
        failure_reasons.append("untrusted_candidate_derived_blueprint")

    return {
        "passed": not bool(failure_reasons),
        "failure_code": "" if not failure_reasons else "execution_plan_failed",
        "failure_reasons": list(dict.fromkeys(failure_reasons)),
        "generation_mode": _text(generation_mode),
        "metrics": {
            "final_case_count": int(len(cases)),
            "main_smoke_count": int(len(main_cases)),
            "p0_count": int(p0_count),
            "state_field_coverage": float(state_field_coverage),
            "workflow_id_missing_count": int(workflow_id_missing_count),
            "workflow_id_missing_rate": float(workflow_id_missing_rate),
            "state_conflict_count": int(len(state_conflicts)),
            "semantic_conflict_count": int(len(semantic_conflicts)),
            "execution_group_order_conflict_count": int(len(order_conflicts)),
            "linear_executable": bool(
                len(main_cases) >= int(resolved_policy.min_main_smoke_count)
                and not state_conflicts
                and not semantic_conflicts
                and not order_conflicts
                and closure.get("commit_downstream_completion_closed")
            ),
            "workflow_blueprint_count": int(blueprint_count),
            "trusted_workflow_contract_count": int(trusted_workflow_contract_count),
            "current_requirement_blueprint_count": int(current_requirement_blueprint_count),
            "untrusted_workflow_blueprint_count": int(blueprint_count - trusted_workflow_contract_count),
            "workflow_contract_source_types": sorted(
                {
                    _text(item.get("source_type")).lower()
                    for item in trusted_workflow_contracts
                    if _text(item.get("source_type"))
                }
            ),
            "workflow_blueprint_source": blueprint_source or "none",
            "current_requirement_blueprint_allowed": bool(current_requirement_blueprint_allowed),
            "candidate_blueprint_without_contract_allowed": bool(candidate_blueprint_without_contract),
            **closure,
        },
        "state_conflicts": state_conflicts[:100],
        "semantic_conflicts": semantic_conflicts[:100],
        "execution_group_order_conflicts": order_conflicts[:100],
        "cases": cases,
    }


__all__ = [
    "ExecutionPlanValidationPolicy",
    "materialize_final_case_state_fields",
    "validate_execution_plan",
    "validate_execution_group_order",
    "validate_main_smoke_state_chain",
    "validate_main_smoke_semantic_alignment",
]
