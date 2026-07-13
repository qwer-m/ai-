from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db.database import SessionLocal
from core.db.models import TestGeneration
from scripts.qa.diagnostics.analyze_generation_stability import (
    _build_diff,
    _build_run_snapshot,
)
from scripts.qa.diagnostics.export_review_decision_table import (
    _find_context as _export_find_context,
    _load_review_diags as _export_load_review_diags,
    _write_outputs as _export_write_outputs,
)
from scripts.qa.diagnostics.validate_fallback_reason_coverage import (
    _build_output_payload as _validate_build_output_payload,
    _load_summary_by_generation_id as _validate_load_summary_by_generation_id,
)


@dataclass
class AcceptanceConfig:
    max_runs: int = 6
    max_no_fallback_streak: int = 3
    low_coverage_threshold: float = 0.05
    max_low_coverage_streak: int = 3


class GenerationRunner(Protocol):
    def trigger_once(self) -> int:
        """Trigger one real run and return generation_id."""


class DiagnosticsCollector(Protocol):
    def collect(self, *, generation_id: int, previous_generation_id: int | None, out_dir: Path) -> dict[str, Any]:
        """Collect run diagnostics."""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _load_requirement_text(*, requirement_text: str, requirement_file: str) -> str:
    text = str(requirement_text or "").strip()
    if text:
        return text
    file_path = str(requirement_file or "").strip()
    if not file_path:
        raise ValueError("requirement_text or requirement_file is required")
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"requirement_file not found: {path}")
    return path.read_text(encoding="utf-8")


def _query_latest_generation_id(project_id: int) -> int:
    db = SessionLocal()
    try:
        row = (
            db.query(TestGeneration)
            .filter(TestGeneration.project_id == int(project_id))
            .order_by(TestGeneration.id.desc())
            .first()
        )
        return int(getattr(row, "id", 0) or 0)
    finally:
        db.close()


def _wait_for_new_generation_id(*, project_id: int, after_generation_id: int, timeout_sec: int, poll_interval_sec: float) -> int:
    start = time.time()
    while (time.time() - start) <= float(timeout_sec):
        db = SessionLocal()
        try:
            row = (
                db.query(TestGeneration)
                .filter(
                    TestGeneration.project_id == int(project_id),
                    TestGeneration.id > int(after_generation_id),
                )
                .order_by(TestGeneration.id.asc())
                .first()
            )
            if row is not None:
                return int(getattr(row, "id", 0) or 0)
        finally:
            db.close()
        time.sleep(max(0.2, float(poll_interval_sec)))
    raise TimeoutError(
        f"no new generation_id found within {timeout_sec}s (project_id={project_id}, after={after_generation_id})"
    )


