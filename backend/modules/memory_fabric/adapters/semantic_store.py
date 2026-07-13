from __future__ import annotations

from typing import Any

from core.db.models import KnowledgeDocument
from modules.domain.knowledge_base import knowledge_base
from modules.memory_fabric.contracts.memory_context import MemoryContext


class SemanticStore:
    """L2 semantic memory adapter via existing knowledge_base and docs table."""

    def read(self, query: dict[str, Any], ctx: MemoryContext) -> Any:
        kind = str(query.get("kind") or "").strip().lower()
        if kind == "context_snapshot":
            db = query.get("db")
            return knowledge_base.get_or_build_context_snapshot(
                project_id=int(query.get("project_id") or int(ctx.project_id)),
                db=db,
                user_id=int(query.get("user_id") or int(ctx.user_id) or 0) or None,
                force_rebuild=bool(query.get("force_rebuild", False)),
                prefer_async_rebuild=bool(query.get("prefer_async_rebuild", True)),
            )
        if kind == "relevant_context":
            db = query.get("db")
            return knowledge_base.get_relevant_context(
                query=str(query.get("query") or ""),
                project_id=int(query.get("project_id") or int(ctx.project_id)),
                limit=int(query.get("limit") or 5),
                db=db,
                user_id=int(query.get("user_id") or int(ctx.user_id) or 0) or None,
                debug=bool(query.get("debug", True)),
                max_tokens=int(query.get("max_tokens") or 1800),
                retrieval_options=query.get("retrieval_options"),
            )
        if kind == "knowledge_documents":
            db = query.get("db")
            if db is None:
                return []
            project_id = int(query.get("project_id") or int(ctx.project_id))
            user_id = int(query.get("user_id") or int(ctx.user_id))
            doc_types = query.get("doc_types") or []
            limit = max(1, int(query.get("limit") or 10))
            q = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.user_id == user_id,
            )
            if doc_types:
                q = q.filter(KnowledgeDocument.doc_type.in_(list(doc_types)))
            return q.order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc()).limit(limit).all()
        return None

    def write(self, doc: dict[str, Any], ctx: MemoryContext) -> None:
        # Stage 2: write path is intentionally deferred.
        return None

