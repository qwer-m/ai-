"""Context retrieval orchestration for the knowledge base."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from modules.knowledge_base_components.context.context_debug_payload import (
    build_error_payload,
    build_success_payload,
)
from modules.knowledge_base_components.context.context_retrieval_executor import (
    execute_retrieval_with_retry,
)
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.knowledge_base_components.retrieval.pipeline.recall_pipeline import (
    recall_chunks,
)
from modules.knowledge_base_components.retrieval.reranker import rerank_chunks
from modules.knowledge_base_components.document.document_summary_service import (
    ensure_document_summary,
)


def get_relevant_context_impl(
    query: str,
    project_id: int,
    limit: int = 5,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    debug: bool = False,
    max_tokens: int = 1800,
    retrieval_options: Optional[dict] = None,
) -> str | dict:
    """
    Semantic retrieval context entrypoint.

    Contract:
    - debug=False: return context string only
    - debug=True: return {"context": "...", "debug": {...}}
    """
    question = (query or "").strip()
    if not question:
        empty = {"context": "", "debug": {"original_query": "", "rewrite_queries": []}}
        return empty if debug else ""

    try:
        exec_result = execute_retrieval_with_retry(
            question=question,
            project_id=project_id,
            limit=limit,
            max_tokens=max_tokens,
            db=db,
            retrieval_options=retrieval_options,
            recall_fn=recall_chunks,
            rerank_fn=rerank_chunks,
        )
        attempt_records = list(exec_result.get("attempt_records") or [])
        last_outcome = exec_result.get("last_outcome")
        last_error = str(exec_result.get("last_error") or "")

        if not debug:
            return str((last_outcome or {}).get("context_text") or "")

        payload = build_success_payload(
            question=question,
            limit=limit,
            max_tokens=max_tokens,
            retrieval_options=retrieval_options,
            attempt_records=attempt_records,
            last_error=last_error,
            last_outcome=last_outcome,
        )
        return payload
    except Exception as e:
        error_payload = build_error_payload(
            question=question,
            limit=limit,
            retrieval_options=retrieval_options,
            attempt_records=[],
            error=e,
        )
        return error_payload if debug else ""


def get_all_context_impl(
    db: Session,
    project_id: int,
    user_id: Optional[int] = None,
    max_docs: Optional[int] = 50,
) -> str:
    """Aggregate all document context for broad analysis scenarios."""
    repo = KnowledgeDocumentRepository(db)
    docs = repo.list_project_docs_created_desc(
        project_id=project_id,
        limit=max_docs if max_docs else None,
    )
    context = ""
    for doc in docs:
        content_to_use = ensure_document_summary(doc=doc, db=db, user_id=user_id)
        context += f"""--- Document: {doc.filename} ({doc.doc_type}) ---
{content_to_use}

"""
    return context
