from __future__ import annotations

from modules.testing.case_access import (
    case_field_alias_key_set,
    case_field_aliases,
    case_fields,
    case_flat_text,
    case_focus_text,
    case_id,
    case_priority,
    case_signature_text,
    case_step_lines,
    case_text_parts,
    case_text_list_field,
    case_text_list_value,
    case_text_value,
    case_steps,
    case_text_field,
)


def test_case_access_exposes_alias_metadata_without_duplicates() -> None:
    aliases = case_field_aliases("id", "caseId", "tcid")

    assert aliases[0] == "id"
    assert aliases.count("caseId") == 1
    assert aliases[-1] == "tcid"
    assert "id" in case_fields()

    alias_keys = case_field_alias_key_set()
    assert "caseId" in alias_keys
    assert "testSteps" in alias_keys
    assert "not_a_case_field" not in alias_keys


def test_case_access_reads_known_aliases() -> None:
    case = {
        "caseId": " TC-001 ",
        "testModule": " Course ",
        "title": " Save plan ",
        "expectedResult": " Saved ",
        "testInput": " Data ",
        "testSteps": [" open page ", "", " click save "],
        "Priority": " P0 ",
    }

    assert case_id(case) == "TC-001"
    assert case_text_field(case, "test_module") == "Course"
    assert case_text_field(case, "description") == "Save plan"
    assert case_text_field(case, "expected_result") == "Saved"
    assert case_text_field(case, "test_input") == "Data"
    assert case_text_field(case, "priority") == "P0"
    assert case_steps(case) == ["open page", "click save"]


def test_case_step_lines_splits_text_steps_without_changing_case_steps() -> None:
    case = {"testSteps": "open page\nclick save;check toast；cleanup、done"}

    assert case_steps(case) == ["open page\nclick save;check toast；cleanup、done"]
    assert case_step_lines(case) == ["open page", "click save", "check toast", "cleanup", "done"]


def test_case_priority_reads_priority_and_final_priority_aliases() -> None:
    assert case_priority({"Priority": " p1 "}) == "P1"
    assert case_priority({"priorityFinal": " p0 "}) == "P0"
    assert case_priority({"priority": "P2", "priorityFinal": "P0"}) == "P2"
    assert case_priority({"priority": "P2", "priorityFinal": "P0"}, prefer_final=True) == "P0"
    assert case_priority({}, default="P2") == "P2"


def test_case_text_list_helpers_preserve_list_and_split_line_modes() -> None:
    assert case_text_list_value([" one ", {"action": "two"}, ""], split_lines=True) == ["one", "two"]
    assert case_text_list_value("one\ntwo", split_lines=True) == ["one", "two"]
    assert case_text_list_value("one\ntwo") == ["one\ntwo"]
    assert case_text_list_field({"precondition": "logged in\nseed data"}, "preconditions", split_lines=True) == [
        "logged in",
        "seed data",
    ]


def test_case_access_reads_chinese_aliases() -> None:
    case = {
        "用例编号": " TC-009 ",
        "标题": " Save course ",
        "所属模块": " Course ",
        "前置条件": [" logged in "],
        "操作步骤": [" open ", " save "],
        "测试输入": " valid data ",
        "预期结果": " saved ",
        "优先级": " P1 ",
    }

    assert case_id(case) == "TC-009"
    assert case_text_field(case, "description") == "Save course"
    assert case_text_field(case, "test_module") == "Course"
    assert case_text_field(case, "preconditions") == "logged in"
    assert case_steps(case) == ["open", "save"]
    assert case_text_field(case, "test_input") == "valid data"
    assert case_text_field(case, "expected_result") == "saved"
    assert case_text_field(case, "priority") == "P1"


def test_case_access_builds_shared_signature_and_focus_text() -> None:
    case = {
        "module": "Course",
        "description": "Save plan",
        "expected": "Saved",
        "input": "Data",
        "steps": ["Open", "Save"],
    }

    assert case_signature_text(case) == "course|save plan|saved|data"
    assert case_focus_text(case) == "Save plan Saved Data Open Save"
    assert case_focus_text(case, lower=True) == "save plan saved data open save"


def test_case_flat_text_keeps_requested_order_and_dedupes_aliases() -> None:
    case = {
        "testModule": "Course",
        "module": "Course",
        "title": "Save plan",
        "description": "Save plan",
        "expected_results": "Saved",
        "expected": "Saved",
        "steps": ["Open", "Save"],
    }

    fields = ("test_module", "module", "description", "title", "expected_result", "expected", "steps")

    assert case_text_parts(case, fields) == ["Course", "Save plan", "Saved", "Open", "Save"]
    assert case_flat_text(case, fields=fields, separator=" | ") == "Course | Save plan | Saved | Open | Save"


def test_case_access_flattens_nested_structured_values() -> None:
    case = {
        "testSteps": [
            {"action": "Open course page", "expect": "Page visible"},
            {"action": "Save", "expect": {"toast": "Saved"}},
        ],
        "expectedResult": {"status": "success", "message": ["Plan", "saved"]},
    }

    assert case_text_value({"outer": ["A", {"inner": "B"}]}) == "A B"
    assert case_steps(case) == ["Open course page Page visible", "Save Saved"]
    assert case_text_field(case, "expected_result") == "success Plan saved"
    assert case_flat_text(case, fields=("steps", "expected_result"), separator=" | ") == (
        "Open course page Page visible | Save Saved | success Plan saved"
    )
