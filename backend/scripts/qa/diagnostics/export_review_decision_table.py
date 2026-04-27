from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db.database import SessionLocal
from core.db.models import LogEntry


@dataclass
class _DiagContext:
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
        "row_count": int(exported_row_count),
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


def _find_context(generation_id: int) -> _DiagContext | None:
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
            if payload.get("kind") != "generation_persisted":
                continue
            persisted_rows.append((row, payload))

        target_row: LogEntry | None = None
        target_payload: dict[str, Any] | None = None
        for row, payload in persisted_rows:
            if int(payload.get("generation_id") or 0) == int(generation_id):
                target_row = row
                target_payload = payload
                break
        if target_row is None or not isinstance(target_payload, dict):
            return None

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

        return _DiagContext(
            generation_id=int(generation_id),
            project_id=project_id,
            request_id=str(target_payload.get("request_id") or "").strip(),
            persisted_log_id=current_id,
            next_persisted_log_id=next_persisted_id,
        )
    finally:
        db.close()
    return None


def _load_review_diags(ctx: _DiagContext) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
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
        summary_payload: dict[str, Any] = {}
        table_payload: dict[str, Any] = {}
        table_rows: list[dict[str, Any]] = []
        for row in rows:
            payload = _parse_gen_diag_payload(row.message or "")
            if not payload:
                continue
            if ctx.request_id:
                payload_request_id = str(payload.get("request_id") or "").strip()
                if payload_request_id and payload_request_id != ctx.request_id:
                    continue
            kind = str(payload.get("kind") or "").strip()
            if kind == "review_decision_summary":
                summary_payload = dict(payload)
            elif kind == "review_decision_table":
                table_payload = dict(payload)
                table_rows = [item for item in (payload.get("rows") or []) if isinstance(item, dict)]
        return summary_payload, table_payload, table_rows
    finally:
        db.close()


def _write_outputs(
    *,
    generation_id: int,
    summary_payload: dict[str, Any],
    table_payload: dict[str, Any],
    table_rows: list[dict[str, Any]],
    out_dir: Path,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"generation_{generation_id}_review_summary.json"
    table_path = out_dir / f"generation_{generation_id}_review_table.csv"
    export_meta = _build_detail_export_meta(
        summary_payload=summary_payload,
        detail_payload=table_payload,
        exported_rows=table_rows,
    )

    summary_obj = {
        "generation_id": int(generation_id),
        "review_decision_summary": summary_payload,
        "candidate_total": int(export_meta.get("candidate_total") or 0),
        "exported_row_count": int(export_meta.get("exported_row_count") or 0),
        "row_count": int(export_meta.get("row_count") or 0),
        "is_truncated": bool(export_meta.get("is_truncated")),
        "truncation_reason": str(export_meta.get("truncation_reason") or ""),
        "source_summary_available": bool(export_meta.get("source_summary_available")),
        "source_detail_available": bool(export_meta.get("source_detail_available")),
        "detail_row_count_total": int(export_meta.get("detail_row_count_total") or 0),
        "detail_rows_scope": str(export_meta.get("detail_rows_scope") or ""),
    }
    summary_path.write_text(json.dumps(summary_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "candidate_index",
        "case_id",
        "description",
        "test_module",
        "model_priority_current",
        "selected_by_review_llm",
        "selected_by_review_must_keep",
        "selected_by_review_constraints",
        "selected_by_review_gate",
        "retained_final",
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
        "review_constraint_reason",
        "bucket",
        "rule_keys",
        "adds_rule",
        "adds_bucket",
        "high_signal",
        "has_coverage_value",
        "retained_reason",
        "rerank_rank",
        "focus_score",
        "covered_rule_ids",
        "missing_rule_hits",
        "core_rule_hits",
        "coverage_gain_score",
        "signature",
    ]
    with table_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in table_rows:
            line = dict(row)
            for key in ("rule_keys", "covered_rule_ids", "missing_rule_hits", "core_rule_hits"):
                value = line.get(key)
                if isinstance(value, list):
                    line[key] = json.dumps(value, ensure_ascii=False)
            if isinstance(line.get("review_llm_drop_reason_evidence"), dict):
                line["review_llm_drop_reason_evidence"] = json.dumps(
                    line.get("review_llm_drop_reason_evidence"), ensure_ascii=False
                )
            writer.writerow({k: line.get(k, "") for k in fieldnames})
    return summary_path, table_path


def main() -> int:
    parser = argparse.ArgumentParser(description="导出指定 generation_id 的 review 保留/淘汰对照表。")
    parser.add_argument("--generation-id", type=int, required=True, help="test_generations.id")
    parser.add_argument(
        "--out-dir",
        type=str,
        default="tmp/review_diagnostics",
        help="导出目录（相对于 backend 工作目录）。",
    )
    args = parser.parse_args()

    ctx = _find_context(int(args.generation_id))
    if not ctx:
        print(f"[ERROR] 未找到 generation_id={args.generation_id} 对应的 generation_persisted 诊断日志。")
        return 2

    summary_payload, table_payload, table_rows = _load_review_diags(ctx)
    if not summary_payload and not table_payload and not table_rows:
        print(
            f"[ERROR] generation_id={args.generation_id} 未找到 review 决策诊断数据。"
            "请先运行带 review_decision 观测字段的新版本。"
        )
        return 3

    summary_path, table_path = _write_outputs(
        generation_id=int(args.generation_id),
        summary_payload=summary_payload,
        table_payload=table_payload,
        table_rows=table_rows,
        out_dir=Path(args.out_dir),
    )
    export_meta = _build_detail_export_meta(
        summary_payload=summary_payload,
        detail_payload=table_payload,
        exported_rows=table_rows,
    )
    print("[OK] review 诊断导出完成")
    print(f"  generation_id: {args.generation_id}")
    print(f"  request_id: {ctx.request_id or '(none)'}")
    print(f"  summary: {summary_path}")
    print(f"  table: {table_path}")
    print(f"  candidate_total: {export_meta.get('candidate_total')}")
    print(f"  exported_row_count: {export_meta.get('exported_row_count')}")
    print(f"  row_count: {export_meta.get('row_count')}")
    print(f"  is_truncated: {export_meta.get('is_truncated')}")
    print(f"  truncation_reason: {export_meta.get('truncation_reason') or '-'}")
    print(f"  source_summary_available: {export_meta.get('source_summary_available')}")
    print(f"  source_detail_available: {export_meta.get('source_detail_available')}")
    if summary_payload:
        print("  drop_summary:")
        for key in (
            "candidate_total",
            "retained_total",
            "dropped_total",
            "drop_by_review_llm_count",
            "drop_by_review_gate_count",
            "drop_by_pre_gate_dedup_count",
            "drop_by_post_review_dedup_count",
            "drop_no_new_signal_count",
            "drop_rule_cap_count",
            "dropped_model_priority_p0_p1_count",
            "dropped_core_rule_hit_count",
            "dropped_missing_rule_hit_count",
        ):
            if key in summary_payload:
                print(f"    {key}: {summary_payload.get(key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
