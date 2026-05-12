"""
测试用例导出组件。
该组件负责把测试用例 JSON 转换为 Excel/CSV 二进制内容。
"""

from __future__ import annotations

import ast
import io
import re
from typing import Any

import pandas as pd
from openpyxl import Workbook

# 中文注释：固定列顺序，保持与历史导出样式一致（每个字段独立列）。
EXPORT_COLUMNS = [
    "id",
    "description",
    "test_module",
    "preconditions",
    "steps",
    "test_input",
    "expected_result",
    "priority",
]

# 中文注释：Excel XML 不允许的控制字符，需在写入前清洗。
_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")
_STEP_NUMBER_RE = re.compile(r"^\s*(?:\d+\s*[\.\)、)]|[（(]\s*\d+\s*[）)])\s*")


def _sanitize_excel_text(value: Any) -> str:
    """清洗单元格文本，避免 openpyxl 因非法字符报错。"""
    if value is None:
        return ""
    text = str(value)
    return _ILLEGAL_XML_RE.sub("", text)


def _format_steps_for_export(values: list[Any]) -> str:
    step_list = [str(s).strip() for s in values if str(s).strip()]
    return "\n".join(
        step if _STEP_NUMBER_RE.match(step) else f"{i}. {step}"
        for i, step in enumerate(step_list, 1)
    )


def _normalize_rows(json_data: list | dict) -> list[dict[str, Any]]:
    """把输入数据统一成可导出的字典列表，并处理 steps/preconditions 格式。"""
    data: Any = json_data
    if isinstance(json_data, dict):
        if "error" in json_data:
            data = [{"error": json_data["error"]}]
        else:
            data = [json_data]
    if not isinstance(data, list):
        data = [data]

    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            rows.append({"raw": str(item)})
            continue

        row = item.copy()

        pre = row.get("preconditions")
        if isinstance(pre, list):
            pre_list = [str(p).strip() for p in pre if str(p).strip()]
            row["preconditions"] = "\n".join(pre_list)
        elif isinstance(pre, str) and pre.strip().startswith("[") and pre.strip().endswith("]"):
            try:
                val = ast.literal_eval(pre)
                if isinstance(val, list):
                    pre_list = [str(p).strip() for p in val if str(p).strip()]
                    row["preconditions"] = "\n".join(pre_list)
            except Exception:
                pass

        steps = row.get("steps")
        if isinstance(steps, list):
            row["steps"] = _format_steps_for_export(steps)
        elif isinstance(steps, str) and steps.strip().startswith("[") and steps.strip().endswith("]"):
            try:
                val = ast.literal_eval(steps)
                if isinstance(val, list):
                    row["steps"] = _format_steps_for_export(val)
            except Exception:
                pass

        rows.append(row)
    return rows


def _build_excel_bytes(rows: list[dict[str, Any]]) -> bytes:
    """使用 openpyxl 按固定列写 Excel，避免退化成单列 CSV。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Test Cases"

    ws.append(EXPORT_COLUMNS)
    for row in rows:
        excel_row = [_sanitize_excel_text(row.get(col, "")) for col in EXPORT_COLUMNS]
        ws.append(excel_row)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.read()


def convert_json_to_excel(json_data: list | dict) -> bytes:
    """
    将测试用例 JSON 转换为 Excel 字节流。
    优先返回 xlsx；仅在极端异常时回退 CSV。
    """
    rows = _normalize_rows(json_data)

    try:
        return _build_excel_bytes(rows)
    except Exception as e:
        # 中文注释：保留兜底，但显式打印异常，便于排查为何回退 CSV。
        print(f"convert_json_to_excel fallback to csv: {e}")
        df = pd.DataFrame(rows)
        # 中文注释：使用 utf-8-sig，减少 Excel 打开 CSV 的乱码风险。
        return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

