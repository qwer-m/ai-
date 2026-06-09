from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_case_keys import (
    case_coverage_bucket,
    case_focus_score,
    case_priority_score,
    case_signature,
    dedupe_by_final_description,
    final_description_dedup_key,
    review_case_id,
)


def test_case_signature_and_review_id_are_stable() -> None:
    case = {
        "id": " TC-001 ",
        "test_module": " Course ",
        "description": " Save plan ",
        "expected_result": " Saved ",
        "test_input": " Data ",
    }

    assert case_signature(case) == "course|save plan|saved|data"
    assert review_case_id(case) == "TC-001"


def test_case_priority_and_focus_scores() -> None:
    assert case_priority_score({"priority": "P0"}) == 3
    assert case_priority_score({"priority": "P2"}) == 1
    assert case_priority_score({"priority": "unknown"}) == 0

    case = {
        "description": "边界状态",
        "expected_result": "error is rejected",
        "steps": ["transition to failed state"],
    }
    assert case_focus_score(case) == 5


def test_case_coverage_bucket_prioritizes_exception_boundary_state_risk() -> None:
    assert case_coverage_bucket({"test_module": "M", "description": "失败"}) == "m|exception"
    assert case_coverage_bucket({"test_module": "M", "description": "最大值"}) == "m|boundary"
    assert case_coverage_bucket({"test_module": "M", "description": "状态流转"}) == "m|state"
    assert case_coverage_bucket({"test_module": "M", "description": "权限校验"}) == "m|risk"
    assert case_coverage_bucket({"description": "normal path"}) == "general|happy"


def test_dedupe_by_final_description_keeps_first_and_returns_dropped_signatures() -> None:
    cases = [
        {"description": "保存  计划", "test_module": "A", "expected_result": "ok"},
        {"description": "保存 计划", "test_module": "B", "expected_result": "duplicate"},
        {"description": "提交计划", "test_module": "C", "expected_result": "ok"},
    ]

    kept, dropped = dedupe_by_final_description(cases)

    assert [final_description_dedup_key(item) for item in kept] == ["保存 计划", "提交计划"]
    assert dropped == {"b|保存 计划|duplicate|"}
