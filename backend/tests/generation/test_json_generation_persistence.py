from __future__ import annotations

from modules.test_generation_components.legacy.json_generation_persistence import (
    _resolved_execution_plan_payload,
    run_json_persistence_flow,
)
from modules.test_generation_components.postprocess.case_contract import (
    project_persistable_cases,
)


def _blueprint(*, source: str, trusted: bool = False) -> dict[str, object]:
    return {
        "id": "flow",
        "repository_source": source,
        "source_type": "human_reviewed" if trusted else "current_requirement_extracted",
        "trusted": trusted,
        "closure_declaration_complete": True,
        "steps": [{"id": "open"}, {"id": "submit"}],
    }


def test_persistence_uses_current_requirement_blueprint_declared_source() -> None:
    payload = _resolved_execution_plan_payload(
        {},
        [_blueprint(source="current_requirement_blueprint")],
    )

    assert payload == {"workflow_blueprint_source": "current_requirement_blueprint"}


def test_persistence_uses_trusted_repository_declared_source() -> None:
    payload = _resolved_execution_plan_payload(
        {},
        [_blueprint(source="workflow_blueprint_repository", trusted=True)],
    )

    assert payload == {"workflow_blueprint_source": "feedback_control_state"}


def test_persistence_does_not_invent_candidate_source_without_declared_blueprint() -> None:
    assert _resolved_execution_plan_payload({}, []) == {}
    assert _resolved_execution_plan_payload(
        {},
        [{"id": "incomplete", "steps": [{"id": "open"}]}],
    ) == {}


def test_persistence_keeps_review_execution_plan_as_source_of_truth() -> None:
    review_plan = {"workflow_blueprint_source": "current_requirement_blueprint", "linear_executable": True}

    assert _resolved_execution_plan_payload(
        {"execution_plan": review_plan},
        [_blueprint(source="workflow_blueprint_repository", trusted=True)],
    ) == review_plan


def _internal_case() -> dict[str, object]:
    return {
        "id": "TC-001",
        "description": "打开入口后进入详情页",
        "test_module": "内容区",
        "preconditions": ["用户已登录"],
        "steps": ["点击内容入口"],
        "test_input": "有效内容 ID",
        "expected_result": "详情页展示对应内容",
        "priority": "P1",
        "_semantic": {
            "workflow_stage_candidates": [
                {"workflow_id": "content_flow", "stage_id": "open_detail"}
            ]
        },
        "execution_group": "main_smoke",
    }


def _persistence_kwargs(*, db: object | None, normalize_priority_fn) -> dict[str, object]:
    def _unused(*args, **kwargs):
        raise AssertionError("该失败路径不应调用此依赖")

    return {
        "db": db,
        "active_db_session": False,
        "client": object(),
        "result": [_internal_case()],
        "raw_response_payload": [],
        "original_requirement": "点击内容入口进入详情页",
        "requirement": "点击内容入口进入详情页",
        "project_id": 2,
        "user_id": 1,
        "request_id": "request-public-contract",
        "normalized_generation_mode": "multi_pass",
        "multi_pass": True,
        "resolved_current_biz": "content",
        "doc_type": "requirement",
        "compress": True,
        "expected_count": 1,
        "kb_context": "",
        "context_result": {},
        "stage_logs": [],
        "coverage_check_payload": None,
        "feedback_control_diag_payload": {},
        "judge_summary_payload": {},
        "judge_decision_table_payload": [],
        "memory_diag": {},
        "system_prompt": "",
        "generation_summary_payload": {},
        "feedback_control_state": {},
        "final_cases_after_judge": [_internal_case()],
        "final_case_count": 1,
        "review_decision_summary_payload": {},
        "review_decision_table_payload": [],
        "convergence_payload": {},
        "candidate_total_before_judge": 1,
        "empty_result_guard_triggered": False,
        "empty_result_stage": "",
        "gen_diag_payload": {},
        "compression_event_payload": {},
        "log_entry_cls": _unused,
        "test_generation_cls": _unused,
        "count_unique_test_cases_fn": _unused,
        "build_context_compression_diagnostics_fn": _unused,
        "emit_pre_persist_generation_diagnostics_fn": _unused,
        "emit_post_persist_generation_diagnostics_fn": _unused,
        "emit_post_persist_coverage_audit_diagnostics_fn": _unused,
        "normalize_missing_priority_final_cases_fn": normalize_priority_fn,
        "merge_contract_quality_gate_fn": _unused,
        "summarize_persistable_case_contract_fn": _unused,
        "summarize_persistence_case_quality_gate_fn": _unused,
        "project_persistable_cases_fn": project_persistable_cases,
        "evaluate_persistence_gate_fn": _unused,
        "build_persistence_gate_diagnostic_fn": _unused,
        "build_coverage_diagnostics_fn": _unused,
        "coverage_diagnostics_enabled": False,
    }


def test_no_database_path_returns_only_public_case_contract() -> None:
    result = run_json_persistence_flow(
        **_persistence_kwargs(
            db=None,
            normalize_priority_fn=lambda cases, **kwargs: cases,
        )
    )

    assert len(result.result) == 1
    assert "_semantic" not in result.result[0]
    assert "execution_group" not in result.result[0]


def test_persistence_exception_path_returns_only_public_case_contract() -> None:
    def _raise_persistence_error(cases, **kwargs):
        raise RuntimeError("forced persistence failure")

    result = run_json_persistence_flow(
        **_persistence_kwargs(
            db=object(),
            normalize_priority_fn=_raise_persistence_error,
        )
    )

    assert len(result.result) == 1
    assert "_semantic" not in result.result[0]
    assert "execution_group" not in result.result[0]
