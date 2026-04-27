"""Persistence helpers for test-generation priority anomaly sample pool."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.testing_components.repositories.evaluation_artifact_repository import (
    EvaluationArtifactRepository,
)

PRIORITY_SAMPLE_POOL_DOC_TYPE = "test_generation_priority_sample_pool"
PRIORITY_SAMPLE_POOL_PATTERN_DOC_TYPE = "priority_sample_pattern"
_PRIORITY_PATTERN_VECTOR_DOC_PREFIX = "priority_sample_pool_patterns"
_MAX_INDEXED_PATTERN_SAMPLES = 5000
_MAX_RETRIEVAL_RESULTS = 8
_MAX_RETRIEVAL_CANDIDATES = 24
_MAX_EMBED_QUERY_CHARS = 1900
_MIN_PATTERN_SUMMARY_LEN = 8
_MAX_PATTERN_SUMMARY_LEN = 180
_MAX_PATTERN_CANONICAL_LEN = 160
_MAX_PATTERN_CLUSTER_LEN = 96
_MIN_PATTERN_WEIGHT_ADJUSTMENT = 0.25
_MAX_PATTERN_WEIGHT_ADJUSTMENT = 1.5
_VALID_SIGNAL_TYPES = {"positive", "negative"}
_VALID_PATTERN_USAGE = {"prefer", "avoid"}
_UI_LOW_VALUE_PATTERN_TOKENS = (
    "ui-only",
    "static ui",
    "static display",
    "copy check",
    "copy-only",
    "style check",
    "layout check",
    "layout-only",
    "visual only",
    "field display",
    "list sorting",
    "placeholder",
    "ui ",
    "display",
    "文案",
    "样式",
    "布局",
    "展示",
    "列表排序",
    "字段展示",
)

logger = logging.getLogger(__name__)


def build_priority_sample_pool_filename(project_id: int) -> str:
    return f"priority_sample_pool_project_{project_id}.json"


def _sample_value(sample: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in sample:
            return sample.get(key)
    return None


def _sanitize_text(raw: Any, *, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return ""
    return text[:max_len]


def _safe_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return float(default)


def _normalize_signal_type(raw: Any) -> str:
    text = _sanitize_text(raw, max_len=24).lower()
    if text in _VALID_SIGNAL_TYPES:
        return text
    if text in {"pos", "good", "gold", "success", "best_practice"}:
        return "positive"
    return "negative"


def _normalize_pattern_usage(raw: Any, *, signal_type: str) -> str:
    text = _sanitize_text(raw, max_len=24).lower()
    if text in _VALID_PATTERN_USAGE:
        return text
    if signal_type == "positive":
        return "prefer"
    return "avoid"


def _normalize_pattern_category(raw: Any) -> str:
    return _sanitize_text(raw, max_len=64).lower()


def _is_ui_low_value_pattern(*parts: Any) -> bool:
    merged = " ".join(str(part or "") for part in parts).strip().lower()
    if not merged:
        return False
    return any(token in merged for token in _UI_LOW_VALUE_PATTERN_TOKENS)


def _canonicalize_pattern_text(raw: Any) -> str:
    text = _sanitize_text(raw, max_len=_MAX_PATTERN_CANONICAL_LEN).lower()
    if not text:
        return ""
    # Remove highly specific tokens so patterns stay reusable.
    text = re.sub(r"(tc|case|rule|req)[\-_ ]?\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[`'\"“”‘’\[\]\(\)\{\}<>]", " ", text)
    text = re.sub(r"\d+", " ", text)
    text = re.sub(r"[:;/|,_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_PATTERN_CANONICAL_LEN]


def _pattern_quality_score(summary: str) -> float:
    text = _sanitize_text(summary, max_len=_MAX_PATTERN_SUMMARY_LEN)
    if not text:
        return 0.0
    score = 0.45
    length = len(text)
    if 12 <= length <= 70:
        score += 0.2
    elif length > 70:
        score += 0.1
    # Encourage abstract action/risk words.
    lowered = text.lower()
    if any(token in lowered for token in ("状态", "异常", "一致", "同步", "回滚", "失败", "state", "retry", "rollback", "consisten")):
        score += 0.2
    # Penalize over-specific UI wording.
    if any(token in lowered for token in ("按钮", "页面", "文案", "样式", "布局", "button", "page", "ui")):
        score -= 0.1
    return round(max(0.0, min(1.0, score)), 4)


def _pattern_source(sample: dict[str, Any], summary: str) -> str:
    original = _sanitize_text(_sample_value(sample, "pattern_summary", "patternSummary"), max_len=_MAX_PATTERN_SUMMARY_LEN)
    if original:
        return "manual"
    return "auto"


def _pattern_cluster_key(sample: dict[str, Any], canonical: str, summary: str) -> str:
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40).lower()
    signal_type = _normalize_signal_type(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_category = _normalize_pattern_category(
        _sample_value(sample, "pattern_category", "patternCategory")
    )
    semantic_bucket = pattern_category if signal_type == "positive" and pattern_category else reason
    base = canonical or _canonicalize_pattern_text(summary)
    if not base:
        return semantic_bucket or "misc"
    tokens = [token for token in base.split(" ") if token]
    # Keep a compact semantic signature to cluster near-duplicate variants.
    cluster = " ".join(tokens[:6])[:_MAX_PATTERN_CLUSTER_LEN]
    if semantic_bucket:
        return f"{semantic_bucket}|{cluster}" if cluster else semantic_bucket
    return cluster or "misc"


def _pattern_weight(sample: dict[str, Any], summary: str, quality: float, source: str) -> float:
    expected = _sanitize_text(_sample_value(sample, "expected_priority", "expectedPriority"), max_len=8).upper()
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40).lower()
    signal_type = _normalize_signal_type(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_usage = _normalize_pattern_usage(
        _sample_value(sample, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    is_positive_signal = bool(signal_type == "positive" or pattern_usage == "prefer")
    is_negative_signal = not is_positive_signal
    ui_low_value = _is_ui_low_value_pattern(
        reason,
        _sample_value(sample, "pattern_category", "patternCategory"),
        summary,
        _sample_value(sample, "title"),
        _sample_value(sample, "user_comment", "userComment"),
    )
    weight = 0.6 + (quality * 0.6)
    if source == "manual":
        weight += 0.15
    if expected in {"P0", "P1"}:
        weight += 0.2
    if reason in {"core_flow", "exception_path", "state_transition"}:
        weight += 0.1
    if reason == "display_issue":
        weight -= 0.05
    if is_negative_signal and ui_low_value:
        # Keep UI-negative patterns retrievable so they can suppress low-value UI-only cases.
        weight += 0.08
    if is_positive_signal and ui_low_value:
        # Avoid over-amplifying UI-positive samples in preferred pattern retrieval.
        weight -= 0.04
    adjustment = _safe_float(
        _sample_value(sample, "pattern_weight_adjustment", "patternWeightAdjustment"),
        default=1.0,
    )
    adjustment = max(_MIN_PATTERN_WEIGHT_ADJUSTMENT, min(_MAX_PATTERN_WEIGHT_ADJUSTMENT, adjustment))
    weight *= adjustment
    return round(max(0.3, min(1.8, weight)), 4)


def _default_pattern_summary(sample: dict[str, Any]) -> str:
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40)
    pattern_category = _normalize_pattern_category(
        _sample_value(sample, "pattern_category", "patternCategory")
    )
    title = _sanitize_text(_sample_value(sample, "title"), max_len=100)
    comment = _sanitize_text(_sample_value(sample, "user_comment", "userComment"), max_len=120)
    case_id = _sanitize_text(_sample_value(sample, "case_id", "caseId"), max_len=40)
    parts = [part for part in [pattern_category, reason, title, comment, case_id] if part]
    if not parts:
        return ""
    return " | ".join(parts)[:_MAX_PATTERN_SUMMARY_LEN]


def normalize_priority_sample(sample: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(sample or {})
    pattern_category = _normalize_pattern_category(
        _sample_value(normalized, "pattern_category", "patternCategory")
    )
    if pattern_category:
        normalized["pattern_category"] = pattern_category
    summary = _sanitize_text(
        _sample_value(normalized, "pattern_summary", "patternSummary"),
        max_len=_MAX_PATTERN_SUMMARY_LEN,
    )
    if len(summary) < _MIN_PATTERN_SUMMARY_LEN:
        summary = _default_pattern_summary(normalized)
    canonical = _canonicalize_pattern_text(summary)
    source = _pattern_source(normalized, summary)
    quality = _pattern_quality_score(summary)
    weight = _pattern_weight(normalized, summary, quality, source)
    cluster_key = _pattern_cluster_key(normalized, canonical, summary)
    normalized["pattern_summary"] = summary
    normalized["pattern_canonical"] = canonical
    normalized["pattern_cluster_key"] = cluster_key
    normalized["pattern_source"] = source
    normalized["pattern_quality_score"] = quality
    normalized["pattern_weight"] = weight
    status = _sanitize_text(
        _sample_value(normalized, "governance_status", "pattern_status", "patternStatus"),
        max_len=16,
    ).lower()
    normalized["governance_status"] = "disabled" if status == "disabled" else "active"
    signal_type = _normalize_signal_type(
        _sample_value(
            normalized,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    normalized["signal_type"] = signal_type
    normalized["pattern_usage"] = _normalize_pattern_usage(
        _sample_value(normalized, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    if signal_type == "positive" and not normalized.get("pattern_category"):
        legacy_reason = _sanitize_text(
            _sample_value(normalized, "reason_category", "reasonCategory"),
            max_len=40,
        ).lower()
        if legacy_reason:
            normalized["pattern_category"] = legacy_reason
    adjustment = _safe_float(
        _sample_value(normalized, "pattern_weight_adjustment", "patternWeightAdjustment"),
        default=1.0,
    )
    normalized["pattern_weight_adjustment"] = round(
        max(_MIN_PATTERN_WEIGHT_ADJUSTMENT, min(_MAX_PATTERN_WEIGHT_ADJUSTMENT, adjustment)),
        4,
    )
    return normalized


def _dedupe_priority_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    winners: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for sample in samples:
        canonical = _sanitize_text(sample.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN)
        if not canonical:
            canonical = _canonicalize_pattern_text(sample.get("pattern_summary"))
        if not canonical:
            # Fallback key for malformed items.
            canonical = f"sample:{len(order)}"
        current = winners.get(canonical)
        if current is None:
            winners[canonical] = sample
            order.append(canonical)
            continue
        left = float(current.get("pattern_weight") or 0.0)
        right = float(sample.get("pattern_weight") or 0.0)
        if right > left:
            winners[canonical] = sample
            continue
        if right == left:
            left_q = float(current.get("pattern_quality_score") or 0.0)
            right_q = float(sample.get("pattern_quality_score") or 0.0)
            if right_q > left_q:
                winners[canonical] = sample
    return [winners[key] for key in order if key in winners]


def normalize_priority_samples(samples: list[dict[str, Any]] | None, *, max_items: int = _MAX_INDEXED_PATTERN_SAMPLES) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in (samples if isinstance(samples, list) else []):
        if isinstance(item, dict):
            normalized.append(normalize_priority_sample(item))
    deduped = _dedupe_priority_samples(normalized)
    return deduped[: max(1, int(max_items))]


def _build_priority_pattern_doc_id(project_id: int, user_id: int) -> str:
    return f"{_PRIORITY_PATTERN_VECTOR_DOC_PREFIX}_p{int(project_id)}_u{int(user_id)}"


def _build_priority_pattern_chunk(sample: dict[str, Any], sample_index: int) -> dict[str, Any] | None:
    summary = _sanitize_text(sample.get("pattern_summary"), max_len=_MAX_PATTERN_SUMMARY_LEN)
    if not summary:
        return None
    title = _sanitize_text(_sample_value(sample, "title"), max_len=120)
    comment = _sanitize_text(_sample_value(sample, "user_comment", "userComment"), max_len=160)
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40)
    pattern_category = _normalize_pattern_category(
        _sample_value(sample, "pattern_category", "patternCategory")
    )
    expected_priority = _sanitize_text(
        _sample_value(sample, "expected_priority", "expectedPriority"),
        max_len=8,
    )
    case_id = _sanitize_text(_sample_value(sample, "case_id", "caseId"), max_len=40)
    pattern_canonical = _sanitize_text(sample.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN)
    pattern_cluster_key = _sanitize_text(sample.get("pattern_cluster_key"), max_len=_MAX_PATTERN_CLUSTER_LEN)
    pattern_source = _sanitize_text(sample.get("pattern_source"), max_len=20)
    governance_status = _sanitize_text(sample.get("governance_status"), max_len=16).lower() or "active"
    signal_type = _normalize_signal_type(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_usage = _normalize_pattern_usage(
        _sample_value(sample, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    try:
        pattern_quality_score = float(sample.get("pattern_quality_score"))
    except Exception:
        pattern_quality_score = 0.0
    try:
        pattern_weight = float(sample.get("pattern_weight"))
    except Exception:
        pattern_weight = 0.6
    chunk_text = " ".join(
        part
        for part in [
            summary,
            f"canonical:{pattern_canonical}" if pattern_canonical else "",
            f"cluster:{pattern_cluster_key}" if pattern_cluster_key else "",
            f"title:{title}" if title else "",
            f"reason:{reason}" if reason else "",
            f"pattern_category:{pattern_category}" if pattern_category else "",
            f"signal:{signal_type}",
            f"usage:{pattern_usage}",
            f"expected:{expected_priority}" if expected_priority else "",
            f"comment:{comment}" if comment else "",
            f"case:{case_id}" if case_id else "",
        ]
        if part
    )
    if not chunk_text:
        return None
    return {
        "chunk_text": chunk_text,
        "metadata": {
            "sample_index": int(sample_index),
            "pattern_summary": summary,
            "pattern_canonical": pattern_canonical,
            "pattern_cluster_key": pattern_cluster_key,
            "pattern_source": pattern_source,
            "governance_status": "disabled" if governance_status == "disabled" else "active",
            "signal_type": signal_type,
            "pattern_usage": pattern_usage,
            "pattern_quality_score": round(max(0.0, min(1.0, pattern_quality_score)), 4),
                "pattern_weight": round(max(0.3, min(1.8, pattern_weight)), 4),
                "reason_category": reason,
                "pattern_category": pattern_category,
                "expected_priority": expected_priority,
                "case_id": case_id,
            },
    }


def _sync_priority_pool_pattern_index(
    *,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    pattern_index_token: str | None,
    samples: list[dict[str, Any]],
) -> None:
    safe_token = _sanitize_text(pattern_index_token, max_len=48) or datetime.utcnow().strftime("%Y%m%d%H%M%S")
    doc_id = f"{_build_priority_pattern_doc_id(project_id=project_id, user_id=user_id)}_{safe_token}"
    chunks: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples[:_MAX_INDEXED_PATTERN_SAMPLES]):
        chunk = _build_priority_pattern_chunk(sample, idx)
        if chunk:
            chunks.append(chunk)

    if not chunks:
        return

    vector_store = get_vector_store()
    if not vector_store.is_ready():
        return
    content = "\n".join(str(item.get("chunk_text") or "") for item in chunks if item.get("chunk_text"))
    try:
        vector_store.add_document(
            doc_id=doc_id,
            content=content,
            metadata={
                "project_id": int(project_id),
                "user_id": int(user_id),
                "doc_type": PRIORITY_SAMPLE_POOL_PATTERN_DOC_TYPE,
                "filename": build_priority_sample_pool_filename(project_id),
                "doc_id": doc_id,
                "generation_id": int(generation_id) if generation_id is not None else "",
                "pattern_index_token": safe_token,
                "is_summary": False,
            },
            chunks=chunks,
        )
    except Exception as err:
        logger.warning("priority_pool_index_upsert_failed doc_id=%s err=%s", doc_id, err)


def ensure_priority_pool_pattern_index(
    *,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    pattern_index_token: str | None = None,
    samples: list[dict[str, Any]],
) -> None:
    normalized_samples = normalize_priority_samples(samples)
    _sync_priority_pool_pattern_index(
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id,
        pattern_index_token=pattern_index_token,
        samples=normalized_samples,
    )


def retrieve_priority_sample_patterns(
    *,
    project_id: int,
    user_id: int,
    query_text: str,
    generation_id: int | None = None,
    pattern_index_token: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    query = _sanitize_text(query_text, max_len=_MAX_EMBED_QUERY_CHARS)
    if not query:
        return []

    capped_top_k = max(1, min(int(top_k or 0), _MAX_RETRIEVAL_RESULTS))
    retrieval_candidates = min(_MAX_RETRIEVAL_CANDIDATES, max(capped_top_k, capped_top_k * 3))
    vector_store = get_vector_store()
    if not vector_store.is_ready():
        return []

    clauses: list[dict[str, Any]] = [
        {"project_id": int(project_id)},
        {"user_id": int(user_id)},
        {"doc_type": PRIORITY_SAMPLE_POOL_PATTERN_DOC_TYPE},
        {"is_summary": False},
    ]
    safe_token = _sanitize_text(pattern_index_token, max_len=48)
    if safe_token:
        clauses.append({"pattern_index_token": safe_token})
    elif generation_id is not None:
        clauses.append({"generation_id": int(generation_id)})
    where = {"$and": clauses}
    try:
        result = vector_store.search(
            query=query,
            n_results=retrieval_candidates,
            where=where,
            raise_on_error=True,
        )
    except Exception as err:
        logger.warning("priority_pool_pattern_search_failed project_id=%s user_id=%s err=%s", project_id, user_id, err)
        return []

    docs = (result.get("documents") or [[]])[0] if isinstance(result.get("documents"), list) else []
    metas = (result.get("metadatas") or [[]])[0] if isinstance(result.get("metadatas"), list) else []
    distances = (result.get("distances") or [[]])[0] if isinstance(result.get("distances"), list) else []

    matched: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for idx, metadata in enumerate(metas):
        if not isinstance(metadata, dict):
            continue
        try:
            sample_index = int(metadata.get("sample_index"))
        except Exception:
            continue
        if sample_index in seen_indices:
            continue
        seen_indices.add(sample_index)
        try:
            pattern_weight = float(metadata.get("pattern_weight"))
        except Exception:
            pattern_weight = 0.6
        try:
            pattern_quality = float(metadata.get("pattern_quality_score"))
        except Exception:
            pattern_quality = 0.0
        rerank_score = (1.0 / (1.0 + idx)) * 0.8 + (max(0.3, min(1.8, pattern_weight)) / 1.8) * 0.2
        matched.append(
            {
                "sample_index": sample_index,
                "pattern_summary": _sanitize_text(metadata.get("pattern_summary"), max_len=_MAX_PATTERN_SUMMARY_LEN),
                "pattern_canonical": _sanitize_text(metadata.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN),
                "pattern_cluster_key": _sanitize_text(metadata.get("pattern_cluster_key"), max_len=_MAX_PATTERN_CLUSTER_LEN),
                "pattern_source": _sanitize_text(metadata.get("pattern_source"), max_len=20),
                "governance_status": _sanitize_text(metadata.get("governance_status"), max_len=16).lower() or "active",
                "signal_type": _normalize_signal_type(metadata.get("signal_type")),
                "pattern_usage": _normalize_pattern_usage(
                    metadata.get("pattern_usage"),
                    signal_type=_normalize_signal_type(metadata.get("signal_type")),
                ),
                "pattern_quality_score": round(max(0.0, min(1.0, pattern_quality)), 4),
                "pattern_weight": round(max(0.3, min(1.8, pattern_weight)), 4),
                "reason_category": _sanitize_text(metadata.get("reason_category"), max_len=40),
                "pattern_category": _normalize_pattern_category(metadata.get("pattern_category")),
                "expected_priority": _sanitize_text(metadata.get("expected_priority"), max_len=8),
                "case_id": _sanitize_text(metadata.get("case_id"), max_len=40),
                "document": _sanitize_text(docs[idx] if idx < len(docs) else "", max_len=220),
                "distance": float(distances[idx]) if idx < len(distances) else None,
                "retrieval_rank": int(idx + 1),
                "rerank_score": round(rerank_score, 6),
            }
        )
    matched.sort(
        key=lambda item: (
            float(item.get("rerank_score") or 0.0),
            float(item.get("pattern_weight") or 0.0),
            float(item.get("pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    return matched[:capped_top_k]


def upsert_priority_sample_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    samples: list[dict[str, Any]],
) -> KnowledgeDocument:
    repo = EvaluationArtifactRepository(db)
    filename = build_priority_sample_pool_filename(project_id)
    normalized_samples = normalize_priority_samples(samples, max_items=5000)
    payload = {
        "project_id": int(project_id),
        "generation_id": int(generation_id) if generation_id is not None else None,
        "samples": normalized_samples,
        "pattern_index_token": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    content = json.dumps(payload, ensure_ascii=False)

    doc = repo.get_latest_artifact_doc(
        project_id=project_id,
        user_id=user_id,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        filename=filename,
    )
    if doc:
        doc.content = content
        doc.parse_status = "success"
        doc.parse_error = None
        repo.commit()
        repo.refresh(doc)
        _sync_priority_pool_pattern_index(
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            pattern_index_token=_sanitize_text(payload.get("pattern_index_token"), max_len=48),
            samples=normalized_samples,
        )
        return doc

    doc = KnowledgeDocument(
        project_id=project_id,
        user_id=user_id,
        filename=filename,
        content=content,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        parse_status="success",
    )
    repo.add(doc)
    repo.commit()
    repo.refresh(doc)
    _sync_priority_pool_pattern_index(
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id,
        pattern_index_token=_sanitize_text(payload.get("pattern_index_token"), max_len=48),
        samples=normalized_samples,
    )
    return doc


def load_priority_sample_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
) -> Optional[dict[str, Any]]:
    if db is None or not hasattr(db, "query"):
        return None
    repo = EvaluationArtifactRepository(db)
    filename = build_priority_sample_pool_filename(project_id)
    doc = repo.get_latest_artifact_doc(
        project_id=project_id,
        user_id=user_id,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        filename=filename,
    )
    if not doc:
        return None
    try:
        payload = json.loads(doc.content or "{}")
        if not isinstance(payload, dict):
            return None
        payload["artifact_doc_id"] = doc.id
        payload["artifact_filename"] = doc.filename
        return payload
    except Exception:
        return None
