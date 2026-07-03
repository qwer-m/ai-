from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


EMPTY_GENERATED_RESULT_MESSAGE = "生成完成但最终测试用例为空"


@dataclass(frozen=True)
class PostPersistGenerationDiagnosticPayloads:
    pre_judge_payloads: list[dict[str, Any]]
    judge_table_payload: dict[str, Any] | None


def _generation_mode(normalized_generation_mode: str | None, multi_pass: bool) -> str:
    return normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass")


def _final_case_count(result: Any, final_case_count: Any) -> int:
    return int(final_case_count if isinstance(result, list) else 0)


def _dict_rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in (rows or []) if isinstance(item, dict)]


def _build_default_review_decision_summary(
    *,
    result: Any,
    candidate_total_before_judge: Any,
    final_case_count: Any,
    empty_result_guard_triggered: Any,
    empty_result_stage: Any,
) -> dict[str, Any]:
    final_count = _final_case_count(result, final_case_count)
    candidate_total = int(candidate_total_before_judge or 0)
    return {
        "candidate_total": candidate_total,
        "retained_total": final_count,
        "dropped_total": max(0, candidate_total - final_count),
        "review_input_size": candidate_total,
        "review_output_size": final_count,
        "review_decision_summary_available": False,
        "review_skipped_reason": "review_postprocess_not_executed_in_generate_tests_json",
        "reason_source_breakdown": {"primary": 0, "fallback": 0, "backfill": 0},
        "priority_decision_state_breakdown": {
            "decided": 0,
            "conflict": 0,
            "undetermined": 0,
            "optional": 0,
            "invalid": 0,
        },
        "priority_final_breakdown": {"P0": 0, "P1": 0, "P2": 0, "null": 0},
        "legacy_priority_breakdown": {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0},
        "priority_conflict_count": 0,
        "priority_undetermined_count": 0,
        "priority_optional_count": 0,
        "priority_invalid_count": 0,
        "needs_priority_review": False,
        "candidate_primary": candidate_total,
        "candidate_gap": 0,
        "final_case_count": final_count,
        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
        "empty_result_stage": str(empty_result_stage or ""),
    }


def _build_default_convergence(
    *,
    result: Any,
    candidate_total_before_judge: Any,
    final_case_count: Any,
    expected_count: Any,
    empty_result_guard_triggered: Any,
    empty_result_stage: Any,
) -> dict[str, Any]:
    final_count = _final_case_count(result, final_case_count)
    candidate_total = int(candidate_total_before_judge or 0)
    return {
        "primary_count": candidate_total,
        "gap_count": 0,
        "review_count": candidate_total,
        "candidate_count_before_review": candidate_total,
        "review_selected_count": final_count,
        "final_count": final_count,
        "expected_count": int(expected_count or 0),
        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
        "empty_result_stage": str(empty_result_stage or ""),
    }


def _build_default_generation_summary(
    *,
    result: Any,
    candidate_total_before_judge: Any,
    final_case_count: Any,
    expected_count: Any,
    empty_result_guard_triggered: Any,
    empty_result_stage: Any,
) -> dict[str, Any]:
    final_count = _final_case_count(result, final_case_count)
    payload = {
        "status": "failed_empty_result" if bool(empty_result_guard_triggered) else "completed",
        "final_status": "empty_result_failed" if bool(empty_result_guard_triggered) else "success",
        "final_count": final_count,
        "expected_count": int(expected_count or 0),
        "candidate_total": int(candidate_total_before_judge or 0),
        "review_input_size": int(candidate_total_before_judge or 0),
        "review_output_size": final_count,
        "review_decision_summary_available": False,
        "review_skipped_reason": "review_postprocess_not_executed_in_generate_tests_json",
        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
        "empty_result_stage": str(empty_result_stage or ""),
    }
    if bool(empty_result_guard_triggered):
        payload["error_code"] = "EMPTY_GENERATED_RESULT"
        payload["error_message"] = EMPTY_GENERATED_RESULT_MESSAGE
    return payload


