from __future__ import annotations

import re
from typing import Any

from ..control.actor_roles import normalize_actor_role as normalize_actor_role_value


def workflow_blueprint_source_label(
    workflow_blueprints: list[dict[str, Any]],
    plan_workflow_blueprints: list[dict[str, Any]],
) -> str:
    if workflow_blueprints:
        for blueprint in workflow_blueprints:
            repository_source = str(blueprint.get("repository_source") or blueprint.get("source") or "").strip()
            source_type = str(blueprint.get("source_type") or "").strip()
            if repository_source == "current_requirement_blueprint" or source_type == "current_requirement_extracted":
                return "current_requirement_blueprint"
        return "feedback_control_state"
    if plan_workflow_blueprints:
        return "current_generation_cases"
    return "none"


def stage_match_patterns(step: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    raw_keywords: list[str] = []
    for key in ("match_keywords", "keywords", "aliases"):
        value = step.get(key)
        if isinstance(value, list):
            raw_keywords.extend(str(item).strip() for item in value if str(item).strip())
    if bool(step.get("allow_bridge")) and raw_keywords:
        return tuple((keyword.lower(),) for keyword in raw_keywords if str(keyword or "").strip())
    for key in ("label", "action", "module", "assertion", "state_in", "state_out"):
        value = str(step.get(key) or "").strip()
        if value:
            raw_keywords.append(value)
    patterns: list[tuple[str, ...]] = []
    for keyword in raw_keywords:
        compact = str(keyword or "").strip().lower()
        if compact:
            patterns.append((compact,))
    return tuple(patterns)


def pattern_match_score(text: str, patterns: tuple[tuple[str, ...], ...]) -> int:
    normalized_text = str(text or "")
    best = 0
    for pattern in patterns:
        tokens = [str(token or "").strip().lower() for token in pattern if str(token or "").strip()]
        if not tokens:
            continue
        if all(token in normalized_text for token in tokens):
            best = max(best, sum(len(token) for token in tokens))
            continue
        if len(tokens) == 1:
            parts = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", tokens[0])
            if parts and all(part in normalized_text for part in parts[:6]):
                best = max(best, sum(len(part) for part in parts[:6]))
    return best


def main_chain_stages_from_blueprints(
    plan_workflow_blueprints: list[dict[str, Any]],
) -> tuple[
    list[tuple[str, str, tuple[tuple[str, ...], ...]]],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    stages: list[tuple[str, str, tuple[tuple[str, ...], ...]]] = []
    workflow_stage_meta_by_key: dict[str, dict[str, Any]] = {}
    workflow_stage_output_state: dict[str, str] = {}
    for blueprint_index, blueprint in enumerate(plan_workflow_blueprints[:3], start=1):
        steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
        if len(steps) < 2:
            continue
        for step_index, step in enumerate(steps[:12], start=1):
            stage_key = str(step.get("id") or f"bp{blueprint_index}_step_{step_index:03d}").strip()
            stage_label = str(
                step.get("label")
                or step.get("action")
                or step.get("description")
                or stage_key
            ).strip()
            patterns = stage_match_patterns(step)
            if not stage_key or not stage_label or not patterns:
                continue
            stage_text = " ".join(
                str(step.get(key) or "")
                for key in ("label", "action", "description", "module", "assertion", "state_out")
            )
            workflow_stage_meta_by_key[stage_key] = {
                **step,
                "actor": normalize_actor_role_value(
                    step.get("actor") or step.get("role"),
                    fallback_text=stage_text,
                ),
                "source_actor_role": str(
                    step.get("source_actor_role") or step.get("actor") or step.get("role") or ""
                ).strip(),
                "blueprint_id": str(blueprint.get("id") or f"blueprint_{blueprint_index}"),
                "blueprint_name": str(blueprint.get("name") or blueprint.get("title") or "workflow_blueprint"),
                "step_index": int(step_index),
            }
            state_out = str(step.get("state_out") or "").strip()
            if state_out:
                workflow_stage_output_state[stage_key] = state_out
            stages.append((stage_key, stage_label, patterns))
        if stages:
            break
    return stages, workflow_stage_meta_by_key, workflow_stage_output_state
