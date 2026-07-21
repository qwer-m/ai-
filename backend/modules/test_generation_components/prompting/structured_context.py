from __future__ import annotations

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
from .structured_context_requirement_semantics import (
    _build_requirement_semantics_context,
    _strip_non_semantic_sections,
)
from .structured_context_scope import (
    _build_biz_key_isolation_log,
    _build_supplement_context,
    _dedupe_ordered_texts,
    _extract_reference_module_order,
    _extract_requirement_module_order,
    _merge_module_order,
)
from .structured_control_context import (
    _build_control_context,
    _build_generation_execution_plan_from_blueprints,
    _env_bool,
    _env_float,
    _env_int,
    _workflow_step_execution_label,
)

_PRIORITY_ORDER = ("P0", "P1", "P2")
_MAX_CASES_PER_BUCKET = 5
_MAX_REQUIREMENTS_PER_BIZ = 8


def _extract_requirement_lines(text: str, limit: int) -> list[str]:
    src = _strip_non_semantic_sections(str(text or "")).strip()
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


def build_structured_prompt_context(
    *,
    requirement: str,
    architecture_requirement: str = "",
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
        requirement_text=architecture_requirement or requirement or requirement_context or "",
        flow_context_text=requirement_context or requirement or "",
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
        "module_catalog": list((project_profile.get("functional_architecture") or {}).get("functional_modules") or []),
        "module_interactions": list((project_profile.get("functional_architecture") or {}).get("module_interactions") or []),
        "excluded_modules": list((project_profile.get("functional_architecture") or {}).get("excluded_modules") or []),
        "fact_profile": fact_profile,
        "project_profile": project_profile,
        "context_by_biz": context_by_biz,
    }
