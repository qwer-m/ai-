from __future__ import annotations

from types import SimpleNamespace

import modules.testing.test_generation_components.legacy.stream.persist as stream_persist_mod
from fastapi import HTTPException
from modules.testing.test_generation_components.legacy.stream.persist import (
    LegacyGenerationStreamPersistMixin,
)
from modules.testing.test_generation_components.postprocess.execution_plan_validator import (
    ExecutionPlanValidationPolicy,
    materialize_final_case_state_fields,
    validate_execution_plan,
)
from modules.testing.test_generation_components.postprocess.persistence_gate import (
    evaluate_persistence_gate,
    summarize_persistence_case_quality_gate,
)
from modules.testing.test_generation_components.postprocess.case_contract import (
    project_persistable_cases,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    filter_invalid_final_cases,
    merge_cases_for_append,
    strip_case_meta_fields,
)
from routers.automation import test_generation_generate_routes_split_helpers as generate_routes
from schemas.automation.test_generation import TestGenRequest as _TestGenRequest


def _main_chain_cases() -> list[dict]:
    stages = [
        ("entry", "ready", "started", "open workflow entry"),
        ("configure", "started", "configured", "select courses and configure schedule time"),
        ("preview", "configured", "preview_ready", "preview schedule plan before save"),
        ("commit", "preview_ready", "committed", "save plan and confirm creation"),
        ("downstream_visibility", "committed", "visible", "saved plan is synced and visible on student home"),
        ("consume", "visible", "consumed", "student clicks visible course and enters learning"),
    ]
    return [
        {
            "id": f"TC-{index:03d}",
            "description": description,
            "priority": "P0",
            "execution_group": "main_smoke",
            "main_chain_step": index,
            "role": "student",
            "session_key": "student_session",
            "expected_result": f"state reaches {target_state}",
            "workflow_transition": {
                "workflow_id": "schedule_flow",
                "source_state": source_state,
                "action": stage_kind,
                "target_state": target_state,
                "path_type": "positive",
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "state_transition_confidence": 0.9,
                "stage_kind": stage_kind,
            },
        }
        for index, (stage_kind, source_state, target_state, description) in enumerate(stages, start=1)
    ]


def _settings(mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        EXECUTION_PLAN_GATE_MODE=mode,
        EXECUTION_PLAN_MIN_MAIN_SMOKE_COUNT=6,
        EXECUTION_PLAN_MIN_P0_COUNT=6,
        EXECUTION_PLAN_MIN_STATE_FIELD_COVERAGE=0.8,
        EXECUTION_PLAN_MAX_WORKFLOW_ID_MISSING_RATE=0.2,
        EXECUTION_PLAN_REJECT_CANDIDATE_DERIVED_BLUEPRINT=True,
    )


def _trusted_blueprint() -> dict:
    return {
        "id": "schedule_flow",
        "workflow_id": "schedule_flow",
        "source_type": "human_reviewed",
        "repository_source": "workflow_blueprint_repository",
        "trusted": True,
        "steps": [
            {"id": "start", "label": "start", "action": "start", "state_in": "ready", "state_out": "started"},
            {"id": "commit", "label": "commit", "action": "commit", "state_in": "started", "state_out": "committed"},
        ],
    }


def test_validator_accepts_connected_main_smoke_and_materializes_final_fields() -> None:
    cases = _main_chain_cases()

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )

    assert result["passed"] is True
    assert result["failure_reasons"] == []
    assert result["metrics"]["state_conflict_count"] == 0
    assert result["metrics"]["linear_executable"] is True
    assert result["cases"][0]["workflow_id"] == "schedule_flow"
    assert result["cases"][0]["source_state"] == "ready"
    assert result["cases"][0]["target_state"] == "started"


def test_validator_treats_save_and_display_case_as_commit_not_downstream() -> None:
    actions = [
        ("open workflow", "ready", "started"),
        ("configure schedule", "started", "configured"),
        ("review preview", "configured", "preview_ready"),
        ("save plan and display confirmation", "preview_ready", "committed"),
        ("display saved plan on student home", "committed", "visible"),
        ("learn course from plan and enter learning", "visible", "consumed"),
    ]
    cases = [
        {
            "id": f"TC-{index:03d}",
            "description": action,
            "priority": "P0",
            "workflow_id": "schedule_flow",
            "source_state": source_state,
            "action": action,
            "target_state": target_state,
            "path_type": "positive",
            "blocking": False,
            "destructive": False,
            "can_advance_main_flow": True,
            "execution_group": "main_smoke",
            "main_chain_step": index,
            "role": "student",
            "session_key": "student_session",
            "expected_result": "saved plan is visible" if index == 5 else f"state reaches {target_state}",
        }
        for index, (action, source_state, target_state) in enumerate(actions, start=1)
    ]

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )

    assert result["passed"] is True
    assert result["metrics"]["main_chain_stage_kinds"][3] == "commit"
    assert result["metrics"]["main_chain_stage_kinds"][4] == "downstream_visibility"
    assert result["metrics"]["commit_downstream_completion_closed"] is True


