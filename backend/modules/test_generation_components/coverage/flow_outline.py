from __future__ import annotations

import re
from typing import Any

from .coverage_strategy import (
    cross_cutting_definitions,
    cross_cutting_hints,
    data_flow_phase_tie_priority,
    data_flow_phases,
    flow_stage_definitions,
)
from .rule_coverage import _normalize_text
from ..postprocess.case_access import case_text_field


_FLOW_STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = flow_stage_definitions()
_FLOW_STAGE_ORDER = [str(item.get("key") or "") for item in _FLOW_STAGE_DEFINITIONS]

_CROSS_CUTTING_DEFINITIONS: tuple[dict[str, Any], ...] = cross_cutting_definitions()
_CROSS_CUTTING_ORDER = [str(item.get("key") or "") for item in _CROSS_CUTTING_DEFINITIONS]

_STAGE_SPLIT_RE = re.compile(r"\s*(?:->|=>|[\\/\|>:_\-—–／：])\s*")
_STAGE_TRAILING_NOISE_RE = re.compile(
    r"\s*(?:page|screen|view|module|panel|tab|section|list|detail|flow|workflow|"
    r"页面|页|模块|面板|区域|列表|详情|流程|验证)\s*$",
    re.IGNORECASE,
)
_CROSS_CUTTING_HINTS = cross_cutting_hints()

_DATA_FLOW_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = data_flow_phases()
_DATA_FLOW_PHASE_RANK = {phase: index for index, (phase, _tokens) in enumerate(_DATA_FLOW_PHASES)}
_DATA_FLOW_PHASE_TIE_PRIORITY = data_flow_phase_tie_priority()
_DATA_FLOW_CROSS_CUTTING_PHASES = {"access_limit", "global_exception", "history_makeup"}


def _keyword_position(text: str, keywords: tuple[str, ...]) -> int | None:
    positions = [text.find(keyword) for keyword in keywords if keyword and text.find(keyword) >= 0]
    return min(positions) if positions else None


def _dedupe_stage_keys(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _data_flow_phase_for_label(label: str) -> str:
    lowered = str(label or "").strip().lower()
    if not lowered:
        return ""
    matches: list[tuple[int, int, str]] = []
    for phase, tokens in _DATA_FLOW_PHASES:
        score = sum(len(str(token)) for token in tokens if str(token).lower() in lowered)
        if score > 0:
            matches.append((int(score), -int(_DATA_FLOW_PHASE_TIE_PRIORITY.get(phase, 99)), phase))
    if not matches:
        return ""
    matches.sort(reverse=True)
    return matches[0][2]


def _apply_data_flow_order_to_outline(outline: dict[str, Any]) -> dict[str, Any]:
    flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = [str(item) for item in (outline.get("cross_cutting") or []) if str(item).strip()]
    flow_labels = dict(outline.get("flow_labels") or {})
    cross_labels = dict(outline.get("cross_cutting_labels") or {})
    stage_phase: dict[str, str] = {}
    retained_flow: list[str] = []
    moved_to_cross: list[str] = []
    for key in flow_order:
        label = str(flow_labels.get(key) or key)
        phase = _data_flow_phase_for_label(label)
        if phase:
            stage_phase[key] = phase
        if phase in _DATA_FLOW_CROSS_CUTTING_PHASES:
            moved_to_cross.append(key)
            cross_labels.setdefault(key, label)
            continue
        retained_flow.append(key)
    matched = [key for key in retained_flow if stage_phase.get(key)]
    if len(matched) >= 2:
        ordered = sorted(
            enumerate(retained_flow),
            key=lambda item: (
                _DATA_FLOW_PHASE_RANK.get(stage_phase.get(item[1]) or "", 10_000),
                item[0],
            ),
        )
        flow_order = [key for _index, key in ordered]
    else:
        flow_order = retained_flow
    original_flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = _dedupe_stage_keys([*cross_cutting, *moved_to_cross])
    edges = [
        {
            "from": left,
            "to": right,
            "from_label": str(flow_labels.get(left) or left),
            "to_label": str(flow_labels.get(right) or right),
        }
        for left, right in zip(flow_order, flow_order[1:])
    ]
    return {
        **outline,
        "flow_order": flow_order,
        "cross_cutting": cross_cutting,
        "cross_cutting_labels": cross_labels,
        "data_flow_edges": edges,
        "data_flow_phase_rank": stage_phase,
        "data_flow_order_applied": bool(flow_order != original_flow_order or moved_to_cross),
    }


def _compact_stage_label(label: str) -> str:
    cleaned = _normalize_text(label).strip()
    cleaned = re.sub(r"^\s*(?:#{1,6}\s*)?", "", cleaned)
    cleaned = re.sub(r"^\s*(?:[一二三四五六七八九十百]+|\d+|[A-Za-z])[\.\、\)\）\-\s]+", "", cleaned)
    cleaned = re.sub(r"[:：]\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:40]


def _canonical_stage_label(label: str) -> str:
    cleaned = _compact_stage_label(label)
    cleaned = re.sub(r"[\(\（\[].*?[\)\）\]]", "", cleaned).strip()
    parts = [part.strip() for part in _STAGE_SPLIT_RE.split(cleaned) if part and part.strip()]
    if len(parts) > 1 and len(parts[0]) >= 2:
        cleaned = parts[0]
    cleaned = _STAGE_TRAILING_NOISE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:40] or _compact_stage_label(label)


def _stage_key_from_label(label: str, index: int) -> str:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "_", _normalize_text(_canonical_stage_label(label)).strip().lower()).strip("_")
    if not compact:
        compact = f"stage_{index:03d}"
    return f"stage:{compact[:48]}"


