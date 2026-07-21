from __future__ import annotations

import re
import unicodedata
from typing import Any

from .case_access import case_flat_text, case_text_field


FUNCTIONAL_PHASE_FIELDS = (
    "functional_phase",
    "functional_module_anchor",
    "functional_interaction_modules",
)


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s\-_/\\:：>]+", "", text)


def _module_catalog(project_profile: Any) -> list[dict[str, Any]]:
    profile = dict(project_profile or {}) if isinstance(project_profile, dict) else {}
    architecture = profile.get("functional_architecture")
    if not isinstance(architecture, dict):
        return []
    return [
        dict(item)
        for item in (architecture.get("functional_modules") or [])
        if isinstance(item, dict)
        and str(item.get("module_name") or "").strip()
        and str(item.get("scope_status") or "in_scope") == "in_scope"
    ]


def build_functional_module_batch_plan(
    project_profile: Any,
    *,
    expected_count: int,
) -> list[dict[str, Any]]:
    """按一级功能模块和跨模块交互分配生成阶段。"""
    profile = dict(project_profile or {}) if isinstance(project_profile, dict) else {}
    architecture = profile.get("functional_architecture")
    if not isinstance(architecture, dict):
        return []
    modules = _module_catalog(profile)
    if not modules:
        return []
    phases: list[dict[str, Any]] = [
        {
            "phase": "module_internal",
            "module_name": str(item.get("module_name") or "").strip(),
            "features": [str(value).strip() for value in (item.get("features") or []) if str(value).strip()],
        }
        for item in modules
    ]
    interactions = [
        dict(item)
        for item in (architecture.get("module_interactions") or [])
        if isinstance(item, dict)
        and str(item.get("source_module") or "").strip()
        and str(item.get("target_module") or "").strip()
        and str(item.get("source_module") or "").strip() != str(item.get("target_module") or "").strip()
        and str(item.get("trigger") or "").strip()
    ]
    if len(modules) > 1 and interactions:
        phases.append(
            {
                "phase": "cross_module",
                "module_name": "",
                "features": [],
                "interactions": interactions,
            }
        )
    remaining = max(1, int(expected_count or 1))
    base, extra = divmod(remaining, len(phases))
    for index, phase in enumerate(phases):
        phase["target_count"] = max(1, base + (1 if index < extra else 0))
    return phases


def _interaction_modules(phase: dict[str, Any]) -> list[str]:
    modules: list[str] = []
    for item in (phase.get("interactions") or []):
        if not isinstance(item, dict):
            continue
        for key in ("source_module", "target_module"):
            value = str(item.get(key) or "").strip()
            if value and value not in modules:
                modules.append(value)
    return modules


def case_matches_functional_phase(case: Any, phase: Any) -> bool:
    if not isinstance(case, dict):
        return False
    current_phase = dict(phase or {}) if isinstance(phase, dict) else {}
    if str(current_phase.get("phase") or "").strip() != "cross_module":
        return True
    case_text = case_flat_text(
        case,
        fields=("test_module", "description", "preconditions", "steps", "test_input", "expected_result"),
        separator=" ",
        lower=True,
    )
    compact_case_text = _key(case_text)
    case_module = _key(case_text_field(case, "test_module"))
    for raw_interaction in (current_phase.get("interactions") or []):
        if not isinstance(raw_interaction, dict):
            continue
        interaction = dict(raw_interaction)
        source = _key(interaction.get("source_module"))
        target = _key(interaction.get("target_module"))
        if not source or not target or source == target:
            continue
        source_hit = source in compact_case_text
        target_hit = case_module == target or target in compact_case_text
        if source_hit and target_hit:
            return True
    return False


def apply_functional_module_phase(
    cases: list[dict[str, Any]],
    phase: Any,
) -> list[dict[str, Any]]:
    """把功能阶段作为内部元数据附着到用例，供评审和补充阶段继续使用。"""
    current_phase = dict(phase or {}) if isinstance(phase, dict) else {}
    phase_name = str(current_phase.get("phase") or "").strip()
    target_module = str(current_phase.get("module_name") or "").strip()
    interaction_modules = _interaction_modules(current_phase)
    output: list[dict[str, Any]] = []
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        if phase_name == "cross_module" and not case_matches_functional_phase(raw_case, current_phase):
            continue
        case = dict(raw_case)
        if phase_name:
            case["functional_phase"] = phase_name
        if phase_name == "module_internal" and target_module:
            case["functional_module_anchor"] = target_module
            case["test_module"] = target_module
            for alias in ("module", "testModule", "所属模块", "功能模块"):
                if alias in case:
                    case[alias] = target_module
        elif phase_name == "cross_module":
            case["functional_interaction_modules"] = list(interaction_modules)
        output.append(case)
    return output


