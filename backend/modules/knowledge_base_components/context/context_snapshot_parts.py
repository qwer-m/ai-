"""Shared helpers for context snapshot orchestration."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument, ProjectContextSnapshot
from modules.domain.stage25_switches import STAGE25_SWITCHES

logger = logging.getLogger(__name__)

_SNAPSHOT_STAGE25_COLUMNS: tuple[tuple[str, str], ...] = (
    ("snapshot_version", "INT NOT NULL DEFAULT 0"),
    ("snapshot_fingerprint", "VARCHAR(64) NULL"),
    ("last_build_latency_ms", "FLOAT NULL"),
)


def _is_unknown_snapshot_column_error(err: Exception) -> bool:
    err_text = str(err or "").lower()
    return "unknown column" in err_text and "project_context_snapshots" in err_text


def _try_repair_snapshot_stage25_schema(db: Session) -> bool:
    """Best-effort repair for stage 2.5 snapshot columns on MySQL."""
    bind = getattr(db, "bind", None)
    if not bind or getattr(bind.dialect, "name", "") != "mysql":
        return False

    changed = False
    try:
        for col_name, col_ddl in _SNAPSHOT_STAGE25_COLUMNS:
            exists = db.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'project_context_snapshots' "
                    "AND COLUMN_NAME = :column_name"
                ),
                {"column_name": col_name},
            ).scalar()
            if int(exists or 0) == 0:
                db.execute(
                    text(
                        f"ALTER TABLE project_context_snapshots "
                        f"ADD COLUMN {col_name} {col_ddl}"
                    )
                )
                changed = True

        idx_exists = db.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'project_context_snapshots' "
                "AND INDEX_NAME = 'idx_project_context_snapshots_fingerprint'"
            )
        ).scalar()
        if int(idx_exists or 0) == 0:
            db.execute(
                text(
                    "CREATE INDEX idx_project_context_snapshots_fingerprint "
                    "ON project_context_snapshots (snapshot_fingerprint)"
                )
            )
            changed = True

        if changed:
            db.commit()
            logger.warning("project_context_snapshots schema auto-repaired for stage2.5")
        return True
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("snapshot schema auto-repair failed: %s", e)
        return False


def _query_snapshot_row(db: Session, project_id: int) -> tuple[Optional[ProjectContextSnapshot], bool]:
    """Return the snapshot row and whether the schema is compatible."""
    try:
        snapshot = (
            db.query(ProjectContextSnapshot)
            .filter(ProjectContextSnapshot.project_id == project_id)
            .first()
        )
        return snapshot, True
    except Exception as e:
        if not _is_unknown_snapshot_column_error(e):
            raise
        try:
            db.rollback()
        except Exception:
            pass
        if _try_repair_snapshot_stage25_schema(db):
            try:
                snapshot = (
                    db.query(ProjectContextSnapshot)
                    .filter(ProjectContextSnapshot.project_id == project_id)
                    .first()
                )
                return snapshot, True
            except Exception as retry_err:
                if not _is_unknown_snapshot_column_error(retry_err):
                    raise
                logger.warning(
                    "snapshot query still incompatible after repair: %s",
                    retry_err,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
                return None, False
        logger.warning("snapshot schema incompatible and auto-repair unavailable")
        return None, False


def _build_source_breakdown(corpus: list[dict], request_user_id: Optional[int]) -> dict:
    """Build source-level observability stats."""
    doc_type_counts: dict[str, int] = {}
    owner_user_counts: dict[str, int] = {}
    non_requirement_examples: list[str] = []
    requirement_like_types = {"requirement", "product_requirement", "incomplete"}
    requirement_like_count = 0
    owned_by_request_user_count = 0

    for item in corpus:
        doc_type = str(item.get("doc_type") or "unknown")
        owner_user_id = item.get("owner_user_id")
        owner_key = "none" if owner_user_id is None else str(owner_user_id)

        doc_type_counts[doc_type] = int(doc_type_counts.get(doc_type, 0)) + 1
        owner_user_counts[owner_key] = int(owner_user_counts.get(owner_key, 0)) + 1

        if doc_type in requirement_like_types:
            requirement_like_count += 1
        else:
            if len(non_requirement_examples) < 8:
                non_requirement_examples.append(
                    f"{item.get('doc_id')}:{item.get('filename')}:{doc_type}"
                )

        if request_user_id is not None and owner_user_id == request_user_id:
            owned_by_request_user_count += 1

    return {
        "source_doc_type_counts": doc_type_counts,
        "source_owner_user_counts": owner_user_counts,
        "source_requirement_like_doc_count": requirement_like_count,
        "source_non_requirement_doc_count": max(0, len(corpus) - requirement_like_count),
        "source_non_requirement_examples": non_requirement_examples,
        "source_request_user_id": request_user_id,
        "source_request_user_owned_doc_count": owned_by_request_user_count,
        "source_request_user_non_owned_doc_count": max(0, len(corpus) - owned_by_request_user_count),
    }


def _save_snapshot_success(
    snapshot: ProjectContextSnapshot,
    snapshot_text: str,
    corpus_hash: str,
    current_fingerprints: dict[str, str],
    source_doc_count: int,
    rebuild_reason: str,
    full_rebuild: bool,
    build_latency_ms: Optional[float],
    db: Session,
) -> None:
    """Persist a successful build outcome."""
    old_text = str(snapshot.snapshot_text or "")
    new_text = str(snapshot_text or "")
    content_changed = old_text != new_text

    snapshot.snapshot_text = snapshot_text
    snapshot.corpus_hash = corpus_hash
    snapshot.source_doc_count = source_doc_count
    snapshot.source_fingerprints = json.dumps(current_fingerprints, ensure_ascii=False)
    snapshot.build_status = "success"
    snapshot.rebuild_reason = rebuild_reason
    if STAGE25_SWITCHES.snapshot_versioning_enabled:
        current_version = int(snapshot.snapshot_version or 0)
        if content_changed or current_version <= 0:
            snapshot.snapshot_version = current_version + 1
        else:
            snapshot.snapshot_version = current_version
        snapshot.snapshot_fingerprint = hashlib.sha256(new_text.encode("utf-8")).hexdigest() if new_text else ""
        if build_latency_ms is not None:
            snapshot.last_build_latency_ms = float(build_latency_ms)
    snapshot.last_built_at = datetime.utcnow()
    snapshot.last_used_at = datetime.utcnow()
    if full_rebuild:
        snapshot.last_full_built_at = datetime.utcnow()
        snapshot.incremental_merge_count = 0
    else:
        snapshot.incremental_merge_count = int(snapshot.incremental_merge_count or 0) + 1
    db.commit()
