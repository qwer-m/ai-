from __future__ import annotations

from datetime import datetime
from math import isclose
from types import SimpleNamespace

from modules.rag_eval.services.rag_eval_compare_service import _calc_metric_diff, compare_runs


class FakeQuery:
    """中文注释：最小 Query 桩，仅覆盖 compare_runs 需要的方法。"""

    def __init__(self, *, first_result=None, all_result=None):
        self._first_result = first_result
        self._all_result = all_result or []

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._first_result

    def all(self):
        return list(self._all_result)


class FakeSession:
    """中文注释：按调用顺序返回预设 Query，避免依赖真实数据库。"""

    def __init__(self, queries):
        self._queries = list(queries)
        self._index = 0

    def query(self, model):
        if self._index >= len(self._queries):
            raise AssertionError('FakeSession query calls exceeded')
        q = self._queries[self._index]
        self._index += 1
        return q


def _make_run(run_id: int, metrics: dict):
    now = datetime(2026, 3, 19, 12, 0, 0)
    return SimpleNamespace(
        id=run_id,
        dataset_id=1,
        project_id=9,
        run_name=f'run-{run_id}',
        status='success',
        total_samples=3,
        finished_samples=3,
        started_at=now,
        finished_at=now,
        metrics_json=metrics,
    )


def _make_row(sample_id: int, answer_correct: bool, failure_reason: str | None, first_hit_rank: int | None, ac: float, faith: float):
    return SimpleNamespace(
        sample_id=sample_id,
        answer_correct=answer_correct,
        failure_reason=failure_reason,
        first_hit_rank=first_hit_rank,
        answer_correctness_score=ac,
        faithfulness_score=faith,
    )


def _make_sample(sample_id: int, query: str, tags: list[str], difficulty: str, doc_type: str):
    return SimpleNamespace(
        id=sample_id,
        query=query,
        tags=tags,
        difficulty=difficulty,
        metadata_filters={'doc_type': doc_type},
    )


def _build_compare_session():
    metrics_a = {
        'overview': {
            'recall@5': 0.40,
            'mrr': 0.60,
            'avg_answer_correctness': 0.55,
            'avg_faithfulness': 0.70,
            'pass_rate': 0.33,
            'avg_context_precision': 0.50,
            'avg_context_recall': 0.45,
        }
    }
    metrics_b = {
        'overview': {
            'recall@5': 0.70,
            'mrr': 0.50,
            'avg_answer_correctness': 0.65,
            'avg_faithfulness': 0.75,
            'pass_rate': 0.66,
            'avg_context_precision': 0.61,
            'avg_context_recall': 0.60,
        }
    }

    rows_a = [
        _make_row(1, False, 'no_recall', None, 0.10, 0.40),
        _make_row(2, True, None, 1, 0.90, 0.95),
        _make_row(3, False, 'incomplete_answer', 3, 0.30, 0.80),
    ]
    rows_b = [
        _make_row(1, True, None, 2, 0.85, 0.88),
        _make_row(2, False, 'hallucination', 4, 0.20, 0.30),
        _make_row(3, False, 'incomplete_answer', 2, 0.35, 0.82),
    ]

    samples = [
        _make_sample(1, 'Q1', ['billing'], 'easy', 'requirement'),
        _make_sample(2, 'Q2', ['permission'], 'hard', 'requirement'),
        _make_sample(3, 'Q3', ['billing'], 'medium', 'test_case'),
    ]

    return FakeSession([
        FakeQuery(first_result=_make_run(101, metrics_a)),
        FakeQuery(first_result=_make_run(102, metrics_b)),
        FakeQuery(all_result=rows_a),
        FakeQuery(all_result=rows_b),
        FakeQuery(all_result=samples),
    ])


def test_compare_runs_basic_diff_structure():
    db = _build_compare_session()
    result = compare_runs(db=db, run_a_id=101, run_b_id=102, user_id=1)

    assert result['run_a']['id'] == 101
    assert result['run_b']['id'] == 102
    assert 'metric_diff' in result
    assert 'summary' in result
    assert 'by_tag_diff' in result
    assert 'by_difficulty_diff' in result
    assert 'by_failure_reason_diff' in result


def test_compare_runs_improved_and_regressed_classification():
    db = _build_compare_session()
    result = compare_runs(db=db, run_a_id=101, run_b_id=102, user_id=1)

    assert result['improved_sample_ids'] == [1]
    assert result['regressed_sample_ids'] == [2]

    summary = result['summary']
    assert summary['improved_samples'] == 1
    assert summary['regressed_samples'] == 1
    assert summary['unchanged_correct'] == 0
    assert summary['unchanged_incorrect'] == 1


def test_metric_diff_calculation_is_correct():
    diff = _calc_metric_diff(
        {
            'overview': {
                'recall@5': 0.40,
                'mrr': 0.60,
                'avg_answer_correctness': 0.55,
                'avg_faithfulness': 0.70,
                'pass_rate': 0.33,
                'avg_context_precision': 0.50,
                'avg_context_recall': 0.45,
            }
        },
        {
            'overview': {
                'recall@5': 0.70,
                'mrr': 0.50,
                'avg_answer_correctness': 0.65,
                'avg_faithfulness': 0.75,
                'pass_rate': 0.66,
                'avg_context_precision': 0.61,
                'avg_context_recall': 0.60,
            }
        },
    )

    assert isclose(diff['recall@5'], 0.30, rel_tol=0.0, abs_tol=1e-9)
    assert isclose(diff['mrr'], -0.10, rel_tol=0.0, abs_tol=1e-9)
    assert isclose(diff['answer_correctness'], 0.10, rel_tol=0.0, abs_tol=1e-9)
    assert isclose(diff['faithfulness'], 0.05, rel_tol=0.0, abs_tol=1e-9)
    assert isclose(diff['pass_rate'], 0.33, rel_tol=0.0, abs_tol=1e-9)
