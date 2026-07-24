from modules.test_generation_components.legacy.stream.batch_candidate_acceptance import (
    accept_stream_batch_candidates,
)
from modules.test_generation_components.legacy.stream.batch_flow_control import (
    select_complete_generated_cases,
)
from modules.test_generation_components.postprocess.module_contract import (
    enforce_functional_module_contract,
)
from modules.test_generation_components.postprocess.streaming_case_normalization import (
    is_placeholder_expected_result,
)
from modules.test_generation_components.postprocess.streaming_execution_plan_metadata import (
    evaluate_required_stage_candidate_coverage,
)


def _profile() -> dict:
    return {
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "home",
                    "module_name": "Home",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                },
                {
                    "module_key": "detail",
                    "module_name": "Detail",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                },
            ],
            "module_interactions": [],
        }
    }


def _workflow() -> dict:
    home_closed = _state(
        "home",
        "closed",
        source="external_fixture",
        temporal="before_case",
    )
    home_open_from_entry = _state(
        "home",
        "open",
        source="current_stage",
        temporal="during_case",
    )
    home_open_from_previous = _state(
        "home",
        "open",
        source="previous_stage",
        temporal="after_previous_stage",
    )
    detail_open = _state(
        "detail",
        "open",
        source="current_stage",
        temporal="during_case",
    )
    return {
        "id": "browse_flow",
        "workflow_id": "browse_flow",
        "name": "Browse flow",
        "primary": True,
        "confidence": 0.9,
        "initial_state": "home_closed",
        "required_stage_ids": ["open_home", "view_detail"],
        "terminal_states": ["detail_open"],
        "steps": [
            {
                "id": "open_home",
                "label": "Open home",
                "action": "Open home",
                "stage_kind": "entry",
                "path_type": "positive",
                "actor": "user",
                "required": True,
                "terminal": False,
                "critical": True,
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
                "state_in": "home_closed",
                "state_out": "home_open",
                "module_candidates": [{"module_key": "home"}],
                "interaction_ids": [],
                "required_states": [home_closed],
                "produced_states": [home_open_from_entry],
            },
            {
                "id": "view_detail",
                "label": "View detail",
                "action": "View detail",
                "stage_kind": "consume",
                "path_type": "positive",
                "actor": "user",
                "required": True,
                "terminal": True,
                "critical": True,
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
                "state_in": "home_open",
                "state_out": "detail_open",
                "module_candidates": [{"module_key": "detail"}],
                "interaction_ids": [],
                "required_states": [home_open_from_previous],
                "produced_states": [detail_open],
            },
        ],
    }


def _state(
    entity: str,
    state: str,
    *,
    source: str,
    temporal: str,
) -> dict:
    return {
        "entity": entity,
        "state": state,
        "source": source,
        "scope": "module",
        "polarity": "positive",
        "temporal": temporal,
        "confidence": 0.9,
        "evidence": [f"{entity} {state}"],
        "evidence_verified": True,
    }


def _case(
    case_id: str,
    *,
    public_module: str,
    semantic_module_key: str,
    semantic_module_name: str,
    stage_id: str,
    stage_kind: str,
) -> dict:
    description = f"Execute {stage_id}"
    if stage_id == "open_home":
        precondition_states = [
            _state(
                "home",
                "closed",
                source="external_fixture",
                temporal="before_case",
            )
        ]
        produced_states = [
            _state(
                "home",
                "open",
                source="current_stage",
                temporal="during_case",
            )
        ]
    else:
        precondition_states = [
            _state(
                "home",
                "open",
                source="previous_stage",
                temporal="after_previous_stage",
            )
        ]
        produced_states = [
            _state(
                "detail",
                "open",
                source="current_stage",
                temporal="during_case",
            )
        ]
    return {
        "id": case_id,
        "description": description,
        "test_module": public_module,
        "preconditions": ["User is signed in"],
        "steps": [description],
        "test_input": stage_id,
        "expected_result": f"The {stage_id} result is observable",
        "priority": "P1",
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": semantic_module_key,
                    "module_name": semantic_module_name,
                    "role": "primary",
                    "confidence": 0.9,
                    "evidence": [description],
                    "evidence_verified": True,
                }
            ],
            "interaction_ids": [],
            "workflow_stage_candidates": [
                {
                    "workflow_id": "browse_flow",
                    "stage_id": stage_id,
                    "stage_kind": stage_kind,
                    "confidence": 0.9,
                    "evidence": [description],
                    "evidence_verified": True,
                }
            ],
            "precondition_states": precondition_states,
            "produced_states": produced_states,
        },
    }


def _accept(cases: list[dict], *, limit: int = 10, start_id: int = 1):
    return accept_stream_batch_candidates(
        cases,
        limit=limit,
        start_id=start_id,
        project_profile=_profile(),
        select_complete_generated_cases_fn=select_complete_generated_cases,
        is_placeholder_expected_result_fn=is_placeholder_expected_result,
        enforce_functional_module_contract_fn=enforce_functional_module_contract,
    )


def test_module_conflict_is_filtered_before_required_stage_coverage() -> None:
    entry = _case(
        "raw-entry",
        public_module="Home",
        semantic_module_key="home",
        semantic_module_name="Home",
        stage_id="open_home",
        stage_kind="entry",
    )
    conflicting_detail = _case(
        "raw-conflict",
        public_module="Home",
        semantic_module_key="detail",
        semantic_module_name="Detail",
        stage_id="view_detail",
        stage_kind="consume",
    )

    acceptance = _accept([entry, conflicting_detail])
    coverage = evaluate_required_stage_candidate_coverage(
        acceptance.cases,
        workflow_blueprints=[_workflow()],
    )

    assert [item["description"] for item in acceptance.cases] == [
        "Execute open_home"
    ]
    assert acceptance.module_contract_summary["module_rejected_case_count"] == 1
    assert acceptance.module_contract_summary["accepted_count"] == 1
    assert coverage["covered_required_stage_ids"] == ["open_home"]
    assert coverage["missing_required_stage_ids"] == ["view_detail"]


def test_later_valid_candidate_replaces_conflict_and_ids_remain_contiguous() -> None:
    entry = _case(
        "raw-entry",
        public_module="Home",
        semantic_module_key="home",
        semantic_module_name="Home",
        stage_id="open_home",
        stage_kind="entry",
    )
    conflicting_detail = _case(
        "raw-conflict",
        public_module="Home",
        semantic_module_key="detail",
        semantic_module_name="Detail",
        stage_id="view_detail",
        stage_kind="consume",
    )
    valid_detail = _case(
        "raw-detail",
        public_module="Detail",
        semantic_module_key="detail",
        semantic_module_name="Detail",
        stage_id="view_detail",
        stage_kind="consume",
    )

    acceptance = _accept(
        [entry, conflicting_detail, valid_detail],
        limit=2,
        start_id=7,
    )
    coverage = evaluate_required_stage_candidate_coverage(
        acceptance.cases,
        workflow_blueprints=[_workflow()],
    )

    assert [item["id"] for item in acceptance.cases] == ["TC-007", "TC-008"]
    assert [item["test_module"] for item in acceptance.cases] == ["Home", "Detail"]
    assert acceptance.module_contract_summary["candidate_count"] == 3
    assert acceptance.module_contract_summary["accepted_count"] == 2
    assert coverage["missing_required_stage_ids"] == []
    assert coverage["required_stage_coverage_complete"] is True
