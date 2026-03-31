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

from sqlalchemy.orm import Session

from core.cache_layer.chroma_client import chroma_client
from modules.knowledge_base_components.query.query_rewriter import rewrite_query
from modules.knowledge_base_components.retrieval.retrieval_hybrid import (
    apply_hybrid_scores,
    build_keyword_candidates,
)

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


def _sanitize_doc_types(doc_types: Optional[list[str]]) -> list[str]:
    """清洗文档类型过滤条件。"""
    sanitized: list[str] = []
    seen: set[str] = set()
    for item in (doc_types or []):
        key = str(item or "").strip().lower().replace("-", "_")
        if not key:
            continue

        aliases = [key]
        if key in {"testcase", "test_case"}:
            aliases.extend(["test_case", "testcase"])
        elif key == "supplement":
            aliases.extend(["supplement", "evaluation_report", "agent_learning"])
        elif key == "requirement":
            aliases.extend(["requirement", "product_requirement", "incomplete"])

        for alias in aliases:
            if alias in seen:
                continue
            seen.add(alias)
            sanitized.append(alias)
    return sanitized


def _build_lane_where(project_id: int, chunk_source: str, doc_types: Optional[list[str]] = None) -> dict:
    """构造 lane 精准 where 过滤条件。"""
    clauses: list[dict] = [
        {"project_id": project_id},
        {"is_summary": chunk_source == "summary"},
    ]
    cleaned_types = _sanitize_doc_types(doc_types)
    if cleaned_types:
        clauses.append({"doc_type": {"$in": cleaned_types}})
    return _compose_where(clauses)


def _build_project_where(project_id: int, doc_types: Optional[list[str]] = None) -> dict:
    """构造 lane 降级 where。"""
    clauses: list[dict] = [{"project_id": project_id}]
    cleaned_types = _sanitize_doc_types(doc_types)
    if cleaned_types:
        clauses.append({"doc_type": {"$in": cleaned_types}})
    return _compose_where(clauses)


def _merge_chunk_lists(chunks: list[dict]) -> list[dict]:
    """统一去重合并，避免关系扩召后重复。"""
    deduped = _dedupe_chunks_exact(chunks)
    deduped = _dedupe_chunks_by_containment(deduped)
    return deduped


def _expand_related_by_biz_key(
    *,
    question: str,
    project_id: int,
    chunks: list[dict],
    top_k_per_biz: int = 5,
) -> tuple[list[dict], dict]:
    """
    轻量关系扩召：按 biz_key 追加同业务块。

    说明：
    - 无关系表阶段，先用 metadata.biz_key 做弱关联；
    - 仅在同 project_id 范围扩召，避免跨项目串数据；
    - 扩召结果打 recall_routes=related_biz_key。
    """
    if not chunks:
        return [], {"expanded_count": 0, "biz_keys": []}

    biz_keys: list[str] = []
    for item in chunks:
        metadata = item.get("metadata") or {}
        value = str(item.get("biz_key") or metadata.get("biz_key") or "").strip()
        if value and value not in biz_keys:
            biz_keys.append(value)

    if not biz_keys:
        # 中文注释：无 biz_key 时透传原候选，不能清空。
        return list(chunks), {"expanded_count": 0, "biz_keys": []}

    related_all: list[dict] = []
    for biz_key in biz_keys[:5]:
        where = {"$and": [{"project_id": project_id}, {"biz_key": biz_key}]}
        try:
            result = chroma_client.search_by_metadata(
                where=where,
                n_results=max(1, int(top_k_per_biz)),
                raise_on_error=True,
            )
            # 中文注释：get 返回结构与 query 略有差异，这里做兼容转换。
            if result and isinstance(result.get("documents"), list) and (
                not result.get("ids") or isinstance(result["ids"], list)
            ):
                result = {
                    "documents": [result.get("documents") or []],
                    "metadatas": [result.get("metadatas") or []],
                    "ids": [result.get("ids") or []],
                    "distances": [[]],
                }
            related_chunks = _extract_chunks_from_result(
                result=result or {},
                query=question,
                query_source="related",
                expected_chunk_source=None,
            )
            for chunk in related_chunks:
                routes = set(chunk.get("recall_routes") or [])
                routes.add("related_biz_key")
                chunk["recall_routes"] = sorted(routes)
                chunk["score"] = max(float(chunk.get("score") or 0.0), 0.45)
                chunk["biz_key"] = str(chunk.get("biz_key") or biz_key)
            related_all.extend(related_chunks)
        except Exception as e:
            logger.warning("biz_key_expand_failed project_id=%s biz_key=%s err=%s", project_id, biz_key, e)

    merged = _merge_chunk_lists(chunks + related_all)
    return merged, {"expanded_count": max(0, len(merged) - len(chunks)), "biz_keys": biz_keys[:5]}


