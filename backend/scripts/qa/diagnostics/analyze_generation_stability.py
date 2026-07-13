from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db.database import SessionLocal
from core.db.models import LogEntry, TestGeneration


@dataclass
class _RunContext:
    generation_id: int
    project_id: int
    request_id: str
    persisted_log_id: int
    next_persisted_log_id: int | None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if int(denominator or 0) <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _parse_gen_diag_payload(message: str) -> dict[str, Any] | None:
    text = str(message or "")
    marker = "GEN_DIAG:"
    idx = text.find(marker)
    if idx < 0:
        return None
    raw = text[idx + len(marker):].strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _find_context(generation_id: int) -> _RunContext | None:
    db = SessionLocal()
    try:
        rows = (
            db.query(LogEntry)
            .filter(LogEntry.message.like("%GEN_DIAG:%"))
            .order_by(LogEntry.id.desc())
            .all()
        )
        persisted_rows: list[tuple[LogEntry, dict[str, Any]]] = []
        for row in rows:
            payload = _parse_gen_diag_payload(row.message or "")
            if not payload:
                continue
            if str(payload.get("kind") or "") != "generation_persisted":
                continue
            persisted_rows.append((row, payload))

        target: tuple[LogEntry, dict[str, Any]] | None = None
        for row, payload in persisted_rows:
            if int(payload.get("generation_id") or 0) == int(generation_id):
                target = (row, payload)
                break
        if not target:
            return None

        target_row, target_payload = target
        project_id = int(target_payload.get("project_id") or target_row.project_id or 0)
        if project_id <= 0:
            return None

        current_id = int(target_row.id or 0)
        next_persisted_id: int | None = None
        for row, payload in persisted_rows:
            if int(row.project_id or 0) != project_id:
                continue
            row_id = int(row.id or 0)
            if row_id > current_id:
                if next_persisted_id is None or row_id < next_persisted_id:
                    next_persisted_id = row_id

        return _RunContext(
            generation_id=int(generation_id),
            project_id=project_id,
            request_id=str(target_payload.get("request_id") or "").strip(),
            persisted_log_id=current_id,
            next_persisted_log_id=next_persisted_id,
        )
    finally:
        db.close()


def _load_run_payloads(ctx: _RunContext) -> list[tuple[int, dict[str, Any]]]:
    db = SessionLocal()
    try:
        query = (
            db.query(LogEntry)
            .filter(
                LogEntry.project_id == ctx.project_id,
                LogEntry.id >= ctx.persisted_log_id,
                LogEntry.message.like("%GEN_DIAG:%"),
            )
            .order_by(LogEntry.id.asc())
        )
        if ctx.next_persisted_log_id is not None:
            query = query.filter(LogEntry.id < ctx.next_persisted_log_id)

        rows = query.all()
        payloads: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            payload = _parse_gen_diag_payload(row.message or "")
            if not payload:
                continue
            if ctx.request_id:
                payload_request_id = str(payload.get("request_id") or "").strip()
                if payload_request_id and payload_request_id != ctx.request_id:
                    continue
            payloads.append((int(row.id or 0), payload))
        return payloads
    finally:
        db.close()


def _pick_latest_payload(payloads: list[tuple[int, dict[str, Any]]], kind: str) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    latest_id = -1
    for row_id, payload in payloads:
        if str(payload.get("kind") or "") != kind:
            continue
        if row_id >= latest_id:
            latest = payload
            latest_id = row_id
    return latest


