from __future__ import annotations

from modules.test_generation_components.postprocess import json_normalizer, json_processing, json_validator


def test_json_normalizer_reuses_validator_ordering_helpers() -> None:
    assert json_normalizer._CASE_KIND_ORDER is json_validator._CASE_KIND_ORDER
    assert json_normalizer.infer_case_kind is json_validator.infer_case_kind
    assert json_normalizer.extract_module_order_from_cases is json_validator.extract_module_order_from_cases
    assert json_normalizer.reorder_cases_by_closed_loop is json_validator.reorder_cases_by_closed_loop


def test_normalize_json_structure_uses_shared_case_alias_registry() -> None:
    normalized = json_normalizer.normalize_json_structure(
        [
            {
                "\u7528\u4f8b\u7f16\u53f7": "7",
                "\u6807\u9898": "create plan",
                "\u6240\u5c5e\u6a21\u5757": "schedule",
                "\u524d\u7f6e\u6761\u4ef6": ["logged in"],
                "\u64cd\u4f5c\u6b65\u9aa4": ["open planner", "save"],
                "\u6d4b\u8bd5\u8f93\u5165": "valid plan",
                "\u9884\u671f\u7ed3\u679c": "plan saved",
                "\u4f18\u5148\u7ea7": "HIGH",
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
