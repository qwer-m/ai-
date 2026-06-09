from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument


WORKFLOW_CONTRACT_DOC_TYPE = "test_generation_workflow_contract"
WORKFLOW_BLUEPRINT_REPOSITORY_SOURCE = "workflow_blueprint_repository"
TRUSTED_WORKFLOW_CONTRACT_SOURCE_TYPES = {
    "human_reviewed",
    "manual_final_case_derived",
    "requirement_doc_extracted",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _content_fingerprint(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", _text(value))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _slug(value: Any) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", _text(value)).strip("_")
    return normalized[:120] or "workflow"


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value).lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _text_list(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values if isinstance(values, list) else []:
        value = _text(raw)
        marker = value.lower()
        if not value or marker in seen:
            continue
        seen.add(marker)
        output.append(value)
    return output


def _normalize_edge(raw: Any, *, index: int, workflow_id: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    edge = dict(raw)
    state_in = _text(edge.get("state_in") or edge.get("source_state"))
    state_out = _text(edge.get("state_out") or edge.get("target_state"))
    action = _text(edge.get("action") or edge.get("label") or edge.get("description"))
    if not state_in or not state_out or not action:
        return None
    actor = _text(edge.get("actor") or edge.get("role") or "student").lower()
    edge_id = _text(edge.get("id")) or f"step_{index:03d}"
    label = _text(edge.get("label")) or action
    return {
        **edge,
        "id": edge_id,
        "workflow_id": workflow_id,
        "label": label,
        "action": action,
        "state_in": state_in,
        "state_out": state_out,
        "actor": actor,
        "path_type": _text(edge.get("path_type") or "positive").lower(),
        "blocking": _bool(edge.get("blocking"), default=False),
        "destructive": _bool(edge.get("destructive"), default=False),
        "can_advance_main_flow": _bool(edge.get("can_advance_main_flow"), default=True),
        "allow_bridge": _bool(edge.get("allow_bridge"), default=True),
        "match_keywords": _text_list(edge.get("match_keywords") or edge.get("keywords") or []),
    }


def normalize_workflow_contract(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    contract = dict(payload)
    workflow_id = _text(contract.get("workflow_id") or contract.get("id"))
    if not workflow_id:
        return None
    raw_edges = contract.get("edges")
    if not isinstance(raw_edges, list):
        raw_edges = contract.get("steps")
    edges: list[dict[str, Any]] = []
    for index, raw_edge in enumerate(raw_edges if isinstance(raw_edges, list) else [], start=1):
        edge = _normalize_edge(raw_edge, index=index, workflow_id=workflow_id)
        if edge is not None:
            edges.append(edge)
    if len(edges) < 2:
        return None
    source_type = _text(contract.get("source_type")).lower()
    trusted = _bool(contract.get("trusted"), default=False)
    if source_type not in TRUSTED_WORKFLOW_CONTRACT_SOURCE_TYPES:
        trusted = False
    return {
        **contract,
        "workflow_id": workflow_id,
        "id": workflow_id,
        "name": _text(contract.get("name") or contract.get("title") or workflow_id),
        "project_id": int(contract.get("project_id") or 0),
        "source_doc_id": int(contract.get("source_doc_id") or 0) or None,
        "source_type": source_type,
        "trusted": trusted,
        "confidence": _confidence(contract.get("confidence")),
        "actors": _text_list(contract.get("actors") or []),
        "match_terms": _text_list(contract.get("match_terms") or []),
        "commit_state": _text(contract.get("commit_state")),
        "downstream_state": _text(contract.get("downstream_state")),
        "completion_state": _text(contract.get("completion_state")),
        "repository_source": WORKFLOW_BLUEPRINT_REPOSITORY_SOURCE,
        "edges": edges,
        "steps": edges,
    }


def is_trusted_workflow_contract(payload: Any) -> bool:
    contract = normalize_workflow_contract(payload)
    return bool(
        contract
        and contract.get("trusted") is True
        and contract.get("repository_source") == WORKFLOW_BLUEPRINT_REPOSITORY_SOURCE
        and contract.get("source_type") in TRUSTED_WORKFLOW_CONTRACT_SOURCE_TYPES
    )


class WorkflowBlueprintRepository:
    """Independent persistence and recall boundary for trusted workflow contracts."""

    def __init__(self, db: Session):
        self.db = db

    def resolve_latest_source_doc(self, *, project_id: int, user_id: int, filename: str) -> KnowledgeDocument | None:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == int(project_id),
                KnowledgeDocument.user_id == int(user_id),
                KnowledgeDocument.doc_type == "requirement",
                KnowledgeDocument.filename == _text(filename),
            )
            .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
            .first()
        )

    def upsert_contract(self, *, project_id: int, user_id: int, contract: dict[str, Any]) -> KnowledgeDocument:
        normalized = normalize_workflow_contract({**dict(contract or {}), "project_id": int(project_id)})
        if normalized is None:
            raise ValueError("invalid workflow contract")
        filename = f"workflow_contract_project_{int(project_id)}_{_slug(normalized['workflow_id'])}.json"
        content = json.dumps(
            {
                **normalized,
                "updated_at": datetime.utcnow().isoformat(),
            },
            ensure_ascii=False,
        )
        doc = (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == int(project_id),
                KnowledgeDocument.user_id == int(user_id),
                KnowledgeDocument.doc_type == WORKFLOW_CONTRACT_DOC_TYPE,
                KnowledgeDocument.filename == filename,
            )
            .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
            .first()
        )
        if doc is None:
            doc = KnowledgeDocument(
                project_id=int(project_id),
                user_id=int(user_id),
                source_doc_id=normalized.get("source_doc_id"),
                filename=filename,
                content=content,
                doc_type=WORKFLOW_CONTRACT_DOC_TYPE,
                parse_status="success",
            )
            self.db.add(doc)
        else:
            doc.source_doc_id = normalized.get("source_doc_id")
            doc.content = content
            doc.parse_status = "success"
            doc.parse_error = None
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def list_matching_trusted_contracts(
        self,
        *,
        project_id: int,
        user_id: int,
        requirement_text: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        docs = (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == int(project_id),
                KnowledgeDocument.user_id == int(user_id),
                KnowledgeDocument.doc_type == WORKFLOW_CONTRACT_DOC_TYPE,
                KnowledgeDocument.parse_status == "success",
            )
            .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
            .all()
        )
        requirement = _text(requirement_text).lower()
        source_doc_ids: list[int] = []
        parsed_contracts: list[dict[str, Any]] = []
        for doc in docs:
            try:
                contract = normalize_workflow_contract(json.loads(doc.content or "{}"))
            except Exception:
                contract = None
            if not contract or not is_trusted_workflow_contract(contract):
                continue
            parsed_contracts.append(contract)
            source_doc_id = int(contract.get("source_doc_id") or 0)
            if source_doc_id > 0:
                source_doc_ids.append(source_doc_id)
        source_docs = (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == int(project_id),
                KnowledgeDocument.id.in_(source_doc_ids),
            )
            .all()
            if source_doc_ids
            else []
        )
        source_content_fingerprints = {
            int(doc.id): _content_fingerprint(doc.content)
            for doc in source_docs
        }
        requirement_fingerprint = _content_fingerprint(requirement_text)
        selected: list[tuple[int, int, float, dict[str, Any]]] = []
        for contract in parsed_contracts:
            match_terms = [item.lower() for item in contract.get("match_terms") or [] if _text(item)]
            hit_count = sum(1 for term in match_terms if term in requirement)
            source_doc_id = int(contract.get("source_doc_id") or 0)
            source_content_match = bool(
                requirement_fingerprint
                and source_content_fingerprints.get(source_doc_id) == requirement_fingerprint
            )
            if requirement and match_terms and hit_count <= 0 and not source_content_match:
                continue
            selected.append(
                (
                    int(source_content_match),
                    int(hit_count),
                    float(contract.get("confidence") or 0.0),
                    contract,
                )
            )
        selected.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        return [contract for _source_match, _hits, _confidence_value, contract in selected[: max(1, int(limit))]]


__all__ = [
    "TRUSTED_WORKFLOW_CONTRACT_SOURCE_TYPES",
    "WORKFLOW_BLUEPRINT_REPOSITORY_SOURCE",
    "WORKFLOW_CONTRACT_DOC_TYPE",
    "WorkflowBlueprintRepository",
    "is_trusted_workflow_contract",
    "normalize_workflow_contract",
]