def _safe_parse_generation_result(raw_text: str) -> Any:
    text = str(raw_text or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def _extract_case_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        cases = payload.get("cases")
        if isinstance(cases, list):
            return [item for item in cases if isinstance(item, dict)]
        nested_error = payload.get("error")
        if isinstance(nested_error, dict):
            nested_cases = nested_error.get("cases")
            if isinstance(nested_cases, list):
                return [item for item in nested_cases if isinstance(item, dict)]
    return []


def _load_generation_row_payload(generation_id: int) -> tuple[TestGeneration | None, Any]:
    db = SessionLocal()
    try:
        row = db.query(TestGeneration).filter(TestGeneration.id == int(generation_id)).first()
        if row is None:
            return None, []
        parsed = _safe_parse_generation_result(getattr(row, "generated_result", ""))
        return row, parsed
    finally:
        db.close()


def _build_run_snapshot_from_generated_result(generation_id: int) -> dict[str, Any]:
    row, parsed_payload = _load_generation_row_payload(int(generation_id))
    if row is None:
        raise RuntimeError(f"generation_id={generation_id} not found")

    cases = _extract_case_list(parsed_payload)
    case_total = int(len(cases))

    state_breakdown: dict[str, int] = {"decided": 0, "conflict": 0, "undetermined": 0, "optional": 0, "invalid": 0}
    final_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "null": 0}
    legacy_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0}
    for case in cases:
        state = str(case.get("priority_decision_state") or "undetermined").strip().lower()
        if state not in state_breakdown:
            state = "undetermined"
        state_breakdown[state] += 1

        priority_final = str(case.get("priority_final") or "").strip().upper()
        if priority_final not in {"P0", "P1", "P2"}:
            priority_final = "null"
        final_breakdown[priority_final] += 1

        legacy = str(case.get("legacy_priority") or case.get("priority") or "").strip().upper()
        if legacy not in {"P0", "P1", "P2"}:
            legacy = "UNKNOWN"
        legacy_breakdown[legacy] += 1

    reason_chain_seed = f"generation:{int(generation_id)}|project:{int(getattr(row, 'project_id', 0) or 0)}|count:{case_total}"
    context_fingerprint = hashlib.sha256(reason_chain_seed.encode("utf-8")).hexdigest()[:16]

    priority_conflict_count = int(state_breakdown.get("conflict") or 0)
    priority_undetermined_count = int(state_breakdown.get("undetermined") or 0)
    priority_optional_count = int(state_breakdown.get("optional") or 0)
    priority_invalid_count = int(state_breakdown.get("invalid") or 0)

    return {
        "generation_id": int(generation_id),
        "request_id": "",
        "candidate_by_stage": {
            "primary": int(case_total),
            "gap": 0,
            "review": int(case_total),
            "before_review": int(case_total),
            "retained": int(case_total),
        },
        "candidate_by_pass": {
            "pass_primary": int(case_total),
            "pass_gap_increment": 0,
        },
        "context_source": "",
        "context_fingerprint": str(context_fingerprint),
        "rag_snapshot_id": "",
        "corpus_hash": "",
        "retrieval_hash": "",
        "review_primary_valid": False,
        "review_retry_valid": False,
        "review_final_source": "generated_result_fallback",
        "review_input_size": int(case_total),
        "review_output_size": int(case_total),
        "reason_source_breakdown": {"primary": 0, "fallback": 0, "backfill": 0},
        "review_runtime": {
            "primary_model": "",
            "primary_invalid_reason": "gen_diag_missing",
            "primary_reason_incomplete": True,
            "primary_dropped_reason_count": 0,
            "primary_dropped_reason_payload_count": 0,
            "primary_reason_coverage_ratio": 0.0,
            "retry_invoked": False,
            "retry_reason": "",
            "retry_model": "",
            "mapped_count": 0,
            "dropped_reason_count": 0,
            "fallback_reason_incomplete": False,
            "fallback_dropped_reason_count": 0,
            "fallback_dropped_reason_mapped_count": 0,
            "fallback_dropped_reason_unmapped_count": 0,
            "fallback_reason_coverage_ratio": 0.0,
            "payload_has_selection_signal": False,
            "retry_mapped_count": 0,
            "retry_payload_has_selection_signal": False,
            "applied_reason": "generated_result_fallback",
        },
        "llm_reason_coverage_ratio": 0.0,
        "deterministic_backfill_ratio": 0.0,
        "reason_metrics": {
            "drop_by_review_llm_count": 0,
            "llm_reason_count": 0,
            "deterministic_backfill_count": 0,
            "llm_reason_coverage_ratio": 0.0,
            "deterministic_backfill_ratio": 0.0,
            "sampled_detail_rows": False,
            "sampled_detail_row_count": 0,
            "sampled_deterministic_row_count": 0,
            "deterministic_source_breakdown_sampled": {},
            "mapped_count_effective": 0,
            "dropped_reason_count_effective": 0,
            "dominant_deterministic_source": "",
        },
        "priority_metrics": {
            "priority_decision_state_breakdown": state_breakdown,
            "priority_final_breakdown": final_breakdown,
            "legacy_priority_breakdown": legacy_breakdown,
            "priority_conflict_count": int(priority_conflict_count),
            "priority_undetermined_count": int(priority_undetermined_count),
            "priority_optional_count": int(priority_optional_count),
            "priority_invalid_count": int(priority_invalid_count),
            "needs_priority_review": bool(priority_conflict_count > 0 or priority_undetermined_count > 0),
        },
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "needs_priority_review": bool(priority_conflict_count > 0 or priority_undetermined_count > 0),
        "drop_ratio": 0.0,
        "generated_model": "",
        "generation_mode": "",
        "diagnostic_source": "generated_result_fallback",
        "diagnostic_depth": "limited",
    }


