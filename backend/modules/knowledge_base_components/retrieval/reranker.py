"""
轻量级重排模块（RAG 第二阶段检索治理）。

策略：
1. 向量分数作为主导分。
2. 启发式 bonus 仅做小幅微调，避免盖过向量相关性。
3. 输出 base/bonus/final 便于 debug 观测权重是否合理。
"""

from __future__ import annotations

import re
from typing import Callable


def _extract_keywords(question: str, limit: int = 10) -> list[str]:
    """提取问题中的关键词，用于轻量命中加分。"""
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{1,}|\d{2,}", question or "")
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def _length_bonus(text: str) -> float:
    """
    长度微调：弱化过短/过长噪音，不让该项主导排序。

    注意：
    - 这里权重显著低于向量分数，仅用于轻微偏置。
    """
    length = len((text or "").strip())
    if length < 20:
        return -0.08
    if length < 80:
        return -0.02
    if 80 <= length <= 1200:
        return 0.02
    if length <= 2500:
        return 0.005
    return -0.03


def _keyword_overlap_bonus(question_keywords: list[str], chunk_text: str) -> float:
    """按关键词命中比例做小幅加分。"""
    if not question_keywords:
        return 0.0
    text = (chunk_text or "").lower()
    hit = 0
    for kw in question_keywords:
        if kw.lower() in text:
            hit += 1
    ratio = hit / max(1, len(question_keywords))
    return min(0.04, ratio * 0.05)


def _score_components(chunk: dict, question_keywords: list[str]) -> tuple[float, float, float]:
    """返回重排分三元组：base_score, bonus_score, final_score。"""
    base_score = float(chunk.get("score") or 0.0)

    bonus_score = 0.0
    bonus_score += _length_bonus(str(chunk.get("chunk_text") or ""))
    bonus_score += _keyword_overlap_bonus(question_keywords, str(chunk.get("chunk_text") or ""))
    bonus_score += 0.015 if chunk.get("query_source") == "original" else 0.0
    bonus_score += 0.005 if chunk.get("chunk_source") == "summary" else 0.0

    final_score = base_score + bonus_score
    return base_score, bonus_score, final_score


def rerank_chunks(
    chunks: list[dict],
    question: str,
    top_k: int,
    external_reranker: Callable[[list[dict], str, int], list[dict]] | None = None,
) -> list[dict]:
    """
    对召回结果进行重排。

    参数：
    - chunks: 候选 chunk 列表
    - question: 原始问题
    - top_k: 输出数量上限
    - external_reranker: 预留给阶段三接入外部重排器
    """
    if not chunks:
        return []

    if external_reranker:
        try:
            result = external_reranker(chunks, question, top_k)
            if isinstance(result, list):
                return result[: max(1, int(top_k))]
        except Exception:
            # 外部重排失败时回退到内置规则，确保主链路可用。
            pass

    keywords = _extract_keywords(question, limit=10)
    scored: list[dict] = []
    for chunk in chunks:
        item = dict(chunk)
        base_score, bonus_score, final_score = _score_components(item, keywords)

        item["base_score"] = base_score
        item["bonus_score"] = bonus_score
        item["final_score"] = final_score

        scored.append(item)

    scored.sort(key=lambda x: float(x.get("final_score") or 0.0), reverse=True)
    return scored[: max(1, int(top_k))]