def test_projected_contract_preserves_explicit_stage_kind_for_gate_validation() -> None:
    cases = _main_chain_cases()
    cases[3]["workflow_transition"]["action"] = "show saved plan confirmation"
    cases[3]["workflow_transition"]["stage_kind"] = "commit"

    projected = project_persistable_cases(cases)
    result = validate_execution_plan(
        projected,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )

    assert projected[3]["main_chain_stage_kind"] == "commit"
    assert result["passed"] is True
    assert result["metrics"]["commit_downstream_completion_closed"] is True


def test_validator_reports_disconnected_state_and_blocking_main_case() -> None:
    cases = _main_chain_cases()
    cases[2]["workflow_transition"]["source_state"] = "unexpected_state"
    cases[3]["workflow_transition"]["blocking"] = True
    cases[3]["workflow_transition"]["can_advance_main_flow"] = False

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = [item["reason"] for item in result["state_conflicts"]]
    assert result["passed"] is False
    assert "state_chain_conflict" in result["failure_reasons"]
    assert "state_not_connected" in reasons
    assert "blocking_case_in_main_smoke" in reasons
    assert "non_advancing_case_in_main_smoke" in reasons


def test_validator_rejects_stage_labels_that_do_not_match_case_text() -> None:
    cases = _main_chain_cases()
    cases[1]["description"] = "existing plan list is sorted by course time and status labels"
    cases[1]["expected_result"] = "list rows are sorted and marked completed or in progress"
    cases[2]["description"] = "exit schedule creation and verify selected courses are not retained"
    cases[2]["expected_result"] = "selection is cleared and the page returns to blank initial state"
    cases[3]["description"] = "preview schedule plan"
    cases[3]["steps"] = ["preview_schedule_plan"]
    cases[3]["expected_result"] = "schedule_preview_ready"
    cases[3]["generated_bridge_case"] = True

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "reset_or_abort_case_in_main_smoke" in reasons
    assert "stage_text_lacks_configure_action" in reasons
    assert "passive_list_status_case_used_as_configure" in reasons
    assert "generated_bridge_case_in_final_main_smoke" in reasons
    assert "internal_placeholder_text_in_final_main_smoke" in reasons


def test_validator_rejects_management_report_case_as_student_completion_sync() -> None:
    cases = _main_chain_cases()
    terminal = cases[-1]
    terminal["description"] = "student info table shows report and history buttons after class"
    terminal["test_module"] = "student info table learning progress"
    terminal["expected_result"] = "report and history buttons open the report page and history page"
    terminal["role"] = "student"
    terminal["workflow_transition"]["stage_kind"] = "completion_sync"
    terminal["workflow_transition"]["action"] = "update_learning_progress"
    terminal["workflow_transition"]["target_state"] = "progress_updated"

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "student_role_with_management_surface_text" in reasons
    assert "report_history_case_not_completion_sync" in reasons


def test_validator_rejects_conditional_visibility_and_resume_state_in_main_smoke() -> None:
    cases = _main_chain_cases()
    cases[4]["description"] = "Only when quiz accuracy is greater than 50%, the review button is visible"
    cases[4]["expected_result"] = "the review button is visible only for the threshold condition"
    cases[5]["description"] = "Re-enter unfinished flow and verify retained dialog history"
    cases[5]["expected_result"] = "retained dialog history is displayed after reentry"

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "conditional_visibility_case_in_main_smoke" in reasons
    assert "resume_state_case_in_main_smoke" in reasons


def test_validator_rejects_candidate_derived_blueprint_as_strong_proof() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "current_generation_cases"},
        policy=ExecutionPlanValidationPolicy(allow_candidate_blueprint_without_contract=False),
    )

    assert result["passed"] is False
    assert "workflow_contract_missing" in result["failure_reasons"]
    assert "untrusted_candidate_derived_blueprint" in result["failure_reasons"]


def test_validator_allows_candidate_blueprint_when_no_contract_is_available() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "current_generation_cases"},
    )

    assert result["passed"] is True
    assert result["metrics"]["trusted_workflow_contract_count"] == 0
    assert result["metrics"]["candidate_blueprint_without_contract_allowed"] is True


def test_validator_rejects_priority_pool_blueprint_without_repository_trust() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[
            {
                "id": "schedule_flow",
                "source_type": "linked_final_case_workflow_blueprint",
                "source": "priority_sample_pool",
                "steps": [
                    {"id": "start", "label": "start", "state_in": "ready", "state_out": "started"},
                    {"id": "commit", "label": "commit", "state_in": "started", "state_out": "committed"},
                ],
            }
        ],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    assert result["passed"] is False
    assert "workflow_contract_missing" in result["failure_reasons"]
    assert result["metrics"]["workflow_blueprint_count"] == 1
    assert result["metrics"]["trusted_workflow_contract_count"] == 0


