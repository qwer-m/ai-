from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.contracts.memory_fabric import MemoryFabric
from modules.memory_fabric.runtime.diagnostics import record_memory_read
from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.prompting.structured_context_split_helpers import (
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
        module_order = sorted(
            module_map.keys(),
            key=lambda key: (-sum(len(items) for items in module_map[key].values()), key),
        )
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
    summary = {
        "control_state_applied": bool(state.has_signals()),
        "must_cover_rules_count": int(len(state.must_cover_rules)),
        "must_have_scenarios_count": int(len(state.must_have_scenarios)),
        "forbidden_patterns_count": int(len(state.forbidden_patterns)),
        "soft_constraints_count": int(len(state.soft_constraints)),
        "rule_quota_keys": sorted(list((state.rule_quota or {}).keys())),
        "quality_fix_hints_count": int(len(state.quality_fix_hints)),
        "soft_constraints_in_prompt": bool(include_soft_constraints_in_text),
        "quality_fix_hints_in_prompt": bool(include_quality_fix_hints_in_text),
        "source_meta": dict(state.source_meta or {}),
    }

    has_prompt_signals = bool(
        state.must_cover_rules
        or state.must_have_scenarios
        or state.rule_quota
        or state.forbidden_patterns
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
    supplement_context, supplement_counts, supplement_scoped = _build_supplement_context(
        chunks=chunks,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
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

    context_by_biz: dict[str, dict[str, str]] = {}
    for biz_key in biz_key_order:
        context_by_biz[biz_key] = {
            "requirement_context": requirement_scoped.get(biz_key) or requirement_context,
            "testcase_context": testcase_scoped.get(biz_key) or testcase_context,
            "supplement_context": supplement_scoped.get(biz_key) or supplement_context,
            "control_context": control_context,
        }

    return {
        "requirement_context": requirement_context or "(empty)",
        "testcase_context": testcase_context or "(empty)",
        "supplement_context": supplement_context or "(empty)",
        "control_context": control_context or "(empty)",
        "control_summary": control_summary,
        "current_biz_key": resolved_current_biz,
        "only_current_biz": bool(only_current_biz),
        "biz_key_isolation_log": isolation_log,
        "biz_key_order": biz_key_order,
        "context_by_biz": context_by_biz,
    }
