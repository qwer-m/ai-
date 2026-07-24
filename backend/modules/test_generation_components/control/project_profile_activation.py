from __future__ import annotations

from typing import Any

from core.processing.document_structure import extract_document_structure, normalize_document_text

from .feedback_control_state import FeedbackControlState
from .functional_architecture import extract_functional_architecture, functional_module_names
from .requirement_evidence_view import build_requirement_business_evidence_view
from .semantic_contract import normalize_requirement_semantic_contract
from ..coverage.coverage_analyzer import extract_flow_outline


_MIN_PROJECT_PROFILE_CONFIDENCE = 0.2


def _dedupe_texts(values: Any, *, limit: int = 80) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()
        key = " ".join(text.lower().split())
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _architecture_scoped_flow_context(
    requirement_text: str,
    functional_architecture: dict[str, Any],
) -> str:
    """只从结构候选所在章节提取流程证据，不据此决定最终模块归属。"""
    modules = [
        dict(item)
        for item in (functional_architecture.get("functional_modules") or [])
        if isinstance(item, dict)
        and str(item.get("module_name") or "").strip()
        and str(item.get("scope_status") or "in_scope") == "in_scope"
    ]
    try:
        confidence = float(functional_architecture.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if len(modules) < 2 or confidence < 0.7:
        return ""

    normalized_requirement = normalize_document_text(requirement_text)
    structure = extract_document_structure(normalized_requirement)
    nodes_by_path = {
        tuple(int(part) for part in (item.get("path") or [])): item
        for item in (structure.get("nodes") or [])
        if isinstance(item, dict) and item.get("path")
    }
    scoped_lines: list[str] = []
    allowed_aliases: list[str] = []
    for module in modules:
        allowed_aliases.extend(
            str(alias).strip()
            for alias in (module.get("aliases") or [module.get("module_name")])
            if str(alias).strip()
        )
        path = tuple(int(part) for part in (module.get("structure_path") or []))
        node = nodes_by_path.get(path)
        if node:
            scoped_lines.append(str(node.get("raw_heading") or node.get("title") or ""))
            scoped_lines.extend(str(line) for line in (node.get("section_lines") or []) if str(line).strip())
        else:
            scoped_lines.extend(str(line) for line in (module.get("evidence") or []) if str(line).strip())
            scoped_lines.extend(str(line) for line in (module.get("features") or []) if str(line).strip())

    allowed_aliases = _dedupe_texts(allowed_aliases, limit=80)
    for line in normalized_requirement.splitlines():
        current = str(line or "").strip()
        if current and any(alias in current for alias in allowed_aliases):
            scoped_lines.append(current)
    return "\n".join(_dedupe_texts(scoped_lines, limit=600))


def _empty_flow_outline(*, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "flow_order": [],
        "document_flow_order": [],
        "flow_labels": {},
        "flow_stage_positions": {},
        "cross_cutting": [],
        "cross_cutting_labels": {},
        "data_flow_edges": [],
        "data_flow_order_applied": False,
    }


def _semantic_flow_outline(contract: dict[str, Any]) -> dict[str, Any]:
    workflows = [
        dict(item)
        for item in (contract.get("workflow_blueprints") or [])
        if isinstance(item, dict) and isinstance(item.get("steps"), list)
    ]
    if not workflows:
        return _empty_flow_outline(source="model_semantic_contract")
    blueprint = workflows[0]
    steps = [dict(item) for item in (blueprint.get("steps") or []) if isinstance(item, dict)]
    flow_order: list[str] = []
    flow_labels: dict[str, str] = {}
    for index, step in enumerate(steps, start=1):
        step_id = str(step.get("id") or f"step_{index:03d}").strip()
        if not step_id or step_id in flow_labels:
            continue
        flow_order.append(step_id)
        flow_labels[step_id] = str(step.get("label") or step.get("action") or step_id).strip()
    return {
        "source": "model_semantic_contract",
        "workflow_id": str(blueprint.get("workflow_id") or blueprint.get("id") or "").strip(),
        "flow_order": flow_order,
        "document_flow_order": list(flow_order),
        "flow_labels": flow_labels,
        "flow_stage_positions": {key: index for index, key in enumerate(flow_order)},
        "cross_cutting": [],
        "cross_cutting_labels": {},
        "data_flow_edges": [
            {
                "from": left,
                "to": right,
                "from_label": flow_labels.get(left, left),
                "to_label": flow_labels.get(right, right),
            }
            for left, right in zip(flow_order, flow_order[1:])
        ],
        "data_flow_order_applied": False,
        "initial_state": str(blueprint.get("initial_state") or "").strip(),
        "terminal_states": _dedupe_texts(blueprint.get("terminal_states") or [], limit=8),
        "required_stage_ids": _dedupe_texts(blueprint.get("required_stage_ids") or [], limit=32),
    }


def normalize_project_profile(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flow_outline = payload.get("flow_outline") if isinstance(payload.get("flow_outline"), dict) else {}
    flow_order = _dedupe_texts(flow_outline.get("flow_order") or payload.get("flow_order") or [])
    cross_cutting = _dedupe_texts(flow_outline.get("cross_cutting") or payload.get("cross_cutting") or [])
    flow_labels = dict(flow_outline.get("flow_labels") or payload.get("flow_labels") or {})
    cross_labels = dict(flow_outline.get("cross_cutting_labels") or payload.get("cross_cutting_labels") or {})
    normalized_outline = {
        **dict(flow_outline),
        "flow_order": flow_order,
        "cross_cutting": cross_cutting,
        "flow_labels": {str(k): str(v) for k, v in flow_labels.items() if str(k).strip() and str(v).strip()},
        "cross_cutting_labels": {
            str(k): str(v) for k, v in cross_labels.items() if str(k).strip() and str(v).strip()
        },
        "data_flow_edges": [
            dict(item)
            for item in (flow_outline.get("data_flow_edges") or payload.get("data_flow_edges") or [])
            if isinstance(item, dict)
        ],
        "data_flow_order_applied": bool(
            flow_outline.get("data_flow_order_applied") or payload.get("data_flow_order_applied")
        ),
    }
    functional_architecture = (
        dict(payload.get("functional_architecture"))
        if isinstance(payload.get("functional_architecture"), dict)
        else {}
    )
    functional_modules = [
        dict(item)
        for item in (functional_architecture.get("functional_modules") or [])
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    ]
    excluded_modules = [
        dict(item)
        for item in (functional_architecture.get("excluded_modules") or [])
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    ]
    normalized_architecture = {
        **functional_architecture,
        "functional_modules": functional_modules,
        "excluded_modules": excluded_modules,
        "module_interactions": [
            dict(item)
            for item in (functional_architecture.get("module_interactions") or [])
            if isinstance(item, dict)
        ],
        "shared_capabilities": _dedupe_texts(functional_architecture.get("shared_capabilities") or []),
    }
    semantic_contract = (
        dict(payload.get("requirement_semantic_contract"))
        if isinstance(payload.get("requirement_semantic_contract"), dict)
        else {}
    )
    has_structure = bool(flow_order or cross_cutting or functional_modules or semantic_contract)
    try:
        confidence = float(payload.get("confidence")) if payload.get("confidence") is not None else (
            0.7 if has_structure else 0.0
        )
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "profile_version": str(payload.get("profile_version") or "project-profile-v3"),
        "profile_source": str(payload.get("profile_source") or ("semantic_contract" if semantic_contract else "document_candidates")),
        "confidence": max(0.0, min(1.0, confidence)),
        "flow_outline": normalized_outline,
        "functional_architecture": normalized_architecture,
        "document_structure_candidates": dict(payload.get("document_structure_candidates") or {}),
        "requirement_semantic_contract": semantic_contract,
        "module_order": functional_module_names({"functional_architecture": normalized_architecture}),
        "ordering_policy": str(payload.get("ordering_policy") or "semantic_workflow_order"),
        "scenario_cluster_policy": dict(payload.get("scenario_cluster_policy") or {}),
        "profile_constraints": _dedupe_texts(payload.get("profile_constraints") or ["strategy_only_not_fact_source"]),
        "strategy_only": True,
    }


def build_project_profile(
    *,
    requirement_text: str = "",
    flow_context_text: str = "",
    cases: list[dict[str, Any]] | None = None,
    module_order_hint: list[str] | None = None,
    module_order_source: str = "",
    semantic_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if semantic_contract is not None:
        structure_candidates: dict[str, Any] = {}
        evidence_requirement_text, _ = build_requirement_business_evidence_view(
            requirement_text
        )
        normalized_contract = normalize_requirement_semantic_contract(
            semantic_contract,
            requirement_text=evidence_requirement_text,
            workflow_blueprints=[
                dict(item)
                for item in (semantic_contract.get("workflow_blueprints") or [])
                if isinstance(item, dict)
            ],
        )
        original_status = str(semantic_contract.get("status") or "").strip()
        graph_publishable = bool(
            (normalized_contract.get("semantic_graph_validation") or {}).get(
                "publishable"
            )
        )
        upstream_compile_failed = (
            semantic_contract.get("semantic_compile_success") is False
        )
        if (
            original_status
            and original_status not in {"applied", "empty"}
            and (graph_publishable or upstream_compile_failed)
        ):
            normalized_contract["status"] = original_status
        functional_architecture = dict(normalized_contract.get("functional_architecture") or {})
        outline = _semantic_flow_outline(normalized_contract)
        source = "model_semantic_contract"
        confidence = float(normalized_contract.get("confidence") or 0.0)
        if not confidence:
            workflow_confidences = [
                float(item.get("confidence") or 0.0)
                for item in (normalized_contract.get("workflow_blueprints") or [])
                if isinstance(item, dict)
            ]
            confidence = max(workflow_confidences or [0.0])
    else:
        # 仅供没有模型语义编译阶段的独立分析调用；生产生成会显式传入契约或失败状态。
        structure_candidates = extract_functional_architecture(requirement_text)
        normalized_contract = {}
        functional_architecture = structure_candidates
        architecture_modules = [
            str(item.get("module_name") or "").strip()
            for item in (functional_architecture.get("functional_modules") or [])
            if isinstance(item, dict) and str(item.get("module_name") or "").strip()
        ]
        hint_cases = [
            {"id": f"PROFILE-MODULE-{index:03d}", "test_module": str(module).strip()}
            for index, module in enumerate(module_order_hint or [], start=1)
            if str(module or "").strip()
        ]
        candidate_cases = hint_cases or [item for item in (cases or []) if isinstance(item, dict)]
        architecture_flow_context = _architecture_scoped_flow_context(requirement_text, functional_architecture)
        outline = extract_flow_outline(
            architecture_flow_context or flow_context_text or requirement_text,
            candidate_cases,
        )
        if architecture_flow_context:
            outline = {
                **outline,
                "scope_source": "functional_architecture_candidates",
                "scope_module_count": len(architecture_modules),
            }
        source = str(module_order_source or "document_structure_candidates") if hint_cases else str(
            functional_architecture.get("source") or "document_structure_candidates"
        )
        confidence = float(functional_architecture.get("confidence") or (0.7 if outline.get("flow_order") else 0.0))

    return normalize_project_profile(
        {
            "profile_source": source,
            "flow_outline": outline,
            "functional_architecture": functional_architecture,
            "document_structure_candidates": structure_candidates,
            "requirement_semantic_contract": normalized_contract,
            "confidence": confidence,
        }
    )


def merge_project_profile_control_state(base_state: Any, project_profile: dict[str, Any] | None) -> FeedbackControlState:
    normalized_base = FeedbackControlState.from_any(base_state)
    profile = normalize_project_profile(project_profile or {})
    outline = dict(profile.get("flow_outline") or {})
    architecture = dict(profile.get("functional_architecture") or {})
    has_profile = bool(
        outline.get("flow_order")
        or outline.get("cross_cutting")
        or architecture.get("functional_modules")
        or architecture.get("module_interactions")
    )
    if not profile or not has_profile:
        return normalized_base
    try:
        confidence = float(profile.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_PROJECT_PROFILE_CONFIDENCE:
        return normalized_base.merge(
            {
                "source_meta": {
                    "sources": ["project_profile_domain_gate"],
                    "project_profile_gate": {
                        "allowed": False,
                        "reason": "low_project_profile_confidence",
                        "confidence": confidence,
                        "min_confidence": _MIN_PROJECT_PROFILE_CONFIDENCE,
                    },
                }
            }
        )
    return normalized_base.merge(
        {
            "source_meta": {
                "sources": ["project_profile"],
                "project_profile": profile,
            }
        }
    )


__all__ = [
    "build_project_profile",
    "merge_project_profile_control_state",
    "normalize_project_profile",
]
