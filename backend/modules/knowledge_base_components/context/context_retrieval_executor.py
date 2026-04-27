"""Retry-aware retrieval execution for context assembly."""

from __future__ import annotations

import time
from typing import Any, Optional

from sqlalchemy.orm import Session

from modules.knowledge_base_components.context.context_helpers import _run_retrieval_once
from modules.knowledge_base_components.retrieval.retrieval_retry import (
    STABILITY_CONFIG,
    is_retryable_exception,
    now_iso,
    should_retry,
)


def execute_retrieval_with_retry(
    *,
    question: str,
    project_id: int,
    limit: int,
    max_tokens: int,
    db: Optional[Session],
    retrieval_options: Optional[dict],
    recall_fn=None,
    rerank_fn=None,
) -> dict[str, Any]:
    """Execute retrieval with bounded retries and structured attempt records."""
    attempt_records: list[dict[str, Any]] = []
    last_outcome: Optional[dict[str, Any]] = None
    last_error: Optional[str] = None

    for attempt_no in range(1, STABILITY_CONFIG.max_retrieve_attempts + 1):
        started_at = now_iso()
        try:
            outcome = _run_retrieval_once(
                question,
                project_id,
                limit,
                max_tokens,
                db=db,
                retrieval_options=retrieval_options,
                recall_fn=recall_fn,
                rerank_fn=rerank_fn,
            )
            recall_debug = outcome.get("recall_result", {}).get("debug", {})
            lane_reasons = recall_debug.get("lane_reasons") or {}
            lane_counts = recall_debug.get("lane_counts") or {}
            has_context = bool(outcome.get("selected_chunks"))

            retry_triggered, retry_reason = should_retry(
                lane_reasons=lane_reasons,
                has_context=has_context,
                attempt_no=attempt_no,
                low_relevance_filtered=bool(outcome.get("low_relevance_filtered")),
            )
            attempt_records.append(
                {
                    "attempt_no": attempt_no,
                    "started_at": started_at,
                    "lane_reasons": lane_reasons,
                    "lane_counts": lane_counts,
                    "result_chunk_count": len(outcome.get("selected_chunks") or []),
                    "retrieval_mode": str((outcome.get("retrieval_tuning") or {}).get("retrieval_mode") or ""),
                    "retry_triggered": bool(retry_triggered),
                    "retry_reason": retry_reason,
                }
            )
            last_outcome = outcome

            if retry_triggered:
                time.sleep(STABILITY_CONFIG.retry_backoff_ms / 1000.0)
                continue
            break
        except Exception as attempt_error:
            last_error = str(attempt_error)
            retryable, retry_reason = is_retryable_exception(attempt_error)
            can_retry = retryable and attempt_no < STABILITY_CONFIG.max_retrieve_attempts
            attempt_records.append(
                {
                    "attempt_no": attempt_no,
                    "started_at": started_at,
                    "lane_reasons": {},
                    "lane_counts": {},
                    "result_chunk_count": 0,
                    "retry_triggered": bool(can_retry),
                    "retry_reason": retry_reason,
                    "error": last_error,
                }
            )
            if can_retry:
                time.sleep(STABILITY_CONFIG.retry_backoff_ms / 1000.0)
                continue
            break

    return {
        "attempt_records": attempt_records,
        "last_outcome": last_outcome,
        "last_error": last_error,
    }
