from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import Evaluation, KnowledgeDocument, TestGenerationComparison, User
from core.processing.file_processing import is_image_filename, parse_file_bytes, parse_image_bytes_with_fallback
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from routers.orchestration.evaluation_shared import (
    build_source_key,
    get_owned_project,
    is_attachment_ocr_ok,
    normalize_source_title,
    source_filename,
)

router = APIRouter()

def _normalize_metric_value(value: Any) -> Optional[float]:
    """将指标值标准化到 0~1 区间，无法解析时返回 None。"""
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            try:
                return max(0.0, min(1.0, float(text[:-1]) / 100.0))
            except ValueError:
                return None
        try:
            value = float(text)
        except ValueError:
            return None

    if not isinstance(value, (int, float)):
        return None

    number = float(value)
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return round(number, 6)


def _extract_first_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    """从文本中提取首个 JSON 对象，兼容 ```json 代码块。"""
    if not raw_text:
        return None

    text = raw_text.strip()
    block = re.search(r"```json\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if not block:
        block = re.search(r"```\s*([\s\S]*?)\s*```", text)
    if block and block.group(1):
        text = block.group(1).strip()

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[i:])
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _pick_metric_value(candidates: dict[str, Any], keys: list[str]) -> Optional[float]:
    for key in keys:
        if key in candidates:
            value = _normalize_metric_value(candidates.get(key))
            if value is not None:
                return value
    return None
