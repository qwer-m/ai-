from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from core.db.models import RagDatasetSample, RagEvalRun, RagEvalSampleResult


def compare_runs(db: Session, run_a_id: int, run_b_id: int, user_id: int) -> dict[str, Any]:
    """对比两个评测运行结果（run_b - run_a）。"""
    run_a = db.query(RagEvalRun).filter(RagEvalRun.id == run_a_id, RagEvalRun.user_id == user_id).first()
    run_b = db.query(RagEvalRun).filter(RagEvalRun.id == run_b_id, RagEvalRun.user_id == user_id).first()
    if not run_a or not run_b:
        raise ValueError("Run not found")

    rows_a = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run_a_id).all()
    rows_b = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run_b_id).all()
    map_a = {int(x.sample_id): x for x in rows_a}
    map_b = {int(x.sample_id): x for x in rows_b}
    sample_ids = sorted(set(map_a.keys()) | set(map_b.keys()))
    sample_map = _load_samples(db, sample_ids)

    improved_ids: list[int] = []
    regressed_ids: list[int] = []
    unchanged_correct = 0
    unchanged_incorrect = 0
    improved_samples: list[dict[str, Any]] = []
    regressed_samples: list[dict[str, Any]] = []
    failure_reason_changes: list[dict[str, Any]] = []

    for sid in sample_ids:
        row_a = map_a.get(sid)
        row_b = map_b.get(sid)
        if not row_a or not row_b:
            continue
        query = str(sample_map.get(sid).query) if sid in sample_map else ""
        a_ok = bool(row_a.answer_correct)
        b_ok = bool(row_b.answer_correct)
        detail = {
            "sample_id": sid,
            "query": query,
            "from_correct": a_ok,
            "to_correct": b_ok,
            "from_failure_reason": row_a.failure_reason,
            "to_failure_reason": row_b.failure_reason,
            "first_hit_rank_a": row_a.first_hit_rank,
            "first_hit_rank_b": row_b.first_hit_rank,
            "answer_correctness_delta": _delta(row_a.answer_correctness_score, row_b.answer_correctness_score),
            "faithfulness_delta": _delta(row_a.faithfulness_score, row_b.faithfulness_score),
        }
        if not a_ok and b_ok:
            improved_ids.append(sid)
            improved_samples.append(detail)
        elif a_ok and not b_ok:
            regressed_ids.append(sid)
            regressed_samples.append(detail)
        elif a_ok and b_ok:
            unchanged_correct += 1
        else:
            unchanged_incorrect += 1
        if (row_a.failure_reason or "") != (row_b.failure_reason or ""):
            failure_reason_changes.append(
                {
                    "sample_id": sid,
                    "query": query,
                    "from_failure_reason": row_a.failure_reason or "pass",
                    "to_failure_reason": row_b.failure_reason or "pass",
                }
            )

    metric_diff = _calc_metric_diff(run_a.metrics_json or {}, run_b.metrics_json or {})
    by_tag_diff = _diff_bucket(_bucket_stats(rows_a, sample_map, "tag"), _bucket_stats(rows_b, sample_map, "tag"))
    by_difficulty_diff = _diff_bucket(
        _bucket_stats(rows_a, sample_map, "difficulty"),
        _bucket_stats(rows_b, sample_map, "difficulty"),
    )
    by_doc_type_diff = _diff_bucket(
        _bucket_stats(rows_a, sample_map, "doc_type"),
        _bucket_stats(rows_b, sample_map, "doc_type"),
    )

    failure_a = Counter([(x.failure_reason or "pass") for x in rows_a])
    failure_b = Counter([(x.failure_reason or "pass") for x in rows_b])
    all_reasons = sorted(set(failure_a.keys()) | set(failure_b.keys()))
    by_failure_reason_diff = {
        reason: {
            "count_a": int(failure_a.get(reason, 0)),
            "count_b": int(failure_b.get(reason, 0)),
            "count_diff": int(failure_b.get(reason, 0) - failure_a.get(reason, 0)),
        }
        for reason in all_reasons
    }

    return {
        "run_a": _run_payload(run_a),
        "run_b": _run_payload(run_b),
        "metric_diff": metric_diff,
        "summary": {
            "improved_samples": len(improved_ids),
            "regressed_samples": len(regressed_ids),
            "unchanged_correct": unchanged_correct,
            "unchanged_incorrect": unchanged_incorrect,
        },
        "improved_sample_ids": improved_ids,
        "regressed_sample_ids": regressed_ids,
        "improved_samples": improved_samples,
        "regressed_samples": regressed_samples,
        "failure_reason_changes": failure_reason_changes[:200],
        "by_tag_diff": by_tag_diff,
        "by_difficulty_diff": by_difficulty_diff,
        "by_doc_type_diff": by_doc_type_diff,
        "by_failure_reason_diff": by_failure_reason_diff,
    }