def functional_phase_key(case: Any) -> str:
    if not isinstance(case, dict):
        return ""
    phase = str(case.get("functional_phase") or "").strip()
    if phase == "cross_module":
        return "cross_module"
    module_name = str(
        case.get("functional_module_anchor")
        or case_text_field(case, "test_module")
        or ""
    ).strip()
    return f"module_internal:{module_name}" if module_name else ""


def _phase_key(phase: dict[str, Any]) -> str:
    phase_name = str(phase.get("phase") or "").strip()
    if phase_name == "cross_module":
        return "cross_module"
    module_name = str(phase.get("module_name") or "").strip()
    return f"module_internal:{module_name}" if module_name else ""


def _functional_phase_coverage_state(
    cases: list[dict[str, Any]],
    *,
    project_profile: Any,
    target_count: int,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    plan = build_functional_module_batch_plan(
        project_profile,
        expected_count=max(1, int(target_count or len(cases) or 1)),
    )
    phase_targets = {
        _phase_key(item): int(item.get("target_count") or 0)
        for item in plan
        if _phase_key(item)
    }
    phase_counts: dict[str, int] = {}
    for item in cases:
        key = functional_phase_key(item)
        if key:
            phase_counts[key] = int(phase_counts.get(key) or 0) + 1
    return plan, phase_targets, phase_counts


def summarize_functional_phase_coverage(
    cases: list[dict[str, Any]],
    *,
    project_profile: Any,
    target_count: int,
) -> dict[str, Any]:
    plan, phase_targets, phase_counts = _functional_phase_coverage_state(
        [dict(item) for item in cases if isinstance(item, dict)],
        project_profile=project_profile,
        target_count=target_count,
    )
    return {
        "applied": bool(plan),
        "phase_targets": phase_targets,
        "phase_counts": phase_counts,
        "remaining_deficits": {
            key: max(0, int(target) - int(phase_counts.get(key) or 0))
            for key, target in phase_targets.items()
        },
    }


def rebalance_functional_phase_coverage(
    selected_cases: list[dict[str, Any]],
    *,
    candidate_cases: list[dict[str, Any]],
    project_profile: Any,
    target_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从已有候选池补回评审阶段丢失的功能阶段，不制造新用例。"""
    selected = [dict(item) for item in selected_cases if isinstance(item, dict)]
    candidates = [dict(item) for item in candidate_cases if isinstance(item, dict)]
    plan, phase_targets, phase_counts = _functional_phase_coverage_state(
        selected,
        project_profile=project_profile,
        target_count=target_count,
    )
    if not plan:
        return selected, {"applied": False, "added_count": 0, "phase_targets": {}, "phase_counts": {}}

    existing_ids = {str(item.get("id") or "").strip() for item in selected if str(item.get("id") or "").strip()}
    existing_text = {
        _key(case_flat_text(item, fields=("test_module", "description", "test_input", "expected_result")))
        for item in selected
    }
    added: list[dict[str, Any]] = []
    for phase in plan:
        key = _phase_key(phase)
        deficit = max(0, int(phase_targets.get(key) or 0) - int(phase_counts.get(key) or 0))
        if deficit <= 0:
            continue
        for item in candidates:
            if deficit <= 0:
                break
            if functional_phase_key(item) != key:
                continue
            case_id = str(item.get("id") or "").strip()
            text_key = _key(case_flat_text(item, fields=("test_module", "description", "test_input", "expected_result")))
            if (case_id and case_id in existing_ids) or (text_key and text_key in existing_text):
                continue
            selected.append(dict(item))
            added.append(dict(item))
            if case_id:
                existing_ids.add(case_id)
            if text_key:
                existing_text.add(text_key)
            phase_counts[key] = int(phase_counts.get(key) or 0) + 1
            deficit -= 1

    return selected, {
        "applied": True,
        "added_count": int(len(added)),
        "phase_targets": phase_targets,
        "phase_counts": phase_counts,
        "remaining_deficits": {
            key: max(0, int(target) - int(phase_counts.get(key) or 0))
            for key, target in phase_targets.items()
        },
    }


def build_functional_supplement_plan(
    project_profile: Any,
    *,
    current_cases: list[dict[str, Any]],
    target_count: int,
    supplement_needed: int,
    max_batch_size: int = 10,
    max_batches: int = 4,
) -> list[dict[str, Any]]:
    """按最终阶段缺口生成定向补充计划，避免补充用例集中到单一模块。"""
    phases = build_functional_module_batch_plan(project_profile, expected_count=max(1, int(target_count or 1)))
    if not phases:
        return []
    counts: dict[str, int] = {}
    for item in current_cases:
        key = functional_phase_key(item)
        if key:
            counts[key] = int(counts.get(key) or 0) + 1

    remaining = max(1, int(supplement_needed or 1))
    plan: list[dict[str, Any]] = []
    ordered = sorted(
        phases,
        key=lambda item: (
            -(max(0, int(item.get("target_count") or 0) - int(counts.get(_phase_key(item)) or 0))),
            phases.index(item),
        ),
    )
    for phase in ordered:
        if remaining <= 0 or len(plan) >= max(1, int(max_batches or 1)):
            break
        key = _phase_key(phase)
        deficit = max(0, int(phase.get("target_count") or 0) - int(counts.get(key) or 0))
        if deficit <= 0:
            continue
        count = min(max(1, int(max_batch_size or 1)), deficit, remaining)
        plan.append({**dict(phase), "target_count": int(count), "phase_key": key})
        remaining -= count
    if remaining > 0 and plan:
        index = 0
        while remaining > 0 and len(plan) < max(1, int(max_batches or 1)):
            phase = ordered[index % len(ordered)]
            count = min(max(1, int(max_batch_size or 1)), remaining)
            plan.append({**dict(phase), "target_count": int(count), "phase_key": _phase_key(phase)})
            remaining -= count
            index += 1
    return plan


def enforce_functional_module_contract(
    cases: list[dict[str, Any]],
    *,
    project_profile: Any,
    inherit_execution_context: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """归一可证明的模块别名，并拒绝无法归属到一级模块目录的用例。"""
    catalog = _module_catalog(project_profile)
    if not catalog:
        return [dict(item) for item in cases if isinstance(item, dict)], {
            "applied": False,
            "allowed_modules": [],
            "normalized_count": 0,
            "rejected_count": 0,
            "rejected_modules": [],
        }

    alias_to_module: dict[str, str] = {}
    aliases_by_module: dict[str, list[str]] = {}
    allowed: list[str] = []
    for item in catalog:
        module_name = str(item.get("module_name") or "").strip()
        allowed.append(module_name)
        aliases = [module_name, *[str(value) for value in (item.get("aliases") or [])]]
        alias_keys = [value for value in (_key(alias) for alias in aliases) if value]
        aliases_by_module[module_name] = alias_keys
        for alias_key in alias_keys:
            alias_to_module.setdefault(alias_key, module_name)

    resolved_rows: list[tuple[dict[str, Any], str, str]] = []
    rejected_modules: list[str] = []
    normalized_count = 0
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case = dict(raw_case)
        raw_module = case_text_field(case, "test_module")
        raw_key = _key(raw_module)
        resolved = alias_to_module.get(raw_key, "")
        if not resolved and raw_key:
            prefix_hits = {
                module_name
                for module_name, alias_keys in aliases_by_module.items()
                if any(len(alias_key) >= 2 and raw_key.startswith(alias_key) for alias_key in alias_keys)
            }
            if len(prefix_hits) == 1:
                resolved = next(iter(prefix_hits))
        if not resolved:
            case_text = _key(
                case_flat_text(
                    case,
                    fields=("description", "preconditions", "steps", "test_input", "expected_result"),
                    separator=" ",
                )
            )
            evidence_hits = {
                module_name
                for module_name, alias_keys in aliases_by_module.items()
                if any(len(alias_key) >= 2 and alias_key in case_text for alias_key in alias_keys)
            }
            if len(evidence_hits) == 1:
                resolved = next(iter(evidence_hits))
        resolved_rows.append((case, raw_module, resolved))

    if inherit_execution_context:
        for index, (case, raw_module, resolved) in enumerate(resolved_rows):
            if resolved:
                continue
            is_materialized = bool(
                case.get("workflow_contract_materialized_case")
                or case.get("generated_bridge_case")
                or case.get("workflow_blueprint_bridge")
            )
            if not is_materialized:
                continue
            inherited = ""
            for previous in range(index - 1, -1, -1):
                if resolved_rows[previous][2]:
                    inherited = resolved_rows[previous][2]
                    break
            if not inherited:
                for following in range(index + 1, len(resolved_rows)):
                    if resolved_rows[following][2]:
                        inherited = resolved_rows[following][2]
                        break
            if inherited:
                resolved_rows[index] = (case, raw_module, inherited)

    accepted: list[dict[str, Any]] = []
    for case, raw_module, resolved in resolved_rows:
        if not resolved:
            rejected_modules.append(raw_module or "(empty)")
            continue
        if raw_module != resolved:
            case["test_module"] = resolved
            for alias in ("module", "testModule", "所属模块", "功能模块"):
                if alias in case:
                    case[alias] = resolved
            normalized_count += 1
        accepted.append(case)

    phase_counts: dict[str, int] = {}
    for case in accepted:
        phase_key = functional_phase_key(case)
        if phase_key:
            phase_counts[phase_key] = int(phase_counts.get(phase_key) or 0) + 1

    return accepted, {
        "applied": True,
        "allowed_modules": allowed,
        "normalized_count": int(normalized_count),
        "rejected_count": int(len(rejected_modules)),
        "rejected_modules": list(dict.fromkeys(rejected_modules))[:30],
        "functional_phase_counts": phase_counts,
    }
