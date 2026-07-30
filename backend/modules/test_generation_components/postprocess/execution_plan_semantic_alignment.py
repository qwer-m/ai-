from __future__ import annotations

import re
from typing import Any

from ..control.actor_roles import is_automated_actor_role
from .case_access import (
    case_id as case_access_id,
    case_text_field,
)
from .execution_plan_action_support import main_chain_action_support_conflict_reason
from .execution_plan_case_state import (
    _case_semantic_text,
    _main_smoke_cases,
    _state_value,
    _text,
    materialize_final_case_state_fields,
)
from .execution_plan_validation_tokens import (
    _COMMIT_ACTION_TOKENS,
    _COMMIT_REQUIRED_TOKENS,
    _COMPLETION_REQUIRED_TOKENS,
    _COMPLETION_STRONG_TOKENS,
    _COMPLETION_SYNC_TOKENS,
    _CONDITIONAL_VISIBILITY_TOKENS,
    _CONFIGURE_ACTION_REQUIRED_TOKENS,
    _CONFIGURE_TOKENS,
    _CONSUME_REQUIRED_TOKENS,
    _CONSUME_TOKENS,
    _DOWNSTREAM_PROPAGATION_TOKENS,
    _DOWNSTREAM_VISIBILITY_TOKENS,
    _INTERNAL_PLACEHOLDER_PATTERN,
    _PASSIVE_LIST_STATUS_TOKENS,
    _PASSIVE_VISIBILITY_SURFACE_TOKENS,
    _PREVIEW_REQUIRED_TOKENS,
    _REPORT_HISTORY_ONLY_TOKENS,
    _RESET_OR_ABORT_TOKENS,
    _RESUME_STATE_ONLY_TOKENS,
)
from .streaming_execution_plan_helpers import (
    evaluate_declared_workflow_closure,
    is_pure_ui_goal_text,
    main_chain_goal_action_text,
    main_chain_goal_text,
)
from .streaming_execution_plan_stage_inference import token_hit

_EDIT_REQUIRED_TOKENS = (
    "编辑",
    "修改",
    "填写",
    "文案",
    "正文",
    "图片",
    "上传",
    "草稿",
    "编辑内容",
    "填写内容",
    "输入内容",
    "输入文字",
    "edit",
    "modify",
    "compose",
    "write",
    "fill",
    "upload",
    "content input",
    "input content",
)
_ATOMIC_EDIT_ACTION_TOKENS = (
    "编辑", "填写", "输入", "上传", "选择版块", "选择分类",
    "edit", "fill", "input", "upload", "compose",
)
_ATOMIC_PREP_NAVIGATION_TOKENS = (
    "进入", "返回", "切换", "打开",
    "navigate", "enter", "return", "switch", "open",
)

_BLUEPRINT_STAGE_SPLIT_RE = re.compile(r"[\s,，。；;：:/\\|｜、（）()\[\]【】{}<>《》\-—_]+")

_BLUEPRINT_STAGE_GENERIC_TOKENS = {
    "button",
    "case",
    "check",
    "click",
    "content",
    "current",
    "detail",
    "display",
    "entry",
    "form",
    "home",
    "input",
    "item",
    "list",
    "module",
    "open",
    "page",
    "preview",
    "result",
    "select",
    "show",
    "status",
    "submit",
    "tab",
    "user",
    "view",
    "页面",
    "按钮",
    "点击",
    "进入",
    "打开",
    "查看",
    "检查",
    "选择",
    "设置",
    "配置",
    "编辑",
    "填写",
    "输入",
    "提交",
    "发布",
    "确认",
    "保存",
    "显示",
    "展示",
    "可见",
    "出现",
    "预览",
    "操作",
    "用户",
    "当前",
    "对应",
    "进行",
    "功能",
    "模块",
    "页面",
    "详情",
    "列表",
    "内容",
    "状态",
    "结果",
}


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


def _has_any_text(text: str, tokens: tuple[str, ...]) -> bool:
    return any(_text(token).lower() in text for token in tokens if _text(token))


def _flatten_semantic_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(
            part for part in (_flatten_semantic_value(item) for item in value.values()) if part
        ).strip()
    if isinstance(value, (list, tuple, set)):
        return " ".join(part for part in (_flatten_semantic_value(item) for item in value) if part).strip()
    return _text(value)


