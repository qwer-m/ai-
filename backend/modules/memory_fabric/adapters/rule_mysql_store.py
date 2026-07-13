from __future__ import annotations

from typing import Any

from core.db.models import RagDataset, RagDatasetSample
from modules.memory_fabric.contracts.memory_context import MemoryContext


class MySQLRuleStore:
    """L3 rule-memory read adapter (Stage 2 read path only)."""

    def read(self, query: dict[str, Any], ctx: MemoryContext) -> Any:
        kind = str(query.get("kind") or "").strip().lower()
        db = query.get("db")
        if db is None:
            return None

        if kind == "anomaly_pool_samples":
            user_id = int(query.get("user_id") or int(ctx.user_id))
            max_samples = max(1, int(query.get("limit") or 500))
            dataset_rows = (
                db.query(RagDataset.id, RagDataset.type)
                .filter(
                    RagDataset.user_id == user_id,
                    RagDataset.type.in_(["challenge", "regression"]),
                )
                .all()
            )
            dataset_map = {int(row.id): str(row.type or "").strip().lower() for row in dataset_rows}
            if not dataset_map:
                return {"dataset_map": {}, "samples": []}
            sample_rows = (
                db.query(RagDatasetSample)
                .filter(
                    RagDatasetSample.dataset_id.in_(list(dataset_map.keys())),
                    RagDatasetSample.enabled.is_(True),
                )
                .order_by(RagDatasetSample.updated_at.desc(), RagDatasetSample.id.desc())
                .limit(max_samples)
                .all()
            )
            return {"dataset_map": dataset_map, "samples": sample_rows}
        if kind == "feedback_control_state":
            # Stage 2 keeps legacy builder logic and does not force a new table schema.
            return {}
        return None

    def write(self, rule_state: dict[str, Any], ctx: MemoryContext) -> None:
        # Stage 2: write path is intentionally deferred.
        return None

