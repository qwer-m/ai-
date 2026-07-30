from __future__ import annotations

from typing import Any


def _positive_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _verified_items(
    semantic: dict[str, Any],
    key: str,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in semantic.get(key) or []:
        if not isinstance(item, dict) or item.get("evidence_verified") is not True:
            continue
        confidence = _positive_confidence(item.get("confidence"))
        if confidence <= 0.0:
            continue
        compact = {
            field: item.get(field)
            for field in fields
            if item.get(field) not in (None, "", [])
        }
        compact["confidence"] = confidence
        output.append(compact)
    return output


def compact_verified_case_semantics(case: dict[str, Any]) -> dict[str, Any]:
    """只把已核验的用例语义交给全局 Review，避免正文推断反向成为事实。"""
    semantic = case.get("_semantic")
    semantic = dict(semantic) if isinstance(semantic, dict) else {}
    summary = {
        "module_candidates": _verified_items(
            semantic,
            "module_candidates",
            ("module_key", "module_name", "role"),
        ),
        "fact_ids": [
            str(item).strip()
            for item in (semantic.get("fact_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ],
        "interaction_ids": [
            str(item).strip()
            for item in (semantic.get("interaction_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ],
        "workflow_stage_candidates": _verified_items(
            semantic,
            "workflow_stage_candidates",
            ("workflow_id", "stage_id", "stage_kind"),
        ),
        "precondition_states": _verified_items(
            semantic,
            "precondition_states",
            ("entity", "state", "source", "scope", "polarity", "temporal"),
        ),
        "produced_states": _verified_items(
            semantic,
            "produced_states",
            ("entity", "state", "source", "scope", "polarity", "temporal"),
        ),
    }
    return summary if any(summary.values()) else {}


def compact_structured_case_risk(case: dict[str, Any]) -> dict[str, Any]:
    """风险摘要只消费显式布尔值和 path_type，不解析正文关键词。"""
    output: dict[str, Any] = {}
    for key in ("critical", "blocking", "destructive", "can_advance_main_flow"):
        if isinstance(case.get(key), bool):
            output[key] = bool(case.get(key))
    path_type = str(case.get("path_type") or "").strip()
    if path_type:
        output["path_type"] = path_type
    return output


def compact_review_contract_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """将需求级契约压缩为 Review 可直接消费的全局结构。"""
    payload = dict(context or {})
    architecture = payload.get("functional_architecture")
    architecture = dict(architecture) if isinstance(architecture, dict) else {}
    workflows: list[dict[str, Any]] = []
    for blueprint in payload.get("workflow_blueprints") or []:
        if not isinstance(blueprint, dict):
            continue
        steps: list[dict[str, Any]] = []
        for step in blueprint.get("steps") or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                {
                    key: step.get(key)
                    for key in (
                        "id",
                        "label",
                        "action",
                        "actor",
                        "source_actor_role",
                        "stage_kind",
                        "path_type",
                        "required",
                        "terminal",
                        "critical",
                        "blocking",
                        "destructive",
                        "can_advance_main_flow",
                        "state_in",
                        "state_out",
                        "module_candidates",
                        "interaction_ids",
                        "required_states",
                        "produced_states",
                    )
                    if step.get(key) not in (None, "")
                }
            )
        workflows.append(
            {
                key: blueprint.get(key)
                for key in (
                    "id",
                    "workflow_id",
                    "name",
                    "primary",
                    "initial_state",
                    "required_stage_ids",
                    "terminal_states",
                )
                if blueprint.get(key) not in (None, "")
            }
            | {"steps": steps}
        )

    modules = [
        {
            key: item.get(key)
            for key in ("module_key", "module_name", "aliases", "features")
            if item.get(key) not in (None, "", [])
        }
        for item in (architecture.get("functional_modules") or [])
        if isinstance(item, dict)
    ]
    interactions: list[dict[str, Any]] = []
    for item in architecture.get("module_interactions") or []:
        if not isinstance(item, dict):
            continue
        compact_interaction = {
            key: item.get(key)
            for key in (
                "interaction_id",
                "source_module_key",
                "source_module",
                "target_module_key",
                "target_module",
                "trigger",
                "transferred_entity",
            )
            if item.get(key) not in (None, "", [])
        }
        result_state = item.get("result_state")
        if result_state in (None, "", []):
            # 仅消费旧输入字段，Review 契约统一输出 canonical result_state。
            result_state = item.get("state_effect")
        if result_state not in (None, "", []):
            compact_interaction["result_state"] = result_state
        interactions.append(compact_interaction)
    return {
        "workflow_absence_declared": payload.get("workflow_absence_declared") is True,
        "workflow_blueprints": workflows,
        "functional_architecture": {
            "functional_modules": modules,
            "module_interactions": interactions,
        },
    }


__all__ = [
    "compact_review_contract_context",
    "compact_structured_case_risk",
    "compact_verified_case_semantics",
]