def _semantic_anchor_tokens(value: Any, *, cjk_only: bool = False) -> set[str]:
    text = _flatten_semantic_value(value).lower()
    if not text:
        return set()
    raw_tokens: list[str] = []
    if not cjk_only:
        for ascii_token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text):
            raw_tokens.extend(part for part in re.split(r"[_\-]+", ascii_token) if len(part) >= 3)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for piece in _BLUEPRINT_STAGE_SPLIT_RE.split(sequence):
            if len(piece) < 2:
                continue
            if len(piece) <= 8:
                raw_tokens.append(piece)
            if len(piece) >= 3:
                for size in (2, 3, 4):
                    if len(piece) < size:
                        continue
                    raw_tokens.extend(piece[index : index + size] for index in range(len(piece) - size + 1))
    tokens: set[str] = set()
    for token in raw_tokens:
        normalized = token.strip().lower()
        if len(normalized) < 2:
            continue
        if normalized in _BLUEPRINT_STAGE_GENERIC_TOKENS:
            continue
        tokens.add(normalized)
    return tokens


def _blueprint_stage_text(case: dict[str, Any]) -> str:
    parts: list[Any] = [
        case.get("main_chain_stage_label"),
        _state_value(case, "action"),
        case.get("main_chain_stage_module"),
        case.get("main_chain_stage_assertion"),
        case.get("main_chain_stage_description"),
        case.get("main_chain_stage_state_in"),
        case.get("main_chain_stage_state_out"),
        case.get("main_chain_stage_keywords"),
        case.get("main_chain_stage_evidence"),
    ]
    return " ".join(part for part in (_flatten_semantic_value(value) for value in parts) if part)


def _blueprint_stage_module_text(case: dict[str, Any]) -> str:
    return _flatten_semantic_value(case.get("main_chain_stage_module"))


def _automated_stage_action_anchor_conflict_reason(case: dict[str, Any]) -> str:
    """系统自动阶段不强制用户动作词，但 action/label 必须能在用例中找到语义锚点。"""
    action_label_text = " ".join(
        value
        for value in (
            _text(_state_value(case, "action")),
            _text(case.get("main_chain_stage_label")),
        )
        if value
    )
    if not action_label_text:
        return "automated_stage_action_anchor_missing"
    stage_tokens = _semantic_anchor_tokens(action_label_text)
    if not stage_tokens:
        return "automated_stage_action_anchor_missing"
    case_tokens = _semantic_anchor_tokens(_case_semantic_text(case))
    if not case_tokens or not (stage_tokens & case_tokens):
        return "automated_stage_action_anchor_not_supported"
    return ""


def main_chain_blueprint_semantic_conflict_reason(case: dict[str, Any]) -> str:
    """Return a conflict when the public case object drifts away from blueprint stage anchors."""
    stage_text = _blueprint_stage_text(case)
    if not stage_text:
        return ""
    stage_tokens = _semantic_anchor_tokens(stage_text, cjk_only=True)
    if not stage_tokens:
        return ""

    case_tokens = _semantic_anchor_tokens(_case_semantic_text(case), cjk_only=True)
    if not case_tokens:
        return "stage_object_not_supported_by_case_text"
    if not (stage_tokens & case_tokens):
        return "stage_object_not_supported_by_case_text"

    stage_kind = _stage_kind(case)
    if stage_kind not in {"entry", "preview"}:
        return ""

    blueprint_module_tokens = _semantic_anchor_tokens(
        _blueprint_stage_module_text(case),
        cjk_only=True,
    )
    if not blueprint_module_tokens:
        return ""
    module_tokens = _semantic_anchor_tokens(case_text_field(case, "test_module"), cjk_only=True)
    if module_tokens and not (module_tokens & blueprint_module_tokens):
        return "stage_module_not_aligned_with_blueprint"
    return ""


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


