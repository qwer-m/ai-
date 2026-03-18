"""snapshot readiness 统一判定工具。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from core.models import ProjectContextSnapshot
from modules.knowledge_base_components.snapshot_builder import SNAPSHOT_CONFIG, decide_rebuild_mode


def _is_success_snapshot(snapshot: Optional[ProjectContextSnapshot]) -> bool:
    """中文注释：判断是否为成功快照状态。"""
    if not snapshot:
        return False
    return (snapshot.build_status or "").strip().lower() == "success"


def evaluate_snapshot_readiness(
    snapshot: Optional[ProjectContextSnapshot],
    current_corpus_hash: str,
    current_doc_count: int,
    changed_doc_ids: list[str],
) -> dict:
    """
    中文注释：统一评估 snapshot readiness，供状态接口与生成链路复用。

    返回字段：
    - snapshot_status: not_exists/pending/building/success/failed/stale
    - is_ready: 是否已准备好可直接复用
    - usable_for_generation: 是否可直接用于生成链路
    - needs_rebuild: 是否需要重建
    - readiness_reason: 判定原因
    - prewarm_status: not_started/in_progress/ready/failed/idle_needs_rebuild
    - dirty_pending_update: pending/building 且仍存在脏更新
    """
    if not snapshot:
        return {
            "snapshot_status": "not_exists",
            "is_ready": False,
            "usable_for_generation": False,
            "needs_rebuild": True,
            "readiness_reason": "snapshot_missing",
            "prewarm_status": "not_started",
            "dirty_pending_update": False,
            "build_status": "not_exists",
            "current_corpus_hash": current_corpus_hash or "",
            "snapshot_corpus_hash": "",
        }

    build_status = (snapshot.build_status or "pending").strip().lower()
    snapshot_hash = str(snapshot.corpus_hash or "")
    hash_matched = bool(snapshot_hash and current_corpus_hash and snapshot_hash == current_corpus_hash)
    has_snapshot_text = bool((snapshot.snapshot_text or "").strip())
    recommended_mode = decide_rebuild_mode(snapshot, changed_doc_ids, current_doc_count, False)

    # 中文注释：基于阈值条件判断是否 stale。
    stale_by_full_rebuild_hours = bool(
        snapshot.last_full_built_at
        and datetime.utcnow() - snapshot.last_full_built_at
        >= timedelta(hours=SNAPSHOT_CONFIG.full_rebuild_hours)
    )
    stale_by_incremental_limit = int(snapshot.incremental_merge_count or 0) >= int(
        SNAPSHOT_CONFIG.max_incremental_merges
    )

    needs_rebuild = (
        build_status in {"failed", "pending"}
        or not _is_success_snapshot(snapshot)
        or not has_snapshot_text
        or not hash_matched
        or recommended_mode != "reuse"
        or stale_by_full_rebuild_hours
        or stale_by_incremental_limit
    )

    if build_status == "failed":
        snapshot_status = "failed"
        readiness_reason = "snapshot_failed"
    elif build_status == "pending":
        # 中文注释：pending 且已有旧快照文本时，表达为 building（正在更新）。
        snapshot_status = "building" if has_snapshot_text else "pending"
        readiness_reason = "snapshot_building" if has_snapshot_text else "snapshot_pending"
    elif _is_success_snapshot(snapshot) and not needs_rebuild:
        snapshot_status = "success"
        readiness_reason = "snapshot_success_and_hash_matched"
    else:
        snapshot_status = "stale"
        if not hash_matched:
            readiness_reason = "corpus_hash_mismatch"
        elif stale_by_full_rebuild_hours or stale_by_incremental_limit:
            readiness_reason = "stale_due_to_rebuild_threshold"
        else:
            readiness_reason = "snapshot_needs_rebuild"

    is_ready = bool(snapshot_status == "success")
    usable_for_generation = bool(is_ready and has_snapshot_text)

    if snapshot_status in {"pending", "building"}:
        prewarm_status = "in_progress"
    elif snapshot_status == "failed":
        prewarm_status = "failed"
    elif snapshot_status == "success":
        prewarm_status = "ready"
    elif snapshot_status == "stale":
        prewarm_status = "idle_needs_rebuild"
    else:
        prewarm_status = "not_started"

    return {
        "snapshot_status": snapshot_status,
        "is_ready": is_ready,
        "usable_for_generation": usable_for_generation,
        "needs_rebuild": bool(needs_rebuild),
        "readiness_reason": readiness_reason,
        "prewarm_status": prewarm_status,
        "dirty_pending_update": bool(snapshot_status in {"pending", "building"} and needs_rebuild),
        "build_status": build_status,
        "current_corpus_hash": current_corpus_hash or "",
        "snapshot_corpus_hash": snapshot_hash,
        "rebuild_mode_recommendation": recommended_mode,
    }
