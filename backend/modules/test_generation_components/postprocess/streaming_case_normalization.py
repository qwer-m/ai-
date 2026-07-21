from __future__ import annotations

import re
from typing import Any


def normalize_priority_value(value: str) -> str:
    text = str(value or "").strip().upper()
    if text in {"P0", "P1", "P2"}:
        return text
    return "P2"


def normalize_steps(steps: Any) -> list[str]:
    if not isinstance(steps, list):
        return []
    normalized: list[str] = []
    for raw_step in steps:
        text = str(raw_step or "").strip()
        if not text:
            continue
        text = re.sub(r"^\s*(?:step\s*)?\d+\s*[\.\):、\-]*\s*", "", text, flags=re.IGNORECASE)
        text = text.strip()
        if not text:
            continue
        normalized.append(text)
    if not normalized:
        return []
    return [f"{idx}. {step}" for idx, step in enumerate(normalized, start=1)]


def strip_step_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+\.\s*", "", str(text or "").strip()).strip()


def strip_validation_prefix(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^\s*(?:验证|校验|检查)\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:verify|validate|check)\b[:：\s-]*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def is_placeholder_expected_result(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return True
    compact = re.sub(r"\s+", "", normalized)
    placeholder_signals = (
        "execution succeeds and result is as configured",
        "result is as configured",
        "as configured",
        "expected result",
        "expectedresult",
        "result meets expectations",
        "works as expected",
        "same as above",
        "placeholder",
        "tbd",
        "todo",
        "待补充",
        "待确认",
        "占位",
        "按配置",
        "符合预期",
        "结果符合预期",
        "执行成功",
        "返回成功",
    )
    if any(signal in normalized for signal in placeholder_signals):
        return True
    return compact in {"成功", "通过", "正常", "ok", "okay"}