def _run_payload(run: RagEvalRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "project_id": run.project_id,
        "run_name": run.run_name,
        "status": run.status,
        "total_samples": int(run.total_samples or 0),
        "finished_samples": int(run.finished_samples or 0),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _calc_metric_diff(metrics_a: dict[str, Any], metrics_b: dict[str, Any]) -> dict[str, float]:
    """统一输出 run_b - run_a 的核心指标差值。"""
    overview_a = dict(metrics_a.get("overview") or {})
    overview_b = dict(metrics_b.get("overview") or {})
    return {
        "recall@5": _delta(overview_a.get("recall@5"), overview_b.get("recall@5")),
        "mrr": _delta(overview_a.get("mrr"), overview_b.get("mrr")),
        "answer_correctness": _delta(overview_a.get("avg_answer_correctness"), overview_b.get("avg_answer_correctness")),
        "faithfulness": _delta(overview_a.get("avg_faithfulness"), overview_b.get("avg_faithfulness")),
        "pass_rate": _delta(overview_a.get("pass_rate"), overview_b.get("pass_rate")),
        "context_precision": _delta(overview_a.get("avg_context_precision"), overview_b.get("avg_context_precision")),
        "context_recall": _delta(overview_a.get("avg_context_recall"), overview_b.get("avg_context_recall")),
    }


def _load_samples(db: Session, sample_ids: list[int]) -> dict[int, RagDatasetSample]:
    if not sample_ids:
        return {}
    rows = db.query(RagDatasetSample).filter(RagDatasetSample.id.in_(sample_ids)).all()
    return {int(x.id): x for x in rows}


def _bucket_stats(rows: list[RagEvalSampleResult], sample_map: dict[int, RagDatasetSample], mode: str) -> dict[str, dict[str, float]]:
    buckets: dict[str, dict[str, float]] = {}
    for row in rows:
        sample = sample_map.get(int(row.sample_id))
        keys = _resolve_bucket_keys(sample, mode)
        for key in keys:
            if key not in buckets:
                buckets[key] = {"total": 0.0, "correct": 0.0}
            buckets[key]["total"] += 1.0
            if bool(row.answer_correct):
                buckets[key]["correct"] += 1.0
    for val in buckets.values():
        total = val["total"] or 1.0
        val["pass_rate"] = val["correct"] / total
    return buckets


def _resolve_bucket_keys(sample: RagDatasetSample | None, mode: str) -> list[str]:
    if not sample:
        return ["unknown"]
    if mode == "tag":
        tags = [str(x).strip() for x in (sample.tags or []) if str(x).strip()]
        return tags if tags else ["__untagged__"]
    if mode == "difficulty":
        return [str(sample.difficulty or "unknown")]
    if mode == "doc_type":
        filters = dict(sample.metadata_filters or {})
        return [str(filters.get("doc_type") or "unknown")]
    return ["unknown"]


def _diff_bucket(a: dict[str, dict[str, float]], b: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    keys = sorted(set(a.keys()) | set(b.keys()))
    out: dict[str, dict[str, float]] = {}
    for key in keys:
        a_item = a.get(key, {})
        b_item = b.get(key, {})
        out[key] = {
            "pass_rate_a": _to_float(a_item.get("pass_rate")),
            "pass_rate_b": _to_float(b_item.get("pass_rate")),
            "pass_rate_diff": _delta(a_item.get("pass_rate"), b_item.get("pass_rate")),
            "count_a": _to_float(a_item.get("total")),
            "count_b": _to_float(b_item.get("total")),
            "count_diff": _to_float(b_item.get("total")) - _to_float(a_item.get("total")),
        }
    return out


def _delta(a: Any, b: Any) -> float:
    return _to_float(b) - _to_float(a)


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0

