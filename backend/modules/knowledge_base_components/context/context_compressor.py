"""
上下文压缩模块（RAG 第二阶段检索治理）。

职责：
1. 去重与噪音过滤，降低无关上下文。
2. 按 token 预算截取高价值片段。
3. 优先保留 original-query 命中片段，保证主问题上下文稳定。
4. 阶段2.5：压缩保真检测（数值范围/时间限制/唯一性/状态枚举/关键字段）。
"""

from __future__ import annotations

import re
from typing import Iterable

from modules.domain.stage25_switches import STAGE25_SWITCHES


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
        or chunk.get("rerank_score")
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
        "final_score": float(chunk.get("final_score") or chunk.get("rerank_score") or chunk.get("score") or 0.0),
        "estimated_tokens": token_cost,
        "reason": reason,
    }


def _extract_constraint_signals(text: str) -> dict[str, set[str]]:
    """
    提取保真检测信号（轻量规则）。
    """
    raw = str(text or "")
    if not raw.strip():
        return {
            "numeric_range": set(),
            "time_limit": set(),
            "uniqueness": set(),
            "enum_status": set(),
            "key_fields": set(),
        }

    numeric_range = set(re.findall(r"\d+\s*[-~至到]\s*\d+", raw))
    numeric_range.update(re.findall(r"[<>]=?\s*\d+(?:\.\d+)?", raw))

    time_limit = set(
        re.findall(r"\d+\s*(?:天|日|小时|分钟|秒|周|月|years?|days?|hours?|minutes?|seconds?)", raw, flags=re.IGNORECASE)
    )
    time_limit.update(re.findall(r"(?:T\+\d+|D\+\d+|within\s+\d+\s+\w+)", raw, flags=re.IGNORECASE))

    uniqueness = set(
        re.findall(r"(仅一次|只能一次|唯一|不可重复|禁止重复|去重|幂等|single-use|unique|no repeat|dedup)", raw, flags=re.IGNORECASE)
    )

    enum_status: set[str] = set()
    enum_status.update(re.findall(r"(已使用|已拒绝|已过期|待处理|处理中|已完成|失败|成功)", raw))
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", raw):
        t = token.lower()
        if t in {"pending", "approved", "rejected", "expired", "success", "failed", "done", "processing"}:
            enum_status.add(t)

    key_fields: set[str] = set()
    key_fields.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,32}", raw))
    for m in re.findall(r"[\u4e00-\u9fff]{2,12}(?:字段|编号|id|状态|金额|日期|时间|账号|用户)", raw, flags=re.IGNORECASE):
        key_fields.add(m)
    key_fields = {x for x in key_fields if len(x) <= 40}

    return {
        "numeric_range": numeric_range,
        "time_limit": time_limit,
        "uniqueness": uniqueness,
        "enum_status": enum_status,
        "key_fields": key_fields,
    }


def _flatten_signals(signals: dict[str, set[str]]) -> set[str]:
    all_terms: set[str] = set()
    for values in signals.values():
        all_terms.update(values)
    return all_terms


def _collect_signals(chunks: Iterable[dict]) -> dict[str, set[str]]:
    aggregate = {
        "numeric_range": set(),
        "time_limit": set(),
        "uniqueness": set(),
        "enum_status": set(),
        "key_fields": set(),
    }
    for chunk in chunks:
        signals = _extract_constraint_signals(str(chunk.get("chunk_text") or ""))
        for key in aggregate:
            aggregate[key].update(signals.get(key) or set())
    return aggregate


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return float(numerator) / float(denominator)


def _fidelity_report(
    input_chunks: Iterable[dict],
    selected_chunks: Iterable[dict],
) -> dict:
    """构建压缩保真检测报告。"""
    source_signals = _collect_signals(input_chunks)
    kept_signals = _collect_signals(selected_chunks)

    source_terms = _flatten_signals(source_signals)
    kept_terms = _flatten_signals(kept_signals)
    missing_terms = sorted(source_terms - kept_terms)

    source_count = len(source_terms)
    kept_count = len(kept_terms.intersection(source_terms))
    missing_count = len(missing_terms)
    loss_ratio = (missing_count / source_count) if source_count else 0.0
    retention_ratio = _ratio(kept_count, source_count)

    category_retention: dict[str, dict] = {}
    for category, source_values in source_signals.items():
        kept_values = kept_signals.get(category) or set()
        retained = len(source_values.intersection(kept_values))
        category_retention[category] = {
            "source": len(source_values),
            "retained": retained,
            "retention_ratio": round(_ratio(retained, len(source_values)), 4),
            "missing_preview": sorted(list(source_values - kept_values))[:12],
        }

    if source_count == 0:
        risk_level = "none"
    elif loss_ratio >= 0.45:
        risk_level = "high"
    elif loss_ratio >= 0.20:
        risk_level = "medium"
    else:
        risk_level = "low"

    min_retention = max(0.0, min(1.0, float(STAGE25_SWITCHES.fidelity_min_retention or 0.7)))
    warning = retention_ratio < min_retention

    return {
        "enabled": True,
        "source_constraint_terms": source_count,
        "retained_constraint_terms": kept_count,
        "missing_constraint_terms": missing_count,
        "constraint_loss_ratio": round(loss_ratio, 4),
        "retention_ratio": round(retention_ratio, 4),
        "retention_threshold": round(min_retention, 4),
        "warning": bool(warning),
        "risk_level": risk_level,
        "missing_terms_preview": missing_terms[:20],
        "category_retention": category_retention,
        "fallback_mode": str(STAGE25_SWITCHES.fidelity_fallback_mode or "warn"),
        "fallback_applied": False,
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
                "fidelity": {"enabled": False},
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

    fidelity = (
        _fidelity_report(deduped, selected)
        if STAGE25_SWITCHES.compression_fidelity_enabled
        else {"enabled": False}
    )

    # 保真告警时按配置执行降级。
    fallback_mode = str(STAGE25_SWITCHES.fidelity_fallback_mode or "warn").lower()
    if fidelity.get("enabled") and fidelity.get("warning") and fallback_mode in {"fallback_light", "fallback_raw"}:
        if fallback_mode == "fallback_light":
            factor = max(1.0, float(STAGE25_SWITCHES.fidelity_light_budget_factor or 1.25))
            alt_budget = int(max(budget, budget * factor))
            alt_keep_original = keep_original_top_n + 1
        else:
            # fallback_raw：尽量保留原文，预算显著放宽但仍保持硬上限，避免无限膨胀。
            alt_budget = int(max(budget, budget * 2.0))
            alt_keep_original = max(keep_original_top_n + 2, 4)

        alt_selected, alt_total_tokens, alt_dropped_over_budget, alt_dropped_over_budget_chunks, alt_kept_by_original = _compress_by_budget(
            deduped,
            budget=alt_budget,
            keep_original_top_n=alt_keep_original,
        )
        alt_fidelity = _fidelity_report(deduped, alt_selected)

        old_retention = float(fidelity.get("retention_ratio") or 0.0)
        new_retention = float(alt_fidelity.get("retention_ratio") or 0.0)
        if new_retention >= old_retention:
            selected = alt_selected
            total_tokens = alt_total_tokens
            dropped_over_budget = alt_dropped_over_budget
            dropped_over_budget_chunks = alt_dropped_over_budget_chunks
            kept_by_original_priority = alt_kept_by_original
            fidelity = alt_fidelity
            fidelity["fallback_applied"] = True
            fidelity["fallback_mode"] = fallback_mode
            fidelity["fallback_budget"] = alt_budget

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
            "fidelity": fidelity,
        },
    }
