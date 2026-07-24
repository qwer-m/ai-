from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..control.reuse_risk_policy import extract_reuse_risks
from ..control.scoped_rule_semantics import is_scoped_requirement_rule
from .structured_context_split_helpers import (
    _biz_tag,
    _clip_text,
    _ordered_biz_keys,
    _safe_str,
)

_MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET = 8
_MAX_REQUIREMENT_SEMANTIC_SOURCE_CHUNKS = 96
_NON_SEMANTIC_SECTION_HEADERS = {
    "[parsed requirement evidence]",
    "[multimodal evidence alignment]",
    "[requirement understanding]",
}

_PENDING_REQUIREMENT_MARKERS = (
    "待确认",
    "待澄清",
    "待补充",
    "待补齐",
    "待定",
    "未明确",
    "未说明",
    "暂无说明",
    "需要确认",
    "需确认",
    "待产品确认",
    "待评审确认",
    "待讨论",
    "pending",
    "tbd",
    "to be confirmed",
    "to confirm",
    "open question",
    "assumption",
)
_REUSE_DECLARATION_MARKERS = (
    "复用",
    "沿用",
    "复刻",
    "继承",
    "同原",
    "原页面",
    "原模块",
    "已有模块",
    "既有模块",
    "已有页面",
    "既有页面",
    "共享页面",
    "共享模块",
    "共用页面",
    "reuse",
    "reused",
    "shared page",
    "shared module",
    "existing page",
    "existing module",
    "legacy page",
    "legacy module",
)
_HARD_FLOW_MARKERS = (
    "流程",
    "顺序",
    "先",
    "再",
    "然后",
    "之后",
    "进入",
    "跳转",
    "返回",
    "回首页",
    "回列表",
    "完成后",
    "完成才",
    "仅当",
    "才展示",
    "才显示",
    "展示",
    "显示",
    "->",
    "→",
    "=>",
    "next",
    "then",
    "after",
    "before",
    "return",
    "redirect",
)
_CONFIRMED_FACT_MARKERS = (
    "req-",
    "规则",
    "要求",
    "必须",
    "禁止",
    "仅",
    "只在",
    "完成后",
    "展示",
    "显示",
    "进入",
    "跳转",
    "返回",
    "选择",
    "按钮",
    "版本",
    "顺序",
    "复用",
    "沿用",
    "确认",
    "最终",
    "以",
    "为准",
)
def _is_bracket_section_header(line: str) -> bool:
    stripped = str(line or "").strip()
    return bool(stripped.startswith("[") and stripped.endswith("]") and len(stripped) <= 120)


def _strip_non_semantic_sections(text: str) -> str:
    lines: list[str] = []
    skipping = False
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", str(raw_line or "").strip())
        lowered = line.lower()
        if lowered in _NON_SEMANTIC_SECTION_HEADERS:
            skipping = True
            continue
        if _is_bracket_section_header(line):
            skipping = False
        if skipping:
            continue
        if " -> requirement score=" in lowered:
            continue
        if re.match(r"^-\s*\w+:\s*filename=.*\bstrategy=", line, flags=re.IGNORECASE):
            continue
        if lowered.startswith(("{", "}", '"version"', '"visual_facts"', '"aligned_evidence"')):
            continue
        lines.append(raw_line)
    return "\n".join(lines)


def _extract_requirement_semantic_fragments(text: str, limit: int = 32) -> list[str]:
    src = _strip_non_semantic_sections(str(text or "")).strip()
    if not src:
        return []

    output: list[str] = []
    seen: set[str] = set()

    def _push(raw: str) -> None:
        cleaned = re.sub(r"^\s*[-*•\d\.\)\(]+\s*", "", str(raw or "").strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) < 4:
            return
        lowered = cleaned.lower()
        if lowered.startswith("biz_key:") or lowered.startswith("test_module:") or lowered.startswith("priority:"):
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        output.append(cleaned[:220])

    for raw in re.split(r"[\n\r]+", src):
        _push(raw)
        if len(output) >= max(1, int(limit)):
            return output[: max(1, int(limit))]

    for raw in re.split(r"[。；;]+", src):
        _push(raw)
        if len(output) >= max(1, int(limit)):
            break

    return output[: max(1, int(limit))]


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in markers)


def _classify_requirement_fragment(fragment: str) -> dict[str, bool]:
    lowered = str(fragment or "").strip().lower()
    if not lowered:
        return {
            "confirmed": False,
            "pending": False,
            "reuse": False,
            "hard_flow": False,
            "scoped_rule": False,
        }

    pending = _contains_any_marker(lowered, _PENDING_REQUIREMENT_MARKERS)
    reuse = _contains_any_marker(lowered, _REUSE_DECLARATION_MARKERS)
    hard_flow = _contains_any_marker(lowered, _HARD_FLOW_MARKERS)
    scoped_rule = is_scoped_requirement_rule(lowered)
    confirmed = bool(
        (not pending)
        and (
            _contains_any_marker(lowered, _CONFIRMED_FACT_MARKERS)
            or reuse
            or hard_flow
            or re.search(r"\bREQ[-_\s]?\d+\b", fragment, flags=re.IGNORECASE)
        )
    )
    return {
        "confirmed": bool(confirmed and (not scoped_rule)),
        "pending": bool(pending),
        "reuse": bool(reuse),
        "hard_flow": bool(hard_flow and (not scoped_rule)),
        "scoped_rule": bool(scoped_rule),
    }