def _looks_like_section_heading(line: str) -> bool:
    text = _normalize_text(line).strip()
    if not text:
        return False
    if len(text) > 80:
        return False
    if re.match(r"^\s*#{1,6}\s+\S+", text):
        return True
    if re.match(r"^\s*(?:[一二三四五六七八九十百]+|\d+|[A-Za-z])[\.\、\)\）\-\s]+.{2,40}$", text):
        return True
    if text.endswith((":", "：")) and len(text) <= 50:
        return True
    return False


def _extract_requirement_sections(requirement_context: str) -> list[dict[str, Any]]:
    text = _normalize_text(requirement_context)
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw_line in enumerate(text.splitlines()):
        line = _normalize_text(raw_line).strip()
        if not _looks_like_section_heading(line):
            continue
        label = _compact_stage_label(line)
        if len(label) < 2 or label.lower() in seen:
            continue
        seen.add(label.lower())
        sections.append(
            {
                "key": _stage_key_from_label(label, len(sections) + 1),
                "label": label,
                "position": int(position),
                "source": "requirement_heading",
            }
        )
        if len(sections) >= 24:
            break
    return sections


def _extract_case_module_stages(requirement_context: str, cases: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    text = _normalize_text(requirement_context)
    modules: list[str] = []
    seen: set[str] = set()
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        module = _canonical_stage_label(case_text_field(case, "test_module"))
        if len(module) < 2:
            continue
        key = module.lower()
        if key in seen:
            continue
        seen.add(key)
        modules.append(module)
    stages: list[dict[str, Any]] = []
    for index, module in enumerate(modules, start=1):
        position = text.find(module)
        stages.append(
            {
                "key": _stage_key_from_label(module, index),
                "label": module,
                "position": int(position if position >= 0 else 100000 + index),
                "source": "case_module",
            }
        )
    stages.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("label") or "")))
    return stages


def _split_cross_cutting(stages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flow: list[dict[str, Any]] = []
    cross: list[dict[str, Any]] = []
    for stage in stages:
        label = str(stage.get("label") or "")
        lowered = label.lower()
        if any(hint.lower() in lowered for hint in _CROSS_CUTTING_HINTS):
            cross.append(stage)
        else:
            flow.append(stage)
    return flow, cross


def _extract_profile_flow_outline(project_profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(project_profile, dict):
        return None
    try:
        confidence = float(project_profile.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    outline = project_profile.get("flow_outline")
    if not isinstance(outline, dict):
        return None
    flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = [str(item) for item in (outline.get("cross_cutting") or []) if str(item).strip()]
    if confidence < 0.2 or (not flow_order and not cross_cutting):
        return None
    normalized = dict(outline)
    normalized["source"] = str(project_profile.get("profile_source") or outline.get("source") or "project_profile")
    normalized["flow_order"] = flow_order
    normalized["cross_cutting"] = cross_cutting
    normalized["flow_labels"] = dict(outline.get("flow_labels") or {})
    normalized["cross_cutting_labels"] = dict(outline.get("cross_cutting_labels") or {})
    normalized["profile_confidence"] = confidence
    return _apply_data_flow_order_to_outline(normalized)


def extract_flow_outline(
    requirement_context: str,
    cases: list[dict[str, Any]] | None = None,
    *,
    project_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract a coarse product flow outline from the requirement document itself."""
    profile_outline = _extract_profile_flow_outline(project_profile)
    if profile_outline is not None:
        return profile_outline
    text = _normalize_text(requirement_context)
    found: list[dict[str, Any]] = _extract_case_module_stages(text, cases)
    if not found:
        found = _extract_requirement_sections(text)
    for definition in _FLOW_STAGE_DEFINITIONS:
        position = _keyword_position(text, tuple(definition.get("keywords") or ()))
        if position is None:
            continue
        found.append(
            {
                "key": str(definition.get("key") or ""),
                "label": str(definition.get("label") or ""),
                "position": int(position),
            }
        )
    found.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("key") or "")))
    found_by_key: dict[str, dict[str, Any]] = {}
    for item in found:
        key = str(item.get("key") or "")
        if key and key not in found_by_key:
            found_by_key[key] = item
    ordered_found = list(found_by_key.values())
    if _FLOW_STAGE_ORDER:
        canonical_found = [found_by_key[key] for key in _FLOW_STAGE_ORDER if key in found_by_key]
        canonical_found.extend([item for item in ordered_found if str(item.get("key") or "") not in _FLOW_STAGE_ORDER])
    else:
        canonical_found = ordered_found

    cross_cutting: list[dict[str, Any]] = []
    for definition in _CROSS_CUTTING_DEFINITIONS:
        position = _keyword_position(text, tuple(definition.get("keywords") or ()))
        if position is None:
            continue
        cross_cutting.append(
            {
                "key": str(definition.get("key") or ""),
                "label": str(definition.get("label") or ""),
                "position": int(position),
            }
        )
    flow_stages, inferred_cross_cutting = _split_cross_cutting(canonical_found)
    cross_cutting.extend(inferred_cross_cutting)
    cross_cutting.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("key") or "")))

    return _apply_data_flow_order_to_outline(
        {
            "source": "requirement_keyword_positions" if found else "no_flow_keywords_detected",
            "flow_order": [item["key"] for item in flow_stages],
            "document_flow_order": [item["key"] for item in found],
            "flow_labels": {item["key"]: item["label"] for item in flow_stages},
            "flow_stage_positions": {item["key"]: item["position"] for item in found},
            "cross_cutting": [item["key"] for item in cross_cutting],
            "cross_cutting_labels": {item["key"]: item["label"] for item in cross_cutting},
        }
    )
