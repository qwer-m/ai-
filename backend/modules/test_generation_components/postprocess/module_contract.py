from __future__ import annotations

import re
import unicodedata
from typing import Any

from .case_access import case_flat_text, case_text_field
from ..control.semantic_contract import normalize_case_semantic


FUNCTIONAL_PHASE_FIELDS = (
    "functional_phase",
    "functional_module_anchor",
    "functional_interaction_modules",
    "functional_interaction_ids",
)


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s\-_/\\:：]+", "", text)


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


def _architecture_interactions(project_profile: Any) -> list[dict[str, Any]]:
    profile = dict(project_profile or {}) if isinstance(project_profile, dict) else {}
    architecture = profile.get("functional_architecture")
    if not isinstance(architecture, dict):
        return []
    return [
        dict(item)
        for item in (architecture.get("module_interactions") or [])
        if isinstance(item, dict)
        and str(item.get("interaction_id") or "").strip()
        and str(item.get("source_module_key") or item.get("source_module") or "").strip()
        and str(item.get("target_module_key") or item.get("target_module") or "").strip()
        and str(item.get("trigger") or "").strip()
    ]


def functional_architecture_generation_context(project_profile: Any) -> dict[str, list[dict[str, Any]]]:
    """返回可供生成链路消费的已核验活动架构，不分配模块配额。"""
    modules = [
        dict(item)
        for item in _module_catalog(project_profile)
        if item.get("evidence_verified") is True
    ]
    active_module_keys = {
        _key(item.get("module_key"))
        for item in modules
        if _key(item.get("module_key"))
    }
    interactions = [
        dict(item)
        for item in _architecture_interactions(project_profile)
        if item.get("evidence_verified") is True
        and _key(item.get("source_module_key")) in active_module_keys
        and _key(item.get("target_module_key")) in active_module_keys
    ]
    return {
        "functional_modules": modules,
        "module_interactions": interactions,
    }


def _interaction_modules(phase: dict[str, Any]) -> list[str]:
    modules: list[str] = []
    for item in phase.get("interactions") or []:
        if not isinstance(item, dict):
            continue
        for key in ("source_module", "target_module"):
            value = str(item.get(key) or "").strip()
            if value and value not in modules:
                modules.append(value)
    return modules


def _interaction_ids(phase: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(item.get("interaction_id") or "").strip()
            for item in (phase.get("interactions") or [])
            if isinstance(item, dict) and str(item.get("interaction_id") or "").strip()
        )
    )


def case_matches_functional_phase(case: Any, phase: Any) -> bool:
    if not isinstance(case, dict):
        return False
    current_phase = dict(phase or {}) if isinstance(phase, dict) else {}
    if str(current_phase.get("phase") or "").strip() != "cross_module":
        return True
    expected_ids = set(_interaction_ids(current_phase))
    if not expected_ids:
        return False
    semantic = normalize_case_semantic(
        case.get("_semantic"),
        case_text=case_flat_text(
            case,
            fields=("test_module", "description", "preconditions", "steps", "test_input", "expected_result"),
            separator=" ",
        ),
    )
    return bool(expected_ids.intersection(str(item) for item in semantic.get("interaction_ids") or []))


def apply_functional_module_phase(
    cases: list[dict[str, Any]],
    phase: Any,
) -> list[dict[str, Any]]:
    """附着契约阶段元数据，不覆盖模型输出的公开模块字段。"""
    current_phase = dict(phase or {}) if isinstance(phase, dict) else {}
    phase_name = str(current_phase.get("phase") or "").strip()
    target_module = str(current_phase.get("module_name") or "").strip()
    interaction_modules = _interaction_modules(current_phase)
    interaction_ids = _interaction_ids(current_phase)
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
        elif phase_name == "cross_module":
            case["functional_interaction_modules"] = list(interaction_modules)
            case["functional_interaction_ids"] = list(interaction_ids)
        output.append(case)
    return output


def functional_phase_key(case: Any) -> str:
    if not isinstance(case, dict):
        return ""
    phase = str(case.get("functional_phase") or "").strip()
    if phase == "cross_module":
        return "cross_module"
    module_name = str(case.get("functional_module_anchor") or case_text_field(case, "test_module") or "").strip()
    return f"module_internal:{module_name}" if module_name else ""