def _collect_main_smoke_semantic_findings(cases: Any) -> list[dict[str, Any]]:
    """Collect semantic findings before separating blocking conflicts from local warnings."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(final_cases)
    conflicts: list[dict[str, Any]] = []

    for case in main_cases:
        stage_kind = _stage_kind(case)
        text = _case_semantic_text(case)
        automated_actor = is_automated_actor_role(
            case.get("role") or case.get("source_actor_role")
        )
        if not text:
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="empty_main_smoke_case_text",
                stage_kind=stage_kind,
            )
            continue

        goal_text = main_chain_goal_text(case)
        if is_pure_ui_goal_text(goal_text):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="display_only_case_used_in_main_chain",
                stage_kind=stage_kind,
            )

        goal_action_text = " ".join(
            [
                main_chain_goal_action_text(case),
                _text(case.get("steps")),
            ]
        )
        if (
            not automated_actor
            and stage_kind in {"entry", "consume", "configure", "edit"}
            and token_hit(goal_action_text, _COMMIT_ACTION_TOKENS)
        ):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="case_goal_spans_commit_stage",
                stage_kind=stage_kind,
            )
        if (
            not automated_actor
            and stage_kind == "commit"
            and token_hit(goal_action_text, _ATOMIC_EDIT_ACTION_TOKENS)
            and token_hit(goal_action_text, _ATOMIC_PREP_NAVIGATION_TOKENS)
        ):
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason="commit_case_replays_edit_stage",
                stage_kind=stage_kind,
            )

        if (
            bool(case.get("generated_bridge_case"))
            or bool(case.get("workflow_blueprint_bridge"))
            or bool(case.get("workflow_contract_materialized_case"))
        ):
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

        action_support_reason = (
            _automated_stage_action_anchor_conflict_reason(case)
            if automated_actor
            else main_chain_action_support_conflict_reason(case)
        )
        if action_support_reason:
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason=action_support_reason,
                stage_kind=stage_kind,
            )
        blueprint_semantic_reason = main_chain_blueprint_semantic_conflict_reason(case)
        if blueprint_semantic_reason:
            _add_semantic_conflict(
                conflicts,
                case=case,
                reason=blueprint_semantic_reason,
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
        elif stage_kind == "edit":
            if not automated_actor and not _has_any_text(text, _EDIT_REQUIRED_TOKENS):
                _add_semantic_conflict(
                    conflicts,
                    case=case,
                    reason="stage_text_lacks_edit_action",
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
            if not automated_actor and not _has_any_text(text, _COMMIT_REQUIRED_TOKENS):
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


_SOFT_SEMANTIC_REASONS = {
    "display_only_case_used_in_main_chain",
    "case_goal_spans_commit_stage",
    "commit_case_replays_edit_stage",
    "resume_state_case_in_main_smoke",
    "conditional_visibility_case_in_main_smoke",
    "stage_action_not_supported_by_case_text",
    "stage_object_not_supported_by_case_text",
    "stage_module_not_aligned_with_blueprint",
    "stage_text_lacks_configure_action",
    "passive_list_status_case_used_as_configure",
    "stage_text_lacks_edit_action",
    "stage_text_lacks_preview_action",
    "stage_text_lacks_commit_action",
    "stage_text_lacks_downstream_propagation",
    "stage_text_lacks_consume_action",
    "stage_text_lacks_completion_sync",
    "report_history_case_not_completion_sync",
}


def analyze_main_smoke_semantic_alignment(cases: Any) -> dict[str, list[dict[str, Any]]]:
    """将局部语义瑕疵保留为警告，只让明确矛盾阻断声明式闭环。"""
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for finding in _collect_main_smoke_semantic_findings(cases):
        current = dict(finding)
        reason = str(current.get("reason") or "").strip()
        if reason in _SOFT_SEMANTIC_REASONS:
            current["severity"] = "warning"
            warnings.append(current)
        else:
            current["severity"] = "error"
            conflicts.append(current)
    return {"conflicts": conflicts, "warnings": warnings}


def validate_main_smoke_semantic_alignment(cases: Any) -> list[dict[str, Any]]:
    """Return only semantic contradictions that must block executable closure."""
    return analyze_main_smoke_semantic_alignment(cases)["conflicts"]


def _closure_metrics(
    main_cases: list[dict[str, Any]],
    *,
    workflow_blueprints: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    closure = evaluate_declared_workflow_closure(
        main_cases,
        workflow_blueprints=workflow_blueprints,
    )
    return {
        "main_chain_stage_kinds": [_stage_kind(item) for item in main_cases],
        "workflow_closure": closure,
        "required_stage_count": int(len(closure.get("required_stage_ids") or [])),
        "covered_required_stage_count": int(
            len(closure.get("required_stage_ids") or [])
            - len(closure.get("missing_required_stage_ids") or [])
        ),
        "required_stage_coverage_complete": not bool(closure.get("missing_required_stage_ids")),
        "terminal_state_reachable": bool(closure.get("terminal_state_reachable")),
        "workflow_closure_satisfied": bool(closure.get("closure_satisfied")),
    }
