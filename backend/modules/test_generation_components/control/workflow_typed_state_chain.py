from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def typed_state_identity(state: dict[str, Any]) -> tuple[str, str, str, str]:
    """返回用于跨阶段衔接的稳定状态身份。"""
    return (
        _text(state.get("entity")),
        _text(state.get("state")),
        _text(state.get("scope")),
        _text(state.get("polarity")) or "positive",
    )


def validate_typed_state_chain(steps: Any) -> list[dict[str, Any]]:
    """
    校验 previous_stage 状态必须由紧邻的上一阶段产出。

    首个阶段没有上游，因此不得声明 previous_stage；其余阶段按
    entity/state/scope/polarity 四元组严格匹配。
    """
    if not isinstance(steps, list):
        return [
            {
                "reason": "workflow_steps_not_array",
                "step_index": 0,
                "state_index": 0,
            }
        ]

    issues: list[dict[str, Any]] = []
    for step_index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            issues.append(
                {
                    "reason": "workflow_step_not_object",
                    "step_index": int(step_index),
                    "state_index": 0,
                }
            )
            continue
        step = raw_step
        for collection in ("required_states", "produced_states"):
            raw_states = step.get(collection)
            if not isinstance(raw_states, list):
                issues.append(
                    {
                        "reason": "typed_state_collection_not_array",
                        "step_index": int(step_index),
                        "state_index": 0,
                        "step_id": _text(step.get("id")),
                        "collection": collection,
                    }
                )
                continue
            for state_index, state in enumerate(raw_states):
                if isinstance(state, dict):
                    continue
                issues.append(
                    {
                        "reason": "typed_state_item_not_object",
                        "step_index": int(step_index),
                        "state_index": int(state_index),
                        "step_id": _text(step.get("id")),
                        "collection": collection,
                    }
                )

        previous_step = steps[step_index - 1] if step_index > 0 else {}
        if not isinstance(previous_step, dict):
            previous_step = {}
        previous_produced_states = previous_step.get("produced_states") or []
        if not isinstance(previous_produced_states, list):
            previous_produced_states = []
        previous_produced_identities = {
            typed_state_identity(state)
            for state in previous_produced_states
            if isinstance(state, dict)
        }
        required_states = step.get("required_states") or []
        if not isinstance(required_states, list):
            required_states = []
        for state_index, required_state in enumerate(required_states):
            if (
                not isinstance(required_state, dict)
                or _text(required_state.get("source")) != "previous_stage"
            ):
                continue
            required_identity = typed_state_identity(required_state)
            if step_index > 0 and required_identity in previous_produced_identities:
                continue
            issues.append(
                {
                    "reason": (
                        "previous_stage_state_without_predecessor"
                        if step_index == 0
                        else "previous_stage_state_not_produced"
                    ),
                    "step_index": int(step_index),
                    "state_index": int(state_index),
                    "step_id": _text(step.get("id")),
                    "required_state_identity": list(required_identity),
                    "previous_produced_state_identities": [
                        list(identity)
                        for identity in sorted(previous_produced_identities)
                    ],
                }
            )
    return issues


__all__ = ["typed_state_identity", "validate_typed_state_chain"]