def _search_lane(
    query: str,
    project_id: int,
    top_k: int,
    chunk_source: str,
    query_source: str,
    doc_types: Optional[list[str]] = None,
) -> dict:
    """
    执行单路召回，并返回 chunks + reason。

    reason 语义：
    - ok
    - no_hit
    - network_error
    - embedding_failed
    """
    where_precise = _build_lane_where(project_id=project_id, chunk_source=chunk_source, doc_types=doc_types)
    where_project = _build_project_where(project_id=project_id, doc_types=doc_types)
    lane_debug: dict[str, Any] = {
        "executed": True,
        "where_filter": where_precise,
        "fallback_where_filter": where_project,
        "raw_result_count": 0,
        "usable_result_count": 0,
        "error": "",
        "error_stage": "",
        "fallback_used": False,
        "query_embedding_status": "unknown",
    }

    try:
        result = chroma_client.search(
            query=query,
            n_results=top_k,
            where=where_precise,
            raise_on_error=True,
        )
        lane_debug["raw_result_count"] = _count_raw_result_rows(result)
        chunks = _extract_chunks_from_result(
            result=result,
            query=query,
            query_source=query_source,
            expected_chunk_source=chunk_source,
        )
        lane_debug["usable_result_count"] = len(chunks)
        lane_debug["query_embedding_status"] = "success"
        return {
            "chunks": chunks,
            "reason": "ok" if chunks else "no_hit",
            "error": None,
            "lane_debug": lane_debug,
        }
    except Exception as precise_error:
        # 中文注释：精准 where 查询失败时，降级到 project where 兜底。
        lane_debug["error_stage"] = "precise"
        lane_debug["error"] = str(precise_error)
        try:
            result = chroma_client.search(
                query=query,
                n_results=top_k,
                where=where_project,
                raise_on_error=True,
            )
            lane_debug["fallback_used"] = True
            lane_debug["raw_result_count"] = _count_raw_result_rows(result)
            chunks = _extract_chunks_from_result(
                result=result,
                query=query,
                query_source=query_source,
                expected_chunk_source=chunk_source,
            )
            lane_debug["usable_result_count"] = len(chunks)
            lane_debug["query_embedding_status"] = "fallback"
            return {
                "chunks": chunks,
                "reason": "ok" if chunks else "no_hit",
                "error": None,
                "lane_debug": lane_debug,
            }
        except Exception as fallback_error:
            reason = _classify_lane_error(fallback_error)
            lane_debug["error_stage"] = "fallback"
            lane_debug["error"] = str(fallback_error or precise_error)
            lane_debug["query_embedding_status"] = "failed"
            return {
                "chunks": [],
                "reason": reason,
                "error": str(fallback_error or precise_error),
                "lane_debug": lane_debug,
            }


def _merge_routes_preserve_best(current: dict, incoming: dict) -> dict:
    """合并召回来源轨迹，并保留更高分 chunk。"""
    route_set = set(current.get("recall_routes") or [])
    route_set.update(incoming.get("recall_routes") or [])

    current_score = float(current.get("score") or 0.0)
    incoming_score = float(incoming.get("score") or 0.0)

    prefer_incoming = incoming_score > current_score
    if incoming_score == current_score:
        prefer_incoming = (
            current.get("query_source") != "original" and incoming.get("query_source") == "original"
        )

    kept = dict(incoming if prefer_incoming else current)
    kept["recall_routes"] = sorted(route_set)
    return kept


def _dedupe_chunks_exact(chunks: list[dict]) -> list[dict]:
    """第一层去重：按归一化文本精确去重。"""
    merged: dict[str, dict] = {}
    for chunk in chunks:
        key = _normalize_chunk_key(chunk.get("chunk_text", ""))
        if not key:
            continue
        current = merged.get(key)
        if not current:
            merged[key] = dict(chunk)
            continue
        merged[key] = _merge_routes_preserve_best(current=current, incoming=chunk)

    return sorted(merged.values(), key=lambda x: float(x.get("score") or 0.0), reverse=True)


def _dedupe_chunks_by_containment(chunks: list[dict]) -> list[dict]:
    """
    第二层去重：在同 doc_id 下做“包含关系去重”。

    规则：
    - 若短文本被长文本完整包含，则丢弃短文本；
    - 仅在同 doc_id 内执行，避免跨文档误杀。
    """
    groups: dict[str, list[dict]] = {}
    no_doc_id_chunks: list[dict] = []

    for chunk in chunks:
        doc_id = str(chunk.get("doc_id") or "").strip()
        if not doc_id:
            no_doc_id_chunks.append(chunk)
            continue
        groups.setdefault(doc_id, []).append(chunk)

    result: list[dict] = list(no_doc_id_chunks)
    for _, group in groups.items():
        group_sorted = sorted(group, key=lambda x: len(str(x.get("chunk_text") or "")), reverse=True)
        kept: list[dict] = []
        kept_norm_texts: list[str] = []

        for chunk in group_sorted:
            norm_text = _normalize_chunk_key(str(chunk.get("chunk_text") or ""))
            if not norm_text:
                continue

            if len(norm_text) >= 20 and any(norm_text in long_text for long_text in kept_norm_texts):
                continue

            kept.append(chunk)
            kept_norm_texts.append(norm_text)

        result.extend(kept)

    result.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return result


