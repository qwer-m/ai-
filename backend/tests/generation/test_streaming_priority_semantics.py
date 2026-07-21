from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.test_generation_components.postprocess.streaming_priority_semantics import (
    apply_coverage_priority_semantics,
    coverage_priority_semantics_result,
)


OPTIONAL_MARKER = "".join(chr(codepoint) for codepoint in (0x53EF, 0x9009, 0x002F, 0x89C6, 0x914D, 0x7F6E))

REQUIREMENT = """
[Requirements - By Business Group]
### biz_key: learning_flow
* REQ-101: Student role must complete the main learning workflow from course entry to answer submission without blocking.
* REQ-102: Student resume after interruption must keep current lesson state and learning progress.
* REQ-103: Teacher report export is optional this phase and needs discussion.
"""


def _real_cases() -> list[Any]:
    return [
        {
            "id": "TC-flow-001",
            "test_module": "Learning workflow",
            "description": "Student role covers REQ-101 course entry answer submission main workflow.",
            "preconditions": ["student has logged in", "course is assigned"],
            "steps": ["open course as student", "submit answer", "finish lesson"],
            "test_input": "student_id=S001 course_id=C001",
            "expected_result": "Answer submission succeeds and learning progress is saved.",
            "priority": "P2",
            "workflow_role": "student",
            "business_semantics": {"biz_key": "learning_flow", "role": "student"},
            "workflow_transition": {"from": "course_detail", "to": "lesson_result"},
        },
        "bad-non-dict-case",
        {
            "id": "TC-export-weak",
            "test_module": "Teacher report",
            "description": "REQ-103 teacher report export button placement only.",
            "preconditions": ["teacher has logged in"],
            "steps": ["open report page", "check export button"],
            "test_input": "teacher_id=T001",
            "expected_result": f"Export button display follows product decision ({OPTIONAL_MARKER}).",
            "priority": "P0",
            "workflow_role": "teacher",
            "business_semantics": {"biz_key": "report_export", "role": "teacher"},
            "workflow_transition": {"from": "report_home", "to": "report_home"},
        },
    ]


def _case_by_id(cases: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    return next(case for case in cases if case.get("id") == case_id)


def test_coverage_priority_semantics_returns_usable_coverage_context() -> None:
    prioritized, coverage_context = coverage_priority_semantics_result(
        REQUIREMENT,
        _real_cases(),
        analyze_coverage_fn=analyze_coverage,
    )

    assert isinstance(coverage_context, dict)
    assert coverage_context["total_rules"] == 3
    assert set(coverage_context["covered_rules"]) >= {"REQ-101", "REQ-102", "REQ-103"}
    assert 0 <= coverage_context["coverage_rate"] <= 1
    assert isinstance(coverage_context["rule_diagnostics"], list)
    assert any(
        item.get("rule_id") == "REQ-101" and item.get("covered") is True
        for item in coverage_context["rule_diagnostics"]
    )
    assert {case["id"] for case in prioritized} == {"TC-flow-001", "TC-export-weak"}


def test_weak_optional_text_does_not_apply_a_hidden_priority_floor() -> None:
    prioritized, _coverage_context = coverage_priority_semantics_result(
        REQUIREMENT,
        _real_cases(),
        analyze_coverage_fn=analyze_coverage,
    )

    weak_case = _case_by_id(prioritized, "TC-export-weak")

    assert weak_case["model_priority_current"] == "P0"
    assert weak_case["priority_decision_source"] == "model_p0_guard_downgrade"
    assert weak_case["priority_final"] == "P1"
    assert weak_case["priority"] == "P1"
    assert weak_case["priority"] != "P0"


def test_apply_coverage_priority_semantics_filters_non_dict_cases_without_breaking_result() -> None:
    prioritized = apply_coverage_priority_semantics(
        REQUIREMENT,
        [*_real_cases(), 42, None],
        analyze_coverage_fn=analyze_coverage,
    )

    assert len(prioritized) == 2
    assert all(isinstance(case, dict) for case in prioritized)
    assert [case["id"] for case in prioritized] == ["TC-flow-001", "TC-export-weak"]


def test_flow_role_and_business_semantics_fields_are_preserved() -> None:
    prioritized, _coverage_context = coverage_priority_semantics_result(
        REQUIREMENT,
        _real_cases(),
        analyze_coverage_fn=analyze_coverage,
    )

    flow_case = _case_by_id(prioritized, "TC-flow-001")
    weak_case = _case_by_id(prioritized, "TC-export-weak")

    assert flow_case["workflow_role"] == "student"
    assert flow_case["business_semantics"] == {"biz_key": "learning_flow", "role": "student"}
    assert flow_case["workflow_transition"] == {"from": "course_detail", "to": "lesson_result"}
    assert weak_case["workflow_role"] == "teacher"
    assert weak_case["business_semantics"] == {"biz_key": "report_export", "role": "teacher"}
