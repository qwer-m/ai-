from __future__ import annotations

import os
from typing import Any

from ..control.feedback_control_state import FeedbackControlState
from ..postprocess.streaming_execution_plan_ordering import execution_side_suite_order_labels
from .structured_context_split_helpers import _clip_text


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


def _workflow_step_execution_label(step: dict[str, Any], *, index: int) -> str:
    step_id = str(step.get("id") or f"step_{index:03d}").strip()
    stage_kind = str(step.get("stage_kind") or "").strip()
    label = str(
        step.get("label")
        or step.get("action")
        or step.get("description")
        or step_id
    ).strip()
    state_in = str(step.get("state_in") or step.get("source_state") or "").strip()
    state_out = str(step.get("state_out") or step.get("target_state") or "").strip()
    state_transition = f"{state_in}->{state_out}" if state_in and state_out else ""
    parts = [part for part in (step_id, stage_kind, label, state_transition) if part]
    return " / ".join(parts)


def _build_generation_execution_plan_from_blueprints(
    workflow_blueprints: list[dict[str, Any]],
) -> dict[str, Any]:
    independent_suite_order = execution_side_suite_order_labels()
    plan_lines: list[str] = [
        "### GENERATION EXECUTION PLAN",
        "* Generate main-chain cases first, in the exact workflow blueprint step order.",
        "* Do not interleave independent suites into the main chain unless a case advances the confirmed workflow state.",
    ]
    blueprint_count = 0
    step_count = 0
    for blueprint in workflow_blueprints[:5]:
        if not isinstance(blueprint, dict):
            continue
        steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
        step_labels = [
            _workflow_step_execution_label(step, index=index)
            for index, step in enumerate(steps[:12], start=1)
        ]
        step_labels = [label for label in step_labels if label.strip()]
        if not step_labels:
            continue
        blueprint_count += 1
        step_count += len(step_labels)
        name = str(blueprint.get("name") or blueprint.get("id") or "workflow").strip()
        plan_lines.append(f"* {name}:")
        plan_lines.extend(
            f"  {index}. {label}"
            for index, label in enumerate(step_labels, start=1)
        )

    if not blueprint_count:
        return {
            "lines": [],
            "blueprint_count": 0,
            "step_count": 0,
            "independent_suite_order": list(independent_suite_order),
        }

    plan_lines.append(
        "* Then generate independent suites in order: "
        + " -> ".join(independent_suite_order)
        + "."
    )
    return {
        "lines": plan_lines,
        "blueprint_count": int(blueprint_count),
        "step_count": int(step_count),
        "independent_suite_order": list(independent_suite_order),
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
    generation_execution_plan = _build_generation_execution_plan_from_blueprints(state.workflow_blueprints)
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
        "generation_execution_plan_blueprint_count": int(generation_execution_plan.get("blueprint_count") or 0),
        "generation_execution_plan_step_count": int(generation_execution_plan.get("step_count") or 0),
        "generation_execution_independent_suite_order": list(
            generation_execution_plan.get("independent_suite_order") or []
        ),
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

    if generation_execution_plan.get("lines"):
        lines.append("")
        lines.extend(list(generation_execution_plan.get("lines") or []))

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
