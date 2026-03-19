from __future__ import annotations

from sqlalchemy.orm import Session

from core.models import RagEvalRun, RagEvalSampleResult


def get_run(db: Session, run_id: int, user_id: int) -> RagEvalRun | None:
    """查询运行记录。"""
    return db.query(RagEvalRun).filter(RagEvalRun.id == run_id, RagEvalRun.user_id == user_id).first()


def list_run_sample_results(
    db: Session,
    run_id: int,
    *,
    page: int = 1,
    page_size: int = 20,
    tag: str | None = None,
    failure_reason: str | None = None,
    answer_correct: bool | None = None,
) -> tuple[list[RagEvalSampleResult], int]:
    """分页查询样本结果。"""
    q = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run_id)
    if tag:
        # 中文注释：detail_json 内保存 tags 列表，这里用 contains 做轻量筛选。
        q = q.filter(RagEvalSampleResult.detail_json.contains({"tags": [str(tag)]}))
    if failure_reason:
        q = q.filter(RagEvalSampleResult.failure_reason == failure_reason)
    if answer_correct is not None:
        q = q.filter(RagEvalSampleResult.answer_correct.is_(bool(answer_correct)))
    total = q.count()
    items = (
        q.order_by(RagEvalSampleResult.id.asc())
        .offset(max(0, page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total
