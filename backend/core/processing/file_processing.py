"""
文件解析模块。

提供三种入口：
1. parse_file_content: 处理 FastAPI UploadFile（在线请求场景）。
2. parse_file_bytes: 处理字节流（复用核心解析逻辑）。
3. parse_file_path: 处理本地文件路径（离线 Celery 场景）。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pandas as pd
import pypdf
from fastapi import UploadFile


def parse_file_bytes(
    filename: str,
    content_bytes: bytes,
    image_prompt: str = "OCR: Extract all text from this image.",
) -> str:
    """
    按扩展名解析文件内容。

    设计目标：同一份解析逻辑可同时服务同步上传和离线任务，避免两套实现漂移。
    """
    lowered_name = (filename or "").lower()
    text_content = ""

    try:
        if lowered_name.endswith(".pdf"):
            pdf_file = io.BytesIO(content_bytes)
            reader = pypdf.PdfReader(pdf_file)
            for page in reader.pages:
                text_content += (page.extract_text() or "") + "\n"

        elif lowered_name.endswith((".xls", ".xlsx")):
            import openpyxl

            excel_file = io.BytesIO(content_bytes)
            try:
                wb = openpyxl.load_workbook(excel_file, data_only=True)
                text_content = ""
                for sheet_name in wb.sheetnames:
                    ws = wb[sheet_name]
                    text_content += f"<h5>Sheet: {sheet_name}</h5>"
                    text_content += (
                        '<div class="table-responsive mb-4"><table class="table table-bordered '
                        'table-sm table-hover" style="border-collapse: collapse; min-width: 100%; '
                        'font-size: 0.9em;">'
                    )

                    merge_map: dict[tuple[int, int], tuple[int, int] | str] = {}
                    for merge_range in ws.merged_cells.ranges:
                        min_col, min_row = merge_range.min_col, merge_range.min_row
                        max_col, max_row = merge_range.max_col, merge_range.max_row
                        merge_map[(min_row, min_col)] = (
                            max_row - min_row + 1,
                            max_col - min_col + 1,
                        )
                        for row in range(min_row, max_row + 1):
                            for col in range(min_col, max_col + 1):
                                if row == min_row and col == min_col:
                                    continue
                                merge_map[(row, col)] = "skip"

                    for row_idx, row in enumerate(ws.iter_rows(), start=1):
                        text_content += "<tr>"
                        for col_idx, cell in enumerate(row, start=1):
                            if (row_idx, col_idx) in merge_map:
                                if merge_map[(row_idx, col_idx)] == "skip":
                                    continue
                                rowspan, colspan = merge_map[(row_idx, col_idx)]
                                value = str(cell.value) if cell.value is not None else ""
                                style = "vertical-align: middle; white-space: pre-wrap;"
                                if rowspan > 1 or colspan > 1:
                                    style += " background-color: #f8f9fa; font-weight: 500;"
                                text_content += (
                                    f'<td rowspan="{rowspan}" colspan="{colspan}" '
                                    f'style="{style}">{value}</td>'
                                )
                            else:
                                value = str(cell.value) if cell.value is not None else ""
                                text_content += f'<td style="white-space: pre-wrap;">{value}</td>'
                        text_content += "</tr>"
                    text_content += "</table></div>"
            except Exception as e:
                text_content = f"[Error reading Excel: {str(e)}]"

        elif lowered_name.endswith(".csv"):
            csv_file = io.BytesIO(content_bytes)
            try:
                df = pd.read_csv(csv_file)
                text_content = df.to_csv(index=False)
            except Exception as e:
                try:
                    text_content = content_bytes.decode("utf-8")
                except Exception:
                    text_content = f"[Error reading CSV: {str(e)}]"

        elif lowered_name.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")):
            try:
                # 当前 OCR 仍是占位实现，先保留行为兼容。
                base64.b64encode(content_bytes).decode("utf-8")
                text_content = f"[Image Content: {lowered_name}]"
            except Exception as e:
                text_content = f"[Error processing image: {str(e)}]"

        else:
            try:
                text_content = content_bytes.decode("utf-8")
            except Exception:
                text_content = f"[Unsupported file type: {lowered_name}]"
    except Exception as e:
        text_content = f"[Error parsing file: {str(e)}]"

    return text_content


async def parse_file_content(
    file: UploadFile,
    image_prompt: str = "OCR: Extract all text from this image.",
) -> str:
    """在线请求入口：解析 UploadFile。"""
    content_bytes = await file.read()
    return parse_file_bytes(file.filename or "", content_bytes, image_prompt=image_prompt)


def parse_file_path(file_path: str, image_prompt: str = "OCR: Extract all text from this image.") -> str:
    """离线任务入口：解析本地路径文件。"""
    path = Path(file_path)
    content_bytes = path.read_bytes()
    return parse_file_bytes(path.name, content_bytes, image_prompt=image_prompt)

