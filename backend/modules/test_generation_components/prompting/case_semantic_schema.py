from __future__ import annotations

import json

from ..control.semantic_contract import (
    MODULE_ROLE_VALUES,
    STATE_POLARITY_VALUES,
    STATE_SCOPE_VALUES,
    STATE_SOURCE_VALUES,
    STATE_TEMPORAL_VALUES,
)


PUBLIC_CASE_FIELDS = (
    "id",
    "description",
    "test_module",
    "preconditions",
    "steps",
    "test_input",
    "expected_result",
    "priority",
)


def _typed_state_example(*, source: str, temporal: str) -> dict:
    return {
        "entity": "entity from this case",
        "state": "state from this case",
        "source": source,
        "scope": "entity",
        "polarity": "positive",
        "temporal": temporal,
        "evidence": ["exact quote from this case public fields"],
        "confidence": 0.8,
    }


def _case_semantic_schema_example() -> str:
    payload = {
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": "active module key",
                    "module_name": "active module name",
                    "role": "primary",
                    "confidence": 0.8,
                    "evidence": ["exact quote from this case public fields"],
                }
            ],
            "interaction_ids": [],
            "workflow_stage_candidates": [
                {
                    "workflow_id": "active workflow id",
                    "stage_id": "active stage id",
                    "stage_kind": "exact declared stage kind",
                    "confidence": 0.8,
                    "evidence": ["exact quote from this case public fields"],
                }
            ],
            "precondition_states": [
                _typed_state_example(
                    source="previous_stage",
                    temporal="after_previous_stage",
                )
            ],
            "produced_states": [
                _typed_state_example(
                    source="current_stage",
                    temporal="after_case",
                )
            ],
        }
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def render_case_semantic_output_contract(*, case_subject: str = "Every case") -> str:
    public_fields = ", ".join(PUBLIC_CASE_FIELDS)
    subject = str(case_subject or "Every case").strip()
    source_values = " | ".join(STATE_SOURCE_VALUES)
    module_role_values = " | ".join(MODULE_ROLE_VALUES)
    scope_values = " | ".join(STATE_SCOPE_VALUES)
    polarity_values = " | ".join(STATE_POLARITY_VALUES)
    temporal_values = " | ".join(STATE_TEMPORAL_VALUES)
    schema_example = _case_semantic_schema_example()
    return f"""
CASE OUTPUT CONTRACT (MANDATORY):
- {subject} MUST contain these public fields: {public_fields}.
- {subject} MUST also contain `_semantic`.
- Exact `_semantic` object shape (schema values are illustrative; IDs and evidence must come from the active contract and this case):
{schema_example}
- Do not omit `evidence` or `confidence` from any module candidate, workflow stage candidate, precondition state, or produced state object. `evidence` is always an array and `confidence` is always a positive number.
- `_semantic.module_candidates` is REQUIRED and non-empty. Every item contains module_key, module_name, role, positive confidence, and evidence, and references an active functional module.
- module candidate role is one of: {module_role_values}.
- `_semantic.interaction_ids` is an array of active requirement interaction IDs; use [] when the case covers no declared interaction.
- `_semantic.workflow_stage_candidates` is an array. A case intended for the declared primary workflow MUST provide the exact workflow_id, stage_id, stage_kind, positive confidence, and evidence. Independent cases use [].
- workflow_id is the exact machine identifier from ACTIVE WORKFLOW SEMANTIC CATALOG, never the workflow name or label. A case matching a required stage must not use [] to bypass the workflow contract.
- `_semantic.precondition_states` and `_semantic.produced_states` are both required arrays. Use [] only when the case has no semantic state dependency or output.
- Every typed state contains entity, state, source, scope, polarity, temporal, evidence, and positive confidence.
- source is one of: {source_values}.
- scope is one of: {scope_values}.
- polarity is one of: {polarity_values}.
- temporal is one of: {temporal_values}.
- Only previous_stage means the immediately preceding primary-workflow stage must produce the state.
- Use current_stage for a state directly produced by this case's tested action. Never use current_stage in precondition_states.
- Use same_case_setup only for a state established by setup before the tested action; do not use it for the action's output.
- When a case references a declared workflow stage, copy that stage contract's entity, state, source, scope, polarity, and temporal exactly into matching precondition_states/produced_states. Evidence and confidence must still come from this case's public fields.
- Every semantic evidence item is an exact quote from the same case's public fields and remains consistent with the current requirement contract.
- Do not infer module, interaction, workflow stage, or typed state from keyword overlap. Unverified semantic items are rejected.
""".strip()


__all__ = ["PUBLIC_CASE_FIELDS", "render_case_semantic_output_contract"]
