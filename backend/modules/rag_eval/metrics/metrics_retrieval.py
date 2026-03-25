from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Iterable


def _norm_ids(ids: Iterable[str] | None) -> list[str]:
    """统一把 ID 列表转换为字符串列表，过滤空值。"""
    if not ids:
        return []
    return [str(x).strip() for x in ids if str(x).strip()]


def recall_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """计算 Recall@K。"""
    gold = set(_norm_ids(gold_ids))
    if not gold:
        return 0.0
    topk = set(_norm_ids(retrieved_ids)[: max(0, int(k))])
    hit = len(gold.intersection(topk))
    return hit / len(gold)


def hit_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """计算 Hit@K（命中至少一个则为 1）。"""
    gold = set(_norm_ids(gold_ids))
    if not gold:
        return 0.0
    topk = set(_norm_ids(retrieved_ids)[: max(0, int(k))])
    return 1.0 if gold.intersection(topk) else 0.0


def first_hit_rank(retrieved_ids: list[str], gold_ids: list[str]) -> int | None:
    """返回首个命中的排名（从 1 开始），未命中返回 None。"""
    gold = set(_norm_ids(gold_ids))
    if not gold:
        return None
    for idx, cid in enumerate(_norm_ids(retrieved_ids), start=1):
        if cid in gold:
            return idx
    return None


def mrr(retrieved_ids: list[str], gold_ids: list[str]) -> float:
    """计算 MRR。"""
    rank = first_hit_rank(retrieved_ids, gold_ids)
    if not rank:
        return 0.0
    return 1.0 / rank


def context_precision(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Context Precision = 前 K 中相关数 / K。"""
    k = max(1, int(k))
    topk = _norm_ids(retrieved_ids)[:k]
    if not topk:
        return 0.0
    gold = set(_norm_ids(gold_ids))
    hit = sum(1 for cid in topk if cid in gold)
    return hit / k


def context_recall(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Context Recall = 前 K 中命中 gold 数 / gold 总数。"""
    gold = set(_norm_ids(gold_ids))
    if not gold:
        return 0.0
    topk = set(_norm_ids(retrieved_ids)[: max(0, int(k))])
    hit = len(gold.intersection(topk))
    return hit / len(gold)


def aggregate_retrieval_metrics(results: list[dict]) -> dict:
    """聚合检索层指标。"""
    if not results:
        return {
            "recall@1": 0.0,
            "recall@3": 0.0,
            "recall@5": 0.0,
            "recall@10": 0.0,
            "hit@1": 0.0,
            "hit@5": 0.0,
            "mrr": 0.0,
            "avg_context_precision": 0.0,
            "avg_context_recall": 0.0,
        }

    def _avg(vals: list[float]) -> float:
        return float(mean(vals)) if vals else 0.0

    recall1 = [_safe_float(r.get("recall@1")) for r in results]
    recall3 = [_safe_float(r.get("recall@3")) for r in results]
    recall5 = [_safe_float(r.get("recall@5")) for r in results]
    recall10 = [_safe_float(r.get("recall@10")) for r in results]
    hit1 = [_safe_float(r.get("hit@1")) for r in results]
    hit5 = [_safe_float(r.get("hit@5")) for r in results]
    mrrs = [_safe_float(r.get("mrr")) for r in results]
    cps = [_safe_float(r.get("context_precision")) for r in results]
    crs = [_safe_float(r.get("context_recall")) for r in results]

    return {
        "recall@1": _avg(recall1),
        "recall@3": _avg(recall3),
        "recall@5": _avg(recall5),
        "recall@10": _avg(recall10),
        "hit@1": _avg(hit1),
        "hit@5": _avg(hit5),
        "mrr": _avg(mrrs),
        "avg_context_precision": _avg(cps),
        "avg_context_recall": _avg(crs),
    }


def aggregate_metrics_by_tag(results: list[dict]) -> dict[str, dict]:
    """按标签聚合指标。"""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in results:
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        if not tags:
            buckets["__untagged__"].append(item)
            continue
        for tag in tags:
            buckets[str(tag)].append(item)

    return {tag: aggregate_retrieval_metrics(items) for tag, items in buckets.items()}


def _safe_float(v) -> float:
    """兜底转换，防止 None/字符串导致聚合异常。"""
    try:
        return float(v)
    except Exception:
        return 0.0