def _build_stage_map(payloads: list[tuple[int, dict[str, Any]]]) -> dict[str, int]:
    stage_map: dict[str, int] = {}
    for _, payload in payloads:
        if str(payload.get("kind") or "") != "generation_stage":
            continue
        stage = str(payload.get("stage") or "").strip()
        if not stage:
            continue
        stage_map[stage] = int(payload.get("case_count") or 0)
    return stage_map


def _build_context_fingerprint(
    *,
    generation_context_compression: dict[str, Any],
    feedback_control_state: dict[str, Any],
    generation_mode_payload: dict[str, Any],
) -> str:
    basis = {
        "context_source": str(generation_context_compression.get("context_source") or ""),
        "input_chars": _as_int(generation_context_compression.get("input_chars"), 0),
        "output_chars": _as_int(generation_context_compression.get("output_chars"), 0),
        "input_chunk_count": _as_int(generation_context_compression.get("input_chunk_count"), 0),
        "retained_chunk_count": _as_int(generation_context_compression.get("retained_chunk_count"), 0),
        "compression_ratio": float(generation_context_compression.get("compression_ratio") or 0.0),
        "relevance_distribution": dict(generation_context_compression.get("relevance_distribution") or {}),
        "soft_constraints_count": _as_int(feedback_control_state.get("soft_constraints_count"), 0),
        "quality_fix_hints_count": _as_int(feedback_control_state.get("quality_fix_hints_count"), 0),
        "mode": str(generation_mode_payload.get("mode") or ""),
        "multi_pass": bool(generation_mode_payload.get("multi_pass")),
    }
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _classify_deterministic_reason_source(row: dict[str, Any], runtime_debug: dict[str, Any]) -> str:
    raw_reason = str(row.get("review_llm_drop_reason_raw") or "").strip()
    evidence = row.get("review_llm_drop_reason_evidence")
    if not isinstance(evidence, dict):
        evidence = {}

    if raw_reason:
        if str(evidence.get("reason_adjusted_from") or "").strip():
            return "reason_adjusted_from_llm"
        return "raw_reason_present_but_reclassified"

    dropped_reason_count = _as_int(runtime_debug.get("dropped_reason_count"), 0)
    mapped_count = _as_int(runtime_debug.get("mapped_count"), 0)
    final_source = str(runtime_debug.get("final_source") or "").strip()
    retry_valid = bool(runtime_debug.get("retry_parse_success")) and _as_int(runtime_debug.get("retry_mapped_count"), 0) > 0
    if dropped_reason_count == 0 and mapped_count > 0:
        if final_source == "fallback_llm" and retry_valid:
            return "fallback_payload_missing_dropped_reasons"
        return "llm_payload_missing_dropped_reasons"
    return "no_raw_reason_after_mapping"


