from __future__ import annotations

from typing import Any

from modules.rag_eval.analysis.failure_rules import DEFAULT_FAILURE_THRESHOLDS, FailureThresholds


def analyze_failure_reason(
    sample: dict[str, Any],
    retrieved_chunks: list[dict],
    reranked_chunks: list[dict],
    answer: str,
    metrics: dict[str, Any],
    judge_result: dict[str, Any],
    rerank_top_n: int,
    thresholds: FailureThresholds = DEFAULT_FAILURE_THRESHOLDS,
) -> tuple[str, str]:
    """
    失败归因主入口。
    返回 (failure_reason, failure_detail)。
    """
    gold_chunks = {str(x) for x in (sample.get("gold_chunks") or []) if str(x).strip()}
    retrieved_ids = [str(c.get("chunk_id") or "").strip() for c in retrieved_chunks if str(c.get("chunk_id") or "").strip()]
    reranked_ids = [str(c.get("chunk_id") or "").strip() for c in reranked_chunks if str(c.get("chunk_id") or "").strip()]

    # 1. no_recall：检索阶段完全没召回 gold
    if gold_chunks and not any(cid in gold_chunks for cid in retrieved_ids):
        return "no_recall", "未召回任何 gold chunk"

    # 2. low_rank：召回到了但排名太靠后，未进入 rerank_top_n
    first_rank = metrics.get("first_hit_rank")
    try:
        first_rank_val = int(first_rank) if first_rank is not None else None
    except Exception:
        first_rank_val = None
    if first_rank_val and first_rank_val > max(rerank_top_n, thresholds.low_rank_cutoff):
        return "low_rank", f"gold chunk 首次出现排名第 {first_rank_val}，未进入 rerank_top_n={rerank_top_n}"

    # 3. context_noise：命中但上下文噪音高
    cp = _to_float(metrics.get("context_precision"))
    if cp > 0 and cp < thresholds.context_precision_threshold:
        return "context_noise", f"context_precision={cp:.3f}，噪音 chunk 过多"

    # 4. wrong_version：版本不一致
    expected_ver = str(sample.get("expected_doc_version") or "").strip()
    if expected_ver:
        hit_versions = [str((c.get("metadata") or {}).get("doc_version") or c.get("doc_version") or "").strip() for c in reranked_chunks[: max(1, rerank_top_n)]]
        hit_versions = [v for v in hit_versions if v]
        if hit_versions and not any(v == expected_ver for v in hit_versions):
            return "wrong_version", f"命中文档版本 {','.join(hit_versions)}，期望版本 {expected_ver}"

    # 5. hallucination：忠实性低且出现上下文不支持断言
    faith = _to_float(judge_result.get("faithfulness_score", metrics.get("faithfulness_score")))
    hallucinated = judge_result.get("hallucinated_claims") or []
    if faith < thresholds.faithfulness_threshold and hallucinated:
        claim = str(hallucinated[0])
        return "hallucination", f"答案包含上下文未支持的说法：{claim}"

    # 6. incomplete_answer：命中且大方向正确，但关键点不全
    correctness = _to_float(judge_result.get("answer_correctness_score", metrics.get("answer_correctness_score")))
    missing = judge_result.get("missing_points") or []
    if missing and correctness < thresholds.answer_correctness_threshold:
        return "incomplete_answer", f"答案遗漏关键点：{str(missing[0])}"

    # 默认兜底
    if not answer.strip():
        return "incomplete_answer", "模型未返回有效答案"
    if gold_chunks and not any(cid in gold_chunks for cid in reranked_ids[: max(1, rerank_top_n)]):
        return "low_rank", "召回到 gold chunk 但未进入最终上下文"
    return "incomplete_answer", "规则未命中明确类别，默认归因为回答不完整"


def _to_float(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0

