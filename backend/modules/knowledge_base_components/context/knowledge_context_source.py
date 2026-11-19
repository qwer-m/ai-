"""把现有知识库检索结果接入上下文来源契约。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.context.context_ops import get_relevant_context_impl
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_layout,
    document_page_text,
    load_document_manifest,
)
from modules.knowledge_base_components.ports.context_memory_port import (
    ContextBudget,
    ContextCandidate,
    ContextQuery,
    ContextSourcePort,
    ContextSourceRef,
)


class KnowledgeBaseContextSourcePort(ContextSourcePort):
    """只从真实项目知识库读取候选，不把 debug 文本当成完整证据。"""

    source_kind = "project_knowledge"

    def __init__(self, db: Session):
        self.db = db

    def retrieve(self, query: ContextQuery, *, budget: ContextBudget) -> list[ContextCandidate]:
        if query.project_id is None:
            return [self._unavailable(query, "project_scope_required")]
        try:
            payload = get_relevant_context_impl(
                query.text,
                project_id=query.project_id,
                limit=min(query.max_candidates, budget.max_candidates),
                db=self.db,
                user_id=query.user_id,
                debug=True,
            )
        except Exception as error:
            return [self._unavailable(query, "retrieval_error", error_type=type(error).__name__)]
        if not isinstance(payload, dict):
            return [self._unavailable(query, "invalid_retrieval_response")]
        debug = payload.get("debug") if isinstance(payload.get("debug"), dict) else {}
        raw_chunks = debug.get("final_chunks")
        chunks = raw_chunks if isinstance(raw_chunks, list) else []
        if not chunks:
            status = str(debug.get("final_status") or "no_hit")
            reason = "no_hit" if status == "degraded_empty_context" else status
            return [self._unavailable(query, reason, final_status=status)]

        doc_ids = {
            int(item.get("doc_id"))
            for item in chunks
            if isinstance(item, dict) and str(item.get("doc_id") or "").isdigit()
        }
        documents = {
            int(document.id): document
            for document in self.db.query(KnowledgeDocument).filter(
                KnowledgeDocument.project_id == query.project_id,
                KnowledgeDocument.id.in_(doc_ids or {0}),
            ).all()
        }
        result: list[ContextCandidate] = []
        for item in chunks:
            if not isinstance(item, dict):
                continue
            try:
                document_id = int(item.get("doc_id"))
            except (TypeError, ValueError):
                continue
            document = documents.get(document_id)
            if document is None or not str(document.content_hash or "").strip():
                result.append(self._unavailable(
                    query,
                    "document_revision_unavailable",
                    document_id=document_id,
                ))
                continue
            text = str(item.get("chunk_text") or "").strip()
            if not text:
                continue
            anchor = self._resolve_exact_anchor(document_id, text)
            if anchor is None:
                result.append(self._unavailable(
                    query,
                    "retrieval_chunk_anchor_unavailable",
                    document_id=document_id,
                    chunk_id=item.get("chunk_id"),
                ))
                continue
            score = float(item.get("final_score") or item.get("score") or 0.0)
            result.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        source_kind=self.source_kind,
                        source_id=f"document:{document_id}:chunk:{item.get('chunk_id')}",
                        scope="project",
                        scope_id=query.project_id,
                        revision=str(document.content_hash),
                        content_hash=str(document.content_hash),
                        status="available",
                    ),
                    text=text,
                    score=max(0.0, min(1.0, score)),
                    evidence={
                        "document_id": document_id,
                        "chunk_id": item.get("chunk_id"),
                        "filename": str(document.filename or item.get("filename") or ""),
                        "doc_type": str(document.doc_type or item.get("doc_type") or ""),
                        "retrieval_routes": list(item.get("recall_routes") or []),
                        "anchor_status": "exact",
                        "anchor": anchor,
                    },
                )
            )
        return result or [self._unavailable(query, "no_usable_chunks")]

    @staticmethod
    def _resolve_exact_anchor(document_id: int, chunk_text: str) -> dict[str, Any] | None:
        """从真实页面资产反查唯一正文区间，调试片段本身不能充当锚点。"""

        try:
            manifest = load_document_manifest(document_id)
        except Exception:
            return None
        matches: list[dict[str, Any]] = []
        for raw_page in list(manifest.get("pages") or []):
            page_number = int(raw_page.get("page_number") or 0)
            if page_number < 1:
                continue
            try:
                page_text = document_page_text(document_id, page_number)
            except Exception:
                continue
            start = page_text.find(chunk_text)
            if start < 0:
                continue
            end = start + len(chunk_text)
            block_ids: list[str] = []
            try:
                for block in document_page_layout(document_id, page_number):
                    span = block.get("source_span") if isinstance(block, dict) else None
                    if isinstance(span, dict) and int(span.get("start") or 0) < end and int(span.get("end") or 0) > start:
                        block_id = str(block.get("block_id") or "").strip()
                        if block_id:
                            block_ids.append(block_id)
            except Exception:
                block_ids = []
            matches.append({
                "document_id": document_id,
                "page_number": page_number,
                "source_span": {"start": start, "end": end},
                "block_ids": block_ids,
            })
        return matches[0] if len(matches) == 1 else None

    def _unavailable(self, query: ContextQuery, reason: str, **details: Any) -> ContextCandidate:
        if query.project_id is None:
            source = ContextSourceRef(
                source_kind=self.source_kind,
                source_id="knowledge-source-unavailable",
                scope="global",
                status="unavailable",
            )
        else:
            source = ContextSourceRef(
                source_kind=self.source_kind,
                source_id="knowledge-source-unavailable",
                scope="project",
                scope_id=query.project_id,
                status="unavailable",
            )
        return ContextCandidate(
            source=source,
            evidence={"retrieval_status": reason, **details},
        )


__all__ = ["KnowledgeBaseContextSourcePort"]
