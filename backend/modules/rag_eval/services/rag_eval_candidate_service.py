from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from core.db.models import RagDataset, RagDatasetSample, RagEvalCandidate, RagEvalRun, RagEvalSampleResult

# 中文注释：失败类型到目标数据集的推荐映射。
_CHALLENGE_FAILURE_REASONS = {"hallucination", "wrong_version", "no_recall", "context_noise"}
_REGRESSION_FAILURE_REASONS = {"incomplete_answer", "low_rank", "incorrect_answer"}


def _is_missing_candidates_table_error(error: Exception) -> bool:
    message = str(error or "").lower()
    return "rag_eval_candidates" in message and ("doesn't exist" in message or "no such table" in message)


def _ensure_candidate_table(db: Session) -> bool:
    """
    Lazily create rag_eval_candidates table for environments that skipped migration/init.
    Returns True only when the table creation action is attempted successfully.
    """
    try:
        bind = db.get_bind()
    except Exception:
        return False
    if bind is None:
        return False

    try:
        table_name = str(RagEvalCandidate.__tablename__)
        if inspect(bind).has_table(table_name):
            return False
        RagEvalCandidate.__table__.create(bind=bind, checkfirst=True)
        return True
    except Exception:
        return False


def infer_suggested_dataset_type(failure_reason: str | None) -> str:
    """根据失败类型推荐候选数据集类型。"""
    reason = (failure_reason or "").strip().lower()
    if reason in _CHALLENGE_FAILURE_REASONS:
        return "challenge"
    if reason in _REGRESSION_FAILURE_REASONS:
        return "regression"
    return "challenge"


def infer_candidate_tags(query: str, failure_reason: str | None, dataset_type: str) -> list[str]:
    """根据 query 与失败归因推断候选标签。"""
    tags = {f"dataset:{dataset_type}", f"failure:{(failure_reason or 'unknown').strip().lower()}"}
    q = (query or "").lower()
    keyword_rules = {
        "version": ["版本", "v1", "v2", "历史", "旧"],
        "permission": ["权限", "可见", "能否", "查看"],
        "field": ["字段", "参数", "列", "属性"],
        "flow": ["流程", "步骤", "审批"],
        "boundary": ["边界", "异常", "错误", "失败"],
    }
    for tag, words in keyword_rules.items():
        if any(w in q for w in words):
            tags.add(tag)
    return sorted(tags)


def build_candidate_from_eval_row(
    *,
    user_id: int,
    run_id: int,
    row: RagEvalSampleResult,
    sample: RagDatasetSample | None,
    target_dataset_type: str | None = None,
) -> RagEvalCandidate:
    """从评测结果行构建候选对象。"""
    failure_reason = (row.failure_reason or "incorrect_answer").strip().lower()
    suggested_type = (target_dataset_type or infer_suggested_dataset_type(failure_reason)).strip().lower()

    query = (sample.query if sample else "").strip()
    if not query:
        query = str((row.detail_json or {}).get("sample", {}).get("query") or "").strip()

    judge_score = {
        "answer_correctness_score": float(row.answer_correctness_score or 0.0),
        "faithfulness_score": float(row.faithfulness_score or 0.0),
        "context_precision": float(row.context_precision or 0.0),
        "context_recall": float(row.context_recall or 0.0),
    }

    return RagEvalCandidate(
        user_id=user_id,
        source_type="eval_result",
        source_id=int(row.id),
        query=query,
        retrieved_chunks=list(row.reranked_chunks or row.retrieved_chunks or []),
        answer_text=row.answer_text,
        failure_reason=failure_reason,
        judge_score_json=judge_score,
        suggested_dataset_type=suggested_type,
        status="pending",
        suggested_gold_docs=list(sample.gold_docs or []) if sample else [],
        suggested_gold_chunks=[str(x) for x in (sample.gold_chunks or [])] if sample else [],
        suggested_answer_points=list(sample.answer_points or []) if sample else [],
        notes=f"from_eval_run:{run_id}",
    )


def _get_owned_run(db: Session, user_id: int, run_id: int) -> RagEvalRun:
    run = db.query(RagEvalRun).filter(RagEvalRun.id == run_id, RagEvalRun.user_id == user_id).first()
    if not run:
        raise ValueError("Run not found")
    return run