def _build_deterministic_backfill_attribution(
    *,
    review_summary: dict[str, Any],
    review_table_payload: dict[str, Any],
) -> dict[str, Any]:
    runtime_debug = dict(review_summary.get("review_llm_runtime_debug") or {})
    compact_breakdown = dict(review_summary.get("reason_source_breakdown") or {})
    if compact_breakdown:
        deterministic_total = _as_int(compact_breakdown.get("backfill"), 0)
        llm_total = _as_int(compact_breakdown.get("primary"), 0) + _as_int(compact_breakdown.get("fallback"), 0)
    else:
        source_breakdown = dict(review_summary.get("review_llm_drop_reason_source_breakdown") or {})
        deterministic_total = _as_int(source_breakdown.get("deterministic_backfill"), 0)
        llm_total = _as_int(source_breakdown.get("llm"), 0) + _as_int(source_breakdown.get("fallback_llm"), 0)
    drop_total = _as_int(review_summary.get("drop_by_review_llm_count"), 0)

    rows = [item for item in (review_table_payload.get("rows") or []) if isinstance(item, dict)]
    deterministic_rows = [
        item
        for item in rows
        if str(item.get("dropped_stage") or "") == "review_llm"
        and str(item.get("review_llm_drop_reason_source") or "") == "deterministic_backfill"
    ]
    sampled_counter = Counter(
        _classify_deterministic_reason_source(item, runtime_debug)
        for item in deterministic_rows
    )
    row_count_total = _as_int(review_table_payload.get("row_count_total"), _as_int(review_table_payload.get("row_count"), len(rows)))
    sampled = row_count_total > len(rows)

    final_source = str(runtime_debug.get("final_source") or "").strip()
    retry_valid = bool(runtime_debug.get("retry_parse_success")) and _as_int(runtime_debug.get("retry_mapped_count"), 0) > 0
    mapped_count_effective = _as_int(runtime_debug.get("mapped_count"), 0)
    if final_source == "fallback_llm" and retry_valid:
        mapped_count_effective = _as_int(runtime_debug.get("retry_mapped_count"), mapped_count_effective)
    dropped_reason_count_effective = _as_int(runtime_debug.get("dropped_reason_count"), 0)
    if final_source == "fallback_llm":
        dropped_reason_count_effective = _as_int(
            runtime_debug.get("final_dropped_reason_count"),
            _as_int(runtime_debug.get("retry_dropped_reason_count"), dropped_reason_count_effective),
        )

    dominant = ""
    if deterministic_total > 0:
        if dropped_reason_count_effective == 0 and mapped_count_effective > 0:
            if final_source == "fallback_llm" and retry_valid:
                dominant = "fallback_missing_dropped_reasons"
            else:
                dominant = "primary_missing_dropped_reasons"
        elif sampled_counter:
            dominant = sampled_counter.most_common(1)[0][0]
        else:
            dominant = "needs_detail_rows"

    return {
        "drop_by_review_llm_count": int(drop_total),
        "llm_reason_count": int(llm_total),
        "deterministic_backfill_count": int(deterministic_total),
        "llm_reason_coverage_ratio": _safe_ratio(llm_total, drop_total),
        "deterministic_backfill_ratio": _safe_ratio(deterministic_total, drop_total),
        "sampled_detail_rows": bool(sampled),
        "sampled_detail_row_count": int(len(rows)),
        "sampled_deterministic_row_count": int(len(deterministic_rows)),
        "deterministic_source_breakdown_sampled": dict(sampled_counter),
        "mapped_count_effective": int(mapped_count_effective),
        "dropped_reason_count_effective": int(dropped_reason_count_effective),
        "dominant_deterministic_source": str(dominant),
    }


