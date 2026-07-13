from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..postprocess.case_access import case_text_field
from .structured_context_split_helpers import _biz_tag, _clip_text, _ordered_biz_keys, _safe_str


_MAX_SUPPLEMENTS_PER_BIZ = 6
_MAX_SUPPLEMENT_CHARS = 220
_MAX_REQUIREMENT_SEMANTIC_SOURCE_CHUNKS = 96


def _dedupe_ordered_texts(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = re.sub(r"\s+", "", text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _extract_requirement_module_order(
    *,
    chunks: list[dict[str, Any]],
    current_biz_key: str,
    only_current_biz: bool,
) -> dict[str, list[str]]:
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, list[str]] = defaultdict(list)

    for chunk in chunks[:_MAX_REQUIREMENT_SEMANTIC_SOURCE_CHUNKS]:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue

        doc_type = _safe_str(chunk.get("doc_type") or metadata.get("doc_type"), "unknown").lower()
        text = str(chunk.get("chunk_text") or "").strip()
        if text and "requirement" not in doc_type and "需求" not in doc_type and "REQ-" not in text:
            continue

        module = _safe_str(
            chunk.get("module")
            or chunk.get("test_module")
            or metadata.get("module")
            or metadata.get("test_module"),
            "",
        )
        if module:
            grouped[biz_key].append(module)

    return {biz_key: _dedupe_ordered_texts(items) for biz_key, items in grouped.items() if items}


def _extract_reference_module_order(
    *,
    existing_cases: list[dict[str, Any]] | None,
    current_biz_key: str,
    only_current_biz: bool,
) -> dict[str, list[str]]:
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, list[str]] = defaultdict(list)
    for case in existing_cases or []:
        if not isinstance(case, dict):
            continue
        metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
        biz_key = _safe_str(case.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue
        module = _safe_str(case_text_field(case, "test_module"), "")
        if module:
            grouped[biz_key].append(module)
    return {biz_key: _dedupe_ordered_texts(items) for biz_key, items in grouped.items() if items}


def _merge_module_order(
    *,
    requirement_order: dict[str, list[str]],
    reference_order: dict[str, list[str]],
    biz_key_order: list[str],
    current_biz_key: str,
) -> tuple[list[str], dict[str, list[str]], str]:
    by_biz: dict[str, list[str]] = {}
    source = "none"
    for biz_key in biz_key_order:
        ordered = _dedupe_ordered_texts([
            *(requirement_order.get(biz_key) or []),
            *(reference_order.get(biz_key) or []),
        ])
        if ordered:
            by_biz[biz_key] = ordered
            if source == "none":
                source = "requirement_document" if requirement_order.get(biz_key) else "reference_cases"

    current_order = by_biz.get(current_biz_key) or []
    if not current_order:
        for biz_key in biz_key_order:
            current_order = by_biz.get(biz_key) or []
            if current_order:
                break
    return current_order, by_biz, source


def _build_supplement_context(
    *,
    chunks: list[dict[str, Any]],
    current_biz_key: str,
    only_current_biz: bool,
    max_chars: int = 16000,
) -> tuple[str, dict[str, int], dict[str, str]]:
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, list[str]] = defaultdict(list)

    for chunk in chunks[:128]:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue
        filename = _safe_str(chunk.get("filename") or metadata.get("filename"), "unknown")
        snippet = _clip_text(str(chunk.get("chunk_text") or "").strip(), _MAX_SUPPLEMENT_CHARS)
        if not snippet:
            continue

        prefix = "Defect/Supplement"
        lowered = snippet.lower()
        if any(key in lowered for key in ("缺陷", "bug", "失败", "错误", "异常")):
            prefix = "Defect"
        elif any(key in lowered for key in ("建议", "优化", "补充")):
            prefix = "Suggestion"

        if len(grouped[biz_key]) < _MAX_SUPPLEMENTS_PER_BIZ:
            grouped[biz_key].append(f"* {prefix}: {snippet} (source: {filename})")

    counts = {key: len(items) for key, items in grouped.items() if items}
    if not counts:
        return "(empty)", {}, {}

    biz_order = _ordered_biz_keys(counts, current_biz_key)
    full_lines: list[str] = ["[Supplement - grouped by biz_key]"]
    scoped_map: dict[str, str] = {}
    for biz_key in biz_order:
        block_lines = [f"### biz_key: {biz_key} ({_biz_tag(biz_key, current_biz_key)})", ""]
        block_lines.extend(grouped.get(biz_key, []))
        block = "\n".join(block_lines).strip()
        scoped_map[biz_key] = _clip_text(f"[Supplement - grouped by biz_key]\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(block_lines)
    return _clip_text("\n".join(full_lines), max_chars) or "(empty)", counts, scoped_map


def _build_biz_key_isolation_log(
    *,
    current_biz_key: str,
    requirement_biz_counts: dict[str, int],
    testcase_biz_counts: dict[str, int],
    supplement_biz_counts: dict[str, int],
    mode: str,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for source_name, source_counts in (
        ("requirement_context", requirement_biz_counts),
        ("testcase_context", testcase_biz_counts),
        ("supplement_context", supplement_biz_counts),
    ):
        for biz_key, count in sorted(source_counts.items(), key=lambda item: (-int(item[1] or 0), item[0])):
            if biz_key == current_biz_key:
                continue
            if current_biz_key == "unknown" and biz_key == "unknown":
                continue
            violations.append({"source": source_name, "biz_key": biz_key, "count": int(count or 0), "tag": "reference"})
    return {
        "kind": "biz_key_isolation_check",
        "current_biz_key": current_biz_key,
        "requirement_biz_keys": sorted(requirement_biz_counts.keys()),
        "testcase_biz_keys": sorted(testcase_biz_counts.keys()),
        "supplement_biz_keys": sorted(supplement_biz_counts.keys()),
        "cross_biz_detected": bool(violations),
        "mode": mode,
        "violations": violations,
    }
