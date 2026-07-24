from __future__ import annotations

import json

from modules.test_generation_components.postprocess.streaming_case_keys import (
    candidate_identity_key,
    case_coverage_bucket,
    case_focus_score,
    case_priority_score,
    case_signature,
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

    signature = json.loads(case_signature(case))
    assert signature["test_module"] == "course"
    assert signature["description"] == "save plan"
    assert signature["expected_result"] == "saved"
    assert signature["test_input"] == "data"
    assert review_case_id(case) == "TC-001"


def test_case_signature_and_review_id_accept_alias_fields() -> None:
    case = {
        "caseId": " TC-002 ",
        "testModule": " Course ",
        "title": " Save plan ",
        "expectedResult": " Saved ",
        "testInput": " Data ",
    }

    signature = json.loads(case_signature(case))
    assert signature["test_module"] == "course"
    assert signature["description"] == "save plan"
    assert signature["expected_result"] == "saved"
    assert signature["test_input"] == "data"
    assert review_case_id(case) == "TC-002"


def test_case_priority_and_focus_scores() -> None:
    assert case_priority_score({"priority": "P0"}) == 3
    assert case_priority_score({"priorityFinal": "P0"}) == 3
    assert case_priority_score({"priority": "P2"}) == 1
    assert case_priority_score({"priority": "unknown"}) == 0

    case = {
        "description": "边界状态",
        "expected_result": "error is rejected",
        "steps": ["transition to failed state"],
    }
    assert case_focus_score(case) == 5


def test_candidate_identity_prefers_frozen_origin_key_after_reordering() -> None:
    case = {
        "id": "TC-001",
        "description": "create record",
        "test_module": "record",
        "expected_result": "record is created",
    }
    origin_key = candidate_identity_key(case)
    reordered = {
        **case,
        "id": "TC-099",
        "origin_case_id": "TC-001",
        "origin_candidate_key": origin_key,
        "blocking": True,
    }

    assert candidate_identity_key(reordered) == origin_key


def test_case_coverage_bucket_prioritizes_exception_boundary_state_risk() -> None:
    assert case_coverage_bucket({"test_module": "M", "description": "失败"}) == "m|exception"
    assert case_coverage_bucket({"test_module": "M", "description": "最大值"}) == "m|boundary"
    assert case_coverage_bucket({"test_module": "M", "description": "状态流转"}) == "m|state"
    assert case_coverage_bucket({"test_module": "M", "description": "权限校验"}) == "m|risk"
    assert case_coverage_bucket({"description": "normal path"}) == "general|happy"


def test_case_coverage_bucket_accepts_alias_module_field() -> None:
    assert case_coverage_bucket({"module": "Alias Module", "description": "normal path"}) == "alias module|happy"