def test_persistence_gate_shadows_then_enforces_execution_failure() -> None:
    broken_cases = materialize_final_case_state_fields(_main_chain_cases()[:2])

    shadow = evaluate_persistence_gate(
        broken_cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        settings=_settings("shadow"),
    )
    enforced = evaluate_persistence_gate(
        broken_cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        settings=_settings("enforce"),
    )

    assert shadow["passed"] is True
    assert shadow["execution_plan_would_block"] is True
    assert enforced["passed"] is False
    assert enforced["failure_code"] == "execution_plan_failed"


def test_persistence_gate_always_blocks_empty_formal_result() -> None:
    result = evaluate_persistence_gate([], settings=_settings("shadow"))

    assert result["passed"] is False
    assert result["failure_code"] == "EMPTY_GENERATED_RESULT"


def test_persistence_case_quality_gate_fails_batch_quality_metrics() -> None:
    quality = summarize_persistence_case_quality_gate(
        {"passed": True, "failed_checks": []},
        generation_summary={"final_count": 89, "min_acceptable_final": 104},
        review_decision_summary={
            "final_scenario_duplicate_case_count": 41,
            "final_flow_misordered_count": 0,
        },
        judge_summary={"rejected_out_count": 44},
    )

    assert quality["passed"] is False
    assert {
        "final_count_below_min_acceptable",
        "judge_rejected_above_threshold",
        "final_scenario_duplicates_above_threshold",
    }.issubset(set(quality["failed_checks"]))
    assert quality["metrics"]["final_count"] == 89
    assert quality["metrics"]["judge_rejected_count"] == 44


def test_final_case_strip_preserves_formal_priority_and_execution_fields() -> None:
    cases = strip_case_meta_fields(
        [
            {
                "id": "TC-001",
                "priority": "P1",
                "priority_final": "P0",
                "priority_decision_source": "execution_plan_final_priority",
                "workflow_id": "schedule_flow",
                "source_state": "draft",
                "target_state": "saved",
                "execution_group": "main_smoke",
                "role": "student",
                "session_key": "student_session",
            }
        ]
    )

    assert cases[0]["priority"] == "P0"
    assert cases[0]["priority_final"] == "P0"
    assert cases[0]["workflow_id"] == "schedule_flow"
    assert cases[0]["source_state"] == "draft"
    assert cases[0]["target_state"] == "saved"
    assert cases[0]["execution_group"] == "main_smoke"
    assert cases[0]["role"] == "student"
    assert cases[0]["session_key"] == "student_session"
    assert "priority_decision_source" not in cases[0]


def test_final_filter_blocks_reasoning_leakage_in_test_input() -> None:
    result = filter_invalid_final_cases(
        [
            {
                "id": "TC-001",
                "description": "valid case",
                "steps": ["open page"],
                "test_input": "需考虑午休？需求未明确，此处假设连续排",
                "expected_result": "shows saved plan",
                "priority": "P1",
            }
        ]
    )

    assert result == []


def test_append_merge_semantically_deduplicates_new_cases() -> None:
    existing = [
        {
            "id": "TC-001",
            "test_module": "学习计划",
            "description": "查看全部学习计划跳转",
            "steps": ["点击查看全部学习计划"],
            "test_input": "存在学习计划",
            "expected_result": "进入学习计划列表页",
            "priority": "P1",
            "priority_final": "P1",
        }
    ]
    new_cases = [
        {
            "id": "TC-002",
            "test_module": "学习计划",
            "description": "点击查看全部学习计划后跳转列表",
            "steps": ["点击查看全部学习计划"],
            "test_input": "存在学习计划",
            "expected_result": "进入学习计划列表页",
            "priority": "P1",
            "priority_final": "P1",
        },
        {
            "id": "TC-003",
            "test_module": "学习进度",
            "description": "学习完成后更新进度",
            "steps": ["完成一节课"],
            "test_input": "课程可学习",
            "expected_result": "学习进度增加",
            "priority": "P0",
            "priority_final": "P0",
        },
    ]

    def _dedupe(cases):  # noqa: ANN001, ANN202
        return [dict(item) for item in cases]

    def _reorder(cases, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return [dict(item) for item in cases]

    merged = merge_cases_for_append(
        existing,
        new_cases,
        deduplicate_test_cases_fn=_dedupe,
        reorder_cases_by_closed_loop_fn=_reorder,
    )

    assert [item["id"] for item in merged] == ["TC-001", "TC-003"]


class _FakeDb:
    def __init__(self) -> None:
        self.entries: list[object] = []

    def add(self, item: object) -> None:
        self.entries.append(item)

    def commit(self) -> None:
        return None


def test_stream_persistence_enforce_mode_blocks_formal_generation_insert(monkeypatch) -> None:
    cases = _main_chain_cases()[:2]

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"}
            },
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "schedule workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 6,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "schedule workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "multi_pass",
        "multi_pass": True,
        "request_id": "req-stream-gate",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert any("execution_plan_failed" in item for item in output)
    assert any("persistence_gate" in str(getattr(item, "message", "")) for item in db.entries)
    assert not [item for item in db.entries if hasattr(item, "generated_result")]


