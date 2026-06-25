from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..postprocess.case_access import (
    case_id as case_access_id,
    case_text_field,
)


_VALID_PRIORITIES = {"P0", "P1", "P2"}
_PRIORITY_ORDER = ("P0", "P1", "P2")

_MAX_BIZ_GROUPS = 8
_MAX_CASES_PER_BUCKET = 5
_MAX_REQUIREMENTS_PER_BIZ = 8
_MAX_SUPPLEMENTS_PER_BIZ = 6
_MAX_SUPPLEMENT_CHARS = 220

def _clip_text(text: str, max_chars: int) -> str:
    """中文注释：统一裁剪文本长度，防止提示词无限膨胀。"""
    value = str(text or "").strip()
    if not value:
        return ""
    return value if len(value) <= max_chars else value[:max_chars]


def _safe_str(value: Any, default: str) -> str:
    """中文注释：空值回退为默认值。"""
    text = str(value or "").strip()
    return text or default


def _normalize_priority(value: Any) -> str:
    """中文注释：优先级统一为 P0/P1/P2，非法值回退 P2。"""
    priority = _safe_str(value, "P2").upper()
    return priority if priority in _VALID_PRIORITIES else "P2"


def _biz_tag(biz_key: str, current_biz_key: str) -> str:
    return "当前业务" if biz_key == current_biz_key else "参考"


def _ordered_biz_keys(counts: dict[str, int], current_biz_key: str) -> list[str]:
    return sorted(
        counts.keys(),
        key=lambda key: (0 if key == current_biz_key else 1, -int(counts.get(key) or 0), key),
    )[:_MAX_BIZ_GROUPS]


def _extract_chunks_from_rag_result(rag_result: Any) -> list[dict[str, Any]]:
    """中文注释：优先从 RAG debug 中提取最终 chunk 列表。"""
    if not isinstance(rag_result, dict):
        return []
    debug = rag_result.get("debug") if isinstance(rag_result.get("debug"), dict) else {}
    if not isinstance(debug, dict):
        return []
    for key in ("final_chunks", "diverse_chunks", "dedup_chunks", "rerank_top"):
        value = debug.get(key)
        if isinstance(value, list) and value:
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_chunks_from_context_text(kb_context: str) -> list[dict[str, Any]]:
    """中文注释：当缺失 debug chunks 时，从文本降级切片。"""
    text = str(kb_context or "").strip()
    if not text:
        return []
    pattern = re.compile(r"--- Relevant Knowledge:\s*(.*?)\s*\((.*?)\)\s*---\s*\n", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [
            {
                "filename": "unknown",
                "doc_type": "unknown",
                "chunk_text": text,
                "biz_key": "unknown",
                "module": "unknown",
            }
        ]
    chunks: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunks.append(
            {
                "filename": _safe_str(match.group(1), "unknown"),
                "doc_type": _safe_str(match.group(2), "unknown"),
                "chunk_text": text[start:end].strip(),
                "biz_key": "unknown",
                "module": "unknown",
            }
        )
    return chunks


def _collect_biz_counts_from_chunks(chunks: list[dict[str, Any]]) -> dict[str, int]:
    """中文注释：统计补充上下文中的 biz_key 分布。"""
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        counts[biz_key] += 1
    return dict(counts)


def _resolve_current_biz_key(
    *, explicit_current_biz_key: str, testcase_counts: dict[str, int], supplement_counts: dict[str, int]
) -> str:
    """中文注释：优先显式 biz_key，缺失时按上下文频次推断。"""
    explicit = _safe_str(explicit_current_biz_key, "unknown")
    if explicit != "unknown":
        return explicit
    merged: dict[str, int] = defaultdict(int)
    for source in (testcase_counts, supplement_counts):
        for key, count in source.items():
            biz_key = _safe_str(key, "unknown")
            if biz_key == "unknown":
                continue
            merged[biz_key] += int(count or 0)
    if not merged:
        return "unknown"
    return sorted(merged.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _normalize_case(case: dict[str, Any], index: int) -> dict[str, str]:
    """中文注释：统一 testcase 字段并兜底缺省值。"""
    metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    return {
        "id": _safe_str(case_access_id(case), f"TC-AUTO-{index:03d}"),
        "description": _clip_text(_safe_str(case_text_field(case, "description"), ""), 160),
        "biz_key": _safe_str(case.get("biz_key") or metadata.get("biz_key"), "unknown"),
        "test_module": _safe_str(case_text_field(case, "test_module"), "未分类模块"),
        "priority": _normalize_priority(case_text_field(case, "priority")),
    }
