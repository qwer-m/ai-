from __future__ import annotations

from fastapi import HTTPException

from routers.automation import test_generation_generate_routes_json as generate_routes
from schemas.automation.test_generation import TestGenRequest


def _patch_generation_route(monkeypatch, generated_payload) -> None:
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
        lambda *args, **kwargs: generated_payload,
    )


def _call_generate_tests():
    request = TestGenRequest(requirement="req", project_id=1, expected_count=20)
    current_user = type("User", (), {"id": 1})()
    return generate_routes.generate_tests(request=request, db=object(), current_user=current_user)


def test_generate_tests_empty_result_raises_http_error(monkeypatch) -> None:
    _patch_generation_route(monkeypatch, [])

    try:
        _call_generate_tests()
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert int(exc.status_code) == 502
        detail = dict(exc.detail or {})
        assert detail.get("error_code") == "EMPTY_GENERATED_RESULT"
        assert detail.get("final_status") == "empty_result_failed"


def test_generate_tests_low_quality_result_raises_http_error(monkeypatch) -> None:
    _patch_generation_route(
        monkeypatch,
        {
            "error_code": "LOW_QUALITY_GENERATED_CASES",
            "error_message": "quality gate failed",
            "final_status": "quality_gate_failed",
            "quality_gate_failed": True,
            "failed_checks": [
                "priority_final_null_count=2",
                "non_assertable_expected_result_count=3",
                "truncated_text_count=1",
            ],
            "priority_final_null_count": 2,
            "invalid_priority_final_case_ids": ["TC-001", "TC-002"],
            "non_assertable_expected_result_count": 3,
            "truncated_text_count": 1,
            "non_assertable_case_ids": ["TC-003", "TC-004", "TC-005"],
            "truncated_case_ids": ["TC-006"],
        },
    )

    try:
        _call_generate_tests()
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert int(exc.status_code) == 502
        detail = dict(exc.detail or {})
        assert detail.get("error_code") == "LOW_QUALITY_GENERATED_CASES"
        assert detail.get("final_status") == "quality_gate_failed"
        assert bool(detail.get("quality_gate_failed")) is True
        assert int(detail.get("priority_final_null_count") or 0) == 2
        assert int(detail.get("non_assertable_expected_result_count") or 0) == 3
        assert int(detail.get("truncated_text_count") or 0) == 1


def test_generate_tests_non_empty_result_still_success(monkeypatch) -> None:
    _patch_generation_route(monkeypatch, [{"id": "TC-001", "description": "ok"}])

    result = _call_generate_tests()
    assert isinstance(result, list)
    assert len(result) == 1