def summarize_functional_phase_coverage(
    cases: list[dict[str, Any]],
    *,
    project_profile: Any,
    target_count: int,
) -> dict[str, Any]:
    """只报告全局架构的实际覆盖，不制造按模块均分的目标或缺口。"""
    architecture = functional_architecture_generation_context(project_profile)
    modules = [dict(item) for item in architecture.get("functional_modules") or []]
    interactions = [dict(item) for item in architecture.get("module_interactions") or []]
    if not modules:
        return {
            "applied": False,
            "module_counts": {},
            "interaction_counts": {},
            "phase_counts": {},
            "uncovered_modules": [],
            "uncovered_interactions": [],
            "uncovered_structured_facts": [],
        }

    alias_to_module, key_to_module = _module_indexes(modules)
    module_key_by_name = {
        str(item.get("module_name") or "").strip(): str(item.get("module_key") or "").strip()
        for item in modules
    }
    interaction_by_id = {
        _key(item.get("interaction_id")): item
        for item in interactions
        if _key(item.get("interaction_id"))
    }
    module_counts = {
        str(item.get("module_key") or "").strip(): 0
        for item in modules
        if str(item.get("module_key") or "").strip()
    }
    interaction_counts = {
        str(item.get("interaction_id") or "").strip(): 0
        for item in interactions
        if str(item.get("interaction_id") or "").strip()
    }
    phase_counts: dict[str, int] = {}
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case = dict(raw_case)
        semantic = _normalized_case_semantic(case)
        module_name = _semantic_primary_module(
            case,
            alias_to_module=alias_to_module,
            key_to_module=key_to_module,
        ) or alias_to_module.get(_key(case_text_field(case, "test_module")), "")
        module_key = module_key_by_name.get(module_name, "")
        if module_key:
            module_counts[module_key] = int(module_counts.get(module_key) or 0) + 1

        valid_interaction_ids: list[str] = []
        for interaction_id in semantic.get("interaction_ids") or []:
            interaction = interaction_by_id.get(_key(interaction_id))
            if not interaction:
                continue
            canonical_id = str(interaction.get("interaction_id") or "").strip()
            if canonical_id and canonical_id not in valid_interaction_ids:
                valid_interaction_ids.append(canonical_id)
                interaction_counts[canonical_id] = int(interaction_counts.get(canonical_id) or 0) + 1
        phase_key = "cross_module" if valid_interaction_ids else (
            f"module_internal:{module_name}" if module_name else ""
        )
        if phase_key:
            phase_counts[phase_key] = int(phase_counts.get(phase_key) or 0) + 1

    uncovered_modules = [key for key, count in module_counts.items() if int(count or 0) <= 0]
    uncovered_interactions = [key for key, count in interaction_counts.items() if int(count or 0) <= 0]
    uncovered_structured_facts = [
        {
            "fact_type": "functional_module",
            "module_key": module_key,
            "module_name": str(key_to_module.get(_key(module_key)) or ""),
        }
        for module_key in uncovered_modules
    ]
    uncovered_structured_facts.extend(
        {
            "fact_type": "module_interaction",
            "interaction_id": interaction_id,
        }
        for interaction_id in uncovered_interactions
    )
    return {
        "applied": True,
        "requested_case_count": max(0, int(target_count or 0)),
        "module_counts": module_counts,
        "interaction_counts": interaction_counts,
        "phase_counts": phase_counts,
        "uncovered_modules": uncovered_modules,
        "uncovered_interactions": uncovered_interactions,
        "uncovered_structured_facts": uncovered_structured_facts,
    }


