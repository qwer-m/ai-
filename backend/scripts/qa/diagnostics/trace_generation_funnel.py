from __future__ import annotations

import argparse
import csv
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
from core.db.models import LogEntry


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


def _build_detail_export_meta(
    *,
    summary_payload: dict[str, Any],
    detail_payload: dict[str, Any],
    exported_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = dict(summary_payload or {})
    detail = dict(detail_payload or {})
    exported_row_count = int(len([item for item in exported_rows if isinstance(item, dict)]))

    has_summary_candidate = "candidate_total" in summary
    summary_candidate_total = _as_int(summary.get("candidate_total"), 0)
    detail_row_count_total = _as_int(detail.get("row_count_total"), _as_int(detail.get("row_count"), 0))
    rows_scope = str(detail.get("rows_scope") or "").strip().lower()

    source_summary_available = bool(has_summary_candidate)
    source_detail_available = bool(detail) or detail_row_count_total > 0 or exported_row_count > 0

    if source_summary_available:
        candidate_total = int(summary_candidate_total)
    elif detail_row_count_total > 0:
        candidate_total = int(detail_row_count_total)
    else:
        candidate_total = int(exported_row_count)

    is_truncated = False
    truncation_reason = ""
    if rows_scope == "sampled_due_to_size":
        is_truncated = True
        truncation_reason = "detail_limit"
    elif rows_scope == "summary_only_due_to_size":
        is_truncated = True
        truncation_reason = "message_truncated"
    elif detail_row_count_total > exported_row_count:
        is_truncated = True
        truncation_reason = "detail_limit"
    elif source_summary_available and not source_detail_available:
        is_truncated = True
        truncation_reason = "no_detail_rows"

    return {
        "candidate_total": int(candidate_total),
        "exported_row_count": int(exported_row_count),
        "is_truncated": bool(is_truncated),
        "truncation_reason": str(truncation_reason),
        "source_summary_available": bool(source_summary_available),
        "source_detail_available": bool(source_detail_available),
        "detail_row_count_total": int(detail_row_count_total),
        "detail_rows_scope": str(rows_scope),
    }


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
                # Payloads without request_id (e.g. generation_stage / gen_diag) should still be kept
                # as long as they are within this persisted-log window.
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


def _write_dropped_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "candidate_index",
        "case_id",
        "description",
        "test_module",
        "model_priority_current",
        "dropped_stage",
        "dropped_reason",
        "review_llm_drop_reason_raw",
        "review_llm_drop_reason",
        "review_llm_drop_reason_source",
        "review_llm_drop_reason_evidence",
        "has_positive_evidence",
        "has_coverage_signal",
        "has_high_signal",
        "has_competition_signal",
        "bucket",
        "has_coverage_value",
        "high_signal",
        "covered_rule_ids",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            line = dict(row)
            if isinstance(line.get("covered_rule_ids"), list):
                line["covered_rule_ids"] = json.dumps(line.get("covered_rule_ids"), ensure_ascii=False)
            if isinstance(line.get("review_llm_drop_reason_evidence"), dict):
                line["review_llm_drop_reason_evidence"] = json.dumps(
                    line.get("review_llm_drop_reason_evidence"), ensure_ascii=False
                )
            writer.writerow({key: line.get(key, "") for key in fieldnames})


def _coerce_case_payload(row: dict[str, Any]) -> dict[str, Any]:
    before_snapshot = row.get("before_case_snapshot")
    if isinstance(before_snapshot, dict) and before_snapshot:
        return before_snapshot
    after_snapshot = row.get("after_case_snapshot")
    if isinstance(after_snapshot, dict) and after_snapshot:
        return after_snapshot
    before_case = row.get("before_case")
    if isinstance(before_case, dict) and before_case:
        return before_case
    after_case = row.get("after_case")
    if isinstance(after_case, dict) and after_case:
        return after_case
    return {}


def _coerce_bool(value: Any) -> bool:
    return bool(value is True)


def _coerce_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _build_judge_export_rows(judge_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    export_rows: list[dict[str, Any]] = []
    for row in judge_rows:
        case_payload = _coerce_case_payload(row)
        signals = row.get("signals") if isinstance(row.get("signals"), dict) else {}
        confirmed_hits = row.get("confirmed_fact_hits")
        if not isinstance(confirmed_hits, list):
            confirmed_hits = signals.get("confirmed_fact_hits")
        pending_hits = row.get("pending_hits")
        if not isinstance(pending_hits, list):
            pending_hits = signals.get("pending_hits")
        reuse_risk_hits = row.get("reuse_risk_hits")
        if not isinstance(reuse_risk_hits, list):
            reuse_risk_hits = signals.get("reuse_risk_hits")
        status = _coerce_str(row.get("status") or row.get("judge_status"), default="-")
        export_rows.append(
            {
                "case_id": _coerce_str(row.get("case_id"), default="-"),
                "module": _coerce_str(case_payload.get("test_module"), default="-"),
                "priority": _coerce_str(case_payload.get("model_priority_current") or case_payload.get("priority"), default="-"),
                "scene": _coerce_str(case_payload.get("description"), default="-"),
                "judge_status": status,
                "reject_reason": _coerce_str(row.get("reject_reason") or row.get("pending_reason"), default="-"),
                "hit_confirmed_fact": _coerce_bool(
                    row.get("violates_confirmed_fact")
                    if row.get("violates_confirmed_fact") is not None
                    else signals.get("violates_confirmed_fact")
                )
                or (isinstance(confirmed_hits, list) and len(confirmed_hits) > 0),
                "hit_pending": _coerce_bool(
                    row.get("contains_pending_logic")
                    if row.get("contains_pending_logic") is not None
                    else signals.get("contains_pending_logic")
                )
                or (isinstance(pending_hits, list) and len(pending_hits) > 0),
                "hit_reuse_risk": isinstance(reuse_risk_hits, list) and len(reuse_risk_hits) > 0,
            }
        )
    return export_rows


def _write_judge_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id",
        "module",
        "priority",
        "scene",
        "judge_status",
        "reject_reason",
        "hit_confirmed_fact",
        "hit_pending",
        "hit_reuse_risk",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace generation funnel (review + judge + final) by generation_id.")
    parser.add_argument("--generation-id", type=int, required=True, help="test_generations.id")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(ROOT_DIR / "tmp" / "review_diagnostics"),
        help="Output directory path.",
    )
    args = parser.parse_args()

    ctx = _find_context(int(args.generation_id))
    if not ctx:
        print(f"[ERROR] generation_id={args.generation_id} context not found.")
        return 2

    payloads = _load_run_payloads(ctx)
    if not payloads:
        print(f"[ERROR] generation_id={args.generation_id} has no GEN_DIAG payloads in scoped window.")
        return 3

    stage_map = _build_stage_map(payloads)
    gen_diag = _pick_latest_payload(payloads, "gen_diag")
    convergence = _pick_latest_payload(payloads, "generation_convergence")
    review_summary = _pick_latest_payload(payloads, "review_decision_summary")
    judge_summary = _pick_latest_payload(payloads, "judge_summary")
    judge_table_payload = _pick_latest_payload(payloads, "judge_decision_table")
    generation_summary = _pick_latest_payload(payloads, "generation_summary")
    review_table_payload = _pick_latest_payload(payloads, "review_decision_table")

    rows = [item for item in (review_table_payload.get("rows") or []) if isinstance(item, dict)]
    review_export_meta = _build_detail_export_meta(
        summary_payload=review_summary,
        detail_payload=review_table_payload,
        exported_rows=rows,
    )
    dropped_rows = [item for item in rows if not bool(item.get("retained_final"))]
    judge_rows_raw = [item for item in (judge_table_payload.get("rows") or []) if isinstance(item, dict)]
    judge_rows = _build_judge_export_rows(judge_rows_raw)
    judge_rejected_rows = [item for item in judge_rows if str(item.get("judge_status") or "").upper() in {"REJECT", "PENDING"}]

    dropped_stage_counter = Counter(str(item.get("dropped_stage") or "") for item in dropped_rows)
    dropped_reason_counter = Counter(str(item.get("dropped_reason") or "") for item in dropped_rows)
    dropped_module_counter = Counter(str(item.get("test_module") or "") for item in dropped_rows)
    dropped_priority_counter = Counter(str(item.get("model_priority_current") or "") for item in dropped_rows)
    judge_status_counter = Counter(str(item.get("judge_status") or "") for item in judge_rows)
    judge_reason_counter = Counter(str(item.get("reject_reason") or "") for item in judge_rejected_rows)
    judge_module_counter = Counter(str(item.get("module") or "") for item in judge_rejected_rows)
    judge_priority_counter = Counter(str(item.get("priority") or "") for item in judge_rejected_rows)

    candidate_total = int(
        review_export_meta.get("candidate_total")
        or _as_int(convergence.get("candidate_count_before_review"), 0)
        or 0
    )
    review_selected_count = int(
        review_summary.get("retained_total")
        or convergence.get("review_selected_count")
        or stage_map.get("review")
        or 0
    )
    post_review_drop = int(review_summary.get("drop_by_post_review_dedup_count") or 0)
    judge_rejected_or_pending = int(judge_summary.get("rejected_out_count") or 0) + int(
        judge_summary.get("pending_out_count") or 0
    )
    post_review_non_judge = max(0, post_review_drop - judge_rejected_or_pending)
    final_count = int(
        generation_summary.get("final_count")
        or convergence.get("final_count")
        or gen_diag.get("generated_count")
        or 0
    )

    funnel = {
        "candidate_total": candidate_total,
        "review_selected_count": review_selected_count,
        "drop_by_review_llm_count": int(review_summary.get("drop_by_review_llm_count") or 0),
        "drop_by_review_gate_count": int(review_summary.get("drop_by_review_gate_count") or 0),
        "drop_by_pre_gate_dedup_count": int(review_summary.get("drop_by_pre_gate_dedup_count") or 0),
        "drop_by_post_review_dedup_count": post_review_drop,
        "judge_rejected_or_pending_count": judge_rejected_or_pending,
        "post_review_non_judge_drop_count": int(post_review_non_judge),
        "final_count": final_count,
        "judge_pass_out_count": int(judge_summary.get("confirmed_pass_out_count") or 0),
        "judge_repaired_pass_out_count": int(judge_summary.get("repaired_pass_out_count") or 0),
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"generation_{ctx.generation_id}_funnel_report.json"
    dropped_csv_path = out_dir / f"generation_{ctx.generation_id}_dropped_rows.csv"
    judge_csv_path = out_dir / f"generation_{ctx.generation_id}_judge_rows.csv"

    report_obj = {
        "generation_id": int(ctx.generation_id),
        "project_id": int(ctx.project_id),
        "request_id": ctx.request_id,
        "persisted_log_id": int(ctx.persisted_log_id),
        "next_persisted_log_id": ctx.next_persisted_log_id,
        "stage_counts": stage_map,
        "gen_diag": gen_diag,
        "generation_convergence": convergence,
        "review_decision_summary": review_summary,
        "judge_summary": judge_summary,
        "generation_summary": generation_summary,
        "review_detail_export_meta": review_export_meta,
        "funnel": funnel,
        "dropped_breakdown": {
            "total": int(review_summary.get("dropped_total") or len(dropped_rows)),
            "exported_row_count": int(len(dropped_rows)),
            "is_truncated": bool(review_export_meta.get("is_truncated")),
            "truncation_reason": str(review_export_meta.get("truncation_reason") or ""),
            "source_summary_available": bool(review_export_meta.get("source_summary_available")),
            "source_detail_available": bool(review_export_meta.get("source_detail_available")),
            "by_stage": dict(dropped_stage_counter),
            "by_reason": dict(dropped_reason_counter),
            "by_priority": dict(dropped_priority_counter),
            "by_module_top10": dropped_module_counter.most_common(10),
            "dropped_case_ids": [str(item.get("case_id") or "") for item in dropped_rows],
        },
        "judge_breakdown": {
            "total": int(len(judge_rows)),
            "rejected_or_pending_total": int(len(judge_rejected_rows)),
            "by_status": dict(judge_status_counter),
            "by_reason": dict(judge_reason_counter),
            "by_priority": dict(judge_priority_counter),
            "by_module_top10": judge_module_counter.most_common(10),
            "rejected_or_pending_case_ids": [str(item.get("case_id") or "") for item in judge_rejected_rows],
        },
    }
    report_path.write_text(json.dumps(report_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_dropped_rows_csv(dropped_csv_path, dropped_rows)
    _write_judge_rows_csv(judge_csv_path, judge_rows)

    print("[OK] generation funnel traced")
    print(f"  generation_id: {ctx.generation_id}")
    print(f"  project_id: {ctx.project_id}")
    print(f"  request_id: {ctx.request_id or '(none)'}")
    print(f"  report: {report_path}")
    print(f"  dropped_rows_csv: {dropped_csv_path}")
    print(f"  judge_rows_csv: {judge_csv_path}")
    print("  funnel:")
    for key in (
        "candidate_total",
        "review_selected_count",
        "drop_by_review_llm_count",
        "drop_by_review_gate_count",
        "drop_by_pre_gate_dedup_count",
        "drop_by_post_review_dedup_count",
        "judge_rejected_or_pending_count",
        "post_review_non_judge_drop_count",
        "final_count",
    ):
        print(f"    {key}: {funnel.get(key)}")
    print("  review_detail_export_meta:")
    for key in (
        "exported_row_count",
        "is_truncated",
        "truncation_reason",
        "source_summary_available",
        "source_detail_available",
    ):
        print(f"    {key}: {review_export_meta.get(key)}")
    print("  judge_breakdown:")
    print(f"    total: {len(judge_rows)}")
    print(f"    rejected_or_pending_total: {len(judge_rejected_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