def _build_priority_decision_metrics(
    *,
    review_summary: dict[str, Any],
    review_table_payload: dict[str, Any],
) -> dict[str, Any]:
    rows = [item for item in (review_table_payload.get("rows") or []) if isinstance(item, dict)]

    summary_state_breakdown = review_summary.get("priority_decision_state_breakdown")
    state_breakdown = dict(summary_state_breakdown) if isinstance(summary_state_breakdown, dict) else {}
    if not state_breakdown:
        for item in rows:
            key = str(item.get("priority_decision_state") or "undetermined").strip().lower()
            if key not in {"decided", "conflict", "undetermined", "optional", "invalid"}:
                key = "undetermined"
            state_breakdown[key] = int(state_breakdown.get(key, 0)) + 1
    for key in ("decided", "conflict", "undetermined", "optional", "invalid"):
        state_breakdown[key] = int(state_breakdown.get(key, 0))

    summary_final_breakdown = review_summary.get("priority_final_breakdown")
    final_breakdown = dict(summary_final_breakdown) if isinstance(summary_final_breakdown, dict) else {}
    if not final_breakdown:
        for item in rows:
            key = str(item.get("priority_final") or "").strip().upper()
            if key not in {"P0", "P1", "P2"}:
                key = "null"
            final_breakdown[key] = int(final_breakdown.get(key, 0)) + 1
    for key in ("P0", "P1", "P2", "null"):
        final_breakdown[key] = int(final_breakdown.get(key, 0))

    summary_legacy_breakdown = review_summary.get("legacy_priority_breakdown")
    legacy_breakdown = dict(summary_legacy_breakdown) if isinstance(summary_legacy_breakdown, dict) else {}
    if not legacy_breakdown:
        for item in rows:
            key = str(item.get("legacy_priority") or item.get("priority") or "").strip().upper()
            if key not in {"P0", "P1", "P2"}:
                key = "UNKNOWN"
            legacy_breakdown[key] = int(legacy_breakdown.get(key, 0)) + 1
    for key in ("P0", "P1", "P2", "UNKNOWN"):
        legacy_breakdown[key] = int(legacy_breakdown.get(key, 0))

    priority_conflict_count = _as_int(review_summary.get("priority_conflict_count"), state_breakdown.get("conflict", 0))
    priority_undetermined_count = _as_int(
        review_summary.get("priority_undetermined_count"),
        state_breakdown.get("undetermined", 0),
    )
    priority_optional_count = _as_int(review_summary.get("priority_optional_count"), state_breakdown.get("optional", 0))
    priority_invalid_count = _as_int(review_summary.get("priority_invalid_count"), state_breakdown.get("invalid", 0))
    needs_priority_review = bool(
        review_summary.get("needs_priority_review")
        or priority_conflict_count > 0
        or priority_undetermined_count > 0
    )
    return {
        "priority_decision_state_breakdown": state_breakdown,
        "priority_final_breakdown": final_breakdown,
        "legacy_priority_breakdown": legacy_breakdown,
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "priority_invalid_count": int(priority_invalid_count),
        "needs_priority_review": bool(needs_priority_review),
    }


