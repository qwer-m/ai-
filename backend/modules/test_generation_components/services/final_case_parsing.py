"""Parse human-final test case payloads into normalized case dictionaries."""

from __future__ import annotations

import csv
import io
import json
import re
from io import StringIO
from typing import Any

from ..postprocess.case_access import (
    case_field_alias_key_set,
    case_fields,
    case_value,
)

_CASE_FIELDS = case_fields()
_CASE_FIELD_ALIAS_KEYS = case_field_alias_key_set()


def parse_test_cases_payload(raw: Any) -> list[dict[str, Any]]:
    """Parse JSON/CSV/plain payloads into normalized case dictionaries."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [_normalize_case_dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("cases", "test_cases", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [_normalize_case_dict(item) for item in value if isinstance(item, dict)]
        return [_normalize_case_dict(raw)]

    text = str(raw or "").strip()
    if not text:
        return []
    parsed = _parse_json_cases(text)
    if parsed:
        return parsed
    parsed = _parse_html_table_cases(text)
    if parsed:
        return parsed
    parsed = _parse_csv_cases(text)
    if parsed:
        return parsed
    return []


def parse_test_cases_spreadsheet_bytes(filename: str, content_bytes: bytes) -> list[dict[str, Any]]:
    """Parse uploaded spreadsheet bytes directly into normalized test cases."""
    lowered = str(filename or "").lower()
    if not lowered.endswith((".xlsx", ".xls")):
        return []

    header_markers = _case_table_header_markers()
    all_rows: list[list[str]] = []
    if lowered.endswith(".xlsx"):
        try:
            import openpyxl

            workbook = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    all_rows.append([_text(cell).strip() for cell in row])
        except Exception:
            all_rows = []

    if not all_rows:
        try:
            import pandas as pd

            sheets = pd.read_excel(io.BytesIO(content_bytes), sheet_name=None, header=None)
            for sheet in sheets.values():
                all_rows.extend(sheet.fillna("").astype(str).values.tolist())
        except Exception:
            return []

    return _parse_case_table_rows(all_rows, header_markers=header_markers)


def _parse_json_cases(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    return parse_test_cases_payload(parsed)


def _parse_csv_cases(text: str) -> list[dict[str, Any]]:
    try:
        reader = csv.DictReader(StringIO(text))
        rows = [dict(row) for row in reader if row]
    except Exception:
        return []
    if not rows:
        return []
    return [_normalize_case_dict(row) for row in rows if any(str(v or "").strip() for v in row.values())]


def _parse_html_table_cases(text: str) -> list[dict[str, Any]]:
    if "<table" not in text.lower():
        return []
    try:
        import pandas as pd

        tables = pd.read_html(StringIO(text))
    except Exception:
        return []

    parsed_cases: list[dict[str, Any]] = []
    header_markers = _case_table_header_markers()
    for table in tables:
        rows = table.fillna("").astype(str).values.tolist()
        parsed_cases.extend(_parse_case_table_rows(rows, header_markers=header_markers))
    return parsed_cases


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
        raw: dict[str, Any] = {}
        for header, value in zip(headers, row):
            key = _text(header).strip()
            cell_value = _text(value).strip()
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