def test_stream_persistence_blocks_case_quality_even_when_execution_plan_passes(monkeypatch) -> None:
    cases = _main_chain_cases()

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "stage_counts": {"primary": 120, "review": 89},
            "coverage": {"coverage_rate": 0.8, "total_rules": 10, "missing_rules": [], "missing_types": {}},
            "convergence_debug": {
                "final_count": 89,
                "candidate_count_before_review": 120,
                "review_selected_count": 89,
            },
            "generation_summary": {"final_count": 89, "min_acceptable_final": 104},
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"},
                "candidate_total": 120,
                "retained_total": 89,
                "final_scenario_duplicate_case_count": 41,
                "final_flow_misordered_count": 0,
            },
            "judge_summary": {"rejected_out_count": 44},
            "judge_decision_table": [
                {
                    "case_id": "TC-088",
                    "status": "REJECT",
                    "reject_reason": "semantic_duplicate:TC-087",
                    "signals": {"is_semantic_duplicate": True},
                }
            ],
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "schedule workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 149,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "schedule workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "multi_pass",
        "multi_pass": True,
        "request_id": "req-stream-case-quality-gate",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert any("LOW_QUALITY_GENERATED_CASES" in item for item in output)
    gate_entries = [str(getattr(item, "message", "")) for item in db.entries]
    assert any("final_count_below_min_acceptable" in item for item in gate_entries)
    assert any("judge_rejected_above_threshold" in item for item in gate_entries)
    assert any('"kind": "generation_summary"' in item for item in gate_entries)
    assert any('"kind": "judge_summary"' in item for item in gate_entries)
    assert any('"kind": "judge_decision_table"' in item for item in gate_entries)
    assert any('"kind": "generation_quality_ledger"' in item for item in gate_entries)
    assert any('"dominant_reason": "semantic_duplicate"' in item for item in gate_entries)
    assert not [item for item in db.entries if hasattr(item, "generated_result")]


def test_stream_persistence_does_not_mask_explicit_invalid_priority_final(monkeypatch) -> None:
    cases = _main_chain_cases()
    cases[0]["priority"] = "P1"
    cases[0]["priority_final"] = None

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"},
            },
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "schedule workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 149,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "schedule workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "multi_pass",
        "multi_pass": True,
        "request_id": "req-stream-explicit-invalid-priority",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert any("LOW_QUALITY_GENERATED_CASES" in item for item in output)
    error_lines = [item for item in output if "Error:" in item]
    assert any("priority_final_null_count=1" in item for item in error_lines)
    assert not any("commit_downstream_completion_missing" in item for item in error_lines)
    gate_entries = [str(getattr(item, "message", "")) for item in db.entries]
    assert any("priority_final_null_count=1" in item for item in gate_entries)
    assert not [item for item in db.entries if hasattr(item, "generated_result")]


def test_json_api_returns_execution_plan_failed_as_502(monkeypatch) -> None:
    monkeypatch.setattr(generate_routes, "get_owned_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.context_orchestrator,
        "assemble_context",
        lambda *args, **kwargs: {"diagnostics": {}},
    )
    monkeypatch.setattr(generate_routes, "log_workflow_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_routes, "log_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.test_generator,
        "generate_test_cases_json",
        lambda *args, **kwargs: {
            "error_code": "execution_plan_failed",
            "error_message": "execution plan gate failed",
            "final_status": "execution_plan_failed",
            "persistence_gate_failed": True,
            "failure_reasons": ["state_chain_conflict"],
            "metrics": {"state_conflict_count": 1},
            "state_conflicts": [{"reason": "state_not_connected"}],
        },
    )

    request = _TestGenRequest(requirement="req", project_id=1, expected_count=20)
    current_user = type("User", (), {"id": 1})()

    try:
        generate_routes.generate_tests(request=request, db=object(), current_user=current_user)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert int(exc.status_code) == 502
        detail = dict(exc.detail or {})
        assert detail["error_code"] == "execution_plan_failed"
        assert detail["failure_reasons"] == ["state_chain_conflict"]
        assert detail["metrics"]["state_conflict_count"] == 1