def _normalize_judge_rows(
    *,
    judge_decision_table_payload: Iterable[Any],
    generation_id: int,
    request_id: str,
) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in judge_decision_table_payload or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("judge_status") or row.get("status") or "").strip().upper()
        signals_raw = row.get("signals") if isinstance(row.get("signals"), dict) else {}
        before_case = row.get("before_case_snapshot")
        if not isinstance(before_case, dict):
            before_case = {}
        after_case = row.get("after_case_snapshot")
        if not isinstance(after_case, dict):
            after_case = {}
        normalized_rows.append(
            {
                "generation_id": int(generation_id),
                "request_id": request_id,
                "case_id": str(row.get("case_id") or "").strip(),
                "judge_status": status,
                "reject_reason": str(row.get("reject_reason") or "").strip(),
                "pending_reason": str(row.get("pending_reason") or "").strip(),
                "signals": {
                    "violates_confirmed_fact": bool(
                        signals_raw.get("violates_confirmed_fact", row.get("violates_confirmed_fact"))
                    ),
                    "missing_core_flow": bool(signals_raw.get("missing_core_flow", row.get("missing_core_flow"))),
                    "missing_reuse_risk": bool(signals_raw.get("missing_reuse_risk", row.get("missing_reuse_risk"))),
                    "contains_pending_logic": bool(
                        signals_raw.get("contains_pending_logic", row.get("contains_pending_logic"))
                    ),
                    "confirmed_fact_hits": list(
                        signals_raw.get("confirmed_fact_hits") or row.get("confirmed_fact_hits") or []
                    ),
                    "confirmed_fact_violations": list(
                        signals_raw.get("confirmed_fact_violations") or row.get("confirmed_fact_violations") or []
                    ),
                    "reuse_risk_hits": list(signals_raw.get("reuse_risk_hits") or row.get("reuse_risk_hits") or []),
                    "pending_hits": list(signals_raw.get("pending_hits") or row.get("pending_hits") or []),
                    "vague_or_unconfirmed_hits": list(
                        signals_raw.get("vague_or_unconfirmed_hits") or row.get("vague_or_unconfirmed_hits") or []
                    ),
                },
                "before_case_snapshot": dict(before_case),
                "after_case_snapshot": dict(after_case),
            }
        )
    return normalized_rows


