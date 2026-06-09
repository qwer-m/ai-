from __future__ import annotations

import difflib
import re
from collections import defaultdict
from typing import Any


def _normalize_text(text: str) -> str:
    """归一化文本，供冗余判断使用。"""
    return " ".join((text or "").strip().lower().split())


def _is_redundant(text_a: str, text_b: str, threshold: float) -> bool:
    """
    判断两段文本是否高冗余。

    规则：
    - 完整包含关系直接判冗余；
    - 其余场景用 SequenceMatcher 相似度判断。
    """
    a = _normalize_text(text_a)
    b = _normalize_text(text_b)
    if not a or not b:
        return False
    if len(a) < 40 or len(b) < 40:
        return False
    if a in b or b in a:
        return True
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= max(0.5, min(0.99, float(threshold)))


def infer_multi_doc_query(query: str) -> bool:
    """
    识别是否更像“流程/规则类”问题，从而建议多文档覆盖。
    """
    q = str(query or "")
    if not q.strip():
        return False
    signals = [
        "流程",
        "规则",
        "权限",
        "字段",
        "条件",
        "异常",
        "对比",
        "差异",
        "关联",
        "跨",
        "如何",
        "哪些",
        "怎么",
    ]
    return any(sig in q for sig in signals)


def build_doc_hit_stats(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    生成文档级命中统计，供前端“文档级命中概览”展示。
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        doc_key = str(chunk.get("doc_id") or chunk.get("filename") or "unknown")
        grouped[doc_key].append(chunk)

    stats: list[dict[str, Any]] = []
    for doc_key, rows in grouped.items():
        rows_sorted = sorted(
            rows,
            key=lambda x: float(x.get("final_score") or x.get("score") or 0.0),
            reverse=True,
        )
        top_score = float(rows_sorted[0].get("final_score") or rows_sorted[0].get("score") or 0.0)
        avg_score = sum(float(r.get("final_score") or r.get("score") or 0.0) for r in rows_sorted) / max(1, len(rows_sorted))
        stats.append(
            {
                "doc_id": rows_sorted[0].get("doc_id"),
                "filename": rows_sorted[0].get("filename"),
                "doc_type": rows_sorted[0].get("doc_type"),
                "hit_chunks": len(rows_sorted),
                "top_score": top_score,
                "avg_score": avg_score,
                "title_hit_terms": rows_sorted[0].get("title_hit_terms") or [],
                "content_hit_terms": rows_sorted[0].get("content_hit_terms") or [],
            }
        )

    stats.sort(key=lambda x: (x["top_score"], x["avg_score"]), reverse=True)
    return stats


def build_dominance_warning(chunks: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any] | None:
    """
    检测是否出现单文档霸榜。
    """
    rows = chunks[: max(1, int(top_n))]
    if not rows:
        return None
    doc_counter: dict[str, int] = defaultdict(int)
    for row in rows:
        key = str(row.get("doc_id") or row.get("filename") or "unknown")
        doc_counter[key] += 1

    if not doc_counter:
        return None

    max_doc, max_count = max(doc_counter.items(), key=lambda x: x[1])
    ratio = max_count / max(1, len(rows))
    if ratio < 0.8:
        return None

    return {
        "type": "single_doc_dominance",
        "message": f"Top{len(rows)} 中有 {max_count} 条来自同一文档，可能存在文档霸榜。",
        "doc_key": max_doc,
        "ratio": ratio,
    }


def select_diverse_chunks(
    chunks: list[dict[str, Any]],
    *,
    final_top_n: int,
    max_chunks_per_doc: int,
    min_docs: int,
    redundancy_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    动态 chunk 分配：先保文档覆盖，再做增益补充。

    算法：
    1. 按分数排序，按文档分组；
    2. 第一轮每文档取 1 条，优先满足 min_docs；
    3. 第二轮按分数增益继续填充，每文档不超过 max_chunks_per_doc；
    4. 对高冗余 chunk 做过滤，避免重复内容占用预算。
    """
    if not chunks:
        return [], {
            "selected_count": 0,
            "doc_count": 0,
            "dropped_doc_cap": 0,
            "dropped_redundant": 0,
            "per_doc_counts": {},
        }

    target_n = max(1, int(final_top_n))
    # 中文注释：阶段2.5加固要求单文档最多 2~3 个片段，后端统一做上限约束。
    per_doc_cap = max(1, min(3, int(max_chunks_per_doc)))
    min_doc_need = max(1, int(min_docs))

    def _row_score(row: dict[str, Any]) -> float:
        for key in ("final_score", "fusion_score", "rerank_score", "score"):
            value = row.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except Exception:
                continue
        return 0.0

    def _extract_info_terms(row: dict[str, Any]) -> set[str]:
        terms: set[str] = set()
        for key in ("title_hit_terms", "content_hit_terms", "query_terms"):
            for item in (row.get(key) or []):
                token = str(item).strip().lower()
                if token and len(token) <= 48:
                    terms.add(token)
        if terms:
            return terms

        # 中文注释：缺命中词时回退到轻量 token 提取，用于信息增益评估。
        text = f"{row.get('filename') or ''} {row.get('chunk_text') or ''}"
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{1,}|\d{2,}", text):
            normalized = token.strip().lower()
            if normalized and len(normalized) <= 48:
                terms.add(normalized)
            if len(terms) >= 20:
                break
        return terms

    sorted_rows = sorted(chunks, key=_row_score, reverse=True)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted_rows:
        key = str(row.get("doc_id") or row.get("filename") or "unknown")
        grouped[key].append(row)

    doc_order = sorted(
        grouped.keys(),
        key=lambda doc_key: float(grouped[doc_key][0].get("final_score") or grouped[doc_key][0].get("score") or 0.0),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    selected_doc_round1: set[str] = set()
    selected_texts: list[str] = []
    per_doc_counts: dict[str, int] = defaultdict(int)
    selected_ids: set[str] = set()
    covered_terms: set[str] = set()
    dropped_doc_cap = 0
    dropped_redundant = 0

    # 第一轮：每文档先拿一个强 chunk，先满足文档覆盖。
    for doc_key in doc_order:
        if len(selected) >= target_n:
            break
        if len(per_doc_counts) >= min_doc_need and len(selected) >= min_doc_need:
            break

        first = grouped[doc_key][0]
        text = str(first.get("chunk_text") or "")
        if any(_is_redundant(text, old, redundancy_threshold) for old in selected_texts):
            dropped_redundant += 1
            continue

        item = dict(first)
        item["selection_reason"] = "doc_coverage_round"
        selected.append(item)
        selected_doc_round1.add(doc_key)
        selected_texts.append(text)
        selected_ids.add(str(item.get("chunk_id") or ""))
        covered_terms.update(_extract_info_terms(item))
        per_doc_counts[doc_key] += 1

    # 第二轮：按“score 增益 + 信息增益”补齐，受每文档上限与冗余阈值约束。
    while len(selected) < target_n:
        best_row: dict[str, Any] | None = None
        best_gain = float("-inf")
        best_doc_key = ""
        best_text = ""
        best_new_terms: set[str] = set()

        for row in sorted_rows:
            doc_key = str(row.get("doc_id") or row.get("filename") or "unknown")
            if per_doc_counts[doc_key] >= per_doc_cap:
                dropped_doc_cap += 1
                continue

            row_id = str(row.get("chunk_id") or "")
            if row_id and row_id in selected_ids:
                continue

            text = str(row.get("chunk_text") or "")
            if any(_is_redundant(text, old, redundancy_threshold) for old in selected_texts):
                dropped_redundant += 1
                continue

            row_terms = _extract_info_terms(row)
            new_terms = row_terms - covered_terms
            info_gain = len(new_terms) / max(1, len(row_terms) or 1)
            score_gain = _row_score(row)
            total_gain = score_gain + (0.18 * info_gain)

            if total_gain > best_gain:
                best_gain = total_gain
                best_row = row
                best_doc_key = doc_key
                best_text = text
                best_new_terms = new_terms

        if not best_row:
            break

        item = dict(best_row)
        item["selection_reason"] = "score_info_gain_round"
        selected.append(item)
        selected_texts.append(best_text)
        selected_ids.add(str(item.get("chunk_id") or ""))
        covered_terms.update(best_new_terms)
        per_doc_counts[best_doc_key] += 1

    stats = {
        "selected_count": len(selected),
        "doc_count": len(per_doc_counts),
        "dropped_doc_cap": dropped_doc_cap,
        "dropped_redundant": dropped_redundant,
        "per_doc_counts": dict(per_doc_counts),
        "doc_coverage_selected_docs": len(selected_doc_round1),
        "doc_coverage_target_docs": min(min_doc_need, len(doc_order)),
        "doc_coverage_triggered": bool(len(selected_doc_round1) >= min(min_doc_need, len(doc_order))),
        "second_round_mode": "score_plus_information_gain",
        "max_chunks_per_doc_applied": per_doc_cap,
    }
    return selected, stats
