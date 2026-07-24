"""从人工最终用例中的显式执行契约构建 workflow blueprint。"""

from __future__ import annotations

from typing import Any

from ..control.actor_roles import normalize_actor_role
from ..postprocess.case_access import case_text_parts
from .final_case_parsing import _text


_WORKFLOW_STAGE_ORDER = {
    "entry": 10,
    "configure": 20,
    "edit": 30,
    "preview": 40,
    "commit": 50,
    "downstream_visibility": 60,
    "consume": 70,
    "completion_sync": 80,
}
_CLOSURE_STAGE_KINDS = frozenset({"downstream_visibility", "consume", "completion_sync"})


def _case_text(case: dict[str, Any]) -> str:
    return " ".join(
        case_text_parts(
            case,
            ("description", "test_module", "preconditions", "steps", "test_input", "expected_result"),
            dedupe=False,
        )
    ).strip()


def _workflow_transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _explicit_stage_kind(case: dict[str, Any]) -> str:
    transition = _workflow_transition_payload(case)
    value = _text(
        transition.get("stage_kind")
        or case.get("main_chain_stage_kind")
        or case.get("stage_kind")
    ).lower()
    return value if value in _WORKFLOW_STAGE_ORDER else ""


def _explicit_actor(case: dict[str, Any]) -> str:
    transition = _workflow_transition_payload(case)
    value = _text(case.get("role") or case.get("actor") or transition.get("actor"))
    if not value:
        return ""
    return normalize_actor_role(value)


def _explicit_action(case: dict[str, Any]) -> str:
    transition = _workflow_transition_payload(case)
    return _text(case.get("action") or transition.get("action"))[:160]


def _explicit_state_in(case: dict[str, Any]) -> str:
    transition = _workflow_transition_payload(case)
    return _text(
        case.get("source_state")
        or case.get("state_in")
        or transition.get("source_state")
        or transition.get("state_in")
    )[:120]


def _explicit_state_out(case: dict[str, Any]) -> str:
    transition = _workflow_transition_payload(case)
    return _text(
        case.get("target_state")
        or case.get("state_out")
        or transition.get("target_state")
        or transition.get("state_out")
    )[:120]


def _workflow_step_keywords(case: dict[str, Any], action: str) -> list[str]:
    transition = _workflow_transition_payload(case)
    raw = (
        transition.get("match_keywords")
        or case.get("match_keywords")
        or []
    )
    values = raw if isinstance(raw, list) else [raw]
    output: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in output:
            output.append(text[:80])
        if len(output) >= 8:
            break
    if not output and action:
        output.append(action[:80])
    return output


def _is_explicit_main_path_case(case: dict[str, Any]) -> bool:
    transition = _workflow_transition_payload(case)
    return bool(
        _text(case.get("execution_group")) == "main_smoke"
        or _text(case.get("chain_id")) == "main_smoke_chain"
        or transition.get("main_path_step") is True
    )


def _workflow_candidate(case: dict[str, Any]) -> tuple[bool, str]:
    if not _is_explicit_main_path_case(case):
        return False, "missing_explicit_main_path"
    transition = _workflow_transition_payload(case)
    path_type = _text(case.get("path_type") or transition.get("path_type")).lower()
    if path_type and path_type != "positive":
        return False, "negative_path"
    if case.get("blocking") is True or transition.get("blocking") is True:
        return False, "blocking_path"
    if case.get("destructive") is True or transition.get("destructive") is True:
        return False, "destructive_path"
    if transition.get("can_advance_main_flow") is False:
        return False, "non_advancing_path"

    stage_kind = _explicit_stage_kind(case)
    if not stage_kind:
        return False, "missing_stage_kind"
    if not _explicit_actor(case):
        return False, "missing_actor"
    if not _explicit_action(case):
        return False, "missing_action"
    if not _explicit_state_in(case) or not _explicit_state_out(case):
        return False, "missing_state_transition"
    return True, stage_kind


def _execution_sequence(case: dict[str, Any], fallback: int) -> int:
    try:
        value = int(case.get("execution_sequence"))
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else 10000 + fallback