def _get_owned_candidate(db: Session, user_id: int, candidate_id: int) -> RagEvalCandidate:
    _ensure_candidate_table(db)
    row = db.query(RagEvalCandidate).filter(RagEvalCandidate.id == candidate_id, RagEvalCandidate.user_id == user_id).first()
    if not row:
        raise ValueError("Candidate not found")
    return row


def _normalize_failure_reasons(raw: list[str] | None) -> list[str]:
    return [x.strip().lower() for x in (raw or []) if str(x).strip()]


def generate_candidates_from_run(
    *,
    db: Session,
    user_id: int,
    run_id: int,
    filters: dict[str, Any] | None = None,
    target_dataset_type: str | None = None,
) -> dict[str, Any]:
    """从指定评测运行中批量生成候选。"""
    _ensure_candidate_table(db)
    _get_owned_run(db, user_id, run_id)
    rules = dict(filters or {})

    q = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run_id)

    if bool(rules.get("answer_correct_false", True)):
        q = q.filter(RagEvalSampleResult.answer_correct.is_(False))

    reasons = _normalize_failure_reasons(rules.get("failure_reasons"))
    if reasons:
        q = q.filter(RagEvalSampleResult.failure_reason.in_(reasons))

    faithfulness_lt = rules.get("faithfulness_lt")
    if faithfulness_lt is not None:
        q = q.filter(RagEvalSampleResult.faithfulness_score < float(faithfulness_lt))

    correctness_lt = rules.get("answer_correctness_lt")
    if correctness_lt is not None:
        q = q.filter(RagEvalSampleResult.answer_correctness_score < float(correctness_lt))

    rows = q.order_by(RagEvalSampleResult.id.asc()).all()
    if not rows:
        return {"success": True, "created_count": 0, "skipped_existing": 0, "candidate_ids": []}

    sample_ids = [int(x.sample_id) for x in rows]
    sample_map = {
        int(x.id): x
        for x in db.query(RagDatasetSample).filter(RagDatasetSample.id.in_(sample_ids)).all()
    }

    existing_ids = [int(x.id) for x in rows]
    existing_source_ids = {
        int(x.source_id)
        for x in db.query(RagEvalCandidate)
        .filter(
            RagEvalCandidate.user_id == user_id,
            RagEvalCandidate.source_type == "eval_result",
            RagEvalCandidate.source_id.in_(existing_ids),
        )
        .all()
    }

    created: list[RagEvalCandidate] = []
    skipped_existing = 0
    for row in rows:
        if int(row.id) in existing_source_ids:
            skipped_existing += 1
            continue
        candidate = build_candidate_from_eval_row(
            user_id=user_id,
            run_id=run_id,
            row=row,
            sample=sample_map.get(int(row.sample_id)),
            target_dataset_type=target_dataset_type,
        )
        db.add(candidate)
        created.append(candidate)

    db.commit()
    for item in created:
        db.refresh(item)

    return {
        "success": True,
        "created_count": len(created),
        "skipped_existing": skipped_existing,
        "candidate_ids": [int(x.id) for x in created],
    }


