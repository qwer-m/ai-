"""读取测试用例质量评估所需的人工执行表。"""

from __future__ import annotations

import io
import json
import re
from typing import Any

from .evaluation_case_fields import (
    case_field_alias_key_set,
    case_fields,
    case_value,
)

_CASE_FIELDS = case_fields()
_CASE_FIELD_ALIAS_KEYS = case_field_alias_key_set()


def parse_test_cases_spreadsheet_bytes(filename: str, content_bytes: bytes) -> list[dict[str, Any]]:
    """从工作簿字节中读取并规范化人工用例。"""
    lowered = str(filename or "").lower()
    if not lowered.endswith((".xlsx", ".xls")):
        return []

    header_markers = _case_table_header_markers()
    try:
        if lowered.endswith(".xlsx"):
            import openpyxl

            workbook = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True, data_only=True)
            try:
                result = []
                for sheet in workbook.worksheets:
                    # 部分真实工作簿错误声明 A1:A1，按存储的实际行读取。
                    sheet.reset_dimensions()
                    result.extend(_parse_case_table_rows(list(sheet.values), header_markers=header_markers))
                return result
            finally:
                workbook.close()
        import pandas as pd

        sheets = pd.read_excel(io.BytesIO(content_bytes), sheet_name=None, header=None)
        return [case for sheet in sheets.values()
                for case in _parse_case_table_rows(sheet.fillna("").values.tolist(), header_markers=header_markers)]
    except Exception:
        # 各格式读取器的解析错误统一由调用方按无有效用例处理。
        return []


def evaluation_spreadsheet_reference(filename: str, content: bytes) -> str:
    """保留人工用例的原始步骤和结果，仅从执行状态列判定跳过。"""
    rows = [row for row in parse_test_cases_spreadsheet_bytes(filename, content)
            if (row.get("description") or row.get("id")) and (row.get("steps") or row.get("expected_result"))]
    if not rows:
        raise ValueError("表格未识别到包含标题和步骤或预期结果的有效用例")
    statuses = {"PASS", "FAIL", "SKIP", "SKIPPED", "BLOCKED", "PENDING", "通过", "失败", "跳过", "未执行", "阻塞"}
    extra = {key for row in rows for key in row if key not in _CASE_FIELDS}
    columns = [key for key in extra if
               {str(row[key]).strip().upper() for row in rows if row.get(key)} <= statuses]
    entries = []
    for number, row in enumerate(rows, 1):
        values = [str(row[key]).strip().upper() for key in columns if row.get(key)]
        entries.append({"ref": f"H{number}", "skipped": bool(values) and all(
            value in {"SKIP", "SKIPPED", "跳过"} for value in values), "content": row})
    return json.dumps({"manual_cases": entries}, ensure_ascii=False, separators=(",", ":"))


def _case_table_header_markers() -> set[str]:
    return set(_CASE_FIELD_ALIAS_KEYS) | set(_CASE_FIELDS)


def _parse_case_table_rows(rows: list[list[Any]], *, header_markers: set[str] | None = None) -> list[dict[str, Any]]:
    markers = header_markers or _case_table_header_markers()
    header_index = -1
    for idx, row in enumerate(rows):
        non_empty = {_text(cell).strip() for cell in row if _text(cell).strip()}
        if len(non_empty & markers) >= 2:
            header_index = idx
            break
    if header_index < 0:
        return []

    headers = [_text(cell).strip() for cell in rows[header_index]]
    parsed_cases: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        if [_text(value) for value in row] == headers:
            continue
        raw: dict[str, Any] = {}
        for header, value in zip(headers, row):
            key = _text(header).strip()
            cell_value = _cell_text(value)
            if not key or key.lower().startswith("unnamed") or not cell_value:
                continue
            raw[key] = cell_value
        normalized = _normalize_case_dict(raw)
        if _text(normalized.get("description")) or _text(normalized.get("expected_result")):
            parsed_cases.append(normalized)
    return parsed_cases


def _normalize_case_dict(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for canonical in _CASE_FIELDS:
        value = case_value(item, canonical, None)
        if value not in (None, ""):
            result[canonical] = value
    for key, value in item.items():
        if key not in result and key not in _CASE_FIELD_ALIAS_KEYS:
            result[key] = value
    return result


def _text(raw: Any) -> str:
    if isinstance(raw, list):
        return " ".join(_text(item) for item in raw)
    if isinstance(raw, dict):
        return " ".join(_text(value) for value in raw.values())
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _cell_text(raw: Any) -> str:
    """保留步骤和前置条件单元格中的换行，仅清理首尾空白。"""
    if isinstance(raw, (list, dict)):
        return _text(raw)
    return str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()