def _select_workflow_cases(cases: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], str]], str]:
    selected: list[tuple[int, int, dict[str, Any], str]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        accepted, stage_kind = _workflow_candidate(case)
        if not accepted:
            continue
        selected.append((_execution_sequence(case, index), index, case, stage_kind))
    selected.sort(key=lambda item: (item[0], item[1], _WORKFLOW_STAGE_ORDER[item[3]]))
    return [(case, stage_kind) for _sequence, _index, case, stage_kind in selected[:12]], "explicit_main_smoke"


def _workflow_chain_is_executable(selected: list[tuple[dict[str, Any], str]]) -> bool:
    stage_kinds = [stage_kind for _case, stage_kind in selected]
    if len(stage_kinds) < 2 or "commit" not in stage_kinds:
        return False
    commit_index = stage_kinds.index("commit")
    if not any(
        index > commit_index and stage_kind in _CLOSURE_STAGE_KINDS
        for index, stage_kind in enumerate(stage_kinds)
    ):
        return False
    for index in range(1, len(selected)):
        previous = selected[index - 1][0]
        current = selected[index][0]
        if _explicit_state_out(previous) != _explicit_state_in(current):
            return False
    return True


def _build_workflow_blueprint_sample(
    cases: list[dict[str, Any]],
    *,
    generation_id: int | None,
    linked_doc_ids: list[int],
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected, selection_source = _select_workflow_cases(cases)
    if not _workflow_chain_is_executable(selected):
        return None

    first_case = selected[0][0]
    first_transition = _workflow_transition_payload(first_case)
    workflow_id = _text(
        first_case.get("workflow_id")
        or first_transition.get("workflow_id")
        or f"workflow_blueprint_{generation_id or 'manual'}"
    )[:120]
    steps: list[dict[str, Any]] = []
    for index, (case, stage_kind) in enumerate(selected, start=1):
        transition = _workflow_transition_payload(case)
        description = _text(case.get("description")) or f"workflow-step-{index}"
        action = _explicit_action(case)
        steps.append(
            {
                "id": _text(
                    case.get("main_chain_stage")
                    or transition.get("step_id")
                    or transition.get("id")
                    or f"step_{index:03d}"
                )[:120],
                "label": description[:160],
                "module": _text(case.get("test_module"))[:80],
                "actor": _explicit_actor(case),
                "action": action,
                "state_in": _explicit_state_in(case),
                "state_out": _explicit_state_out(case),
                "assertion": _text(case.get("expected_result"))[:240],
                "test_steps": case.get("steps") if isinstance(case.get("steps"), list) else [],
                "match_keywords": _workflow_step_keywords(case, action),
                "source_case_id": _text(case.get("id")),
                "workflow_id": workflow_id,
                "stage_kind": stage_kind,
                "path_type": "positive",
                "required": True,
                "terminal": index == len(selected),
                "critical": bool(
                    case.get("critical") is True
                    or transition.get("critical") is True
                ),
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "main_path_step": True,
                "state_transition_reason": selection_source,
            }
        )

    title = _text(first_case.get("test_module")) or "final_case_workflow"
    return {
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": "main_smoke_flow",
        "reason_category": "main_smoke_flow",
        "expected_priority": "P0",
        "case_id": workflow_id,
        "title": f"Workflow blueprint: {title}"[:120],
        "user_comment": "Derived from an explicit human-final execution contract.",
        "pattern_summary": f"workflow_blueprint | main_smoke_flow | {title}"[:180],
        "pattern_grain": "workflow_blueprint",
        "source": "linked_final_case_workflow_blueprint",
        "source_type": "linked_final_case_workflow_blueprint",
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(first_case.get("id")) or None,
        "learning_signal_source": "explicit_final_case_workflow_blueprint",
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=True),
        "quality_ledger": dict(quality_ledger or {}),
        "generation_id": generation_id,
        "linked_doc_ids": linked_doc_ids,
        "workflow_blueprint": {
            "id": workflow_id,
            "name": title[:120],
            "source": "linked_final_case_workflow_blueprint",
            "selection_source": selection_source,
            "state_machine_version": "workflow-blueprint-v2",
            "initial_state": steps[0]["state_in"],
            "required_stage_ids": [str(step["id"]) for step in steps],
            "terminal_states": [steps[-1]["state_out"]],
            "steps": steps,
            "terminal_state": steps[-1]["state_out"],
        },
    }


def _pattern_confidence_from_ledger(payload: dict[str, Any] | None, *, positive: bool) -> float:
    if not isinstance(payload, dict) or not payload:
        return 0.72 if positive else 0.65
    coverage_rate = float(payload.get("coverage_rate") or 0.0)
    missing_rules = int(payload.get("missing_rules_count") or 0)
    rejected = int(payload.get("judge_rejected_out_count") or 0) + int(payload.get("judge_pending_out_count") or 0)
    confidence = 0.68
    if coverage_rate >= 0.9:
        confidence += 0.08
    if missing_rules <= 2:
        confidence += 0.06
    if rejected <= 0:
        confidence += 0.04
    confidence += 0.06 if positive else -0.02
    return round(max(0.35, min(0.92, confidence)), 4)
