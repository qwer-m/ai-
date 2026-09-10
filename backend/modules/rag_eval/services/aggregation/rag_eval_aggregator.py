from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db.model_defs import RagDataset, RagDatasetSample, RagEvalRun, RagEvalSampleResult
from modules.rag_eval.metrics.metrics_generation import aggregate_generation_metrics
from modules.rag_eval.metrics.metrics_retrieval import aggregate_metrics_by_tag, aggregate_retrieval_metrics


def aggregate_run_metrics(db: Session, run_id: int) -> dict[str, Any]:
    """聚合 run 的总览与分类指标。"""
    rows = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run_id).all()
    records: list[dict[str, Any]] = []
    for row in rows:
        detail = row.detail_json or {}
        retrieval = detail.get("retrieval_metrics") or {}
        records.append(
            {
                **retrieval,
                "answer_correctness_score": row.answer_correctness_score,
                "faithfulness_score": row.faithfulness_score,
                "latency_ms": row.latency_ms,
                "retrieval_latency_ms": row.retrieval_latency_ms,
                "generation_latency_ms": row.generation_latency_ms,
                "cost_json": row.cost_json or {},
                "answer_correct": row.answer_correct,
                "tags": detail.get("tags") or [],
            }
        )

    retrieval_metrics = aggregate_retrieval_metrics(records)
    generation_metrics = aggregate_generation_metrics(records)
    by_tag = aggregate_metrics_by_tag(records)

    sample_ids = [int(x.sample_id) for x in rows]
    sample_map = (
        {s.id: s for s in db.query(RagDatasetSample).filter(RagDatasetSample.id.in_(sample_ids)).all()} if sample_ids else {}
    )

    by_difficulty: dict[str, dict[str, float]] = {}
    for level in {"easy", "medium", "hard"}:
        subset = []
        for row in rows:
            sample = sample_map.get(int(row.sample_id))
            if sample and sample.difficulty == level:
                detail = row.detail_json or {}
                retrieval = detail.get("retrieval_metrics") or {}
                subset.append(
                    {
                        **retrieval,
                        "answer_correctness_score": row.answer_correctness_score,
                        "faithfulness_score": row.faithfulness_score,
                        "latency_ms": row.latency_ms,
                        "retrieval_latency_ms": row.retrieval_latency_ms,
                        "generation_latency_ms": row.generation_latency_ms,
                        "cost_json": row.cost_json or {},
                        "answer_correct": row.answer_correct,
                    }
                )
        by_difficulty[level] = {
            **aggregate_retrieval_metrics(subset),
            **aggregate_generation_metrics(subset),
            "count": len(subset),
        }

    by_failure_reason: dict[str, int] = {}
    for row in rows:
        key = str(row.failure_reason or "pass")
        by_failure_reason[key] = by_failure_reason.get(key, 0) + 1

    dataset_type = "unknown"
    run = db.query(RagEvalRun).filter(RagEvalRun.id == run_id).first()
    if run:
        dataset = db.query(RagDataset).filter(RagDataset.id == run.dataset_id).first()
        if dataset:
            dataset_type = str(dataset.type or "unknown")

    by_doc_type: dict[str, int] = {}
    by_cross_document: dict[str, int] = {"true": 0, "false": 0}
    by_version_diff: dict[str, int] = {"true": 0, "false": 0}
    for sample in sample_map.values():
        filters = dict(sample.metadata_filters or {})
        doc_type = str(filters.get("doc_type") or "unknown")
        by_doc_type[doc_type] = by_doc_type.get(doc_type, 0) + 1
        by_cross_document["true" if bool(filters.get("cross_document", False)) else "false"] += 1
        by_version_diff["true" if bool(filters.get("version_diff", False)) else "false"] += 1

    return {
        "overview": {**retrieval_metrics, **generation_metrics},
        "by_tag": by_tag,
        "by_difficulty": by_difficulty,
        "by_failure_reason": by_failure_reason,
        "by_dataset_type": {dataset_type: len(records)},
        "by_doc_type": by_doc_type,
        "by_cross_document": by_cross_document,
        "by_version_diff": by_version_diff,
        "count": len(records),
    }


def estimate_tokens(context: str, answer: str) -> dict[str, Any]:
    """轻量 token 估算（字符近似）。"""
    input_tokens = int(max(1, len(context) / 4))
    output_tokens = int(max(1, len(answer) / 4)) if answer else 0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": True,
    }

