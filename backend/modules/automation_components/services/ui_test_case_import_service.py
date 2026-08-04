"""解析用户上传的真实测试用例文件，输出统一的 UI 自动化输入结构。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.processing.file_processing import parse_file_bytes
from modules.testing.case_access import (
    case_step_lines,
    case_text_field,
    case_text_list_field,
)
from modules.automation_components.services.test_case_file_parser import (
    parse_test_cases_payload,
    parse_test_cases_spreadsheet_bytes,
)


class UITestCaseImportService:
    """复用平台统一用例字段别名，避免为单个文档格式增加特调。"""

    SUPPORTED_SUFFIXES = {".xlsx", ".csv", ".json", ".txt", ".html", ".htm"}

    def __init__(self, db):
        self._db = db
        self.max_bytes = int(os.environ.get("UI_TEST_CASE_UPLOAD_MAX_BYTES", 20 * 1024 * 1024))

    def parse(self, *, filename: str, content: bytes, user_id: int) -> dict[str, Any]:
        safe_filename = Path(filename or "uploaded_cases").name
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            supported = "、".join(sorted(self.SUPPORTED_SUFFIXES))
            raise ValueError(f"不支持的测试用例文件类型：{suffix or '无扩展名'}；支持 {supported}")
        if not content:
            raise ValueError("上传的测试用例文件为空")
        if len(content) > self.max_bytes:
            raise ValueError(f"测试用例文件超过大小限制：{self.max_bytes // (1024 * 1024)} MB")

        strategy = "spreadsheet_rows"
        cases = parse_test_cases_spreadsheet_bytes(safe_filename, content)
        if not cases:
            strategy = "structured_text"
            parsed_text = parse_file_bytes(
                filename=safe_filename,
                content_bytes=content,
                db=self._db,
                user_id=user_id,
            )
            cases = parse_test_cases_payload(parsed_text)
        if not cases:
            raise ValueError("未从文件中识别到测试用例，请确认包含标题、步骤或预期结果等表头")

        normalized: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            description = case_text_field(case, "description")
            steps = case_step_lines(case)
            expected_result = case_text_field(case, "expected_result")
            if not description and not steps and not expected_result:
                continue
            source_id = case_text_field(case, "id")
            normalized.append(
                {
                    "key": f"case-{index}",
                    "source_index": index,
                    "id": source_id or f"CASE-{index:03d}",
                    "description": description or f"测试用例 {index}",
                    "test_module": case_text_field(case, "test_module"),
                    "preconditions": case_text_list_field(case, "preconditions", split_lines=True),
                    "steps": steps,
                    "test_input": case_text_field(case, "test_input"),
                    "expected_result": expected_result,
                    "priority": case_text_field(case, "priority").upper(),
                }
            )
        if not normalized:
            raise ValueError("测试用例文件中没有可转化的有效行")
        return {
            "filename": safe_filename,
            "parse_strategy": strategy,
            "case_count": len(normalized),
            "cases": normalized,
        }
