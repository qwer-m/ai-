"""
多路召回管道（RAG 检索治理）。

目标：
1. 在现有 Chroma 索引基础上实现 original/rewrite + raw/summary 多路召回；
2. 对召回结果做可解释合并与去重，并补充调试信息；
3. 让原始 query 路由优先于 rewrite 路由，降低小语料噪音。
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Optional

logger = logging.getLogger(__name__)

def _normalize_chunk_key(text: str) -> str:
    """用于精确去重的文本归一化键。"""
    return " ".join((text or "").strip().lower().split())


def _compose_where(clauses: list[dict]) -> dict:
    """按 Chroma where 语法拼装条件；单条件时不使用 $and。"""
    cleaned = [item for item in (clauses or []) if isinstance(item, dict) and item]
    if not cleaned:
        return {}
    if len(cleaned) == 1:
        return cleaned[0]
    return {"$and": cleaned}


def _count_raw_result_rows(result: dict | None) -> int:
    """统计 Chroma 原始查询返回行数（过滤前）。"""
    payload = result or {}
    docs = payload.get("documents") or []
    if not docs:
        return 0
    if isinstance(docs, list) and docs and isinstance(docs[0], list):
        return len(docs[0])
    if isinstance(docs, list):
        return len(docs)
    return 0


def _classify_lane_error(error: Exception) -> str:
    """
    将召回异常归类为可观测原因。

    说明：
    - network_error：网络/SSL/代理/DNS 等问题；
    - embedding_failed：向量化链路失败（含索引读取失败）。
    """
    msg = str(error or "").lower()
    network_signals = (
        "httpsconnectionpool",
        "max retries exceeded",
        "ssl",
        "unexpected_eof_while_reading",
        "eof occurred",
        "timed out",
        "timeout",
        "connection refused",
        "name or service not known",
        "temporary failure in name resolution",
        "proxy",
    )
    if any(sig in msg for sig in network_signals):
        return "network_error"
    if "embedding" in msg or "dashscope" in msg or "text-embedding" in msg or "hnsw" in msg:
        return "embedding_failed"
    return "embedding_failed"


def _extract_chunks_from_result(
    result: dict,
    query: str,
    query_source: str,
    expected_chunk_source: Optional[str],
) -> list[dict]:
    """将 Chroma 查询结果转换为统一 chunk 结构。"""
    documents = (result or {}).get("documents") or []
    metadatas = (result or {}).get("metadatas") or []
    distances = (result or {}).get("distances") or []
    ids = (result or {}).get("ids") or []

    docs = documents[0] if documents else []
    metas = metadatas[0] if metadatas else []
    dists = distances[0] if distances else []
    chunk_ids = ids[0] if ids else []

    chunks: list[dict] = []
    for idx, text in enumerate(docs):
        metadata = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
        distance = dists[idx] if idx < len(dists) else None
        chunk_id = str(chunk_ids[idx] or "").strip() if idx < len(chunk_ids) else ""
        if not chunk_id:
            chunk_id = str(metadata.get("chunk_id") or "").strip()
        if not chunk_id:
            chunk_id = f"{metadata.get('doc_id') or 'unknown'}::{idx}"

        detected_source = "summary" if bool(metadata.get("is_summary")) else "raw"
        if expected_chunk_source is not None:
            if expected_chunk_source == "summary" and detected_source != "summary":
                continue
            if expected_chunk_source == "raw" and detected_source == "summary":
                continue

        chunks.append(
            {
                "chunk_text": str(text or "").strip(),
                "chunk_id": chunk_id,
                "chunk_source": detected_source,
                "query_source": query_source,
                "query": query,
                "score": 0.35,
                "distance": distance,
                "filename": metadata.get("filename"),
                "doc_type": metadata.get("doc_type"),
                "doc_id": metadata.get("doc_id"),
                "biz_key": metadata.get("biz_key"),
                "metadata": metadata,
                "recall_routes": [f"{query_source}_{detected_source}"],
            }
        )
    return chunks


def _apply_min_max_scores(chunks: list[dict], neutral_score: float = 0.35) -> None:
    """对当前批次距离做 min-max 归一化并写回 score。"""
    numeric_distances: list[float] = []
    for chunk in chunks:
        try:
            if chunk.get("distance") is not None:
                numeric_distances.append(float(chunk["distance"]))
        except Exception:
            continue

    if not chunks:
        return

    if not numeric_distances:
        for chunk in chunks:
            chunk["score"] = float(neutral_score)
        return

    min_d = min(numeric_distances)
    max_d = max(numeric_distances)
    span = max(max_d - min_d, 1e-9)

    for chunk in chunks:
        raw_d = chunk.get("distance")
        if raw_d is None:
            chunk["score"] = float(neutral_score)
            continue
        try:
            d = float(raw_d)
        except Exception:
            chunk["score"] = float(neutral_score)
            continue

        if span <= 1e-9:
            chunk["score"] = 0.7
            continue

        normalized = 1.0 - ((d - min_d) / span)
        chunk["score"] = max(0.0, min(1.0, float(normalized)))
