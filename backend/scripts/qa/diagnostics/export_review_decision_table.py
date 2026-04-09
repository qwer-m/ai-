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
        for row in rows:
            payload = _parse_gen_diag_payload(row.message or "")
            if not payload:
                continue
            if payload.get("kind") != "generation_persisted":
                continue
            if int(payload.get("generation_id") or 0) != int(generation_id):
                continue
            project_id = int(payload.get("project_id") or row.project_id or 0)
            if project_id <= 0:
                continue
            return _DiagContext(
                generation_id=int(generation_id),
                project_id=project_id,
                request_id=str(payload.get("request_id") or "").strip(),
                persisted_log_id=int(row.id or 0),
            )
    finally:
        db.close()
    return None


def _load_review_diags(ctx: _DiagContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = SessionLocal()
    try:
        query = (
            db.query(LogEntry)
            .filter(
                LogEntry.project_id == ctx.project_id,
                LogEntry.id <= ctx.persisted_log_id,
                LogEntry.message.like("%GEN_DIAG:%"),
            )
            .order_by(LogEntry.id.desc())
        )
        rows = query.all()
        summary_payload: dict[str, Any] = {}
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
            if kind == "review_decision_summary" and not summary_payload:
                summary_payload = dict(payload)
            elif kind == "review_decision_table" and not table_rows:
                table_rows = [item for item in (payload.get("rows") or []) if isinstance(item, dict)]
            if summary_payload and table_rows:
                break
        return summary_payload, table_rows
    finally:
        db.close()


def _write_outputs(
    *,
    generation_id: int,
    summary_payload: dict[str, Any],
    table_rows: list[dict[str, Any]],
    out_dir: Path,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"generation_{generation_id}_review_summary.json"
    table_path = out_dir / f"generation_{generation_id}_review_table.csv"

    summary_obj = {
        "generation_id": int(generation_id),
        "review_decision_summary": summary_payload,
        "row_count": int(len(table_rows)),
    }
    summary_path.write_text(json.dumps(summary_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "candidate_index",
        "case_id",
        "description",
        "test_module",
        "model_priority_current",
        "selected_by_review_llm",
        "selected_by_review_gate",
        "retained_final",
        "dropped_stage",
        "dropped_reason",
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

    summary_payload, table_rows = _load_review_diags(ctx)
    if not summary_payload and not table_rows:
        print(
            f"[ERROR] generation_id={args.generation_id} 未找到 review 决策诊断数据。"
            "请先运行带 review_decision 观测字段的新版本。"
        )
        return 3

    summary_path, table_path = _write_outputs(
        generation_id=int(args.generation_id),
        summary_payload=summary_payload,
        table_rows=table_rows,
        out_dir=Path(args.out_dir),
    )
    print("[OK] review 诊断导出完成")
    print(f"  generation_id: {args.generation_id}")
    print(f"  request_id: {ctx.request_id or '(none)'}")
    print(f"  summary: {summary_path}")
    print(f"  table: {table_path}")
    print(f"  row_count: {len(table_rows)}")
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
