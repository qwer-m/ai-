from __future__ import annotations

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.test_generation_components.postprocess.streaming_case_quality import (
    final_quality_drop_reason,
    filter_final_quality_cases,
    filter_low_quality_cases_with_stats,
    is_low_quality,
    low_quality_reason,
    normalize_case_structure,
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


def _real_quality_case(case_id: str, description: str = "Save course schedule") -> dict[str, object]:
    return {
        "id": case_id,
        "test_module": "Course scheduling",
        "description": description,
        "expected_result": 'System shows "Save success" toast and record status is saved',
        "priority": "P1",
        "steps": ["Open course scheduling form", "Save course schedule"],
        "preconditions": ["Teacher has logged in"],
    }


def test_filter_final_quality_cases_ignores_non_dict_and_records_final_drop_detail() -> None:
    low_quality_drop_details: list[dict[str, object]] = []
    valid_case = _real_quality_case("TC-FINAL-KEEP")
    non_assertable_case = {
        **_real_quality_case("TC-FINAL-DROP"),
        "expected_result": "result is as configured",
    }

    filtered, drop_total = filter_final_quality_cases(
        [valid_case, "not-a-case", non_assertable_case],
        low_quality_drop_details,
        stage="final_quality_gate",
    )

    assert filtered == [valid_case]
    assert drop_total == 1
    assert low_quality_drop_details == [
        {
            "stage": "final_quality_gate",
            "reason": "non_assertable_expected_result",
            "case_id": "TC-FINAL-DROP",
            "test_module": "Course scheduling",
            "priority": "P1",
            "description": "Save course schedule",
            "expected_result": "result is as configured",
        }
    ]


def test_normalize_case_structure_fills_required_fields_and_normalizes_priority() -> None:
    normalized = normalize_case_structure(
        {
            "id": "TC-NORMALIZE",
            "test_module": "Course scheduling",
            "description": "Save course schedule",
            "priority": "urgent",
            "steps": ["1) Fill course name", "2) Save schedule"],
        }
    )

    assert normalized is not None
    assert normalized["steps"] == ["1. Fill course name", "2. Save schedule"]
    assert normalized["preconditions"] == [
        "User has logged in and can access module Course scheduling"
    ]
    assert normalized["test_input"] == "Fill course name"
    assert str(normalized["expected_result"]).strip()
    assert normalized["priority"] == "P2"


def test_filter_low_quality_cases_with_stats_counts_structure_and_weak_drops() -> None:
    weak_case = {
        "id": "TC-WEAK",
        "test_module": "Report",
        "description": "Review report columns",
        "priority": "P1",
        "steps": ["Inspect report columns"],
    }

    filtered, stats = filter_low_quality_cases_with_stats(
        [
            "not-a-case",
            {"id": "TC-NORMALIZE-FAILED", "test_module": "Report", "description": "bad"},
            weak_case,
            _real_quality_case("TC-KEEP"),
        ],
        requirement_text="",
        analyze_coverage_fn=analyze_coverage,
    )

    assert [case["id"] for case in filtered] == ["TC-KEEP"]
    assert stats["invalid_structure_dropped"] == 2
    assert stats["weak_case_dropped"] == 1
    assert stats["semantic_dedup_dropped"] == 0
    assert stats["total_dropped"] == 3
    assert [detail["reason"] for detail in stats["dropped_details"]] == [
        "non_dict_case",
        "normalize_failed",
        "missing_expected_result",
    ]
    assert [detail["stage"] for detail in stats["dropped_details"]] == [
        "initial_structure_filter",
        "initial_structure_filter",
        "initial_quality_filter",
    ]


def test_filter_low_quality_cases_with_stats_counts_semantic_dedup_in_total() -> None:
    duplicate_a = _real_quality_case("TC-DEDUP-A", "Save course schedule")
    duplicate_b = _real_quality_case("TC-DEDUP-B", "Save course schedule")
    unique_case = _real_quality_case("TC-UNIQUE", "Delete course schedule")
    unique_case["expected_result"] = 'System shows "Delete success" toast and record is removed'
    unique_case["steps"] = ["Open course scheduling list", "Delete course schedule"]

    filtered, stats = filter_low_quality_cases_with_stats(
        [duplicate_a, duplicate_b, unique_case],
        requirement_text="",
        analyze_coverage_fn=analyze_coverage,
    )

    assert len(filtered) == 2
    assert {case["id"] for case in filtered} == {"TC-DEDUP-A", "TC-UNIQUE"}
    assert stats["invalid_structure_dropped"] == 0
    assert stats["weak_case_dropped"] == 0
    assert stats["semantic_dedup_dropped"] == 1
    assert stats["total_dropped"] == 1
    assert stats["dropped_details"] == []
