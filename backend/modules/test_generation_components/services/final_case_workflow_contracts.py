from __future__ import annotations

from typing import Any

from ..control.workflow_blueprint_repository import WorkflowBlueprintRepository


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _first_positive_int(values: Any) -> int | None:
    for raw in (values if isinstance(values, list) else []):
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _workflow_contract_from_learning_sample(sample: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(sample, dict):
        return None
    if _safe_text(sample.get("pattern_grain") or sample.get("patternGrain")).lower() != "workflow_blueprint":
        return None
    blueprint = sample.get("workflow_blueprint") or sample.get("workflowBlueprint")
    if not isinstance(blueprint, dict):
        return None
    steps = [dict(item) for item in (blueprint.get("steps") or []) if isinstance(item, dict)]
    if len(steps) < 2:
        return None
    workflow_id = _safe_text(
        blueprint.get("workflow_id")
        or blueprint.get("id")
        or sample.get("case_id")
        or sample.get("id")
    )
    if not workflow_id:
        return None
    match_terms = [
        _safe_text(blueprint.get("name") or blueprint.get("title")),
        _safe_text(sample.get("pattern_summary") or sample.get("title")),
        _safe_text(sample.get("user_comment")),
    ]
    actors = sorted(
        {
            _safe_text(step.get("actor") or step.get("role"))
            for step in steps
            if _safe_text(step.get("actor") or step.get("role"))
        }
    )
    linked_doc_ids = sample.get("linked_doc_ids") or sample.get("linkedDocIds")
    return {
        **blueprint,
        "id": workflow_id,
        "workflow_id": workflow_id,
        "source_type": "manual_final_case_derived",
        "trusted": True,
        "source_doc_id": _first_positive_int(linked_doc_ids),
        "confidence": sample.get("pattern_confidence") or sample.get("confidence") or 0.8,
        "actors": actors,
        "match_terms": [term for term in match_terms if term],
        "steps": steps,
        "edges": steps,
    }


def _workflow_contract_candidates_from_derived(derived: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sample in (derived.get("samples") or []):
        if not isinstance(sample, dict):
            continue
        contract = _workflow_contract_from_learning_sample(sample)
        if contract is None:
            continue
        key = _safe_text(contract.get("workflow_id")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(contract)
    return candidates


def upsert_workflow_contracts_from_derived(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    derived: dict[str, Any],
) -> dict[str, Any]:
    candidates = _workflow_contract_candidates_from_derived(derived if isinstance(derived, dict) else {})
    if not candidates:
        return {
            "candidate_count": 0,
            "upserted_count": 0,
            "doc_ids": [],
            "errors": [],
        }
    repo = WorkflowBlueprintRepository(db)
    doc_ids: list[int] = []
    errors: list[dict[str, Any]] = []
    for contract in candidates:
        try:
            doc = repo.upsert_contract(
                project_id=int(project_id),
                user_id=int(user_id),
                contract=contract,
            )
            if getattr(doc, "id", None) is not None:
                doc_ids.append(int(doc.id))
        except Exception as exc:
            errors.append(
                {
                    "workflow_id": _safe_text(contract.get("workflow_id")),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "candidate_count": int(len(candidates)),
        "upserted_count": int(len(doc_ids)),
        "doc_ids": doc_ids,
        "errors": errors,
    }


__all__ = [
    "_workflow_contract_candidates_from_derived",
    "_workflow_contract_from_learning_sample",
    "upsert_workflow_contracts_from_derived",
]
