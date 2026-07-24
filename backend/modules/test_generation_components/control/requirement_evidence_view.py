from __future__ import annotations

import json
from typing import Any


_DIAGNOSTIC_SECTION_HEADERS = {
    "[parsed requirement evidence]",
    "[multimodal evidence alignment]",
    "[attachment diagnostics]",
    "[requirement parse diagnostics]",
}


def build_requirement_business_evidence_view(
    requirement_text: Any,
) -> tuple[str, dict[str, Any]]:
    """构建语义编译与后续重校验共用的当前需求证据视图。"""

    main_lines: list[str] = []
    understanding_lines: list[str] = []
    section = "business"
    for raw_line in str(requirement_text or "").splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if line.startswith("[") and line.endswith("]"):
            if lowered == "[requirement understanding]":
                section = "requirement_understanding"
                continue
            if lowered in _DIAGNOSTIC_SECTION_HEADERS:
                section = "diagnostic"
                continue
            section = "business"
            continue
        if section == "business":
            main_lines.append(raw_line)
        elif section == "requirement_understanding":
            understanding_lines.append(raw_line)

    visual_facts: list[str] = []
    try:
        understanding = json.loads("\n".join(understanding_lines).strip() or "{}")
    except Exception:
        understanding = {}
    if isinstance(understanding, dict):
        for item in understanding.get("visual_facts") or []:
            if not isinstance(item, dict) or item.get("valid") is False:
                continue
            fact = str(item.get("text") or item.get("fact") or "").strip()
            if fact:
                visual_facts.append(fact)

    business_text = "\n".join(main_lines).strip()
    evidence_text = "\n".join(
        [
            part
            for part in (
                business_text,
                *list(dict.fromkeys(visual_facts)),
            )
            if part
        ]
    ).strip()
    return evidence_text, {
        "business_evidence_chars": len(evidence_text),
        "business_body_chars": len(business_text),
        "business_visual_fact_count": len(set(visual_facts)),
        "diagnostic_sections_excluded": True,
    }


__all__ = ["build_requirement_business_evidence_view"]
