from __future__ import annotations

import os
import re
from collections import defaultdict
from typing import Any

from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.contracts.memory_fabric import MemoryFabric
from modules.memory_fabric.runtime.diagnostics import record_memory_read
from ..control.fact_profile_activation import (
    build_fact_profile,
    merge_fact_profile_control_state,
)
from ..control.feedback_control_state import FeedbackControlState
from ..control.project_profile_activation import (
    build_project_profile,
    merge_project_profile_control_state,
)
from ..postprocess.case_access import case_text_field
from .structured_context_split_helpers import (
    _biz_tag,
    _clip_text,
    _collect_biz_counts_from_chunks,
    _extract_chunks_from_context_text,
    _extract_chunks_from_rag_result,
    _normalize_case,
    _ordered_biz_keys,
    _resolve_current_biz_key,
    _safe_str,
)

_PRIORITY_ORDER = ("P0", "P1", "P2")
_MAX_CASES_PER_BUCKET = 5
_MAX_REQUIREMENTS_PER_BIZ = 8
_MAX_SUPPLEMENTS_PER_BIZ = 6
_MAX_SUPPLEMENT_CHARS = 220
_MAX_REQUIREMENT_SEMANTIC_ITEMS_PER_BUCKET = 8
_MAX_REQUIREMENT_SEMANTIC_SOURCE_CHUNKS = 96

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
    "年级",
    "顺序",
    "复用",
    "沿用",
    "确认",
    "最终",
    "以",
    "为准",
)
_REUSE_RISK_HINTS: dict[str, tuple[str, ...]] = {
    "wrong_return_target_risk": (
        "返回",
        "回首页",
        "回列表",
        "返回首页",
        "返回列表",
        "return",
        "home",
        "list",
    ),
    "legacy_behavior_risk": (
        "旧",
        "原",
        "残留",
        "按钮",
        "文案",
        "跳转",
        "legacy",
        "residual",
        "button",
        "copy",
    ),
    "shared_page_residual_risk": (
        "页面",
        "页面壳",
        "共享页面",
        "共用页面",
        "shared page",
        "existing page",
    ),
    "shared_flow_residual_risk": (
        "流程",
        "串",
        "课文",
        "单元",
        "上下文",
        "顺序",
        "flow",
        "context",
        "wrong progression",
    ),
}
_REUSE_RISK_DESCRIPTIONS = {
    "wrong_return_target_risk": "wrong_return_target_risk: verify reused flow returns to the current module target instead of a legacy page.",
    "legacy_behavior_risk": "legacy_behavior_risk: verify reused module does not retain legacy buttons, copy, or obsolete behaviors.",
    "shared_page_residual_risk": "shared_page_residual_risk: verify shared page shells do not leak legacy entry or exit behavior into the new module.",
    "shared_flow_residual_risk": "shared_flow_residual_risk: verify reused flow does not串原模块流程、串课文/单元或污染当前上下文。",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "true" if default else "false") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        parsed = int(default)
    return max(int(min_value), min(int(max_value), int(parsed)))


def _env_float(name: str, default: float, *, min_value: float, max_value: float) -> float:
    try:
        parsed = float(str(os.getenv(name, str(default))).strip())
    except Exception:
        parsed = float(default)
    return max(float(min_value), min(float(max_value), float(parsed)))


def _extract_requirement_lines(text: str, limit: int) -> list[str]:
    src = str(text or "").strip()
    if not src:
        return []

    output: list[str] = []
    seen: set[str] = set()

    for raw in re.split(r"[\n\r]+", src):
        line = re.sub(r"^\s*[-*•]\s*", "", str(raw or "").strip())
        if not line:
            continue
        if re.search(r"\bREQ[-_\s]?\d+\b", line, flags=re.IGNORECASE):
            if line not in seen:
                output.append(line)
                seen.add(line)

    for raw in re.split(r"[。；;]+", src):
        line = re.sub(r"^\s*[-*•]\s*", "", str(raw or "").strip())
        if len(line) < 6 or line in seen:
            continue
        output.append(line)
        seen.add(line)
        if len(output) >= max(1, int(limit)):
            break

    return output[: max(1, int(limit))]


