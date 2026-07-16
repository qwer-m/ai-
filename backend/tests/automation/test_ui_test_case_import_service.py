from __future__ import annotations

from io import BytesIO

import openpyxl

from modules.automation_components.services.ui_test_case_import_service import UITestCaseImportService
from modules.test_generation_components.services.final_case_parsing import parse_test_cases_payload


def test_csv_scans_metadata_before_real_header_and_preserves_multiline_steps() -> None:
    csv_content = """导出时间,2026-07-15
项目,天天练
用例ID,用例标题,前置条件,测试步骤,预期结果,优先级
TC-001,游客登录,已清理应用存储,"1. 点击手机登录
2. 关闭登录弹窗",进入游客内容界面,P0
""".strip()

    cases = parse_test_cases_payload(csv_content)

    assert len(cases) == 1
    assert cases[0]["id"] == "TC-001"
    assert cases[0]["description"] == "游客登录"
    assert cases[0]["steps"] == "1. 点击手机登录\n2. 关闭登录弹窗"


def test_import_real_xlsx_bytes_preserves_case_id_and_step_lines() -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "测试用例"
    sheet.append(["天天练测试用例"])
    sheet.append(["用例ID", "用例标题", "前置条件", "测试步骤", "预期结果", "优先级"])
    sheet.append([
        "TC-001",
        "游客登录",
        "已清理应用存储",
        "1. 点击手机登录\n2. 关闭登录弹窗",
        "进入游客内容界面",
        "P0",
    ])
    payload = BytesIO()
    workbook.save(payload)

    result = UITestCaseImportService(db=None).parse(
        filename="天天练测试用例.xlsx",
        content=payload.getvalue(),
        user_id=1,
    )

    assert result["case_count"] == 1
    assert result["parse_strategy"] == "spreadsheet_rows"
    assert result["cases"][0]["id"] == "TC-001"
    assert result["cases"][0]["steps"] == ["1. 点击手机登录", "2. 关闭登录弹窗"]
