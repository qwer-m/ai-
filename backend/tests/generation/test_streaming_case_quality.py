from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_case_quality import (
    final_quality_drop_reason,
    is_low_quality,
    low_quality_reason,
    quality_drop_detail,
    record_low_quality_drop,
    strip_case_meta_list,
)


def _valid_case() -> dict[str, object]:
    return {
        "id": "TC-001",
        "test_module": "课程排课",
        "description": "保存课程排课",
        "expected_result": "系统提示保存成功",
        "priority": "P1",
        "steps": ["1. 填写课程", "2. 保存"],
        "preconditions": ["已登录"],
    }


def test_low_quality_reason_accepts_valid_case() -> None:
    case = _valid_case()

    assert low_quality_reason(case) == ""
    assert not is_low_quality(case)


def test_low_quality_reason_accepts_alias_fields() -> None:
    case = {
        "caseId": "TC-ALIAS",
        "testModule": "Course scheduling",
        "title": "Save course scheduling",
        "expectedResult": "System shows save success message",
        "Priority": "P1",
        "testSteps": ["1. Fill course", "2. Save"],
        "prerequisites": ["Teacher has logged in"],
    }

    assert low_quality_reason(case) == ""
    assert not is_low_quality(case)


def test_low_quality_reason_accepts_final_priority_alias_when_priority_missing() -> None:
    case = {
        **_valid_case(),
        "priority": "",
        "finalPriority": "P1",
    }

    assert low_quality_reason(case) == ""


def test_low_quality_reason_reports_first_structural_failure() -> None:
    cases = [
        ({**_valid_case(), "description": "短"}, "description_too_short"),
        ({**_valid_case(), "test_module": ""}, "missing_test_module"),
        ({**_valid_case(), "expected_result": ""}, "missing_expected_result"),
        ({**_valid_case(), "priority": "P9"}, "invalid_priority"),
        ({**_valid_case(), "steps": []}, "missing_steps"),
        ({**_valid_case(), "preconditions": []}, "missing_preconditions"),
    ]

    for case, reason in cases:
        assert low_quality_reason(case) == reason
        assert is_low_quality(case)


def test_quality_drop_detail_truncates_large_text_fields() -> None:
    case = {
        "id": "TC-009",
        "test_module": "课程",
        "priority_final": "P0",
        "description": "a" * 300,
        "expected_result": "b" * 300,
    }

    detail = quality_drop_detail(case, reason="bad", stage="stage")

    assert detail["case_id"] == "TC-009"
    assert detail["priority"] == "P0"
    assert len(detail["description"]) == 240
    assert len(detail["expected_result"]) == 240


def test_quality_drop_detail_uses_alias_fields() -> None:
    case = {
        "caseId": "TC-ALIAS",
        "testModule": "Course scheduling",
        "priorityFinal": "P0",
        "title": "Save course scheduling",
        "expectedResult": "System shows save success message",
    }

    detail = quality_drop_detail(case, reason="bad", stage="stage")

    assert detail["case_id"] == "TC-ALIAS"
    assert detail["test_module"] == "Course scheduling"
    assert detail["priority"] == "P0"
    assert detail["description"] == "Save course scheduling"
    assert detail["expected_result"] == "System shows save success message"


def test_record_low_quality_drop_appends_standard_detail() -> None:
    details: list[dict[str, object]] = []
    case = {"id": "TC-010", "description": "短", "priority": "P2"}

    record_low_quality_drop(details, case, reason="description_too_short", stage="initial")

    assert details == [
        {
            "stage": "initial",
            "reason": "description_too_short",
            "case_id": "TC-010",
            "test_module": "",
            "priority": "P2",
            "description": "短",
            "expected_result": "",
        }
    ]


def test_final_quality_drop_reason_keeps_hard_invalid_expected_quality_marker() -> None:
    assert final_quality_drop_reason({"expected_result_quality": "invalid_case"}) == "expected_result_quality:invalid_case"


def test_final_quality_drop_reason_does_not_trust_stale_expected_quality_marker() -> None:
    assert (
        final_quality_drop_reason(
            {
                "expected_result_quality": "non_assertable",
                "expected_result": "默认每节2小时，一天最多只能设置5节，第6节无法添加或提示超出限制",
            }
        )
        == ""
    )
    assert (
        final_quality_drop_reason(
            {
                "expected_result_quality": "truncated",
                "expected_result": "系统提示保存成功",
            }
        )
        == ""
    )
    assert (
        final_quality_drop_reason(
            {
                "expected_result_quality": "non_assertable",
                "expected_result": "result is as configured",
            }
        )
        == "expected_result_quality:non_assertable"
    )
    assert (
        final_quality_drop_reason(
            {
                "expected_result_quality": "truncated",
                "expected_result": "操作后应正常展",
            }
        )
        == "expected_result_quality:truncated"
    )


def test_final_quality_drop_reason_detects_reasoning_truncation_and_non_assertable_text() -> None:
    assert final_quality_drop_reason({"expected_result": "需求未明确时先按默认配置"}) == "reasoning_leakage"
    assert final_quality_drop_reason({"expected_result": "操作后应正常展"}) == "truncated_text"
    assert final_quality_drop_reason({"expected_result": "result is as configured"}) == "non_assertable_expected_result"
    assert final_quality_drop_reason({"expectedResult": "result is as configured"}) == "non_assertable_expected_result"
    assert final_quality_drop_reason({"expected_result": "系统提示“课程保存成功”"}) == ""


def test_strip_case_meta_list_removes_debug_fields_and_promotes_final_priority() -> None:
    cases = [
        {
            "id": "TC-001",
            "priority": "P2",
            "priority_final": "P0",
            "priority_score": 99,
            "meta": {"debug": True},
            "description": "保存课程排课",
        },
        "bad",
    ]

    stripped = strip_case_meta_list(cases)  # type: ignore[arg-type]

    assert stripped == [{"id": "TC-001", "priority": "P0", "priority_final": "P0", "description": "保存课程排课"}]


def test_strip_case_meta_list_promotes_final_priority_alias() -> None:
    cases = [
        {
            "id": "TC-ALIAS",
            "priority": "P2",
            "finalPriority": "P0",
            "description": "Save course scheduling",
        }
    ]

    stripped = strip_case_meta_list(cases)

    assert stripped == [
        {
            "id": "TC-ALIAS",
            "priority": "P0",
            "priority_final": "P0",
            "description": "Save course scheduling",
        }
    ]
