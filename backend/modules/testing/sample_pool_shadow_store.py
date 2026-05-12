"""Shadow write path for priority sample pool — real DB tables alongside KnowledgeDocument JSON.

This module writes to the new tables (sample_pool_items, learned_patterns,
quality_feedback_events) in parallel with the existing JSON artifact storage.
The JSON path remains the authoritative read source until the cutover.

Usage:
  - shadow_write_samples / shadow_write_patterns / shadow_write_event:
      Called from priority_sample_pool_store after every JSON write.
  - shadow_read_consistency_check:
      Compares new-table counts against JSON payload for monitoring.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from core.db.models import Base, LearnedPattern, QualityFeedbackEvent, SamplePoolItem

logger = logging.getLogger(__name__)

_MAX_INSERT_BATCH = 200


def ensure_shadow_tables(db: Session) -> bool:
    """Create shadow tables on first use when migrations have not run yet."""
    try:
        bind = db.get_bind()
        inspector = inspect(bind)
        existing = set(inspector.get_table_names())
        required_tables = [
            SamplePoolItem.__table__,
            LearnedPattern.__table__,
            QualityFeedbackEvent.__table__,
        ]
        missing = [table for table in required_tables if table.name not in existing]
        if missing:
            Base.metadata.create_all(bind=bind, tables=missing, checkfirst=True)
        return True
    except Exception:
        logger.warning("sample_pool_shadow_table_ensure_failed", exc_info=True)
        return False


# ── SamplePoolItem ──────────────────────────────────────────────────

def _sample_to_row(
    sample: dict[str, Any],
    project_id: int,
    user_id: int | None,
) -> SamplePoolItem:
    import json as _json

    tags = sample.get("tags")
    if isinstance(tags, list):
        tags_json = _json.dumps(tags, ensure_ascii=False)
    else:
        tags_json = str(tags) if tags else None

    # Collect miscellaneous fields that don't have dedicated columns
    known_keys = {
        "sample_id", "sampleId",
        "source_type", "sourceType", "source",
        "source_id", "sourceId",
        "source_case_id", "sourceCaseId",
        "sample_kind", "sampleKind", "signal_type", "signalType",
        "pattern_usage", "patternUsage",
        "case_id", "caseId",
        "title",
        "user_comment", "userComment",
        "expected_priority", "expectedPriority",
        "reason_category", "reasonCategory",
        "pattern_category", "patternCategory",
        "pattern_summary", "patternSummary",
        "pattern_canonical",
        "pattern_cluster_key", "pattern_cluster_key",
        "confidence", "pattern_confidence", "patternConfidence",
        "pattern_weight", "patternWeight",
        "pattern_quality_score", "patternQualityScore",
        "status", "deleted_at", "deletedAt", "delete_reason", "deleteReason",
        "learning_status", "learningStatus",
        "learning_confirmed_at", "learningConfirmedAt",
        "learning_confirmed_by", "learningConfirmedBy",
        "tags", "pattern_scope", "patternScope",
        "pattern_grain", "patternGrain",
        "pattern_source", "patternSource",
        "governance_status", "governanceStatus",
        "artifact_doc_id", "artifact_filename",
        "generation_id", "generationId",
        "project_id", "user_id",
    }
    extra = {k: v for k, v in sample.items() if k not in known_keys and v is not None}
    extra_json = _json.dumps(extra, ensure_ascii=False) if extra else None

    def _dt(raw: Any) -> datetime | None:
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except (ValueError, TypeError):
            return None

    return SamplePoolItem(
        project_id=int(project_id),
        user_id=int(user_id) if user_id is not None else None,
        sample_id=str(sample.get("sample_id") or sample.get("sampleId") or ""),
        source_type=str(sample.get("source_type") or sample.get("source") or "manual_pool_input"),
        source_id=int(sample.get("source_id") or sample.get("sourceId") or 0) if (sample.get("source_id") or sample.get("sourceId")) is not None else None,
        source_case_id=str(sample.get("source_case_id") or sample.get("sourceCaseId") or "") or None,
        sample_kind=str(sample.get("sample_kind") or sample.get("signal_type") or "negative"),
        pattern_usage=str(sample.get("pattern_usage") or sample.get("patternUsage") or "") or None,
        case_id=str(sample.get("case_id") or sample.get("caseId") or "") or None,
        title=str(sample.get("title") or "")[:256] or None,
        user_comment=str(sample.get("user_comment") or sample.get("userComment") or "") or None,
        expected_priority=str(sample.get("expected_priority") or sample.get("expectedPriority") or "") or None,
        reason_category=str(sample.get("reason_category") or sample.get("reasonCategory") or "") or None,
        pattern_category=str(sample.get("pattern_category") or sample.get("patternCategory") or "") or None,
        pattern_summary=str(sample.get("pattern_summary") or sample.get("patternSummary") or "")[:256] or None,
        pattern_canonical=str(sample.get("pattern_canonical") or "")[:256] or None,
        pattern_cluster_key=str(sample.get("pattern_cluster_key") or "")[:128] or None,
        confidence=float(sample.get("confidence") or sample.get("pattern_confidence") or 0.5),
        pattern_weight=float(sample.get("pattern_weight") or 0.6),
        pattern_quality_score=float(sample.get("pattern_quality_score") or 0.0) if sample.get("pattern_quality_score") is not None else None,
        status=str(sample.get("status") or "active"),
        deleted_at=_dt(sample.get("deleted_at") or sample.get("deletedAt")),
        delete_reason=str(sample.get("delete_reason") or sample.get("deleteReason") or "") or None,
        learning_status=str(sample.get("learning_status") or sample.get("learningStatus") or "") or None,
        learning_confirmed_at=_dt(sample.get("learning_confirmed_at") or sample.get("learningConfirmedAt")),
        learning_confirmed_by=int(sample.get("learning_confirmed_by") or sample.get("learningConfirmedBy") or 0) if (sample.get("learning_confirmed_by") or sample.get("learningConfirmedBy")) is not None else None,
        tags_json=tags_json,
        extra_json=extra_json,
    )


def shadow_write_samples(
    db: Session,
    project_id: int,
    user_id: int | None,
    samples: list[dict[str, Any]],
) -> None:
    """Replace all sample_pool_items rows for this project with the current samples."""
    if not ensure_shadow_tables(db):
        return
    try:
        query = db.query(SamplePoolItem).filter(
            SamplePoolItem.project_id == int(project_id)
        )
        if user_id is not None:
            query = query.filter(SamplePoolItem.user_id == int(user_id))
        query.delete(synchronize_session=False)
        db.flush()
    except Exception:
        db.rollback()
        logger.warning("shadow_write_samples_delete_failed project_id=%s", project_id, exc_info=True)
        return

    rows = [_sample_to_row(s, project_id, user_id) for s in samples]
    for i in range(0, len(rows), _MAX_INSERT_BATCH):
        batch = rows[i : i + _MAX_INSERT_BATCH]
        try:
            db.add_all(batch)
            db.flush()
        except Exception:
            db.rollback()
            logger.warning(
                "shadow_write_samples_insert_failed project_id=%s batch=%d",
                project_id, i // _MAX_INSERT_BATCH,
                exc_info=True,
            )
            return
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("shadow_write_samples_commit_failed project_id=%s", project_id, exc_info=True)


# ── LearnedPattern ──────────────────────────────────────────────────

def _pattern_to_row(pattern: dict[str, Any], project_id: int) -> LearnedPattern:
    import json as _json

    top_sources = pattern.get("top_source_types")
    top_sources_json = _json.dumps(top_sources, ensure_ascii=False) if isinstance(top_sources, list) else None

    active_ids = pattern.get("active_sample_ids")
    active_ids_json = _json.dumps(active_ids, ensure_ascii=False) if isinstance(active_ids, list) else None
    raw_pattern_id = str(pattern.get("pattern_id") or "")

    return LearnedPattern(
        project_id=int(project_id),
        pattern_id=f"p{int(project_id)}:{raw_pattern_id}"[:256],
        cluster_key=str(pattern.get("cluster_key") or ""),
        signal_type=str(pattern.get("signal_type") or "negative"),
        pattern_usage=str(pattern.get("pattern_usage") or "") or None,
        pattern_summary=str(pattern.get("pattern_summary") or "")[:256] or None,
        pattern_canonical=str(pattern.get("pattern_canonical") or "")[:256] or None,
        pattern_category=str(pattern.get("pattern_category") or "") or None,
        reason_category=str(pattern.get("reason_category") or "") or None,
        pattern_scope=str(pattern.get("pattern_scope") or "") or None,
        pattern_grain=str(pattern.get("pattern_grain") or "") or None,
        sample_count=int(pattern.get("sample_count") or 0),
        avg_confidence=float(pattern.get("avg_confidence") or 0.0) if pattern.get("avg_confidence") is not None else None,
        avg_weight=float(pattern.get("avg_weight") or 0.0) if pattern.get("avg_weight") is not None else None,
        top_weight=float(pattern.get("top_weight") or 0.0) if pattern.get("top_weight") is not None else None,
        top_source_types_json=top_sources_json,
        active_sample_ids_json=active_ids_json,
        governance_status=str(pattern.get("governance_status") or "active"),
    )


def shadow_write_patterns(
    db: Session,
    project_id: int,
    patterns: list[dict[str, Any]],
) -> None:
    """Replace all learned_patterns rows for this project with the current patterns."""
    if not ensure_shadow_tables(db):
        return
    try:
        db.query(LearnedPattern).filter(
            LearnedPattern.project_id == int(project_id)
        ).delete(synchronize_session=False)
        db.flush()
    except Exception:
        db.rollback()
        logger.warning("shadow_write_patterns_delete_failed project_id=%s", project_id, exc_info=True)
        return

    rows = [_pattern_to_row(p, project_id) for p in patterns]
    for i in range(0, len(rows), _MAX_INSERT_BATCH):
        batch = rows[i : i + _MAX_INSERT_BATCH]
        try:
            db.add_all(batch)
            db.flush()
        except Exception:
            db.rollback()
            logger.warning(
                "shadow_write_patterns_insert_failed project_id=%s batch=%d",
                project_id, i // _MAX_INSERT_BATCH,
                exc_info=True,
            )
            return
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("shadow_write_patterns_commit_failed project_id=%s", project_id, exc_info=True)


# ── QualityFeedbackEvent ────────────────────────────────────────────

def shadow_write_event(
    db: Session,
    project_id: int,
    user_id: int | None,
    event_type: str,
    event_payload: dict[str, Any] | None = None,
) -> None:
    """Append a row to quality_feedback_events."""
    if not ensure_shadow_tables(db):
        return
    try:
        row = QualityFeedbackEvent(
            project_id=int(project_id),
            user_id=int(user_id) if user_id is not None else None,
            event_type=str(event_type)[:64],
            event_payload_json=json.dumps(event_payload or {}, ensure_ascii=False),
        )
        db.add(row)
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "shadow_write_event_failed project_id=%s event_type=%s",
            project_id, event_type,
            exc_info=True,
        )


# ── Shadow read / consistency ──────────────────────────────────────

def shadow_read_consistency_check(
    db: Session,
    project_id: int,
    json_samples: list[dict[str, Any]] | None = None,
    json_patterns: list[dict[str, Any]] | None = None,
    json_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare counts between new tables and JSON payload for the given project.

    Returns a dict with table_name -> {json_count, table_count, ok}.
    Intended for monitoring / cutover readiness checks.
    """
    result: dict[str, Any] = {}
    tables_ready = ensure_shadow_tables(db)
    result["tables_ready"] = tables_ready
    # Samples
    try:
        table_sample_count = (
            db.query(SamplePoolItem)
            .filter(SamplePoolItem.project_id == int(project_id))
            .count()
        )
    except Exception:
        table_sample_count = -1
    json_sample_count = len(json_samples) if isinstance(json_samples, list) else -1
    result["sample_pool_items"] = {
        "json_count": json_sample_count,
        "table_count": table_sample_count,
        "ok": json_sample_count >= 0 and table_sample_count >= 0 and table_sample_count == json_sample_count,
    }

    # Patterns
    try:
        table_pattern_count = (
            db.query(LearnedPattern)
            .filter(LearnedPattern.project_id == int(project_id))
            .count()
        )
    except Exception:
        table_pattern_count = -1
    json_pattern_count = len(json_patterns) if isinstance(json_patterns, list) else -1
    result["learned_patterns"] = {
        "json_count": json_pattern_count,
        "table_count": table_pattern_count,
        "ok": json_pattern_count >= 0 and table_pattern_count >= 0 and table_pattern_count == json_pattern_count,
    }

    # Events
    try:
        table_event_count = (
            db.query(QualityFeedbackEvent)
            .filter(QualityFeedbackEvent.project_id == int(project_id))
            .count()
        )
    except Exception:
        table_event_count = -1
    json_event_count = len(json_events) if isinstance(json_events, list) else -1
    result["quality_feedback_events"] = {
        "json_count": json_event_count,
        "table_count": table_event_count,
        "ok": json_event_count >= 0 and table_event_count >= 0 and table_event_count == json_event_count,
    }

    return result
