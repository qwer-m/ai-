from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import ProgrammingError

from core.db.models import RagDatasetSample, RagEvalCandidate, RagEvalRun, RagEvalSampleResult
from modules.rag_eval import rag_eval_candidate_service as svc


class _FakeQuery:
    """中文注释：最小 Query 桩，只覆盖当前测试用到的方法。"""

    def __init__(self, first_result=None, all_result=None):
        self._first = first_result
        self._all = list(all_result or [])

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def count(self):
        return len(self._all)

    def first(self):
        return self._first

    def all(self):
        return list(self._all)


class _FakeDB:
    """中文注释：按模型类型返回预置 Query，并记录新增对象。"""

    def __init__(self, query_map):
        self.query_map = query_map
        self.added = []

    def query(self, model):
        key = model.__name__
        if key not in self.query_map:
            return _FakeQuery()
        value = self.query_map[key]
        if isinstance(value, list):
            return _FakeQuery(all_result=value)
        return value

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        return None

    def refresh(self, obj):
        if getattr(obj, 'id', None) is None:
            obj.id = 999 + len(self.added)


class _FlakyMissingTableQuery(_FakeQuery):
    def __init__(self):
        super().__init__(all_result=[])
        self.count_calls = 0

    def count(self):
        self.count_calls += 1
        if self.count_calls == 1:
            raise ProgrammingError(
                "SELECT count(*) FROM rag_eval_candidates",
                {},
                Exception("(1146, \"Table 'ai_test_platform.rag_eval_candidates' doesn't exist\")"),
            )
        return 0


def test_suggested_dataset_type_rule():
    assert svc.infer_suggested_dataset_type('hallucination') == 'challenge'
    assert svc.infer_suggested_dataset_type('wrong_version') == 'challenge'
    assert svc.infer_suggested_dataset_type('incomplete_answer') == 'regression'
    assert svc.infer_suggested_dataset_type('low_rank') == 'regression'


def test_generate_candidates_from_eval_run():
    run = SimpleNamespace(id=11, user_id=1)
    row = SimpleNamespace(
        id=21,
        sample_id=31,
        reranked_chunks=[{'chunk_id': 'c1'}],
        retrieved_chunks=[],
        answer_text='bad answer',
        failure_reason='hallucination',
        answer_correct=False,
        answer_correctness_score=0.2,
        faithfulness_score=0.3,
        context_precision=0.1,
        context_recall=0.1,
        detail_json={'sample': {'query': 'q1'}},
    )
    sample = SimpleNamespace(
        id=31,
        query='线上真实query',
        gold_docs=[{'id': 'doc-1'}],
        gold_chunks=['chunk-1'],
        answer_points=['point-1'],
    )

    db = _FakeDB(
        {
            RagEvalRun.__name__: _FakeQuery(first_result=run),
            RagEvalSampleResult.__name__: _FakeQuery(all_result=[row]),
            RagDatasetSample.__name__: _FakeQuery(all_result=[sample]),
            RagEvalCandidate.__name__: _FakeQuery(all_result=[]),
        }
    )

    result = svc.generate_candidates_from_run(
        db=db,
        user_id=1,
        run_id=11,
        filters={'answer_correct_false': True},
        target_dataset_type=None,
    )

    assert result['created_count'] == 1
    assert result['skipped_existing'] == 0
    assert len(db.added) == 1
    candidate = db.added[0]
    assert candidate.source_type == 'eval_result'
    assert candidate.source_id == 21
    assert candidate.suggested_dataset_type == 'challenge'


def test_approve_and_reject_flow():
    candidate = SimpleNamespace(
        id=100,
        user_id=1,
        suggested_dataset_type='challenge',
        status='pending',
        query='query-a',
    )
    dataset = SimpleNamespace(id=200)

    db = _FakeDB({RagDatasetSample.__name__: _FakeQuery(first_result=None)})

    origin_get = svc._get_owned_candidate
    origin_ensure = svc._ensure_target_dataset
    origin_build = svc.build_candidate_draft
    try:
        svc._get_owned_candidate = lambda db, user_id, candidate_id: candidate
        svc._ensure_target_dataset = lambda db, user_id, target_dataset_type: dataset
        svc.build_candidate_draft = lambda **kwargs: (
            candidate,
            {
                'query': 'query-a',
                'gold_docs': [],
                'gold_chunks': [],
                'gold_answer': '',
                'answer_points': [],
                'tags': ['failure:hallucination'],
                'difficulty': 'medium',
                'metadata_filters': {},
                'expected_doc_version': None,
            },
        )

        approve_result = svc.approve_candidate(
            db=db,
            user_id=1,
            candidate_id=100,
            target_dataset_type='challenge',
            draft_payload=None,
        )
        assert approve_result['success'] is True
        assert approve_result['created_new_sample'] is True
        assert candidate.status == 'approved'

        reject_row = svc.reject_candidate(db=db, user_id=1, candidate_id=100, notes='manual reject')
        assert reject_row.status == 'rejected'
    finally:
        svc._get_owned_candidate = origin_get
        svc._ensure_target_dataset = origin_ensure
        svc.build_candidate_draft = origin_build


def test_list_candidates_missing_table_will_retry_after_auto_create():
    query = _FlakyMissingTableQuery()
    db = _FakeDB({RagEvalCandidate.__name__: query})

    origin_ensure = svc._ensure_candidate_table
    try:
        svc._ensure_candidate_table = lambda _db: True
        items, total = svc.list_candidates(db=db, user_id=1, page=1, page_size=20)
    finally:
        svc._ensure_candidate_table = origin_ensure

    assert items == []
    assert total == 0
    assert query.count_calls >= 2