def list_candidates(
    *,
    db: Session,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    source_type: str | None = None,
    failure_reason: str | None = None,
    suggested_dataset_type: str | None = None,
) -> tuple[list[RagEvalCandidate], int]:
    """分页查询候选列表。"""
    _ensure_candidate_table(db)

    def _build_query():
        q = db.query(RagEvalCandidate).filter(RagEvalCandidate.user_id == user_id)
        if status:
            q = q.filter(RagEvalCandidate.status == status.strip().lower())
        if source_type:
            q = q.filter(RagEvalCandidate.source_type == source_type.strip().lower())
        if failure_reason:
            q = q.filter(RagEvalCandidate.failure_reason == failure_reason.strip().lower())
        if suggested_dataset_type:
            q = q.filter(RagEvalCandidate.suggested_dataset_type == suggested_dataset_type.strip().lower())
        return q

    q = _build_query()
    try:
        total = q.count()
        items = (
            q.order_by(RagEvalCandidate.id.desc())
            .offset(max(0, page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total
    except (ProgrammingError, OperationalError) as e:
        if not _is_missing_candidates_table_error(e):
            raise
        created = _ensure_candidate_table(db)
        if not created:
            return [], 0

        q = _build_query()
        total = q.count()
        items = (
            q.order_by(RagEvalCandidate.id.desc())
            .offset(max(0, page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total


def build_candidate_draft(
    *,
    db: Session,
    user_id: int,
    candidate_id: int,
    draft_payload: dict[str, Any] | None = None,
) -> tuple[RagEvalCandidate, dict[str, Any]]:
    """为候选生成（或更新）样本草稿。"""
    candidate = _get_owned_candidate(db, user_id, candidate_id)
    draft_payload = dict(draft_payload or {})

    tags = list(draft_payload.get("tags") or infer_candidate_tags(candidate.query, candidate.failure_reason, candidate.suggested_dataset_type))
    draft = {
        "query": candidate.query,
        "gold_docs": list(draft_payload.get("gold_docs") or candidate.suggested_gold_docs or []),
        "gold_chunks": [str(x) for x in (draft_payload.get("gold_chunks") or candidate.suggested_gold_chunks or [])],
        "gold_answer": str(draft_payload.get("gold_answer") or ""),
        "answer_points": list(draft_payload.get("answer_points") or candidate.suggested_answer_points or []),
        "tags": tags,
        "difficulty": str(draft_payload.get("difficulty") or "medium"),
        "metadata_filters": dict(draft_payload.get("metadata_filters") or {}),
        "expected_doc_version": draft_payload.get("expected_doc_version"),
        "enabled": True,
    }

    candidate.suggested_gold_docs = draft["gold_docs"]
    candidate.suggested_gold_chunks = draft["gold_chunks"]
    candidate.suggested_answer_points = draft["answer_points"]
    if draft_payload.get("notes"):
        candidate.notes = str(draft_payload.get("notes"))
    db.commit()
    db.refresh(candidate)
    return candidate, draft


def _ensure_target_dataset(db: Session, user_id: int, target_dataset_type: str) -> RagDataset:
    target = target_dataset_type.strip().lower()
    if target not in {"challenge", "regression"}:
        raise ValueError("target_dataset_type must be challenge/regression")

    name = "自动回流-挑战集" if target == "challenge" else "自动回流-回归集"
    ds = db.query(RagDataset).filter(RagDataset.user_id == user_id, RagDataset.name == name, RagDataset.type == target).first()
    if ds:
        return ds

    ds = RagDataset(user_id=user_id, name=name, type=target, description="由真实查询候选回流沉淀")
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def approve_candidate(
    *,
    db: Session,
    user_id: int,
    candidate_id: int,
    target_dataset_type: str | None = None,
    draft_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """审核通过候选并落地到 challenge / regression 数据集。"""
    candidate = _get_owned_candidate(db, user_id, candidate_id)
    target_type = (target_dataset_type or candidate.suggested_dataset_type or infer_suggested_dataset_type(candidate.failure_reason)).strip().lower()
    dataset = _ensure_target_dataset(db, user_id, target_type)

    candidate, draft = build_candidate_draft(db=db, user_id=user_id, candidate_id=candidate_id, draft_payload=draft_payload)

    exists = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset.id, RagDatasetSample.query == draft["query"]).first()
    created_new_sample = False
    if exists:
        sample = exists
    else:
        sample = RagDatasetSample(
            dataset_id=dataset.id,
            query=draft["query"],
            gold_docs=draft["gold_docs"],
            gold_chunks=draft["gold_chunks"],
            gold_answer=draft["gold_answer"],
            answer_points=draft["answer_points"],
            tags=draft["tags"],
            difficulty=draft["difficulty"],
            metadata_filters=draft["metadata_filters"],
            expected_doc_version=draft["expected_doc_version"],
            enabled=True,
        )
        db.add(sample)
        db.commit()
        db.refresh(sample)
        created_new_sample = True

    candidate.status = "approved"
    candidate.suggested_dataset_type = target_type
    db.commit()
    db.refresh(candidate)

    return {
        "success": True,
        "candidate_id": int(candidate.id),
        "target_dataset_id": int(dataset.id),
        "target_sample_id": int(sample.id),
        "created_new_sample": created_new_sample,
    }


def reject_candidate(*, db: Session, user_id: int, candidate_id: int, notes: str | None = None) -> RagEvalCandidate:
    """审核拒绝候选。"""
    candidate = _get_owned_candidate(db, user_id, candidate_id)
    candidate.status = "rejected"
    if notes:
        candidate.notes = str(notes)
    db.commit()
    db.refresh(candidate)
    return candidate

