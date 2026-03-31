from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument


def _clamp01(value: float) -> float:
    """把分数限制在 [0,1] 区间。"""
    return max(0.0, min(1.0, float(value)))


def extract_query_terms(query: str, limit: int = 12) -> list[str]:
    """
    提取 query 关键词。

    说明：
    - 中文取连续 2 字及以上，避免单字噪音；
    - 英文/数字保留常见业务 token；
    - 保序去重，便于前端展示命中解释。
    """
    raw_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{1,}|\d{2,}", query or "")
    terms: list[str] = []
    seen: set[str] = set()
    for t in raw_terms:
        # 中文注释：长中文串（例如“本库中都有哪些与机构相关的文档”）需要二次切词，
        # 否则会变成一个大词，导致标题/正文命中率很差。
        sub_terms: list[str]
        if re.fullmatch(r"[\u4e00-\u9fff]{6,}", t):
            pieces = re.split(
                r"(?:有哪些|什么|如何|怎么|是否|以及|相关|本库|这个|那个|一个|一些|关于|对于|里面|中的|中的|都|与|和|及|或|的|了|在|中)",
                t,
            )
            sub_terms = [p.strip() for p in pieces if len(p.strip()) >= 2]
            if not sub_terms:
                # 中文注释：兜底取 2~4 字 n-gram，避免无词可用。
                sub_terms = [t[i : i + 3] for i in range(0, max(1, len(t) - 2))][:6]
        else:
            sub_terms = [t]

        for sub in sub_terms:
            key = sub.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(sub)
            if len(terms) >= max(1, int(limit)):
                break
        if len(terms) >= max(1, int(limit)):
            break
    return terms


def find_hit_terms(text: str, terms: list[str]) -> list[str]:
    """找出 text 中实际命中的 query 词。"""
    source = (text or "").lower()
    hits: list[str] = []
    for term in terms:
        if term.lower() in source:
            hits.append(term)
    return hits


def _split_passages(text: str, max_chars: int = 500) -> list[str]:
    """
    将文档文本拆成可读片段，用于关键词召回候选。

    这里不引入新依赖，用简单规则分段：
    - 按换行和中文句号等标点切分；
    - 超长片段再按固定长度二次切分。
    """
    source = str(text or "").strip()
    if not source:
        return []

    units = re.split(r"(?:\r?\n)+|(?<=[。！？；;.!?])", source)
    chunks: list[str] = []
    limit = max(120, int(max_chars))
    for unit in units:
        seg = unit.strip()
        if not seg:
            continue
        if len(seg) <= limit:
            chunks.append(seg)
            continue
        for i in range(0, len(seg), limit):
            part = seg[i : i + limit].strip()
            if part:
                chunks.append(part)
    return chunks


