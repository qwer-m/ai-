from __future__ import annotations

from modules.test_generation_components.postprocess import json_normalizer, json_processing, json_validator
from modules.test_generation_components.postprocess.result_postprocess import finalize_generated_cases


def test_json_normalizer_reuses_validator_ordering_helpers() -> None:
    assert json_normalizer._CASE_KIND_ORDER is json_validator._CASE_KIND_ORDER
    assert json_normalizer.infer_case_kind is json_validator.infer_case_kind
    assert json_normalizer.extract_module_order_from_cases is json_validator.extract_module_order_from_cases
    assert json_normalizer.reorder_cases_by_closed_loop is json_validator.reorder_cases_by_closed_loop


def test_normalize_json_structure_uses_shared_case_alias_registry() -> None:
    normalized = json_normalizer.normalize_json_structure(
        [
            {
                "用例编号": "7",
                "标题": "create plan",
                "所属模块": "schedule",
                "前置条件": ["logged in"],
                "操作步骤": ["open planner", "save"],
                "测试输入": "valid plan",
                "预期结果": "plan saved",
                "优先级": "HIGH",
            }
        ]
    )

    assert normalized == [
        {
            "id": "TC-007",
            "description": "create plan",
            "test_module": "schedule",
            "preconditions": ["logged in"],
            "steps": ["open planner", "save"],
            "test_input": "valid plan",
            "expected_result": "plan saved",
            "priority": "P0",
        }
    ]


def test_json_case_order_helpers_accept_alias_fields() -> None:
    cases = [
        {
            "caseId": "TC-001",
            "testModule": "Login",
            "title": "invalid login shows error",
            "expectedResult": "error message is shown",
            "Priority": "P0",
        },
        {
            "caseId": "TC-002",
            "module": "Payment",
            "title": "happy path payment success",
            "expectedResult": "payment succeeds",
            "Priority": "P1",
        },
    ]

    assert json_validator.infer_case_kind(cases[0]) == "exception_error"
    assert json_normalizer.infer_case_kind(cases[0]) == "exception_error"

    assert json_validator.extract_module_order_from_cases(cases) == ["Login", "Payment"]
    assert json_normalizer.extract_module_order_from_cases(cases) == ["Login", "Payment"]

    assert [case["caseId"] for case in json_validator.reorder_cases_by_closed_loop(cases, renumber_ids=False)] == [
        "TC-001",
        "TC-002",
    ]
    assert [case["caseId"] for case in json_normalizer.reorder_cases_by_closed_loop(cases, renumber_ids=False)] == [
        "TC-001",
        "TC-002",
    ]


def test_json_processing_keeps_safe_text_join_compatibility() -> None:
    assert json_processing._safe_text_join({"outer": ["A", {"inner": "B"}]}) == "A B"


def test_finalize_generated_cases_reapplies_execution_group_order_after_legacy_sort() -> None:
    cases = [
        {
            "id": "raw-display",
            "description": "display final result",
            "test_module": "A",
            "steps": ["open detail"],
            "expected_result": "detail displayed",
            "priority": "P1",
            "priority_final": "P1",
            "execution_group": "display",
            "execution_sequence": 3,
        },
        {
            "id": "raw-main",
            "description": "submit success",
            "test_module": "Z",
            "steps": ["submit"],
            "expected_result": "submitted",
            "priority": "P0",
            "priority_final": "P0",
            "execution_group": "main_smoke",
            "execution_sequence": 1,
        },
        {
            "id": "raw-permission",
            "description": "permission denied",
            "test_module": "B",
            "steps": ["open without permission"],
            "expected_result": "access denied",
            "priority": "P1",
            "priority_final": "P1",
            "execution_group": "permission",
            "execution_sequence": 2,
        },
    ]

    def legacy_sort(candidate_cases: list[dict], **_kwargs: object) -> list[dict]:
        return sorted((dict(item) for item in candidate_cases), key=lambda item: str(item["test_module"]))

    result = finalize_generated_cases(
        cases,
        start_id=5,
        clean_and_parse_json_fn=lambda raw: raw,
        normalize_json_structure_fn=lambda raw: raw,
        deduplicate_test_cases_fn=lambda raw: raw,
        reorder_cases_by_closed_loop_fn=legacy_sort,
    )

    assert [item["execution_group"] for item in result] == ["main_smoke", "permission", "display"]
    assert [item["id"] for item in result] == ["TC-005", "TC-006", "TC-007"]
    assert [item["execution_sequence"] for item in result] == [1, 2, 3]
    assert [item["presentation_order"] for item in result] == [3, 2, 1]