def _derive_reuse_risks(fragments: list[str]) -> list[str]:
    return extract_reuse_risks(*fragments, default_shared_flow=bool(fragments))


def _build_requirement_semantics_context(
    *,
    requirement: str,
    chunks: list[dict[str, Any]],
    current_biz_key: str,
    only_current_biz: bool,
    max_chars: int = 12000,
) -> tuple[str, dict[str, dict[str, list[str]]], dict[str, str]]:
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {
            "confirmed_facts": [],
            "scoped_rules": [],
            "pending_items": [],
            "reuse_declarations": [],
            "hard_flow_constraints": [],
            "reuse_risks": [],
        }
    )

    def _append_fragment(biz_key: str, fragment: str) -> None:
        flags = _classify_requirement_fragment(fragment)
        bucket = grouped[biz_key]
        if flags["confirmed"] and len(bucket["confirmed_facts"]) < _MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET:
            bucket["confirmed_facts"].append(fragment)
        if flags["scoped_rule"] and len(bucket["scoped_rules"]) < _MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET:
            bucket["scoped_rules"].append(fragment)
        if flags["pending"] and len(bucket["pending_items"]) < _MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET:
            bucket["pending_items"].append(fragment)
        if flags["reuse"] and len(bucket["reuse_declarations"]) < _MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET:
            bucket["reuse_declarations"].append(fragment)
        if flags["hard_flow"] and len(bucket["hard_flow_constraints"]) < _MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET:
            bucket["hard_flow_constraints"].append(fragment)

    for item in _extract_requirement_semantic_fragments(requirement, limit=48):
        _append_fragment(current_biz_key, item)

    for chunk in chunks[:_MAX_REQUIREMENT_SEMANTIC_SOURCE_CHUNKS]:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue
        doc_type = _safe_str(chunk.get("doc_type") or metadata.get("doc_type"), "unknown").lower()
        text = str(chunk.get("chunk_text") or "").strip()
        if not text or ("requirement" not in doc_type and "需求" not in doc_type):
            continue
        for item in _extract_requirement_semantic_fragments(text, limit=12):
            _append_fragment(biz_key, item)

    normalized: dict[str, dict[str, list[str]]] = {}
    for biz_key, buckets in grouped.items():
        deduped: dict[str, list[str]] = {}
        for bucket_name, items in buckets.items():
            seen: set[str] = set()
            output: list[str] = []
            for item in items:
                cleaned = str(item or "").strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                output.append(cleaned)
                if len(output) >= _MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET:
                    break
            deduped[bucket_name] = output

        reuse_fragments = [
            *deduped.get("reuse_declarations", []),
            *deduped.get("hard_flow_constraints", []),
            *deduped.get("confirmed_facts", []),
        ]
        deduped["reuse_risks"] = _derive_reuse_risks(reuse_fragments) if deduped.get("reuse_declarations") else []
        if any(deduped.values()):
            normalized[biz_key] = deduped

    if not normalized:
        return "(empty)", {}, {}

    counts = {
        biz_key: sum(len(items) for bucket_name, items in bucket.items() if bucket_name != "reuse_risks")
        for biz_key, bucket in normalized.items()
    }
    biz_order = _ordered_biz_keys(counts, current_biz_key)
    scoped_map: dict[str, str] = {}
    full_lines: list[str] = ["[Requirement Semantics - grouped by biz_key]"]

    section_titles = (
        ("confirmed_facts", "Confirmed Facts"),
        ("scoped_rules", "Scoped Rules"),
        ("pending_items", "Pending / Open Questions"),
        ("reuse_declarations", "Reuse Declarations"),
        ("hard_flow_constraints", "Hard Flow Constraints"),
        ("reuse_risks", "Reuse Risks"),
    )

    for biz_key in biz_order:
        bucket = normalized.get(biz_key, {})
        block_lines = [f"### biz_key: {biz_key} ({_biz_tag(biz_key, current_biz_key)})"]
        for bucket_name, title in section_titles:
            items = list(bucket.get(bucket_name) or [])
            if not items:
                continue
            block_lines.append("")
            block_lines.append(f"#### {title}")
            block_lines.extend([f"* {item}" for item in items])
        block = "\n".join(block_lines).strip()
        scoped_map[biz_key] = _clip_text(f"[Requirement Semantics - grouped by biz_key]\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(block_lines)

    return _clip_text("\n".join(full_lines), max_chars) or "(empty)", normalized, scoped_map
