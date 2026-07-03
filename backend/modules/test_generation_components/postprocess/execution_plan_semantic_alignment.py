from __future__ import annotations

import re
from typing import Any

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
    _MANAGEMENT_SURFACE_TOKENS,
    _PASSIVE_LIST_STATUS_TOKENS,
    _PASSIVE_VISIBILITY_SURFACE_TOKENS,
    _PREVIEW_REQUIRED_TOKENS,
    _REPORT_HISTORY_ONLY_TOKENS,
    _RESET_OR_ABORT_TOKENS,
    _RESUME_STATE_ONLY_TOKENS,
)


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
