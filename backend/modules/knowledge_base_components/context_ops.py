"""
知识库上下文检索实现。

聚焦 RAG 上下文拼装逻辑，避免与文档增删改逻辑耦合在同一文件。
"""

from typing import Optional

from sqlalchemy.orm import Session

from core.chroma_client import chroma_client
from core.models import KnowledgeDocument


def get_relevant_context_impl(
    module,
    query: str,
    project_id: int,
    limit: int = 5,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> str:
    """
    语义检索上下文。
    保持历史输出格式：`--- Relevant Knowledge: 文件名 (类型) ---`。
    """
    if not query:
        return ""

    try:
        results = chroma_client.search(
            query=query,
            n_results=limit,
            where={"project_id": project_id},
        )

        context = ""
        if results and results.get("documents") and len(results["documents"]) > 0:
            for i, doc_text in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                filename = meta.get("filename", "Unknown")
                doc_type = meta.get("doc_type", "Unknown")
                doc_id = meta.get("doc_id") if isinstance(meta, dict) else None
                if db and doc_id:
                    try:
                        kb_doc = db.query(KnowledgeDocument).filter(
                            KnowledgeDocument.id == int(doc_id),
                            KnowledgeDocument.project_id == project_id,
                        ).first()
                        if kb_doc:
                            # 优先摘要可显著降低 token 消耗，且与原行为一致。
                            doc_text = module._ensure_summary(kb_doc, db, user_id)
                    except Exception:
                        pass
                context += f"""--- Relevant Knowledge: {filename} ({doc_type}) ---
{doc_text}

"""
        return context
    except Exception as e:
        print(f"RAG retrieval failed: {e}")
        return ""


def get_all_context_impl(
    module,
    db: Session,
    project_id: int,
    user_id: Optional[int] = None,
    max_docs: Optional[int] = 50,
) -> str:
    """全量上下文聚合，主要用于全局分析场景。"""
    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
    if max_docs:
        query = query.order_by(KnowledgeDocument.created_at.desc()).limit(max_docs)
    docs = query.all()
    context = ""
    for doc in docs:
        content_to_use = module._ensure_summary(doc, db, user_id)
        context += f"""--- Document: {doc.filename} ({doc.doc_type}) ---
{content_to_use}

"""
    return context

