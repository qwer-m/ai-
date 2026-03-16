"""
多路召回管道（RAG 第二阶段检索治理）。

目标：
1. 在现有 Chroma 索引基础上实现 original/rewrite + raw/summary 多路召回。
2. 对召回结果做可解释合并与去重，并补充调试信息。
3. 让原始 query 路优先于 rewrite 路，降低小语料场景噪音。
"""

from __future__ import annotations

from typing import Any

from core.chroma_client import chroma_client
from modules.knowledge_base_components.query_rewriter import rewrite_query


def _normalize_chunk_key(text: str) -> str:
    """用于精确去重的文本归一化键。"""
    return " ".join((text or "").strip().lower().split())


def _classify_lane_error(error: Exception) -> str:
    """
    将召回异常归类为可观测原因。

    说明：
    - network_error：网络/SSL/代理/DNS 层异常。
    - embedding_failed：向量化链路异常（非纯网络类）。
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
    if "embedding" in msg or "dashscope" in msg or "text-embedding" in msg:
        return "embedding_failed"
    return "embedding_failed"


def _extract_chunks_from_result(
    result: dict,
    query: str,
    query_source: str,
    expected_chunk_source: str,
) -> list[dict]:
    """将 Chroma 查询结果转换为统一 chunk 结构。"""
    documents = (result or {}).get("documents") or []
    metadatas = (result or {}).get("metadatas") or []
    distances = (result or {}).get("distances") or []

    docs = documents[0] if documents else []
    metas = metadatas[0] if metadatas else []
    dists = distances[0] if distances else []

    chunks: list[dict] = []
    for idx, text in enumerate(docs):
        metadata = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
        distance = dists[idx] if idx < len(dists) else None
        detected_source = "summary" if bool(metadata.get("is_summary")) else "raw"

        # raw/summary 分路召回后，再次兜底过滤，避免 lane 串线。
        if expected_chunk_source == "summary" and detected_source != "summary":
            continue
        if expected_chunk_source == "raw" and detected_source == "summary":
            continue

        chunks.append(
            {
                "chunk_text": str(text or "").strip(),
                "chunk_source": detected_source,
                "query_source": query_source,
                "query": query,
                # 这里先给中性分，后续统一做 min-max 归一化。
                "score": 0.35,
                "distance": distance,
                "filename": metadata.get("filename"),
                "doc_type": metadata.get("doc_type"),
                "doc_id": metadata.get("doc_id"),
                "metadata": metadata,
                "recall_routes": [f"{query_source}_{detected_source}"],
            }
        )
    return chunks


def _apply_min_max_scores(chunks: list[dict], neutral_score: float = 0.35) -> None:
    """
    按当前批次召回结果做 min-max 归一化，把 distance 映射到 [0, 1]。

    这样做的原因：
    - 不同 embedding 模型/版本的距离量纲差异很大（可能是 0~2，也可能上万）。
    - 若用固定阈值截断，容易把所有分数压成 0，导致 rerank 失去向量主导。
    """
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
            # 整批距离几乎一致时，保留一个中高分，避免全部退化为 0。
            chunk["score"] = 0.7
            continue

        # 距离越小越相似，因此做 1 - minmax(distance)。
        normalized = 1.0 - ((d - min_d) / span)
        chunk["score"] = max(0.0, min(1.0, float(normalized)))


def _search_lane(
    query: str,
    project_id: int,
    top_k: int,
    chunk_source: str,
    query_source: str,
) -> dict:
    """
    执行单路召回，并返回 chunks + reason。

    reason 语义：
    - ok
    - no_hit
    - network_error
    - embedding_failed
    """
    where_precise = {"$and": [{"project_id": project_id}, {"is_summary": chunk_source == "summary"}]}
    where_project = {"project_id": project_id}

    try:
        result = chroma_client.search(
            query=query,
            n_results=top_k,
            where=where_precise,
            raise_on_error=True,
        )
        chunks = _extract_chunks_from_result(
            result=result,
            query=query,
            query_source=query_source,
            expected_chunk_source=chunk_source,
        )
        return {"chunks": chunks, "reason": "ok" if chunks else "no_hit", "error": None}
    except Exception as precise_error:
        # 精确过滤失败时降级 project 级过滤，避免 where 条件兼容性问题导致全丢失。
        try:
            result = chroma_client.search(
                query=query,
                n_results=top_k,
                where=where_project,
                raise_on_error=True,
            )
            chunks = _extract_chunks_from_result(
                result=result,
                query=query,
                query_source=query_source,
                expected_chunk_source=chunk_source,
            )
            return {"chunks": chunks, "reason": "ok" if chunks else "no_hit", "error": None}
        except Exception as fallback_error:
            reason = _classify_lane_error(fallback_error)
            return {"chunks": [], "reason": reason, "error": str(fallback_error or precise_error)}


def _merge_routes_preserve_best(current: dict, incoming: dict) -> dict:
    """合并召回来源轨迹，并保留更高分 chunk。"""
    route_set = set(current.get("recall_routes") or [])
    route_set.update(incoming.get("recall_routes") or [])

    current_score = float(current.get("score") or 0.0)
    incoming_score = float(incoming.get("score") or 0.0)

    prefer_incoming = incoming_score > current_score
    # 分数相同时优先 original 路，保证原问题召回优先级更高。
    if incoming_score == current_score:
        prefer_incoming = (
            current.get("query_source") != "original" and incoming.get("query_source") == "original"
        )

    kept = dict(incoming if prefer_incoming else current)
    kept["recall_routes"] = sorted(route_set)
    return kept


def _dedupe_chunks_exact(chunks: list[dict]) -> list[dict]:
    """第一层去重：按归一化文本做精确去重。"""
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
    第二层去重：同 doc_id 下做“包含关系去重”。

    规则：
    - 若短文本被长文本完整包含，则丢弃短文本，保留长文本。
    - 只在同 doc_id 下执行，避免跨文档误杀。
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

            # 过短片段不做包含判定，避免把“关键词句”误判为冗余。
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
) -> dict:
    """
    执行检索治理层的多路召回。

    返回：
    - chunks: 合并去重后的 chunk 列表
    - debug: 召回侧可观测信息
    """
    all_queries = rewrite_query(question, max_queries=max(1, int(rewrite_count) + 1))
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
    }
    lane_topk: dict[str, int] = {
        "original_raw": base_top_k * 2,
        "original_summary": base_top_k,
        "rewrite_raw": base_top_k,
        "rewrite_summary": max(1, base_top_k // 2),
    }
    lane_reason_sets: dict[str, set[str]] = {
        "original_raw": set(),
        "original_summary": set(),
        "rewrite_raw": set(),
        "rewrite_summary": set(),
    }

    # 没有 rewrite 查询时，显式标记为 disabled，便于调试区分“未执行”和“执行无结果”。
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
        )
        lane_chunks = lane_output.get("chunks") or []
        lane_reason = str(lane_output.get("reason") or "no_hit")

        lane_counts[lane_key] += len(lane_chunks)
        lane_reason_sets[lane_key].add(lane_reason)
        merged_chunks.extend(lane_chunks)

    # 在全量 merged 结果上做一次统一归一化，避免不同 lane 的 score 不可比。
    _apply_min_max_scores(merged_chunks)

    deduped_chunks = _dedupe_chunks_exact(merged_chunks)
    deduped_chunks = _dedupe_chunks_by_containment(deduped_chunks)

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
            "merged_count": len(merged_chunks),
            "deduped_count": len(deduped_chunks),
        },
    }