def build_post_persist_generation_diagnostic_payloads(
    *,
    project_id: int,
    request_id: str,
    generation_id: int,
    normalized_generation_mode: str | None,
    multi_pass: bool,
    resolved_current_biz: str,
    doc_type: str,
    compress: bool,
    expected_count: int,
    result: Any,
    generated_count: int,
    candidate_total_before_judge: int,
    final_case_count: int,
    empty_result_guard_triggered: bool,
    empty_result_stage: str,
    gen_diag_payload: Mapping[str, Any] | None,
    compression_event_payload: Mapping[str, Any] | None,
    review_decision_summary_payload: Mapping[str, Any] | None,
    review_decision_table_payload: Iterable[Any] | None,
    convergence_payload: Mapping[str, Any] | None,
    generation_summary_payload: Mapping[str, Any] | None,
    judge_summary_payload: Mapping[str, Any] | None,
    judge_decision_table_payload: Iterable[Any] | None,
) -> PostPersistGenerationDiagnosticPayloads:
    mode = _generation_mode(normalized_generation_mode, multi_pass)
    generation_id_int = int(generation_id)

    persisted_payload = {
        "kind": "generation_persisted",
        "generation_id": generation_id_int,
        "project_id": int(project_id),
        "request_id": request_id,
    }
    generation_mode_payload = {
        "kind": "generation_mode",
        "mode": mode,
        "biz_keys": [resolved_current_biz],
        "current_biz_key": resolved_current_biz,
        "multi_pass": bool(multi_pass),
        "request_id": request_id,
        "generation_id": generation_id_int,
    }

    post_persist_gen_diag_payload = dict(gen_diag_payload or {})
    if not post_persist_gen_diag_payload:
        post_persist_gen_diag_payload = {
            "kind": "gen_diag",
            "mode": "json",
            "doc_type": doc_type,
            "compress": bool(compress),
            "expected_count": int(expected_count or 0),
            "generated_count": int(generated_count or 0),
            "request_id": request_id,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
    post_persist_gen_diag_payload["generation_id"] = generation_id_int

    post_persist_compression_payload = dict(compression_event_payload or {})
    if not post_persist_compression_payload:
        post_persist_compression_payload = {
            "kind": "generation_context_compression",
            "request_id": request_id,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
            "snapshot_id": "",
            "corpus_hash": "",
            "retrieval_hash": "",
        }
    post_persist_compression_payload["generation_id"] = generation_id_int

    summary_payload = dict(review_decision_summary_payload or {})
    if not summary_payload:
        summary_payload = _build_default_review_decision_summary(
            result=result,
            candidate_total_before_judge=candidate_total_before_judge,
            final_case_count=final_case_count,
            empty_result_guard_triggered=empty_result_guard_triggered,
            empty_result_stage=empty_result_stage,
        )
    review_summary_diag_payload = {
        "kind": "review_decision_summary",
        **summary_payload,
        "request_id": request_id,
        "generation_id": generation_id_int,
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }

    pre_judge_payloads = [
        persisted_payload,
        generation_mode_payload,
        post_persist_gen_diag_payload,
        post_persist_compression_payload,
        review_summary_diag_payload,
    ]

    if review_decision_table_payload:
        review_rows = _dict_rows(review_decision_table_payload)
        pre_judge_payloads.append(
            {
                "kind": "review_decision_table",
                "generation_id": generation_id_int,
                "request_id": request_id,
                "rows": review_rows,
                "row_count": int(len(review_rows)),
                "row_count_total": int(len(review_rows)),
                "rows_scope": "all",
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )

    normalized_convergence_payload = dict(convergence_payload or {})
    if not normalized_convergence_payload:
        normalized_convergence_payload = _build_default_convergence(
            result=result,
            candidate_total_before_judge=candidate_total_before_judge,
            final_case_count=final_case_count,
            expected_count=expected_count,
            empty_result_guard_triggered=empty_result_guard_triggered,
            empty_result_stage=empty_result_stage,
        )
    pre_judge_payloads.append(
        {
            "kind": "generation_convergence",
            **normalized_convergence_payload,
            "request_id": request_id,
            "generation_id": generation_id_int,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
    )

    normalized_generation_summary_payload = dict(generation_summary_payload or {})
    if not normalized_generation_summary_payload:
        normalized_generation_summary_payload = _build_default_generation_summary(
            result=result,
            candidate_total_before_judge=candidate_total_before_judge,
            final_case_count=final_case_count,
            expected_count=expected_count,
            empty_result_guard_triggered=empty_result_guard_triggered,
            empty_result_stage=empty_result_stage,
        )
    pre_judge_payloads.append(
        {
            "kind": "generation_summary",
            **normalized_generation_summary_payload,
            "request_id": request_id,
            "generation_id": generation_id_int,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
    )

    normalized_judge_rows = _normalize_judge_rows(
        judge_decision_table_payload=judge_decision_table_payload or [],
        generation_id=generation_id_int,
        request_id=request_id,
    )
    reject_pending_rows = [
        row
        for row in normalized_judge_rows
        if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
    ]
    judge_summary = dict(judge_summary_payload or {})
    judge_table_payload = None
    if judge_summary or normalized_judge_rows:
        judge_table_payload = {
            "kind": "judge_decision_table",
            "generation_id": generation_id_int,
            "request_id": request_id,
            "rows": reject_pending_rows or normalized_judge_rows,
            "row_count": int(len(reject_pending_rows or normalized_judge_rows)),
            "row_count_total": int(len(normalized_judge_rows)),
            "row_count_reject_pending": int(len(reject_pending_rows)),
            "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
            "row_evidence_incomplete": bool(
                int(judge_summary.get("rejected_out_count") or 0)
                + int(judge_summary.get("pending_out_count") or 0) > 0
                and len(reject_pending_rows) == 0
            ),
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }

    return PostPersistGenerationDiagnosticPayloads(
        pre_judge_payloads=pre_judge_payloads,
        judge_table_payload=judge_table_payload,
    )