def _extract_requirement_semantic_fragments(text: str, limit: int = 32) -> list[str]:
    src = str(text or "").strip()
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


def _is_time_window_scope_rule(fragment: str) -> bool:
    lowered = str(fragment or "").strip().lower()
    if not lowered:
        return False

    time_markers = (
        "周日24:00",
        "周日 24:00",
        "24:00后",
        "24:00 后",
        "24点后",
        "24 点后",
        "sunday 24:00",
        "after sunday 24:00",
    )
    view_only_markers = (
        "仅可查看",
        "只可查看",
        "仅查看",
        "不可操作",
        "不能操作",
        "禁止操作",
        "view only",
        "read only",
        "read-only",
        "readonly",
    )
    scope_markers = (
        "历史周",
        "补做",
        "补学",
        "历史任务",
        "周末任务",
        "过期规则",
        "历史周补学",
        "补做期",
    )

    has_time_gate = any(marker in lowered for marker in time_markers)
    has_view_only = any(marker in lowered for marker in view_only_markers)
    has_scope = any(marker in lowered for marker in scope_markers)
    return bool(has_time_gate and has_view_only and (has_scope or "周日" in lowered))


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
    scoped_rule = _is_time_window_scope_rule(lowered)
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
    if not fragments:
        return []

    merged = " ".join(str(item or "") for item in fragments)
    lowered = merged.lower()
    output: list[str] = []
    for risk_key, markers in _REUSE_RISK_HINTS.items():
        if any(marker.lower() in lowered for marker in markers):
            output.append(_REUSE_RISK_DESCRIPTIONS[risk_key])
    if not output:
        output.append(
            "shared_flow_residual_risk: verify reused modules do not inherit legacy routing, context, or flow side effects."
        )
    return output


