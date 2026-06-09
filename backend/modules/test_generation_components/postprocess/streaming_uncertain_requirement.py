from __future__ import annotations

import re
from typing import Any

from .streaming_case_normalization import normalize_priority_value


UNCERTAIN_SIGNALS = (
    "需教研确认",
    "需要讨论",
    "本期可以不做",
    "模型暂不支持",
    "待确认",
    "待讨论",
    "暂不支持",
    "to be confirmed",
    "need discussion",
    "optional this phase",
    "model not supported",
)


def extract_uncertain_requirement_tokens(requirement_text: str) -> set[str]:
    requirement_raw = str(requirement_text or "")
    lines = [line.strip() for line in re.split(r"[\r\n]+", requirement_raw) if str(line).strip()]
    uncertain_lines = [
        line for line in lines if any(signal in line for signal in UNCERTAIN_SIGNALS)
    ]
    tokens: set[str] = set()
    for line in uncertain_lines:
        line_clean = str(line or "")
        for signal in UNCERTAIN_SIGNALS:
            signal_text = str(signal or "").strip()
            if signal_text:
                line_clean = line_clean.replace(signal_text, " ")
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_\-]{2,}", line_clean.lower()):
            if len(token) >= 2:
                tokens.add(token)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_\-]{2,}", line.lower()):
            if len(token) >= 2:
                tokens.add(token)
    return tokens


def apply_uncertain_requirement_downgrade(
    cases: list[dict[str, Any]],
    *,
    requirement_text: str,
) -> list[dict[str, Any]]:
    uncertain_tokens = extract_uncertain_requirement_tokens(requirement_text)
    if not uncertain_tokens and not any(signal in str(requirement_text or "") for signal in UNCERTAIN_SIGNALS):
        return [dict(item) for item in cases if isinstance(item, dict)]

    output: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        updated = dict(case)
        text = " ".join(
            [
                str(updated.get("test_module") or ""),
                str(updated.get("description") or ""),
                str(updated.get("expected_result") or ""),
            ]
        ).lower()
        hit_uncertain = any(signal in text for signal in UNCERTAIN_SIGNALS)
        if not hit_uncertain and uncertain_tokens:
            hit_uncertain = any(token in text for token in uncertain_tokens)
        if hit_uncertain:
            updated["priority"] = "P2"
            expected_text = str(updated.get("expected_result") or "").strip()
            if expected_text and "可选/视配置" not in expected_text:
                updated["expected_result"] = f"{expected_text}（可选/视配置）"
        output.append(updated)
    return output


def enforce_uncertain_priority_floor(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        updated = dict(case)
        expected_text = str(updated.get("expected_result") or "").strip()
        if "可选/视配置" in expected_text and normalize_priority_value(str(updated.get("priority") or "")) != "P2":
            updated["priority"] = "P2"
        output.append(updated)
    return output