def _build_run_snapshot(generation_id: int) -> dict[str, Any]:
    ctx = _find_context(int(generation_id))
    if not ctx:
        return _build_run_snapshot_from_generated_result(int(generation_id))
    payloads = _load_run_payloads(ctx)
    if not payloads:
        return _build_run_snapshot_from_generated_result(int(generation_id))

    stage_map = _build_stage_map(payloads)
    convergence = _pick_latest_payload(payloads, "generation_convergence")
    review_summary = _pick_latest_payload(payloads, "review_decision_summary")
    if not review_summary:
        return _build_run_snapshot_from_generated_result(int(generation_id))
    review_table_payload = _pick_latest_payload(payloads, "review_decision_table")
    generation_context_compression = _pick_latest_payload(payloads, "generation_context_compression")
    feedback_control_state = _pick_latest_payload(payloads, "feedback_control_state")
    generation_mode_payload = _pick_latest_payload(payloads, "generation_mode")
    gen_diag = _pick_latest_payload(payloads, "gen_diag")

    review_runtime = dict(review_summary.get("review_llm_runtime_debug") or {})
    reason_source_breakdown = dict(review_summary.get("reason_source_breakdown") or {})
    if not reason_source_breakdown:
        source_breakdown_raw = dict(review_summary.get("review_llm_drop_reason_source_breakdown") or {})
        reason_source_breakdown = {
            "primary": _as_int(source_breakdown_raw.get("llm"), 0),
            "fallback": _as_int(source_breakdown_raw.get("fallback_llm"), 0),
            "backfill": _as_int(source_breakdown_raw.get("deterministic_backfill"), 0),
        }
    candidate_primary = _as_int(convergence.get("primary_count"), _as_int(stage_map.get("primary"), 0))
    candidate_gap = _as_int(convergence.get("gap_count"), _as_int(stage_map.get("gap"), 0))
    candidate_review = _as_int(convergence.get("review_count"), _as_int(stage_map.get("review"), 0))
    candidate_total = _as_int(review_summary.get("candidate_total"), _as_int(convergence.get("candidate_count_before_review"), 0))
    drop_total = _as_int(review_summary.get("drop_by_review_llm_count"), 0)
    retained_total = _as_int(review_summary.get("retained_total"), _as_int(convergence.get("review_selected_count"), 0))

    reason_metrics = _build_deterministic_backfill_attribution(
        review_summary=review_summary,
        review_table_payload=review_table_payload,
    )
    priority_metrics = _build_priority_decision_metrics(
        review_summary=review_summary,
        review_table_payload=review_table_payload,
    )
    context_fingerprint = _build_context_fingerprint(
        generation_context_compression=generation_context_compression,
        feedback_control_state=feedback_control_state,
        generation_mode_payload=generation_mode_payload,
    )

    # Placeholder fields for future hash-based attribution when upstream starts emitting them.
    rag_snapshot_id = generation_context_compression.get("snapshot_id")
    corpus_hash = generation_context_compression.get("corpus_hash")
    retrieval_hash = generation_context_compression.get("retrieval_hash")

    primary_invalid_reason = str(review_runtime.get("primary_invalid_reason") or "").strip()
    review_primary_valid = bool(review_runtime.get("invoked")) and not primary_invalid_reason
    review_retry_valid = bool(review_runtime.get("retry_invoked")) and bool(review_runtime.get("retry_parse_success")) and (
        _as_int(review_runtime.get("retry_mapped_count"), 0) > 0
    )
    review_final_source = str(review_runtime.get("final_source") or "unknown")

    return {
        "generation_id": int(generation_id),
        "request_id": str(ctx.request_id or ""),
        "candidate_by_stage": {
            "primary": int(candidate_primary),
            "gap": int(candidate_gap),
            "review": int(candidate_review),
            "before_review": int(candidate_total),
            "retained": int(retained_total),
        },
        "candidate_by_pass": {
            "pass_primary": int(candidate_primary),
            "pass_gap_increment": int(candidate_gap),
        },
        "context_source": str(generation_context_compression.get("context_source") or ""),
        "context_fingerprint": str(context_fingerprint),
        "rag_snapshot_id": rag_snapshot_id,
        "corpus_hash": corpus_hash,
        "retrieval_hash": retrieval_hash,
        "review_primary_valid": bool(review_primary_valid),
        "review_retry_valid": bool(review_retry_valid),
        "review_final_source": str(review_final_source),
        "review_input_size": _as_int(review_summary.get("review_input_size"), candidate_total),
        "review_output_size": _as_int(review_summary.get("review_output_size"), retained_total),
        "reason_source_breakdown": reason_source_breakdown,
        "review_runtime": {
            "primary_model": str(review_runtime.get("primary_model") or ""),
            "primary_invalid_reason": str(primary_invalid_reason),
            "primary_reason_incomplete": bool(review_summary.get("primary_reason_incomplete")),
            "primary_dropped_reason_count": _as_int(review_summary.get("primary_dropped_reason_count"), 0),
            "primary_dropped_reason_payload_count": _as_int(
                review_summary.get("primary_dropped_reason_payload_count"), 0
            ),
            "primary_reason_coverage_ratio": float(review_summary.get("primary_reason_coverage_ratio") or 0.0),
            "retry_invoked": bool(review_runtime.get("retry_invoked")),
            "retry_reason": str(review_runtime.get("retry_reason") or ""),
            "retry_model": str(review_runtime.get("retry_model") or ""),
            "mapped_count": _as_int(review_runtime.get("mapped_count"), 0),
            "dropped_reason_count": _as_int(review_runtime.get("dropped_reason_count"), 0),
            "fallback_reason_incomplete": bool(review_summary.get("fallback_reason_incomplete")),
            "fallback_dropped_reason_count": _as_int(review_summary.get("fallback_dropped_reason_count"), 0),
            "fallback_dropped_reason_mapped_count": _as_int(review_summary.get("fallback_dropped_reason_mapped_count"), 0),
            "fallback_dropped_reason_unmapped_count": _as_int(review_summary.get("fallback_dropped_reason_unmapped_count"), 0),
            "fallback_reason_coverage_ratio": float(review_summary.get("fallback_reason_coverage_ratio") or 0.0),
            "payload_has_selection_signal": bool(review_runtime.get("payload_has_selection_signal")),
            "retry_mapped_count": _as_int(review_runtime.get("retry_mapped_count"), 0),
            "retry_payload_has_selection_signal": bool(review_runtime.get("retry_payload_has_selection_signal")),
            "applied_reason": str(review_runtime.get("applied_reason") or ""),
        },
        "llm_reason_coverage_ratio": float(reason_metrics.get("llm_reason_coverage_ratio") or 0.0),
        "deterministic_backfill_ratio": float(reason_metrics.get("deterministic_backfill_ratio") or 0.0),
        "reason_metrics": reason_metrics,
        "priority_metrics": priority_metrics,
        "priority_conflict_count": int(priority_metrics.get("priority_conflict_count") or 0),
        "priority_undetermined_count": int(priority_metrics.get("priority_undetermined_count") or 0),
        "priority_optional_count": int(priority_metrics.get("priority_optional_count") or 0),
        "needs_priority_review": bool(priority_metrics.get("needs_priority_review")),
        "drop_ratio": _safe_ratio(drop_total, candidate_total),
        "generated_model": str(gen_diag.get("model") or ""),
        "generation_mode": str(gen_diag.get("generation_mode") or generation_mode_payload.get("mode") or ""),
        "diagnostic_source": "gen_diag",
        "diagnostic_depth": "full",
    }


