from __future__ import annotations

import json
from pathlib import Path

from modules.agent_platform.evaluation_case_fields import (
    case_field_alias_key_set,
    case_field_aliases,
    case_fields,
    case_value,
)
from modules.agent_platform.evaluation_spreadsheet import evaluation_spreadsheet_reference


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

    assert case_value(case, "id") == " TC-001 "
    assert case_value(case, "test_module") == " Course "
    assert case_value(case, "description") == " Save plan "
    assert case_value(case, "expected_result") == " Saved "
    assert case_value(case, "test_input") == " Data "
    assert case_value(case, "priority") == " P0 "
    assert case_value(case, "steps") == [" open page ", "", " click save "]


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

    assert case_value(case, "id") == " TC-009 "
    assert case_value(case, "description") == " Save course "
    assert case_value(case, "test_module") == " Course "
    assert case_value(case, "preconditions") == [" logged in "]
    assert case_value(case, "steps") == [" open ", " save "]
    assert case_value(case, "test_input") == " valid data "
    assert case_value(case, "expected_result") == " saved "
    assert case_value(case, "priority") == " P1 "


def test_evaluation_reads_real_exported_workbook() -> None:
    root = Path(__file__).resolve().parents[3]
    workbook = next((root / "output/qa/2026-11-19-observable-coverage").glob("*.xlsx"))

    rows = json.loads(evaluation_spreadsheet_reference(workbook.name, workbook.read_bytes()))["manual_cases"]

    assert len(rows) == 30
    assert all(row["content"]["steps"] and row["content"]["expected_result"] for row in rows)