def build_keyword_candidates(
    *,
    query: str,
    project_id: int,
    db: Session | None,
    query_source: str,
    top_docs: int = 8,
    per_doc_chunks: int = 2,
    doc_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    从项目文档中构造关键词候选（补足纯向量召回盲区）。

    设计目的：
    - 当 query 包含“销售/考勤/打卡”等业务词时，文件名命中可直接拉起候选；
    - 避免向量空间里“泛化文档”长期霸榜。
    """
    if db is None:
        return []

    terms = extract_query_terms(query, limit=12)
    if not terms:
        return []

    query_builder = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
    sanitized_types = [str(item or "").strip().lower() for item in (doc_types or []) if str(item or "").strip()]
    if sanitized_types:
        query_builder = query_builder.filter(KnowledgeDocument.doc_type.in_(sanitized_types))

    docs = query_builder.order_by(KnowledgeDocument.created_at.desc()).all()

    scored_docs: list[tuple[float, KnowledgeDocument, list[str], list[str]]] = []
    for doc in docs:
        title = str(doc.filename or "")
        body_text = str(doc.summary or doc.content or "")
        if not body_text:
            continue

        title_hits = find_hit_terms(title, terms)
        # 中文注释：正文仅截取前 4 万字符用于匹配，兼顾性能与召回。
        content_hits = find_hit_terms(body_text[:40000], terms)

        if not title_hits and not content_hits:
            continue

        title_score = len(title_hits) / max(1, len(terms))
        keyword_score = len(content_hits) / max(1, len(terms))
        # 标题命中更稀疏、更可靠，给稍高权重。
        doc_score = (0.65 * title_score) + (0.35 * keyword_score)
        scored_docs.append((doc_score, doc, title_hits, content_hits))

    scored_docs.sort(key=lambda x: x[0], reverse=True)
    selected_docs = scored_docs[: max(1, int(top_docs))]

    candidates: list[dict[str, Any]] = []
    per_doc = max(1, int(per_doc_chunks))

    for doc_score, doc, title_hits, content_hits in selected_docs:
        text = str(doc.summary or doc.content or "")
        passages = _split_passages(text, max_chars=500)
        if not passages:
            continue

        passage_scored: list[tuple[float, str, list[str]]] = []
        for passage in passages:
            p_hits = find_hit_terms(passage, terms)
            if not p_hits:
                continue
            p_score = len(p_hits) / max(1, len(terms))
            passage_scored.append((p_score, passage, p_hits))

        # 中文注释：若正文没有命中片段，仍保留文档首段作为弱候选。
        if not passage_scored:
            lead = passages[0]
            passage_scored = [(0.05, lead, [])]

        passage_scored.sort(key=lambda x: x[0], reverse=True)
        for idx, (p_score, passage, p_hits) in enumerate(passage_scored[:per_doc]):
            candidates.append(
                {
                    "chunk_text": passage,
                    "chunk_id": f"kw:{doc.id}:{idx}",
                    "chunk_source": "raw",
                    "query_source": query_source,
                    "query": query,
                    # 关键词候选没有向量距离，先给中性分，后续融合打分会覆盖。
                    "score": 0.35,
                    "distance": None,
                    "filename": doc.filename,
                    "doc_type": doc.doc_type,
                    "doc_id": doc.id,
                    "metadata": {
                        "doc_id": doc.id,
                        "filename": doc.filename,
                        "doc_type": doc.doc_type,
                        "keyword_candidate": True,
                    },
                    "recall_routes": [f"{query_source}_keyword"],
                    "query_terms": terms,
                    "title_hit_terms": title_hits,
                    "content_hit_terms": p_hits or content_hits,
                    "title_score": _clamp01(len(title_hits) / max(1, len(terms))),
                    "keyword_score": _clamp01(max(p_score, len(content_hits) / max(1, len(terms)))),
                    "doc_keyword_score": _clamp01(doc_score),
                }
            )

    return candidates


def apply_hybrid_scores(
    *,
    chunks: list[dict[str, Any]],
    query: str,
    retrieval_mode: str,
    vector_weight: float,
    keyword_weight: float,
    title_weight: float,
) -> None:
    """
    在候选 chunk 上计算可解释融合分。原地更新字段：
    - vector_score / keyword_score / title_score
    - fusion_score
    - title_hit_terms / content_hit_terms
    """
    mode = str(retrieval_mode or "hybrid").lower()
    terms = extract_query_terms(query, limit=12)

    raw_weights = {
        "vector": max(0.0, float(vector_weight)),
        "keyword": max(0.0, float(keyword_weight)),
        "title": max(0.0, float(title_weight)),
    }
    weight_sum = sum(raw_weights.values())
    if weight_sum <= 1e-9:
        normalized = {"vector": 0.6, "keyword": 0.25, "title": 0.15}
    else:
        normalized = {k: v / weight_sum for k, v in raw_weights.items()}

    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "")
        title = str(chunk.get("filename") or "")

        title_hits = list(chunk.get("title_hit_terms") or find_hit_terms(title, terms))
        content_hits = list(chunk.get("content_hit_terms") or find_hit_terms(text, terms))

        title_score = _clamp01(float(chunk.get("title_score") or (len(title_hits) / max(1, len(terms)))))
        keyword_score = _clamp01(float(chunk.get("keyword_score") or (len(content_hits) / max(1, len(terms)))))
        vector_score = _clamp01(float(chunk.get("score") or 0.0))

        if mode == "vector":
            fusion = vector_score
        elif mode in {"keyword", "bm25"}:
            # 中文注释：当前先用轻量词匹配模拟 BM25 风格排序，避免引入新依赖。
            fusion = (0.8 * keyword_score) + (0.2 * title_score)
        else:
            fusion = (
                normalized["vector"] * vector_score
                + normalized["keyword"] * keyword_score
                + normalized["title"] * title_score
            )

        chunk["query_terms"] = terms
        chunk["title_hit_terms"] = title_hits
        chunk["content_hit_terms"] = content_hits
        chunk["title_score"] = title_score
        chunk["keyword_score"] = keyword_score
        chunk["vector_score"] = vector_score
        chunk["fusion_score"] = _clamp01(fusion)
        chunk["score"] = chunk["fusion_score"]
        chunk["retrieval_mode"] = mode
