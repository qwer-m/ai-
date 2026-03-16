"""
知识库上下文检索实现。

该文件作为“检索治理层”编排入口，负责：
1. 查询改写（query_rewriter）
2. 多路召回（recall_pipeline）
3. 轻量重排（reranker）
4. 上下文压缩（context_compressor）
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.models import KnowledgeDocument
from modules.knowledge_base_components.context_compressor import compress_context
from modules.knowledge_base_components.recall_pipeline import recall_chunks
from modules.knowledge_base_components.reranker import rerank_chunks


def _format_context_chunks(chunks: list[dict]) -> str:
    """
    将最终片段格式化为历史上下文文本格式。

    为兼容旧 prompt 逻辑，保持头部形态不变：
    --- Relevant Knowledge: 文件名 (类型) ---
    """
    blocks: list[str] = []
    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        filename = chunk.get("filename") or "Unknown"
        doc_type = chunk.get("doc_type") or "Unknown"
        blocks.append(f"--- Relevant Knowledge: {filename} ({doc_type}) ---\n{text}")
    return "\n\n".join(blocks).strip() + ("\n\n" if blocks else "")


def get_relevant_context_impl(
    module,
    query: str,
    project_id: int,
    limit: int = 5,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    debug: bool = False,
    max_tokens: int = 1800,
) -> str | dict:
    """
    语义检索上下文（治理版）。

    兼容约定：
    - debug=False：返回字符串（保持历史行为）
    - debug=True：返回 {"context": "...", "debug": {...}}
    """
    question = (query or "").strip()
    if not question:
        empty = {"context": "", "debug": {"original_query": "", "rewrite_queries": []}}
        return empty if debug else ""

    try:
        # 1) 多路召回：原始 query + 改写 query，覆盖 raw/summary 两路。
        recall_result = recall_chunks(
            question=question,
            project_id=project_id,
            top_k=max(limit * 3, 6),
            rewrite_count=1,
        )
        recalled_chunks = recall_result.get("chunks") or []

        # 2) 轻量重排：向量分数主导，规则分作为微调。
        reranked_chunks = rerank_chunks(
            chunks=recalled_chunks,
            question=question,
            top_k=max(limit * 4, 8),
        )

        # 3) 上下文压缩：优先保留 original-query 命中，再按预算填充。
        compressed = compress_context(
            chunks=reranked_chunks,
            max_tokens=max_tokens,
            keep_original_top_n=2,
        )
        selected_chunks = (compressed.get("selected_chunks") or [])[: max(1, int(limit))]
        context_text = _format_context_chunks(selected_chunks)

        if not debug:
            return context_text

        # 4) 构造可观测信息，供 API debug=true 返回。
        final_chunk_debug = []
        for chunk in selected_chunks:
            final_chunk_debug.append(
                {
                    "chunk_source": chunk.get("chunk_source"),
                    "query_source": chunk.get("query_source"),
                    "score": float(chunk.get("score") or 0.0),
                    "base_score": float(chunk.get("base_score") or chunk.get("score") or 0.0),
                    "bonus_score": float(chunk.get("bonus_score") or 0.0),
                    "final_score": float(
                        chunk.get("final_score")
                        or chunk.get("rerank_score")
                        or chunk.get("score")
                        or 0.0
                    ),
                    "doc_id": chunk.get("doc_id"),
                    "filename": chunk.get("filename"),
                    "doc_type": chunk.get("doc_type"),
                    "kept_reason": chunk.get("kept_reason"),
                    "recall_routes": chunk.get("recall_routes") or [],
                }
            )

        rerank_top = []
        for chunk in reranked_chunks[: max(1, int(limit))]:
            rerank_top.append(
                {
                    "doc_id": chunk.get("doc_id"),
                    "filename": chunk.get("filename"),
                    "query_source": chunk.get("query_source"),
                    "chunk_source": chunk.get("chunk_source"),
                    "score": float(chunk.get("score") or 0.0),
                    "base_score": float(chunk.get("base_score") or chunk.get("score") or 0.0),
                    "bonus_score": float(chunk.get("bonus_score") or 0.0),
                    "final_score": float(
                        chunk.get("final_score")
                        or chunk.get("rerank_score")
                        or chunk.get("score")
                        or 0.0
                    ),
                }
            )

        return {
            "context": context_text,
            "debug": {
                "original_query": recall_result.get("debug", {}).get("original_query"),
                "rewrite_queries": recall_result.get("debug", {}).get("rewrite_queries") or [],
                "lane_counts": recall_result.get("debug", {}).get("lane_counts") or {},
                "lane_reasons": recall_result.get("debug", {}).get("lane_reasons") or {},
                "lane_topk": recall_result.get("debug", {}).get("lane_topk") or {},
                "merged_count": int(recall_result.get("debug", {}).get("merged_count") or 0),
                "deduped_count": int(recall_result.get("debug", {}).get("deduped_count") or 0),
                "reranked_count": len(reranked_chunks),
                "compressed_count": len(selected_chunks),
                "max_tokens": int(max_tokens),
                "compressor_stats": compressed.get("stats") or {},
                "rerank_top": rerank_top,
                "final_chunks": final_chunk_debug,
            },
        }
    except Exception as e:
        error_payload = {"context": "", "debug": {"error": f"RAG retrieval failed: {e}"}}
        return error_payload if debug else ""


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