def _build_diff(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    l_stage = dict(left.get("candidate_by_stage") or {})
    r_stage = dict(right.get("candidate_by_stage") or {})
    l_runtime = dict(left.get("review_runtime") or {})
    r_runtime = dict(right.get("review_runtime") or {})
    l_reason = dict(left.get("reason_metrics") or {})
    r_reason = dict(right.get("reason_metrics") or {})

    diff_rows = [
        {
            "metric": "candidate_total",
            "left": _as_int(l_stage.get("before_review"), 0),
            "right": _as_int(r_stage.get("before_review"), 0),
            "delta": _as_int(r_stage.get("before_review"), 0) - _as_int(l_stage.get("before_review"), 0),
        },
        {
            "metric": "candidate_primary",
            "left": _as_int(l_stage.get("primary"), 0),
            "right": _as_int(r_stage.get("primary"), 0),
            "delta": _as_int(r_stage.get("primary"), 0) - _as_int(l_stage.get("primary"), 0),
        },
        {
            "metric": "candidate_gap",
            "left": _as_int(l_stage.get("gap"), 0),
            "right": _as_int(r_stage.get("gap"), 0),
            "delta": _as_int(r_stage.get("gap"), 0) - _as_int(l_stage.get("gap"), 0),
        },
        {
            "metric": "review_input_size",
            "left": _as_int(left.get("review_input_size"), 0),
            "right": _as_int(right.get("review_input_size"), 0),
            "delta": _as_int(right.get("review_input_size"), 0) - _as_int(left.get("review_input_size"), 0),
        },
        {
            "metric": "review_output_size",
            "left": _as_int(left.get("review_output_size"), 0),
            "right": _as_int(right.get("review_output_size"), 0),
            "delta": _as_int(right.get("review_output_size"), 0) - _as_int(left.get("review_output_size"), 0),
        },
        {
            "metric": "drop_ratio",
            "left": float(left.get("drop_ratio") or 0.0),
            "right": float(right.get("drop_ratio") or 0.0),
            "delta": round(float(right.get("drop_ratio") or 0.0) - float(left.get("drop_ratio") or 0.0), 4),
        },
        {
            "metric": "deterministic_backfill_ratio",
            "left": float(left.get("deterministic_backfill_ratio") or 0.0),
            "right": float(right.get("deterministic_backfill_ratio") or 0.0),
            "delta": round(
                float(right.get("deterministic_backfill_ratio") or 0.0)
                - float(left.get("deterministic_backfill_ratio") or 0.0),
                4,
            ),
        },
        {
            "metric": "llm_reason_coverage_ratio",
            "left": float(left.get("llm_reason_coverage_ratio") or 0.0),
            "right": float(right.get("llm_reason_coverage_ratio") or 0.0),
            "delta": round(
                float(right.get("llm_reason_coverage_ratio") or 0.0)
                - float(left.get("llm_reason_coverage_ratio") or 0.0),
                4,
            ),
        },
        {
            "metric": "priority_conflict_count",
            "left": _as_int(left.get("priority_conflict_count"), 0),
            "right": _as_int(right.get("priority_conflict_count"), 0),
            "delta": _as_int(right.get("priority_conflict_count"), 0) - _as_int(left.get("priority_conflict_count"), 0),
        },
        {
            "metric": "priority_undetermined_count",
            "left": _as_int(left.get("priority_undetermined_count"), 0),
            "right": _as_int(right.get("priority_undetermined_count"), 0),
            "delta": _as_int(right.get("priority_undetermined_count"), 0) - _as_int(left.get("priority_undetermined_count"), 0),
        },
    ]

    variance_sources: list[str] = []
    if _as_int(r_stage.get("primary"), 0) != _as_int(l_stage.get("primary"), 0) or _as_int(r_stage.get("gap"), 0) != _as_int(l_stage.get("gap"), 0):
        variance_sources.append("generation_stage_variance")
    if str(right.get("context_fingerprint") or "") != str(left.get("context_fingerprint") or ""):
        variance_sources.append("context_variance")
    if str(right.get("review_final_source") or "") != str(left.get("review_final_source") or ""):
        variance_sources.append("review_runtime_variance")
    if str(r_runtime.get("primary_invalid_reason") or "") != str(l_runtime.get("primary_invalid_reason") or ""):
        variance_sources.append("review_payload_validity_variance")
    if _as_int(right.get("priority_conflict_count"), 0) != _as_int(left.get("priority_conflict_count"), 0) or _as_int(
        right.get("priority_undetermined_count"), 0
    ) != _as_int(left.get("priority_undetermined_count"), 0):
        variance_sources.append("priority_decision_variance")
    if float(right.get("deterministic_backfill_ratio") or 0.0) != float(left.get("deterministic_backfill_ratio") or 0.0):
        variance_sources.append("reason_coverage_variance")
    if not variance_sources:
        variance_sources.append("no_significant_variance_detected")

    attribution = {
        "left_dominant_deterministic_source": str(l_reason.get("dominant_deterministic_source") or ""),
        "right_dominant_deterministic_source": str(r_reason.get("dominant_deterministic_source") or ""),
        "left_final_source": str(left.get("review_final_source") or ""),
        "right_final_source": str(right.get("review_final_source") or ""),
        "left_reason_source_breakdown": dict(left.get("reason_source_breakdown") or {}),
        "right_reason_source_breakdown": dict(right.get("reason_source_breakdown") or {}),
        "left_primary_invalid_reason": str(l_runtime.get("primary_invalid_reason") or ""),
        "right_primary_invalid_reason": str(r_runtime.get("primary_invalid_reason") or ""),
    }
    return {
        "diff_rows": diff_rows,
        "variance_sources": variance_sources,
        "attribution": attribution,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze stability attribution across generation ids.")
    parser.add_argument("--left", type=int, required=True, help="left generation_id")
    parser.add_argument("--right", type=int, required=True, help="right generation_id")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(ROOT_DIR / "tmp" / "review_diagnostics"),
        help="Output directory path.",
    )
    args = parser.parse_args()

    left = _build_run_snapshot(int(args.left))
    right = _build_run_snapshot(int(args.right))
    diff = _build_diff(left, right)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"compare_generation_{int(args.left)}_{int(args.right)}_stability_attribution.json"
    payload = {
        "left": left,
        "right": right,
        "diff": diff,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[OK] stability attribution generated")
    print(f"  left: {args.left}")
    print(f"  right: {args.right}")
    print(f"  out: {out_path}")
    print(
        f"  left_source: {left.get('diagnostic_source')} ({left.get('diagnostic_depth')})"
    )
    print(
        f"  right_source: {right.get('diagnostic_source')} ({right.get('diagnostic_depth')})"
    )
    if str(left.get("diagnostic_depth") or "").strip().lower() == "limited" or str(
        right.get("diagnostic_depth") or ""
    ).strip().lower() == "limited":
        print(
            "  warning: one or both runs are generated_result fallback (limited); "
            "review/convergence comparisons may be incomplete."
        )
    print("  variance_sources:")
    for item in diff.get("variance_sources") or []:
        print(f"    - {item}")
    print("  diff_rows:")
    for row in diff.get("diff_rows") or []:
        print(
            f"    {row.get('metric')}: left={row.get('left')} right={row.get('right')} delta={row.get('delta')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