def recall_chunks(
    question: str,
    project_id: int,
    top_k: int = 6,
    rewrite_count: int = 1,
    db: Optional[Session] = None,
    retrieval_mode: str = "hybrid",
    enable_query_rewrite: bool = True,
    vector_weight: float = 0.6,
    keyword_weight: float = 0.25,
    title_weight: float = 0.15,
    doc_types: Optional[list[str]] = None,
    enable_biz_key_expansion: bool = True,
    related_top_k: int = 5,
) -> dict:
    """
    执行检索治理层的多路召回。

    返回：
    - chunks: 合并去重后的 chunk 列表
    - debug: 召回侧可观测信息
    """
    mode = str(retrieval_mode or "hybrid").strip().lower()
    if mode not in {"vector", "keyword", "hybrid", "bm25"}:
        mode = "hybrid"
    sanitized_doc_types = _sanitize_doc_types(doc_types)

    if enable_query_rewrite:
        all_queries = rewrite_query(question, max_queries=max(1, int(rewrite_count) + 1))
    else:
        all_queries = [str(question or "").strip()]

    if not all_queries:
        return {
            "chunks": [],
            "debug": {
                "original_query": "",
                "rewrite_queries": [],
                "lane_counts": {
                    "original_raw": 0,
                    "original_summary": 0,
                    "rewrite_raw": 0,
                    "rewrite_summary": 0,
                },
                "lane_reasons": {
                    "original_raw": "disabled",
                    "original_summary": "disabled",
                    "rewrite_raw": "disabled",
                    "rewrite_summary": "disabled",
                },
                "lane_topk": {
                    "original_raw": 0,
                    "original_summary": 0,
                    "rewrite_raw": 0,
                    "rewrite_summary": 0,
                },
                "query_embedding_status": "failed",
                "query_embedding_error": "empty_query_after_rewrite",
                "recall_lanes": {},
                "merge_stage": {
                    "before_merge_count": 0,
                    "after_merge_count": 0,
                    "after_dedup_count": 0,
                },
                "merged_count": 0,
                "deduped_count": 0,
            },
        }

    original_query = all_queries[0]
    rewrite_queries = all_queries[1 : 1 + max(0, int(rewrite_count))]

    base_top_k = max(1, int(top_k))
    lane_plan: list[tuple[str, str, str, int]] = [
        (original_query, "original", "raw", base_top_k * 2),
        (original_query, "original", "summary", base_top_k),
    ]
    for rq in rewrite_queries:
        lane_plan.append((rq, "rewrite", "raw", base_top_k))
        lane_plan.append((rq, "rewrite", "summary", max(1, base_top_k // 2)))

    lane_counts: dict[str, int] = {
        "original_raw": 0,
        "original_summary": 0,
        "rewrite_raw": 0,
        "rewrite_summary": 0,
        "keyword_docs": 0,
    }
    lane_topk: dict[str, int] = {
        "original_raw": base_top_k * 2,
        "original_summary": base_top_k,
        "rewrite_raw": base_top_k,
        "rewrite_summary": max(1, base_top_k // 2),
        "keyword_docs": max(4, base_top_k * 2),
    }
    lane_reason_sets: dict[str, set[str]] = {
        "original_raw": set(),
        "original_summary": set(),
        "rewrite_raw": set(),
        "rewrite_summary": set(),
        "keyword_docs": set(),
    }
    recall_lanes: dict[str, dict] = {}

    if not rewrite_queries:
        lane_reason_sets["rewrite_raw"].add("disabled")
        lane_reason_sets["rewrite_summary"].add("disabled")

    merged_chunks: list[dict] = []
    for q, query_source, chunk_source, lane_k in lane_plan:
        lane_key = f"{query_source}_{chunk_source}"
        lane_output = _search_lane(
            query=q,
            project_id=project_id,
            top_k=lane_k,
            chunk_source=chunk_source,
            query_source=query_source,
            doc_types=sanitized_doc_types,
        )
        lane_chunks = lane_output.get("chunks") or []
        lane_reason = str(lane_output.get("reason") or "no_hit")
        recall_lanes[lane_key] = lane_output.get("lane_debug") or {
            "executed": True,
            "where_filter": _build_lane_where(project_id=project_id, chunk_source=chunk_source, doc_types=sanitized_doc_types),
            "fallback_where_filter": _build_project_where(project_id=project_id, doc_types=sanitized_doc_types),
            "raw_result_count": len(lane_chunks),
            "usable_result_count": len(lane_chunks),
            "error": str(lane_output.get("error") or ""),
            "error_stage": "",
            "fallback_used": False,
            "query_embedding_status": "unknown",
        }

        lane_counts[lane_key] += len(lane_chunks)
        lane_reason_sets[lane_key].add(lane_reason)
        merged_chunks.extend(lane_chunks)

    if mode in {"keyword", "hybrid", "bm25"}:
        keyword_candidates = build_keyword_candidates(
            query=original_query,
            project_id=project_id,
            db=db,
            query_source="original",
            top_docs=max(4, base_top_k * 2),
            per_doc_chunks=2,
            doc_types=sanitized_doc_types,
        )
        lane_counts["keyword_docs"] = len(keyword_candidates)
        lane_reason_sets["keyword_docs"].add("ok" if keyword_candidates else "no_hit")
        merged_chunks.extend(keyword_candidates)
    else:
        lane_reason_sets["keyword_docs"].add("disabled")

    merge_before_count = len(merged_chunks)
    _apply_min_max_scores(merged_chunks)
    apply_hybrid_scores(
        chunks=merged_chunks,
        query=original_query,
        retrieval_mode=mode,
        vector_weight=vector_weight,
        keyword_weight=keyword_weight,
        title_weight=title_weight,
    )

    dedup_exact_chunks = _dedupe_chunks_exact(merged_chunks)
    merge_after_count = len(dedup_exact_chunks)
    deduped_before_relation = _dedupe_chunks_by_containment(dedup_exact_chunks)
    deduped_chunks = list(deduped_before_relation)

    relation_debug = {"expanded_count": 0, "biz_keys": []}
    if enable_biz_key_expansion:
        deduped_chunks, relation_debug = _expand_related_by_biz_key(
            question=original_query,
            project_id=project_id,
            chunks=deduped_chunks,
            top_k_per_biz=max(1, int(related_top_k)),
        )

    for lane_key in ("rewrite_raw", "rewrite_summary"):
        if lane_key in recall_lanes:
            continue
        recall_lanes[lane_key] = {
            "executed": False,
            "where_filter": {},
            "fallback_where_filter": {},
            "raw_result_count": 0,
            "usable_result_count": 0,
            "error": "",
            "error_stage": "",
            "fallback_used": False,
            "query_embedding_status": "disabled",
        }

    vector_lane_keys = ("original_raw", "original_summary", "rewrite_raw", "rewrite_summary")
    vector_statuses: list[str] = []
    vector_errors: list[str] = []
    for lane_key in vector_lane_keys:
        lane_info = recall_lanes.get(lane_key) or {}
        status = str(lane_info.get("query_embedding_status") or "")
        if status:
            vector_statuses.append(status)
        err = str(lane_info.get("error") or "").strip()
        if err:
            vector_errors.append(err)

    if any(status == "success" for status in vector_statuses):
        query_embedding_status = "success"
    elif any(status == "fallback" for status in vector_statuses):
        query_embedding_status = "fallback"
    elif any(status == "failed" for status in vector_statuses):
        query_embedding_status = "failed"
    else:
        query_embedding_status = "failed"
    query_embedding_error = " | ".join(dict.fromkeys(vector_errors))

    lane_reasons: dict[str, Any] = {}
    for lane_key, reasons in lane_reason_sets.items():
        if not reasons:
            lane_reasons[lane_key] = "no_hit"
        elif len(reasons) == 1:
            lane_reasons[lane_key] = next(iter(reasons))
        else:
            lane_reasons[lane_key] = sorted(reasons)

    return {
        "chunks": deduped_chunks,
        "debug": {
            "original_query": original_query,
            "rewrite_queries": rewrite_queries,
            "lane_counts": lane_counts,
            "lane_reasons": lane_reasons,
            "lane_topk": lane_topk,
            "query_embedding_status": query_embedding_status,
            "query_embedding_error": query_embedding_error,
            "recall_lanes": recall_lanes,
            "merge_stage": {
                "before_merge_count": int(merge_before_count),
                "after_merge_count": int(merge_after_count),
                "after_dedup_count": int(len(deduped_before_relation)),
            },
            "merged_count": len(merged_chunks),
            "deduped_count": len(deduped_chunks),
            "doc_type_filter": sanitized_doc_types,
            "biz_relation_expand": relation_debug,
            "retrieval_mode": mode,
            "fusion_weights": {
                "vector_weight": float(vector_weight),
                "keyword_weight": float(keyword_weight),
                "title_weight": float(title_weight),
            },
        },
    }
