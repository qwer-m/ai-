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

from modules.knowledge_base_components.ports.vector_store_port import VectorStorePort

logger = logging.getLogger(__name__)

from modules.knowledge_base_components.retrieval.pipeline.recall_pipeline_helpers_split_helpers import (
    _normalize_chunk_key,
    _compose_where,
    _count_raw_result_rows,
    _classify_lane_error,
    _extract_chunks_from_result,
    _apply_min_max_scores,
)

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
    vector_store: VectorStorePort,
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
            result = vector_store.search_by_metadata(
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
    vector_store: VectorStorePort,
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
        result = vector_store.search(
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
            result = vector_store.search(
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