def _http_post_json(*, url: str, payload: dict[str, Any], token: str = "", timeout_sec: int = 1200) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url=url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if str(token or "").strip():
        request.add_header("Authorization", f"Bearer {str(token).strip()}")
    try:
        with urllib.request.urlopen(request, timeout=int(timeout_sec)) as response:
            raw = response.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except Exception:
                parsed = {"raw": raw}
            return {"status_code": int(getattr(response, "status", 200) or 200), "payload": parsed, "raw": raw}
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body_text = ""
        if int(getattr(exc, "code", 0) or 0) == 401:
            raise RuntimeError(
                "HTTP 401 from generation endpoint: Not authenticated. "
                "Pass --token or set TG_API_TOKEN."
            ) from exc
        raise RuntimeError(
            f"HTTP {int(getattr(exc, 'code', 0) or 0)} from generation endpoint: {body_text or str(exc)}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"generation HTTP request failed: {exc}") from exc


@dataclass
class ApiGenerationRunner:
    base_url: str
    token: str
    project_id: int
    requirement: str
    expected_count: int = 50
    compress: bool = False
    multi_pass: bool = True
    generation_mode: str = "multi_pass"
    enable_sample_pool_feedback: bool = True
    timeout_sec: int = 1200
    poll_timeout_sec: int = 300
    poll_interval_sec: float = 2.0

    def trigger_once(self) -> int:
        before_id = _query_latest_generation_id(int(self.project_id))
        endpoint = f"{str(self.base_url).rstrip('/')}/api/generate-tests"
        payload = {
            "requirement": str(self.requirement or ""),
            "project_id": int(self.project_id),
            "compress": bool(self.compress),
            "expected_count": int(self.expected_count),
            "enable_sample_pool_feedback": bool(self.enable_sample_pool_feedback),
            "batch_index": 0,
            "batch_size": max(20, int(self.expected_count)),
            "current_biz_key": "",
            "only_current_biz": False,
            "multi_pass": bool(self.multi_pass),
            "generation_mode": str(self.generation_mode or ""),
        }
        _http_post_json(
            url=endpoint,
            payload=payload,
            token=self.token,
            timeout_sec=int(self.timeout_sec),
        )
        return _wait_for_new_generation_id(
            project_id=int(self.project_id),
            after_generation_id=int(before_id),
            timeout_sec=int(self.poll_timeout_sec),
            poll_interval_sec=float(self.poll_interval_sec),
        )


@dataclass
class RealDiagnosticsCollector:
    summary_ready_timeout_sec: int = 180
    summary_ready_poll_interval_sec: float = 2.0

    def _wait_for_review_summary(self, generation_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        start = time.time()
        last_error: Exception | None = None
        while (time.time() - start) <= float(self.summary_ready_timeout_sec):
            try:
                return _validate_load_summary_by_generation_id(int(generation_id))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            time.sleep(max(0.2, float(self.summary_ready_poll_interval_sec)))
        raise RuntimeError(
            f"review_decision_summary not ready for generation_id={generation_id}: {last_error}"
        ) from last_error

    def collect(self, *, generation_id: int, previous_generation_id: int | None, out_dir: Path) -> dict[str, Any]:
        review_summary, meta = self._wait_for_review_summary(int(generation_id))
        validate_payload = _validate_build_output_payload(
            mode="generation_id",
            meta=meta,
            review_summary=review_summary,
        )
        validate_metrics = dict(validate_payload.get("metrics") or {})
        validate_acceptance = dict(validate_payload.get("acceptance") or {})

        run_snapshot = _build_run_snapshot(int(generation_id))
        reason_source_breakdown = dict(run_snapshot.get("reason_source_breakdown") or {})
        review_runtime = dict(run_snapshot.get("review_runtime") or {})
        candidate_by_stage = dict(run_snapshot.get("candidate_by_stage") or {})

        diff_payload: dict[str, Any] = {}
        if previous_generation_id is not None and int(previous_generation_id) > 0:
            try:
                previous_snapshot = _build_run_snapshot(int(previous_generation_id))
                diff_payload = _build_diff(previous_snapshot, run_snapshot)
            except Exception:
                diff_payload = {}

        export_paths: dict[str, str] = {}
        ctx = _export_find_context(int(generation_id))
        if ctx is not None:
            summary_payload, table_payload, table_rows = _export_load_review_diags(ctx)
            summary_path, table_path = _export_write_outputs(
                generation_id=int(generation_id),
                summary_payload=summary_payload,
                table_payload=table_payload,
                table_rows=table_rows,
                out_dir=out_dir,
            )
            export_paths = {
                "review_summary_json": str(summary_path),
                "review_table_csv": str(table_path),
            }

        return {
            "generation_id": int(generation_id),
            "request_id": str(run_snapshot.get("request_id") or ""),
            "final_source": str(validate_metrics.get("final_source") or run_snapshot.get("review_final_source") or ""),
            "review_primary_valid": bool(run_snapshot.get("review_primary_valid")),
            "review_retry_valid": bool(run_snapshot.get("review_retry_valid")),
            "primary_reason_coverage_ratio": float(review_runtime.get("primary_reason_coverage_ratio") or 0.0),
            "fallback_reason_coverage_ratio": float(validate_metrics.get("fallback_reason_coverage_ratio") or 0.0),
            "llm_reason_coverage_ratio": float(validate_metrics.get("llm_reason_coverage_ratio") or 0.0),
            "deterministic_backfill_ratio": float(validate_metrics.get("deterministic_backfill_ratio") or 0.0),
            "reason_source_breakdown": reason_source_breakdown,
            "candidate_total": int(candidate_by_stage.get("before_review") or 0),
            "candidate_primary": int(candidate_by_stage.get("primary") or 0),
            "candidate_gap": int(candidate_by_stage.get("gap") or 0),
            "review_input_size": int(run_snapshot.get("review_input_size") or 0),
            "review_output_size": int(run_snapshot.get("review_output_size") or 0),
            "snapshot_id": str(run_snapshot.get("rag_snapshot_id") or ""),
            "corpus_hash": str(run_snapshot.get("corpus_hash") or ""),
            "retrieval_hash": str(run_snapshot.get("retrieval_hash") or ""),
            "fallback_dropped_reason_mapped_count": int(validate_metrics.get("fallback_dropped_reason_mapped_count") or 0),
            "fallback_dropped_reason_count": int(validate_metrics.get("fallback_dropped_reason_count") or 0),
            "retry_parse_success": bool(validate_metrics.get("retry_parse_success")),
            "fallback_reason_incomplete": bool(validate_metrics.get("fallback_reason_incomplete")),
            "validate_passed": bool(validate_acceptance.get("passed")),
            "validate_checks": dict(validate_acceptance.get("checks") or {}),
            "diff": diff_payload,
            "export_paths": export_paths,
        }


def run_acceptance_loop(
    *,
    runner: GenerationRunner,
    collector: DiagnosticsCollector,
    config: AcceptanceConfig,
    out_dir: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    previous_generation_id: int | None = None
    no_fallback_streak = 0
    low_coverage_streak = 0
    stop_reason = "max_runs_reached"

    for run_index in range(1, max(1, int(config.max_runs)) + 1):
        generation_id = int(runner.trigger_once())
        run_out_dir = out_dir / f"generation_{generation_id}"
        run_out_dir.mkdir(parents=True, exist_ok=True)
        record = collector.collect(
            generation_id=generation_id,
            previous_generation_id=previous_generation_id,
            out_dir=run_out_dir,
        )
        record["run_index"] = int(run_index)
        records.append(record)
        previous_generation_id = int(generation_id)

        final_source = str(record.get("final_source") or "")
        fallback_mapped = _as_int(record.get("fallback_dropped_reason_mapped_count"), 0)
        llm_reason_coverage_ratio = _as_float(record.get("llm_reason_coverage_ratio"), 0.0)
        deterministic_backfill_ratio = _as_float(record.get("deterministic_backfill_ratio"), 0.0)
        validate_passed = bool(record.get("validate_passed"))

        if (
            final_source == "fallback_llm"
            and fallback_mapped > 0
            and llm_reason_coverage_ratio > 0.0
            and deterministic_backfill_ratio < 1.0
            and validate_passed
        ):
            stop_reason = "fallback_acceptance_passed"
            break

        if final_source == "fallback_llm":
            no_fallback_streak = 0
        else:
            no_fallback_streak += 1
            if no_fallback_streak >= max(1, int(config.max_no_fallback_streak)):
                stop_reason = "no_fallback_streak_exceeded"
                break

        if llm_reason_coverage_ratio < float(config.low_coverage_threshold):
            low_coverage_streak += 1
            if low_coverage_streak >= max(1, int(config.max_low_coverage_streak)):
                stop_reason = "reason_coverage_regression"
                break
        else:
            low_coverage_streak = 0

    fallback_hit_count = sum(1 for row in records if str(row.get("final_source") or "") == "fallback_llm")
    primary_coverage_values = [
        _as_float(row.get("primary_reason_coverage_ratio"), 0.0)
        for row in records
        if str(row.get("final_source") or "") == "primary_llm"
    ]
    primary_coverage_avg = round(sum(primary_coverage_values) / len(primary_coverage_values), 4) if primary_coverage_values else 0.0

    summary = {
        "kind": "reason_coverage_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stop_reason": str(stop_reason),
        "max_runs": int(config.max_runs),
        "max_no_fallback_streak": int(config.max_no_fallback_streak),
        "low_coverage_threshold": float(config.low_coverage_threshold),
        "max_low_coverage_streak": int(config.max_low_coverage_streak),
        "total_runs": int(len(records)),
        "fallback_hit_count": int(fallback_hit_count),
        "primary_reason_coverage_avg": float(primary_coverage_avg),
        "records": records,
    }
    return summary


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_index",
        "generation_id",
        "request_id",
        "final_source",
        "review_primary_valid",
        "review_retry_valid",
        "primary_reason_coverage_ratio",
        "fallback_reason_coverage_ratio",
        "llm_reason_coverage_ratio",
        "deterministic_backfill_ratio",
        "reason_source_breakdown",
        "candidate_total",
        "candidate_primary",
        "candidate_gap",
        "review_input_size",
        "review_output_size",
        "snapshot_id",
        "corpus_hash",
        "retrieval_hash",
        "fallback_dropped_reason_count",
        "fallback_dropped_reason_mapped_count",
        "validate_passed",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in records:
            item = dict(row)
            if isinstance(item.get("reason_source_breakdown"), dict):
                item["reason_source_breakdown"] = json.dumps(item.get("reason_source_breakdown"), ensure_ascii=False)
            writer.writerow({name: item.get(name, "") for name in fieldnames})


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    records = [item for item in (summary.get("records") or []) if isinstance(item, dict)]
    lines: list[str] = []
    lines.append("# Reason Coverage Acceptance Report")
    lines.append("")
    lines.append(f"- generated_at: {summary.get('generated_at')}")
    lines.append(f"- stop_reason: {summary.get('stop_reason')}")
    lines.append(f"- total_runs: {summary.get('total_runs')}")
    lines.append(f"- fallback_hit_count: {summary.get('fallback_hit_count')}")
    lines.append(f"- primary_reason_coverage_avg: {summary.get('primary_reason_coverage_avg')}")
    lines.append("")
    lines.append("| run | generation_id | final_source | llm_reason_coverage_ratio | deterministic_backfill_ratio | validate_passed |")
    lines.append("|---|---:|---|---:|---:|---|")
    for row in records:
        lines.append(
            "| {run} | {gid} | {src} | {llm:.4f} | {backfill:.4f} | {passed} |".format(
                run=_as_int(row.get("run_index"), 0),
                gid=_as_int(row.get("generation_id"), 0),
                src=str(row.get("final_source") or ""),
                llm=_as_float(row.get("llm_reason_coverage_ratio"), 0.0),
                backfill=_as_float(row.get("deterministic_backfill_ratio"), 0.0),
                passed=bool(row.get("validate_passed")),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _persist_outputs(out_dir: Path, summary: dict[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "reason_coverage_acceptance_summary.json"
    csv_path = out_dir / "reason_coverage_acceptance_runs.csv"
    md_path = out_dir / "reason_coverage_acceptance_brief.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, [item for item in (summary.get("records") or []) if isinstance(item, dict)])
    _write_markdown(md_path, summary)
    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(md_path),
    }


def _print_console(summary: dict[str, Any], output_paths: dict[str, str]) -> None:
    print("[OK] reason coverage acceptance finished")
    print(f"  stop_reason: {summary.get('stop_reason')}")
    print(f"  total_runs: {summary.get('total_runs')}")
    print(f"  fallback_hit_count: {summary.get('fallback_hit_count')}")
    print(f"  primary_reason_coverage_avg: {summary.get('primary_reason_coverage_avg')}")
    print("  outputs:")
    print(f"    json: {output_paths.get('json')}")
    print(f"    csv: {output_paths.get('csv')}")
    print(f"    markdown: {output_paths.get('markdown')}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run reason coverage acceptance loop on real generation runs.")
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:8000", help="Backend base URL.")
    parser.add_argument(
        "--token",
        type=str,
        default=os.getenv("TG_API_TOKEN", os.getenv("API_TOKEN", "")),
        help="Bearer token for API auth. Defaults to TG_API_TOKEN/API_TOKEN env if provided.",
    )
    parser.add_argument("--project-id", type=int, required=True, help="Project id.")
    parser.add_argument("--requirement-text", type=str, default="", help="Requirement text.")
    parser.add_argument("--requirement-file", type=str, default="", help="Requirement text file path.")
    parser.add_argument("--expected-count", type=int, default=50, help="Expected case count.")
    parser.add_argument("--compress", action="store_true", help="Enable compress mode.")
    parser.add_argument("--single-pass", action="store_true", help="Use single-pass generation mode.")
    parser.add_argument("--generation-mode", type=str, default="multi_pass", help="Generation mode.")
    parser.add_argument("--disable-sample-pool-feedback", action="store_true", help="Disable sample pool feedback.")
    parser.add_argument("--request-timeout-sec", type=int, default=1200, help="Generation request timeout.")
    parser.add_argument("--poll-timeout-sec", type=int, default=300, help="Wait timeout for new generation id.")
    parser.add_argument("--poll-interval-sec", type=float, default=2.0, help="Polling interval.")
    parser.add_argument("--summary-ready-timeout-sec", type=int, default=180, help="Wait timeout for GEN_DIAG summary.")
    parser.add_argument("--summary-ready-poll-interval-sec", type=float, default=2.0, help="Polling interval for summary.")
    parser.add_argument("--max-runs", type=int, default=6, help="Maximum acceptance runs.")
    parser.add_argument("--max-no-fallback-streak", type=int, default=3, help="Stop after N consecutive non-fallback.")
    parser.add_argument(
        "--low-coverage-threshold",
        type=float,
        default=0.05,
        help="llm_reason_coverage_ratio threshold for regression.",
    )
    parser.add_argument(
        "--max-low-coverage-streak",
        type=int,
        default=3,
        help="Stop after N consecutive low-coverage runs.",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(ROOT_DIR / "tmp" / "review_diagnostics"),
        help="Output directory.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    requirement = _load_requirement_text(
        requirement_text=str(args.requirement_text or ""),
        requirement_file=str(args.requirement_file or ""),
    )
    out_dir = Path(str(args.out_dir or ROOT_DIR / "tmp" / "review_diagnostics"))

    runner = ApiGenerationRunner(
        base_url=str(args.base_url or "").strip(),
        token=str(args.token or "").strip(),
        project_id=int(args.project_id),
        requirement=requirement,
        expected_count=int(args.expected_count or 50),
        compress=bool(args.compress),
        multi_pass=not bool(args.single_pass),
        generation_mode=str(args.generation_mode or ""),
        enable_sample_pool_feedback=not bool(args.disable_sample_pool_feedback),
        timeout_sec=int(args.request_timeout_sec or 1200),
        poll_timeout_sec=int(args.poll_timeout_sec or 300),
        poll_interval_sec=float(args.poll_interval_sec or 2.0),
    )
    collector = RealDiagnosticsCollector(
        summary_ready_timeout_sec=int(args.summary_ready_timeout_sec or 180),
        summary_ready_poll_interval_sec=float(args.summary_ready_poll_interval_sec or 2.0),
    )
    config = AcceptanceConfig(
        max_runs=int(args.max_runs or 6),
        max_no_fallback_streak=int(args.max_no_fallback_streak or 3),
        low_coverage_threshold=float(args.low_coverage_threshold or 0.05),
        max_low_coverage_streak=int(args.max_low_coverage_streak or 3),
    )

    summary = run_acceptance_loop(
        runner=runner,
        collector=collector,
        config=config,
        out_dir=out_dir,
    )
    output_paths = _persist_outputs(out_dir, summary)
    _print_console(summary, output_paths)

    stop_reason = str(summary.get("stop_reason") or "")
    if stop_reason == "fallback_acceptance_passed":
        return 0
    if stop_reason in {"no_fallback_streak_exceeded", "reason_coverage_regression"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