def _build_testcase_context(
    *,
    existing_cases: list[dict[str, Any]] | None,
    current_biz_key: str,
    only_current_biz: bool,
    max_chars: int = 8000,
) -> tuple[str, dict[str, int], dict[str, int], str, dict[str, str]]:
    raw_cases = [item for item in (existing_cases or []) if isinstance(item, dict)]
    if not raw_cases:
        return "(empty)", {}, {}, "empty_cases", {}

    normalized_cases = [_normalize_case(case, idx) for idx, case in enumerate(raw_cases, start=1)]
    all_counts: dict[str, int] = defaultdict(int)
    for case in normalized_cases:
        all_counts[case["biz_key"]] += 1

    mode = "reference_allowed"
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    if bool(only_current_biz) and current_biz_key == "unknown":
        mode = "reference_allowed_current_unknown"
    elif effective_only_current:
        mode = "strict_current_only"

    grouped: dict[str, dict[str, dict[str, list[dict[str, str]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    rendered_counts: dict[str, int] = defaultdict(int)
    for case in normalized_cases:
        biz_key = case["biz_key"]
        if effective_only_current and biz_key != current_biz_key:
            continue
        bucket = grouped[biz_key][case["test_module"]][case["priority"]]
        if len(bucket) < _MAX_CASES_PER_BUCKET:
            bucket.append(case)
            rendered_counts[biz_key] += 1

    if not grouped:
        return "(empty)", dict(all_counts), {}, mode, {}

    biz_order = _ordered_biz_keys(dict(rendered_counts), current_biz_key)
    full_lines: list[str] = ["[Testcases - grouped by biz_key]"]
    scoped_map: dict[str, str] = {}
    for biz_key in biz_order:
        lines: list[str] = [f"### biz_key: {biz_key} ({_biz_tag(biz_key, current_biz_key)})"]
        module_map = grouped.get(biz_key, {})
        module_order = list(module_map.keys())
        for module_name in module_order:
            lines.append(f"#### test_module: {module_name}")
            for priority in _PRIORITY_ORDER:
                items = module_map[module_name].get(priority) or []
                if not items:
                    continue
                lines.append(f"##### priority: {priority}")
                for case in items:
                    lines.append(f"* {case['id']}: {case['description'] or '(no description)'}")
        block = "\n".join(lines)
        scoped_map[biz_key] = _clip_text(f"[Testcases - grouped by biz_key]\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(lines)

    return (
        _clip_text("\n".join(full_lines), max_chars) or "(empty)",
        dict(all_counts),
        dict(rendered_counts),
        mode,
        scoped_map,
    )


def _build_requirement_context(
    *,
    requirement: str,
    chunks: list[dict[str, Any]],
    current_biz_key: str,
    only_current_biz: bool,
    max_chars: int = 12000,
) -> tuple[str, dict[str, int], dict[str, str]]:
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, list[str]] = defaultdict(list)

    for item in _extract_requirement_lines(requirement, _MAX_REQUIREMENTS_PER_BIZ):
        grouped[current_biz_key].append(item)

    for chunk in chunks[:96]:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue
        doc_type = _safe_str(chunk.get("doc_type") or metadata.get("doc_type"), "unknown").lower()
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        if (
            "requirement" not in doc_type
            and "需求" not in doc_type
            and "规则" not in text
            and "REQ-" not in text
        ):
            continue
        for item in _extract_requirement_lines(text, 2):
            if len(grouped[biz_key]) < _MAX_REQUIREMENTS_PER_BIZ:
                grouped[biz_key].append(item)

    deduped: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for biz_key, items in grouped.items():
        seen: set[str] = set()
        merged: list[str] = []
        for item in items:
            key = str(item).strip()
            if (not key) or key in seen:
                continue
            seen.add(key)
            merged.append(key)
            if len(merged) >= _MAX_REQUIREMENTS_PER_BIZ:
                break
        if merged:
            deduped[biz_key] = merged
            counts[biz_key] = len(merged)

    if not deduped:
        return "(empty)", {}, {}

    biz_order = _ordered_biz_keys(counts, current_biz_key)
    full_lines: list[str] = ["[Requirements - grouped by biz_key]"]
    scoped_map: dict[str, str] = {}
    for biz_key in biz_order:
        block_lines = [f"### biz_key: {biz_key} ({_biz_tag(biz_key, current_biz_key)})", ""]
        block_lines.extend([f"* {item}" for item in deduped.get(biz_key, [])])
        block = "\n".join(block_lines).strip()
        scoped_map[biz_key] = _clip_text(f"[Requirements - grouped by biz_key]\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(block_lines)
    return _clip_text("\n".join(full_lines), max_chars) or "(empty)", counts, scoped_map


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


def _build_control_context(
    *,
    control_state: FeedbackControlState | dict[str, Any] | None,
    max_chars: int = 6000,
    include_soft_constraints_in_text: bool = False,
    include_quality_fix_hints_in_text: bool = False,
) -> tuple[str, dict[str, Any]]:
    state = FeedbackControlState.from_any(control_state)
    strong_preferred_quota_enabled = bool(
        _env_bool("TESTGEN_ENABLE_STRONG_PREFERRED_QUOTA_AB", True)
    )
    preferred_flow_case_quota = _env_int(
        "TESTGEN_PREFERRED_FLOW_CASE_QUOTA",
        2,
        min_value=1,
        max_value=6,
    )
    ui_case_ratio_cap = _env_float(
        "TESTGEN_UI_CASE_RATIO_CAP",
        0.40,
        min_value=0.20,
        max_value=0.60,
    )
    preferred_quota_active = bool(strong_preferred_quota_enabled and state.preferred_patterns)
    generation_profile = dict((state.source_meta or {}).get("generation_coverage_profile") or {})
    fact_profile = dict((state.source_meta or {}).get("fact_profile") or {})
    project_profile = dict((state.source_meta or {}).get("project_profile") or {})
    manual_quality_profile = dict((state.source_meta or {}).get("manual_quality_profile") or {})
    project_flow_outline = dict(project_profile.get("flow_outline") or {})
    generation_coverage_mode = str(generation_profile.get("coverage_mode") or "").strip()
    summary = {
        "control_state_applied": bool(state.has_signals()),
        "must_cover_rules_count": int(len(state.must_cover_rules)),
        "must_have_scenarios_count": int(len(state.must_have_scenarios)),
        "forbidden_patterns_count": int(len(state.forbidden_patterns)),
        "preferred_patterns_count": int(len(state.preferred_patterns)),
        "reuse_risks_count": int(len(state.reuse_risks)),
        "soft_constraints_count": int(len(state.soft_constraints)),
        "rule_quota_keys": sorted(list((state.rule_quota or {}).keys())),
        "quality_fix_hints_count": int(len(state.quality_fix_hints)),
        "workflow_blueprint_count": int(len(state.workflow_blueprints)),
        "soft_constraints_in_prompt": bool(include_soft_constraints_in_text),
        "quality_fix_hints_in_prompt": bool(include_quality_fix_hints_in_text),
        "preferred_quota_variant": "B" if preferred_quota_active else "A",
        "preferred_flow_case_quota": int(preferred_flow_case_quota) if preferred_quota_active else 0,
        "ui_case_ratio_cap": float(ui_case_ratio_cap),
        "generation_coverage_mode": generation_coverage_mode,
        "generation_case_density": str(generation_profile.get("case_density") or "").strip(),
        "generation_target_case_range": dict(generation_profile.get("target_case_range") or {}),
        "fact_profile_source": str(fact_profile.get("profile_source") or "").strip(),
        "fact_profile_confidence": float(fact_profile.get("confidence") or 0.0),
        "fact_profile_confirmed_count": int(len(fact_profile.get("confirmed_facts") or [])),
        "fact_profile_pending_count": int(len(fact_profile.get("pending_items") or [])),
        "fact_profile_forbidden_count": int(len(fact_profile.get("forbidden_facts") or [])),
        "project_profile_source": str(project_profile.get("profile_source") or "").strip(),
        "project_profile_confidence": float(project_profile.get("confidence") or 0.0),
        "project_profile_flow_count": int(len(project_flow_outline.get("flow_order") or [])),
        "project_profile_cross_cutting_count": int(len(project_flow_outline.get("cross_cutting") or [])),
        "manual_quality_profile_source": str(manual_quality_profile.get("profile_source") or "").strip(),
        "manual_quality_profile_version": str(manual_quality_profile.get("profile_version") or "").strip(),
        "manual_quality_profile_trusted_count": int(manual_quality_profile.get("trusted_sample_count") or 0),
        "manual_quality_profile_high_priority_ratio": float(manual_quality_profile.get("high_priority_ratio") or 0.0),
        "manual_quality_profile_display_ratio_cap": float(manual_quality_profile.get("display_ratio_cap") or 0.0),
        "source_meta": dict(state.source_meta or {}),
    }

    has_prompt_signals = bool(
        state.must_cover_rules
        or state.must_have_scenarios
        or state.rule_quota
        or state.forbidden_patterns
        or state.preferred_patterns
        or state.reuse_risks
        or state.workflow_blueprints
        or generation_coverage_mode
        or fact_profile
        or project_profile
        or manual_quality_profile
        or (include_soft_constraints_in_text and state.soft_constraints)
        or (include_quality_fix_hints_in_text and state.quality_fix_hints)
    )
    if not has_prompt_signals:
        return "(empty)", summary

    lines: list[str] = ["[Generation Control - Structured]"]
    lines.append("### MUST COVER RULES")
    if state.must_cover_rules:
        lines.extend([f"* {item}" for item in state.must_cover_rules])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### MUST HAVE SCENARIOS")
    if state.must_have_scenarios:
        lines.extend([f"* {item}" for item in state.must_have_scenarios])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### RULE QUOTA")
    if state.rule_quota:
        for rule, quota in sorted(state.rule_quota.items(), key=lambda item: (item[0], -int(item[1] or 0))):
            lines.append(f"* {rule}: >= {int(quota)}")
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### FORBIDDEN PATTERNS")
    if state.forbidden_patterns:
        lines.extend([f"* {item}" for item in state.forbidden_patterns])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### PREFERRED PATTERNS")
    if state.preferred_patterns:
        lines.extend([f"* {item}" for item in state.preferred_patterns])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### REUSE RISKS")
    if state.reuse_risks:
        lines.extend([f"* {item}" for item in state.reuse_risks])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### WORKFLOW BLUEPRINTS")
    if state.workflow_blueprints:
        lines.append("* Treat workflow blueprints as execution-order contracts, not as reusable RAG examples.")
        for blueprint in state.workflow_blueprints[:5]:
            if not isinstance(blueprint, dict):
                continue
            name = str(blueprint.get("name") or blueprint.get("id") or "workflow").strip()
            source = str(
                blueprint.get("repository_source")
                or blueprint.get("source")
                or blueprint.get("source_type")
                or "unknown"
            ).strip()
            source_suffix = f" [source={source}]" if source and source.lower() != "unknown" else ""
            steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
            labels = [
                " / ".join(
                    item
                    for item in (
                        str(step.get("stage_kind") or "").strip(),
                        str(step.get("label") or step.get("action") or step.get("id") or "").strip(),
                        (
                            f"{step.get('state_in')}->{step.get('state_out')}"
                            if str(step.get("state_in") or "").strip()
                            and str(step.get("state_out") or "").strip()
                            else ""
                        ),
                    )
                    if item
                )
                for step in steps[:12]
                if str(step.get("label") or step.get("action") or step.get("id") or "").strip()
            ]
            if labels:
                lines.append(f"* {name}{source_suffix}: {' -> '.join(labels)}")
    else:
        lines.append("* (none)")

    if fact_profile:
        lines.append("")
        lines.append("### FACT PROFILE")
        lines.append(f"* source: {str(fact_profile.get('profile_source') or 'unknown')}")
        lines.append(f"* confidence: {float(fact_profile.get('confidence') or 0.0):.2f}")
        lines.append("* Use this as factual guardrail. Current requirement wins on conflict.")
        for title, key, limit in (
            ("confirmed facts", "confirmed_facts", 8),
            ("forbidden facts", "forbidden_facts", 8),
            ("pending items", "pending_items", 6),
            ("hard flow constraints", "hard_flow_constraints", 6),
        ):
            values = [str(item).strip() for item in (fact_profile.get(key) or []) if str(item).strip()]
            if not values:
                continue
            lines.append(f"* {title}:")
            lines.extend([f"  - {item}" for item in values[:limit]])

    if project_profile:
        flow_outline = dict(project_profile.get("flow_outline") or {})
        flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item).strip()]
        flow_labels = dict(flow_outline.get("flow_labels") or {})
        cross_cutting = [str(item) for item in (flow_outline.get("cross_cutting") or []) if str(item).strip()]
        cross_labels = dict(flow_outline.get("cross_cutting_labels") or {})
        lines.append("")
        lines.append("### PROJECT STRUCTURE PROFILE")
        lines.append(f"* source: {str(project_profile.get('profile_source') or 'unknown')}")
        lines.append(f"* confidence: {float(project_profile.get('confidence') or 0.0):.2f}")
        lines.append("* Use this as ordering and coverage structure only; it is not a fact source.")
        lines.append("* Final test cases should follow the flow outline first; put cross-cutting modules after the main flow unless a case explicitly validates their interaction with a main-flow step.")
        if flow_order:
            labels = [str(flow_labels.get(key) or key) for key in flow_order]
            lines.append(f"* flow outline: {' -> '.join(labels[:24])}")
        data_flow_edges = [
            item for item in (flow_outline.get("data_flow_edges") or []) if isinstance(item, dict)
        ]
        if data_flow_edges:
            edge_labels = [
                f"{str(item.get('from_label') or item.get('from') or '')} -> {str(item.get('to_label') or item.get('to') or '')}"
                for item in data_flow_edges[:12]
            ]
            lines.append(f"* data-flow edges: {'; '.join(edge_labels)}")
        if cross_cutting:
            labels = [str(cross_labels.get(key) or key) for key in cross_cutting]
            lines.append(f"* cross-cutting modules: {', '.join(labels[:16])}")
        scenario_policy = dict(project_profile.get("scenario_cluster_policy") or {})
        if scenario_policy:
            lines.append(
                f"* default max per scenario: {int(scenario_policy.get('default_max_per_scenario') or 2)}"
            )

    if manual_quality_profile:
        priority_distribution = dict(manual_quality_profile.get("priority_distribution") or {})
        module_distribution = dict(manual_quality_profile.get("module_distribution_top") or {})
        lifecycle_fields = [
            str(item).strip()
            for item in (manual_quality_profile.get("execution_lifecycle_fields") or [])
            if str(item).strip()
        ]
        lines.append("")
        lines.append("### MANUAL QUALITY PROFILE")
        lines.append(f"* source: {str(manual_quality_profile.get('profile_source') or 'unknown')}")
        lines.append(f"* version: {str(manual_quality_profile.get('profile_version') or '')}")
        lines.append(f"* trusted samples: {int(manual_quality_profile.get('trusted_sample_count') or 0)}")
        lines.append("* Use this as stable delivery-quality target; do not hard-code its business values.")
        if priority_distribution:
            parts = [f"{key}:{int(value)}" for key, value in priority_distribution.items()]
            lines.append(f"* target priority mix: {', '.join(parts[:8])}")
        high_ratio = float(manual_quality_profile.get("high_priority_ratio") or 0.0)
        if high_ratio > 0:
            lines.append(f"* target P0/P1 ratio: about {int(round(high_ratio * 100.0))}%")
        display_cap = float(manual_quality_profile.get("display_ratio_cap") or 0.0)
        if display_cap > 0:
            lines.append(f"* display-only cap: <= {int(round(display_cap * 100.0))}%")
        if module_distribution:
            modules = [str(key) for key in module_distribution.keys()]
            lines.append(f"* target module coverage: {', '.join(modules[:12])}")
        if lifecycle_fields:
            lines.append(f"* lifecycle fields to preserve: {', '.join(lifecycle_fields[:8])}")

    if generation_coverage_mode:
        target_range = dict(generation_profile.get("target_case_range") or {})
        lines.append("")
        lines.append("### GENERATION COVERAGE MODE")
        lines.append(f"* mode: {generation_coverage_mode}")
        if target_range:
            lines.append(
                f"* target case range: {int(target_range.get('min') or 0)}-{int(target_range.get('max') or 0)}"
            )
        lines.append("* Use this as a coverage-density strategy, not a quota.")
        if generation_coverage_mode == "full_functional_regression":
            lines.append("* Expand module x state x exception x cross-module coverage before stopping.")
            lines.append("* Prefer high-value failure/recovery, permission, moderation, retry, upload/download, and state-sync cases over additional display-only variants.")
        elif generation_coverage_mode == "expanded_regression":
            lines.append("* Keep broad requirement coverage, then prune near-duplicates and generic low-value checks.")
        elif generation_coverage_mode == "standard_regression":
            lines.append("* Balance core flow, state transitions, boundary/exception, and regression coverage.")
        else:
            lines.append("* Keep a compact high-value core set.")

    if preferred_quota_active:
        lines.append("")
        lines.append("### PREFERRED PATTERN QUOTA (AB)")
        lines.append(
            f"* Must generate at least {int(preferred_flow_case_quota)} workflow/state-transition cases expanded from PREFERRED PATTERNS."
        )
        lines.append(
            f"* UI-only cases (display/layout/copy/style) must not exceed {int(round(ui_case_ratio_cap * 100.0))}% of total generated cases."
        )
        lines.append("* If quota conflicts with weak dedup/display heuristics, keep preferred-pattern quota first.")

    if include_soft_constraints_in_text:
        lines.append("")
        lines.append("### SOFT CONSTRAINTS (NEGATIVE BIAS)")
        if state.soft_constraints:
            lines.extend([f"* {item}" for item in state.soft_constraints])
        else:
            lines.append("* (none)")

    if include_quality_fix_hints_in_text:
        lines.append("")
        lines.append("### QUALITY FIX HINTS")
        if state.quality_fix_hints:
            lines.extend([f"* {item}" for item in state.quality_fix_hints])
        else:
            lines.append("* (none)")

    return _clip_text("\n".join(lines), max_chars) or "(empty)", summary


def build_structured_prompt_context(
    *,
    requirement: str,
    kb_context: str = "",
    rag_result: Any = None,
    existing_cases: list[dict[str, Any]] | None = None,
    current_biz_key: str = "",
    only_current_biz: bool = False,
    feedback_control_state: FeedbackControlState | dict[str, Any] | None = None,
    include_soft_constraints_in_prompt: bool = False,
    include_quality_fix_hints_in_prompt: bool = False,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_control_state = feedback_control_state
    if resolved_control_state is None and memory_fabric is not None and memory_ctx is not None:
        try:
            resolved_control_state = memory_fabric.read_rule({"kind": "feedback_control_state"}, memory_ctx)
            record_memory_read(memory_diag, "rule", via_memory_fabric=True)
        except Exception:
            record_memory_read(memory_diag, "rule", via_memory_fabric=False)

    chunks = _extract_chunks_from_rag_result(rag_result) or _extract_chunks_from_context_text(kb_context)
    tentative_current = _safe_str(current_biz_key, "unknown")
    strict_only_enabled = bool(only_current_biz) and tentative_current != "unknown"

    testcase_context, testcase_all_counts, testcase_render_counts, testcase_mode, testcase_scoped = _build_testcase_context(
        existing_cases=existing_cases,
        current_biz_key=tentative_current,
        only_current_biz=strict_only_enabled,
    )
    supplement_raw_counts = _collect_biz_counts_from_chunks(chunks)
    resolved_current_biz = _resolve_current_biz_key(
        explicit_current_biz_key=tentative_current,
        testcase_counts=testcase_all_counts,
        supplement_counts=supplement_raw_counts,
    )

    testcase_context, _, testcase_render_counts, testcase_mode, testcase_scoped = _build_testcase_context(
        existing_cases=existing_cases,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    requirement_context, requirement_counts, requirement_scoped = _build_requirement_context(
        requirement=requirement,
        chunks=chunks,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    requirement_semantics_context, requirement_semantics_by_biz, requirement_semantics_scoped = (
        _build_requirement_semantics_context(
            requirement=requirement,
            chunks=chunks,
            current_biz_key=resolved_current_biz,
            only_current_biz=strict_only_enabled,
        )
    )
    supplement_context, supplement_counts, supplement_scoped = _build_supplement_context(
        chunks=chunks,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    semantic_reuse_risks = list(
        ((requirement_semantics_by_biz.get(resolved_current_biz) or {}).get("reuse_risks") or [])
    )
    resolved_control_state = FeedbackControlState.from_any(resolved_control_state).merge(
        {
            "reuse_risks": semantic_reuse_risks,
            "source_meta": {
                "sources": ["requirement_semantics"] if semantic_reuse_risks else [],
            },
        }
    )
    control_context, control_summary = _build_control_context(
        control_state=resolved_control_state,
        include_soft_constraints_in_text=bool(include_soft_constraints_in_prompt),
        include_quality_fix_hints_in_text=bool(include_quality_fix_hints_in_prompt),
    )

    isolation_log = _build_biz_key_isolation_log(
        current_biz_key=resolved_current_biz,
        requirement_biz_counts=requirement_counts,
        testcase_biz_counts=testcase_render_counts,
        supplement_biz_counts=supplement_counts,
        mode=testcase_mode,
    )
    if bool(only_current_biz) and (not strict_only_enabled):
        isolation_log["mode"] = "reference_allowed_current_unknown"
        isolation_log["degraded_reason"] = "current_biz_key_unknown"

    biz_keys = set(requirement_scoped.keys()) | set(testcase_scoped.keys()) | set(supplement_scoped.keys())
    if not biz_keys:
        biz_keys = {resolved_current_biz}
    biz_counts_for_order: dict[str, int] = {}
    for biz_key in biz_keys:
        biz_counts_for_order[biz_key] = int(requirement_counts.get(biz_key, 0)) + int(
            testcase_render_counts.get(biz_key, 0)
        ) + int(supplement_counts.get(biz_key, 0))
    biz_key_order = _ordered_biz_keys(biz_counts_for_order, resolved_current_biz)
    requirement_module_order_by_biz = _extract_requirement_module_order(
        chunks=chunks,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    reference_module_order_by_biz = _extract_reference_module_order(
        existing_cases=existing_cases,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    module_order_hint, module_order_by_biz, module_order_source = _merge_module_order(
        requirement_order=requirement_module_order_by_biz,
        reference_order=reference_module_order_by_biz,
        biz_key_order=biz_key_order,
        current_biz_key=resolved_current_biz,
    )
    fact_profile = build_fact_profile(
        requirement_semantics_by_biz=requirement_semantics_by_biz,
        current_biz_key=resolved_current_biz,
        source="requirement_semantics",
    )
    project_profile = build_project_profile(
        requirement_text=requirement_context or requirement or "",
        cases=[c for c in (existing_cases or []) if isinstance(c, dict)],
        module_order_hint=list(module_order_hint),
        module_order_source=module_order_source,
    )
    resolved_control_state = merge_fact_profile_control_state(resolved_control_state, fact_profile)
    resolved_control_state = merge_project_profile_control_state(resolved_control_state, project_profile)
    control_context, control_summary = _build_control_context(
        control_state=resolved_control_state,
        include_soft_constraints_in_text=bool(include_soft_constraints_in_prompt),
        include_quality_fix_hints_in_text=bool(include_quality_fix_hints_in_prompt),
    )

    context_by_biz: dict[str, dict[str, Any]] = {}
    for biz_key in biz_key_order:
        scoped_semantics = dict(requirement_semantics_by_biz.get(biz_key) or {})
        context_by_biz[biz_key] = {
            "requirement_context": requirement_scoped.get(biz_key) or requirement_context,
            "requirement_semantics_context": requirement_semantics_scoped.get(biz_key) or requirement_semantics_context,
            "testcase_context": testcase_scoped.get(biz_key) or testcase_context,
            "supplement_context": supplement_scoped.get(biz_key) or supplement_context,
            "control_context": control_context,
            "confirmed_facts": list(scoped_semantics.get("confirmed_facts") or []),
            "scoped_rules": list(scoped_semantics.get("scoped_rules") or []),
            "pending_items": list(scoped_semantics.get("pending_items") or []),
            "reuse_declarations": list(scoped_semantics.get("reuse_declarations") or []),
            "hard_flow_constraints": list(scoped_semantics.get("hard_flow_constraints") or []),
            "reuse_risks": list(scoped_semantics.get("reuse_risks") or []),
            "module_order_hint": list(module_order_by_biz.get(biz_key) or []),
            "fact_profile": fact_profile,
            "project_profile": project_profile,
        }

    current_semantics = dict(requirement_semantics_by_biz.get(resolved_current_biz) or {})
    return {
        "requirement_context": requirement_context or "(empty)",
        "requirement_semantics_context": requirement_semantics_context or "(empty)",
        "testcase_context": testcase_context or "(empty)",
        "supplement_context": supplement_context or "(empty)",
        "control_context": control_context or "(empty)",
        "control_summary": control_summary,
        "feedback_control_state": resolved_control_state.to_dict(),
        "current_biz_key": resolved_current_biz,
        "only_current_biz": bool(only_current_biz),
        "confirmed_facts": list(current_semantics.get("confirmed_facts") or []),
        "scoped_rules": list(current_semantics.get("scoped_rules") or []),
        "pending_items": list(current_semantics.get("pending_items") or []),
        "reuse_declarations": list(current_semantics.get("reuse_declarations") or []),
        "hard_flow_constraints": list(current_semantics.get("hard_flow_constraints") or []),
        "reuse_risks": list(current_semantics.get("reuse_risks") or []),
        "requirement_semantics_by_biz": requirement_semantics_by_biz,
        "biz_key_isolation_log": isolation_log,
        "biz_key_order": biz_key_order,
        "module_order_hint": list(module_order_hint),
        "module_order_by_biz": module_order_by_biz,
        "module_order_source": module_order_source,
        "fact_profile": fact_profile,
        "project_profile": project_profile,
        "context_by_biz": context_by_biz,
    }
