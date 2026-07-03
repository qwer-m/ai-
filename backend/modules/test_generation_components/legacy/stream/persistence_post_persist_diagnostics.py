from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .persistence_diagnostics import (
    _fit_table_diag_payload_size,
    _normalize_judge_row,
    _normalize_review_compact_rows,
)


@dataclass(frozen=True)
class StreamPostPersistDiagnosticPayloads:
    before_generation_summary: list[dict[str, Any]]
    generation_summary: dict[str, Any] | None
    after_generation_summary: list[dict[str, Any]]


def stream_generation_mode(generation_mode: str, multi_pass: bool) -> str:
    return generation_mode or ("multi_pass" if multi_pass else "single_pass")


def add_diagnostic_log(
    *,
    db: Any,
    log_entry_type: Any,
    project_id: int,
    user_id: int | None,
    payload: dict[str, Any],
    prefix: str = "GEN_DIAG",
) -> str:
    payload_text = json.dumps(payload, ensure_ascii=False)
    if db:
        db.add(
            log_entry_type(
                project_id=project_id,
                log_type="system",
                message=f"{prefix}:{payload_text}",
                user_id=user_id,
            )
        )
    return f"{prefix}:{payload_text}\n"


def build_stream_post_persist_diagnostic_payloads(
    *,
    generation_id: int | None,
    project_id: int,
    request_id: str,
    generation_mode: str,
    multi_pass: bool,
    current_biz_key: str,
    timing_payload: dict[str, Any] | None,
    stage_counts: dict[str, Any],
    duration_by_stage_ms: dict[str, int],
    doc_type: str,
    compress: bool,
    expected_count: int,
    generated_count: int,
    requirement_length: int,
    kb_length: int,
    model: str,
    max_tokens: Any,
    compression_diag_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
    memory_diag: dict[str, Any],
    review_decision_table_payload: list[dict[str, Any]],
    generation_summary_payload: dict[str, Any],
    quality_ledger_payload: dict[str, Any],
    coverage_payload: dict[str, Any],
) -> StreamPostPersistDiagnosticPayloads:
    mode = stream_generation_mode(generation_mode, multi_pass)
    generation_id_int = int(generation_id or 0)
    before_summary: list[dict[str, Any]] = []

    if generation_id:
        persisted_payload = {
            "kind": "generation_persisted",
            "generation_id": generation_id_int,
            "project_id": int(project_id),
        }
        if request_id:
            persisted_payload["request_id"] = request_id
        before_summary.append(persisted_payload)

    if timing_payload:
        before_summary.append(dict(timing_payload))

    before_summary.append(
        {
            "kind": "generation_mode",
            "mode": mode,
            "biz_keys": [current_biz_key or "unknown"],
            "current_biz_key": current_biz_key or "unknown",
            "multi_pass": bool(multi_pass),
        }
    )

    for stage in ("primary", "gap", "review"):
        before_summary.append(
            {
                "kind": "generation_stage",
                "stage": stage,
                "case_count": int(stage_counts.get(stage, 0)),
                "duration_ms": int(duration_by_stage_ms.get(stage) or 0),
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )

    before_summary.append(
        {
            "kind": "gen_diag",
            "mode": "stream",
            "doc_type": doc_type,
            "compress": compress,
            "expected_count": expected_count,
            "generated_count": generated_count,
            "content_length": requirement_length,
            "kb_length": kb_length,
            "model": model,
            "max_tokens": max_tokens,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
            "context_compression_ratio": compression_diag_payload.get("compression_ratio"),
            "context_retained_chunk_count": compression_diag_payload.get("retained_chunk_count"),
            "context_relevance_distribution": compression_diag_payload.get("relevance_distribution") or {},
        }
    )

    compression_diag = {
        "kind": "generation_context_compression",
        **compression_diag_payload,
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }
    if request_id:
        compression_diag["request_id"] = request_id
    before_summary.append(compression_diag)

    if convergence_payload:
        before_summary.append(
            {
                "kind": "generation_convergence",
                **convergence_payload,
                "expected_count": int(expected_count or 0),
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )

    if review_decision_summary_payload:
        review_summary_diag = {
            "kind": "review_decision_summary",
            **review_decision_summary_payload,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        if request_id:
            review_summary_diag["request_id"] = request_id
        before_summary.append(review_summary_diag)

    if feedback_control_debug_payload:
        control_diag = {
            "kind": "feedback_control_state",
            **feedback_control_debug_payload,
        }
        if request_id:
            control_diag["request_id"] = request_id
        before_summary.append(control_diag)

    if judge_summary_payload:
        judge_diag = {
            "kind": "judge_summary",
            **judge_summary_payload,
        }
        if generation_id:
            judge_diag["generation_id"] = generation_id_int
        if request_id:
            judge_diag["request_id"] = request_id
        before_summary.append(judge_diag)

    if judge_summary_payload or judge_decision_table_payload:
        before_summary.append(
            _build_judge_table_payload(
                generation_id=generation_id_int,
                request_id=request_id,
                multi_pass=multi_pass,
                mode=mode,
                judge_summary_payload=judge_summary_payload,
                judge_decision_table_payload=judge_decision_table_payload,
            )
        )

    if memory_diag:
        memory_diag_payload = {
            "kind": "memory_fabric_diag",
            **dict(memory_diag),
        }
        if request_id:
            memory_diag_payload["request_id"] = request_id
        before_summary.append(memory_diag_payload)

    before_summary.extend(
        _build_review_table_payloads(
            generation_id=generation_id_int,
            request_id=request_id,
            multi_pass=multi_pass,
            mode=mode,
            review_decision_table_payload=review_decision_table_payload,
        )
    )

    generation_summary_diag = None
    if generation_summary_payload:
        generation_summary_diag = {
            "kind": "generation_summary",
            **generation_summary_payload,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }

    after_summary = [dict(quality_ledger_payload)]
    case_quality_gate_payload = dict(quality_ledger_payload.get("case_quality_gate") or {})
    if case_quality_gate_payload:
        if generation_id:
            case_quality_gate_payload["generation_id"] = generation_id_int
        if request_id:
            case_quality_gate_payload["request_id"] = request_id
        after_summary.append(case_quality_gate_payload)

    if coverage_payload:
        coverage_diag = dict(coverage_payload)
        coverage_diag["multi_pass"] = bool(multi_pass)
        coverage_diag["generation_mode"] = mode
        after_summary.append(coverage_diag)

    return StreamPostPersistDiagnosticPayloads(
        before_generation_summary=before_summary,
        generation_summary=generation_summary_diag,
        after_generation_summary=after_summary,
    )


def _build_judge_table_payload(
    *,
    generation_id: int,
    request_id: str,
    multi_pass: bool,
    mode: str,
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_rows = [
        _normalize_judge_row(
            item,
            generation_id=int(generation_id or 0),
            request_id=request_id,
        )
        for item in judge_decision_table_payload
        if isinstance(item, dict)
    ]
    reject_pending_rows = [
        row
        for row in normalized_rows
        if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
    ]
    rows_to_persist = reject_pending_rows or normalized_rows
    judge_table_diag = {
        "kind": "judge_decision_table",
        "generation_id": int(generation_id or 0),
        "rows": rows_to_persist,
        "row_count": int(len(rows_to_persist)),
        "row_count_total": int(len(normalized_rows)),
        "row_count_reject_pending": int(len(reject_pending_rows)),
        "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
        "row_evidence_incomplete": bool(
            int(judge_summary_payload.get("rejected_out_count") or 0)
            + int(judge_summary_payload.get("pending_out_count") or 0) > 0
            and len(reject_pending_rows) == 0
        ),
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }
    if request_id:
        judge_table_diag["request_id"] = request_id
    return _fit_table_diag_payload_size(judge_table_diag)


def _build_review_table_payloads(
    *,
    generation_id: int,
    request_id: str,
    multi_pass: bool,
    mode: str,
    review_decision_table_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not review_decision_table_payload:
        return []

    review_table_diag = {
        "kind": "review_decision_table",
        "generation_id": int(generation_id or 0),
        "rows": review_decision_table_payload,
        "row_count": int(len(review_decision_table_payload)),
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }
    if request_id:
        review_table_diag["request_id"] = request_id
    payloads = [_fit_table_diag_payload_size(review_table_diag)]

    compact_rows = _normalize_review_compact_rows(
        review_decision_table_payload,
        generation_id=int(generation_id or 0),
        request_id=request_id,
    )
    if compact_rows:
        review_table_compact_diag = {
            "kind": "review_decision_table_compact",
            "generation_id": int(generation_id or 0),
            "rows": compact_rows,
            "row_count": int(len(compact_rows)),
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        if request_id:
            review_table_compact_diag["request_id"] = request_id
        payloads.append(_fit_table_diag_payload_size(review_table_compact_diag))

    return payloads
