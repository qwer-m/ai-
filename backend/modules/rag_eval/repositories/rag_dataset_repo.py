from __future__ import annotations

from sqlalchemy.orm import Session

from core.models import RagDataset, RagDatasetSample


def list_datasets(db: Session, user_id: int) -> list[RagDataset]:
    """查询当前用户的数据集。"""
    return (
        db.query(RagDataset)
        .filter(RagDataset.user_id == user_id)
        .order_by(RagDataset.updated_at.desc(), RagDataset.id.desc())
        .all()
    )


def get_dataset(db: Session, dataset_id: int, user_id: int) -> RagDataset | None:
    """按 ID 查询数据集。"""
    return (
        db.query(RagDataset)
        .filter(RagDataset.id == dataset_id, RagDataset.user_id == user_id)
        .first()
    )


def list_samples(
    db: Session,
    dataset_id: int,
    *,
    tags: list[str] | None = None,
    difficulty: str | None = None,
    enabled_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RagDatasetSample], int]:
    """按筛选条件分页查询样本。"""
    q = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset_id)
    if enabled_only:
        q = q.filter(RagDatasetSample.enabled.is_(True))
    if difficulty and difficulty != "all":
        q = q.filter(RagDatasetSample.difficulty == difficulty)
    if tags:
        # 中文注释：MySQL JSON contains 条件写法，逐个标签做包含匹配。
        for tag in tags:
            q = q.filter(RagDatasetSample.tags.contains([str(tag)]))

    total = q.count()
    items = (
        q.order_by(RagDatasetSample.id.asc())
        .offset(max(0, page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total

