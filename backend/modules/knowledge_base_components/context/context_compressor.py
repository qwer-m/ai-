"""
上下文压缩模块（RAG 第二阶段检索治理）。

职责：
1. 去重与噪音过滤，降低无关上下文。
2. 按 token 预算截取高价值片段。
3. 优先保留 original-query 命中片段，保证主问题上下文稳定。
"""

from __future__ import annotations

import re


def _normalize_key(text: str) -> str:
    """用于去重的归一化键。"""
    return " ".join((text or "").strip().lower().split())


def _estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数。

    这里保持保守估算，避免超出下游模型 token 预算。
    """
    length = len(text or "")
    return max(1, int(length / 1.6))


def _is_noisy(text: str) -> bool:
    """识别明显噪音片段。"""
    raw = (text or "").strip()
    if not raw:
        return True
    if len(raw) < 12:
        return True
    if re.fullmatch(r"[\W_]+", raw):
        return True
    return False


def _rank_score(chunk: dict) -> float:
    """压缩阶段统一读取重排分。"""
    return float(
        chunk.get("final_score")
        or chunk.get("score")
        or 0.0
    )


def _to_drop_brief(chunk: dict, token_cost: int, reason: str) -> dict:
    """构造预算丢弃片段的精简调试信息。"""
    return {
        "doc_id": chunk.get("doc_id"),
        "filename": chunk.get("filename"),
        "query_source": chunk.get("query_source"),
        "score": float(chunk.get("score") or 0.0),
        "final_score": float(chunk.get("final_score") or chunk.get("score") or 0.0),
        "estimated_tokens": token_cost,
        "reason": reason,
    }


def _compress_by_budget(
    deduped: list[dict],
    *,
    budget: int,
    keep_original_top_n: int,
) -> tuple[list[dict], int, int, list[dict], int]:
    selected: list[dict] = []
    selected_keys: set[str] = set()
    total_tokens = 0
    dropped_over_budget = 0
    dropped_over_budget_chunks: list[dict] = []
    kept_by_original_priority = 0

    def try_add_chunk(chunk: dict, keep_reason: str) -> bool:
        nonlocal total_tokens, dropped_over_budget, kept_by_original_priority

        text = str(chunk.get("chunk_text") or "")
        token_cost = _estimate_tokens(text)
        key = _normalize_key(text)
        if key in selected_keys:
            return False

        if total_tokens + token_cost <= budget:
            item = dict(chunk)
            item["kept_reason"] = keep_reason
            selected.append(item)
            selected_keys.add(key)
            total_tokens += token_cost
            if keep_reason == "original_priority":
                kept_by_original_priority += 1
            return True

        if not selected and budget >= 128:
            max_chars = int(budget * 1.6)
            clipped = dict(chunk)
            clipped["chunk_text"] = text[:max_chars]
            clipped["compressed"] = True
            clipped["kept_reason"] = f"{keep_reason}_clipped"
            selected.append(clipped)
            selected_keys.add(key)
            total_tokens = _estimate_tokens(clipped["chunk_text"])
            if keep_reason == "original_priority":
                kept_by_original_priority += 1
            return True

        dropped_over_budget += 1
        dropped_over_budget_chunks.append(_to_drop_brief(chunk, token_cost, reason="token_budget"))
        return False

    original_chunks = [c for c in deduped if c.get("query_source") == "original"]
    rewrite_chunks = [c for c in deduped if c.get("query_source") != "original"]
    for chunk in original_chunks[:keep_original_top_n]:
        try_add_chunk(chunk, keep_reason="original_priority")

    taken_ids = {id(c) for c in original_chunks[:keep_original_top_n]}
    for chunk in deduped:
        if id(chunk) in taken_ids:
            continue
        keep_reason = "rewrite_fill" if chunk in rewrite_chunks else "original_fill"
        try_add_chunk(chunk, keep_reason=keep_reason)

    return selected, total_tokens, dropped_over_budget, dropped_over_budget_chunks, kept_by_original_priority


def compress_context(
    chunks: list[dict],
    max_tokens: int,
    keep_original_top_n: int = 2,
) -> dict:
    """
    压缩上下文片段，输出可直接送入 prompt 的候选。

    参数：
    - keep_original_top_n: 至少优先尝试保留的 original-query 命中数量。
    """
    budget = max(128, int(max_tokens or 1800))
    keep_original_top_n = max(0, int(keep_original_top_n or 0))

    if not chunks:
        return {
            "selected_chunks": [],
            "total_tokens": 0,
            "stats": {
                "input_count": 0,
                "deduped_count": 0,
                "dropped_noisy": 0,
                "dropped_over_budget": 0,
                "dropped_over_budget_chunks": [],
                "kept_by_original_priority": 0,
                "input_chars": 0,
                "output_chars": 0,
            },
        }

    # 第一步：噪音过滤 + 精确去重（保留分数更高者）。
    deduped_map: dict[str, dict] = {}
    dropped_noisy = 0
    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "")
        if _is_noisy(text):
            dropped_noisy += 1
            continue

        key = _normalize_key(text)
        if not key:
            dropped_noisy += 1
            continue

        existing = deduped_map.get(key)
        if not existing or _rank_score(chunk) > _rank_score(existing):
            deduped_map[key] = dict(chunk)

    deduped = list(deduped_map.values())
    deduped.sort(key=_rank_score, reverse=True)

    selected, total_tokens, dropped_over_budget, dropped_over_budget_chunks, kept_by_original_priority = _compress_by_budget(
        deduped,
        budget=budget,
        keep_original_top_n=keep_original_top_n,
    )

    input_chars = sum(len(str(x.get("chunk_text") or "")) for x in deduped)
    output_chars = sum(len(str(x.get("chunk_text") or "")) for x in selected)

    return {
        "selected_chunks": selected,
        "total_tokens": total_tokens,
        "stats": {
            "input_count": len(chunks),
            "deduped_count": len(deduped),
            "dropped_noisy": dropped_noisy,
            "dropped_over_budget": dropped_over_budget,
            "dropped_over_budget_chunks": dropped_over_budget_chunks,
            "kept_by_original_priority": kept_by_original_priority,
            "input_chars": input_chars,
            "output_chars": output_chars,
        },
    }