def _module_indexes(catalog: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    aliases: dict[str, str] = {}
    keys: dict[str, str] = {}
    for item in catalog:
        module_name = str(item.get("module_name") or "").strip()
        module_key = str(item.get("module_key") or "").strip()
        if module_key:
            keys[_key(module_key)] = module_name
        for alias in [module_name, *[str(value) for value in (item.get("aliases") or [])]]:
            alias_key = _key(alias)
            if alias_key:
                aliases.setdefault(alias_key, module_name)
    return aliases, keys


def _normalized_case_semantic(case: dict[str, Any]) -> dict[str, Any]:
    return normalize_case_semantic(
        case.get("_semantic"),
        case_text=case_flat_text(
            case,
            fields=("test_module", "description", "preconditions", "steps", "test_input", "expected_result"),
            separator=" ",
        ),
    )


def _semantic_primary_module(
    case: dict[str, Any],
    *,
    alias_to_module: dict[str, str],
    key_to_module: dict[str, str],
) -> str:
    semantic = _normalized_case_semantic(case)
    candidates = [dict(item) for item in (semantic.get("module_candidates") or []) if isinstance(item, dict)]
    candidates.sort(
        key=lambda item: (
            0 if str(item.get("role") or "") == "primary" else 1,
            -float(item.get("confidence") or 0.0),
            str(item.get("module_key") or item.get("module_name") or ""),
        )
    )
    for item in candidates:
        resolved = key_to_module.get(_key(item.get("module_key"))) or alias_to_module.get(
            _key(item.get("module_name"))
        )
        if resolved:
            return resolved
    return ""


def enforce_functional_module_contract(
    cases: list[dict[str, Any]],
    *,
    project_profile: Any,
    inherit_execution_context: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """仅按精确别名或结构化模块候选归一，不再从正文和前缀猜模块。"""
    catalog = _module_catalog(project_profile)
    if not catalog:
        return [dict(item) for item in cases if isinstance(item, dict)], {
            "applied": False,
            "allowed_modules": [],
            "normalized_count": 0,
            "rejected_count": 0,
            "rejected_modules": [],
            "rejected_interaction_count": 0,
            "rejected_interactions": [],
        }
    alias_to_module, key_to_module = _module_indexes(catalog)
    allowed = [str(item.get("module_name") or "").strip() for item in catalog]
    architecture = functional_architecture_generation_context(project_profile)
    interactions_by_id = {
        _key(item.get("interaction_id")): dict(item)
        for item in (architecture.get("module_interactions") or [])
        if isinstance(item, dict) and _key(item.get("interaction_id"))
    }
    accepted: list[dict[str, Any]] = []
    rejected_modules: list[str] = []
    rejected_interactions: list[str] = []
    normalized_count = 0
    semantic_resolution_count = 0
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case = dict(raw_case)
        raw_module = case_text_field(case, "test_module")
        raw_resolved = alias_to_module.get(_key(raw_module), "")
        semantic_resolved = _semantic_primary_module(
            case,
            alias_to_module=alias_to_module,
            key_to_module=key_to_module,
        )
        if raw_resolved and semantic_resolved and raw_resolved != semantic_resolved:
            rejected_modules.append(raw_module or "(empty)")
            continue
        resolved = semantic_resolved or raw_resolved
        if semantic_resolved:
            semantic_resolution_count += 1
        if not resolved:
            rejected_modules.append(raw_module or "(empty)")
            continue
        semantic = _normalized_case_semantic(case)
        declared_interaction_ids = [
            str(item).strip()
            for item in (semantic.get("interaction_ids") or [])
            if str(item).strip()
        ]
        unknown_interaction_ids = [
            item for item in declared_interaction_ids if _key(item) not in interactions_by_id
        ]
        if unknown_interaction_ids:
            rejected_interactions.extend(unknown_interaction_ids)
            continue
        if raw_module != resolved:
            case["test_module"] = resolved
            for alias in ("module", "testModule", "所属模块", "功能模块"):
                if alias in case:
                    case[alias] = resolved
            normalized_count += 1
        for field in FUNCTIONAL_PHASE_FIELDS:
            case.pop(field, None)
        if declared_interaction_ids:
            canonical_interaction_ids: list[str] = []
            interaction_modules: list[str] = []
            for interaction_id in declared_interaction_ids:
                interaction = interactions_by_id.get(_key(interaction_id)) or {}
                canonical_id = str(interaction.get("interaction_id") or "").strip()
                if canonical_id and canonical_id not in canonical_interaction_ids:
                    canonical_interaction_ids.append(canonical_id)
                for module_field in ("source_module", "target_module"):
                    module_name = str(interaction.get(module_field) or "").strip()
                    if module_name and module_name not in interaction_modules:
                        interaction_modules.append(module_name)
            case["functional_phase"] = "cross_module"
            case["functional_interaction_ids"] = canonical_interaction_ids
            case["functional_interaction_modules"] = interaction_modules
        else:
            case["functional_phase"] = "module_internal"
            case["functional_module_anchor"] = resolved
        accepted.append(case)

    phase_counts: dict[str, int] = {}
    for case in accepted:
        phase_key = functional_phase_key(case)
        if phase_key:
            phase_counts[phase_key] = int(phase_counts.get(phase_key) or 0) + 1
    return accepted, {
        "applied": True,
        "allowed_modules": allowed,
        "normalized_count": normalized_count,
        "semantic_resolution_count": semantic_resolution_count,
        "rejected_count": len(rejected_modules) + len(rejected_interactions),
        "rejected_modules": list(dict.fromkeys(rejected_modules))[:30],
        "rejected_interaction_count": len(rejected_interactions),
        "rejected_interactions": list(dict.fromkeys(rejected_interactions))[:30],
        "functional_phase_counts": phase_counts,
        "execution_context_inheritance_applied": False,
        "inherit_execution_context_requested": bool(inherit_execution_context),
    }
