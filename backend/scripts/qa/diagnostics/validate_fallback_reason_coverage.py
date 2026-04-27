from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.qa.diagnostics.analyze_generation_stability import (  # noqa: E402
    _find_context,
    _load_run_payloads,
    _pick_latest_payload,
)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if int(denominator or 0) <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _extract_metrics_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    review_summary = dict(summary or {})
    runtime = dict(review_summary.get("review_llm_runtime_debug") or {})
    source_breakdown = dict(review_summary.get("review_llm_drop_reason_source_breakdown") or {})
    drop_by_review_llm_count = _as_int(review_summary.get("drop_by_review_llm_count"), 0)

    fallback_dropped_reason_count = _as_int(
        review_summary.get("fallback_dropped_reason_count"),
        _as_int(runtime.get("final_dropped_reason_payload_count"), _as_int(runtime.get("final_dropped_reason_count"), 0)),
    )
    fallback_dropped_reason_mapped_count = _as_int(
        review_summary.get("fallback_dropped_reason_mapped_count"),
        _as_int(runtime.get("final_dropped_reason_count"), _as_int(source_breakdown.get("fallback_llm"), 0)),
    )

    llm_reason_total = _as_int(source_breakdown.get("llm"), 0) + _as_int(source_breakdown.get("fallback_llm"), 0)
    deterministic_backfill_total = _as_int(source_breakdown.get("deterministic_backfill"), 0)

    fallback_reason_coverage_ratio_value = review_summary.get("fallback_reason_coverage_ratio")
    if fallback_reason_coverage_ratio_value is not None:
        try:
            fallback_reason_coverage_ratio = float(fallback_reason_coverage_ratio_value or 0.0)
        except Exception:
            fallback_reason_coverage_ratio = 0.0
    else:
        denominator = fallback_dropped_reason_count if fallback_dropped_reason_count > 0 else drop_by_review_llm_count
        fallback_reason_coverage_ratio = _safe_ratio(fallback_dropped_reason_mapped_count, denominator)
    if fallback_reason_coverage_ratio > 1.0:
        denominator = fallback_dropped_reason_count if fallback_dropped_reason_count > 0 else drop_by_review_llm_count
        fallback_reason_coverage_ratio = _safe_ratio(fallback_dropped_reason_mapped_count, denominator)
    llm_reason_coverage_ratio = float(
        review_summary.get(
            "llm_reason_coverage_ratio",
            _safe_ratio(llm_reason_total, drop_by_review_llm_count),
        )
        or 0.0
    )
    deterministic_backfill_ratio = float(
        review_summary.get(
            "deterministic_backfill_ratio",
            _safe_ratio(deterministic_backfill_total, drop_by_review_llm_count),
        )
        or 0.0
    )

    fallback_reason_incomplete = bool(
        review_summary.get(
            "fallback_reason_incomplete",
            str(runtime.get("final_source") or "") == "fallback_llm" and fallback_dropped_reason_count <= 0,
        )
    )

    return {
        "final_source": str(runtime.get("final_source") or "unknown"),
        "retry_parse_success": bool(runtime.get("retry_parse_success")),
        "fallback_reason_incomplete": bool(fallback_reason_incomplete),
        "fallback_dropped_reason_count": int(fallback_dropped_reason_count),
        "fallback_dropped_reason_mapped_count": int(fallback_dropped_reason_mapped_count),
        "fallback_reason_coverage_ratio": float(fallback_reason_coverage_ratio),
        "llm_reason_coverage_ratio": float(llm_reason_coverage_ratio),
        "deterministic_backfill_ratio": float(deterministic_backfill_ratio),
        "drop_by_review_llm_count": int(drop_by_review_llm_count),
    }


def _evaluate_acceptance(metrics: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "final_source_is_fallback_llm": str(metrics.get("final_source") or "") == "fallback_llm",
        "fallback_dropped_reason_mapped_count_gt_0": _as_int(metrics.get("fallback_dropped_reason_mapped_count"), 0) > 0,
        "llm_reason_coverage_ratio_gt_0": float(metrics.get("llm_reason_coverage_ratio") or 0.0) > 0.0,
        "deterministic_backfill_ratio_lt_1": float(metrics.get("deterministic_backfill_ratio") or 0.0) < 1.0,
    }
    return {
        "passed": bool(all(checks.values())),
        "checks": checks,
    }


def _load_summary_by_generation_id(generation_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    ctx = _find_context(int(generation_id))
    if not ctx:
        raise RuntimeError(f"generation_id={generation_id} context not found")
    payloads = _load_run_payloads(ctx)
    if not payloads:
        raise RuntimeError(f"generation_id={generation_id} has no GEN_DIAG payloads")
    review_summary = _pick_latest_payload(payloads, "review_decision_summary")
    if not review_summary:
        raise RuntimeError(f"generation_id={generation_id} has no review_decision_summary payload")
    meta = {
        "generation_id": int(generation_id),
        "project_id": int(ctx.project_id),
        "request_id": str(ctx.request_id or ""),
    }
    return review_summary, meta


def _run_simulated_fallback(case_count: int = 18, keep_count: int = 4) -> tuple[dict[str, Any], dict[str, Any]]:
    from modules.testing.test_generation_components.legacy.adapters import (
        clean_and_parse_json,
        count_unique_test_cases,
        deduplicate_test_cases,
        infer_case_kind,
        normalize_json_structure,
        reorder_cases_by_closed_loop,
    )
    from modules.testing.test_generation_components.postprocess.result_postprocess import (
        stream_postprocess_cases,
    )

    def _build_case(index: int) -> dict[str, Any]:
        return {
            "id": f"TC-{index:03d}",
            "description": f"fallback回放用例-{index}",
            "test_module": f"module-{(index % 5) + 1}",
            "preconditions": [],
            "steps": [f"step-{index}"],
            "test_input": f"input-{index}",
            "expected_result": f"ok-{index}",
            "priority": "P1",
        }

    def _build_full_content(count: int) -> str:
        return json.dumps([_build_case(i) for i in range(1, count + 1)], ensure_ascii=False)

    def _valid_retry_payload(keep_n: int, total_n: int) -> str:
        kept_ids = [f"TC-{i:03d}" for i in range(1, keep_n + 1)]
        dropped = [
            {"case_id": f"TC-{i:03d}", "reason": "coverage_redundant"}
            for i in range(keep_n + 1, total_n + 1)
        ]
        return json.dumps({"kept_case_ids": kept_ids, "dropped": dropped}, ensure_ascii=False)

    class _ReplayClient:
        def __init__(self, *, primary_review_response: str, retry_review_response: str) -> None:
            self.primary_review_response = str(primary_review_response or "")
            self.retry_review_response = str(retry_review_response or "")
            self.model = "deepseek-reasoner"
            self.turbo_model = "deepseek-chat"

        def select_model(self, full_input: str, task_type: str = "generation") -> str:  # noqa: ARG002
            return "deepseek-reasoner"

        def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:  # noqa: ARG002
            if str(prompt or "").strip() == "You are a QA Auditor.":
                if str(kwargs.get("model") or "").strip():
                    return self.retry_review_response
                return self.primary_review_response
            return "[]"

        def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
            yield "[]"

    def _drain_with_return(gen):
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                return stop.value

    client = _ReplayClient(
        primary_review_response="NOT_JSON_PAYLOAD",
        retry_review_response=_valid_retry_payload(keep_count, case_count),
    )

    gen = stream_postprocess_cases(
        client=client,
        requirement="fallback验收模拟需求",
        base_prompt="BASE",
        kb_context="",
        full_content=_build_full_content(case_count),
        expected_count=max(20, case_count),
        append=False,
        existing_cases=[],
        existing_unique_count=0,
        start_id=1,
        db=None,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        count_unique_test_cases_fn=count_unique_test_cases,
        infer_case_kind_fn=infer_case_kind,
        build_supplement_closed_loop_instruction_fn=lambda **_: "",
        multi_pass=True,
        generation_mode="multi_pass",
    )
    result = _drain_with_return(gen)
    if not isinstance(result, dict):
        raise RuntimeError("simulated fallback run did not return dict result")
    review_summary = dict((result or {}).get("review_decision_summary") or {})
    if not review_summary:
        raise RuntimeError("simulated fallback run has no review_decision_summary")
    meta = {
        "simulation": "primary_schema_parse_error",
        "case_count": int(case_count),
        "keep_count": int(keep_count),
    }
    return review_summary, meta


def _build_output_payload(*, mode: str, meta: dict[str, Any], review_summary: dict[str, Any]) -> dict[str, Any]:
    metrics = _extract_metrics_from_summary(review_summary)
    acceptance = _evaluate_acceptance(metrics)
    return {
        "kind": "fallback_reason_coverage_validation",
        "mode": str(mode),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": dict(meta or {}),
        "metrics": metrics,
        "acceptance": acceptance,
    }


def _write_output(out_dir: Path, payload: dict[str, Any], suffix: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"fallback_reason_coverage_validation_{suffix}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate fallback reason coverage for review_llm fallback path.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generation-id", type=int, help="Use an existing generation_id from GEN_DIAG logs.")
    group.add_argument(
        "--simulate-primary-schema-error",
        action="store_true",
        help="Run a local replay that forces primary schema_parse_error and validates fallback coverage.",
    )
    parser.add_argument("--case-count", type=int, default=18, help="Simulation case count (simulate mode only).")
    parser.add_argument("--keep-count", type=int, default=4, help="Simulation kept case count (simulate mode only).")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(ROOT_DIR / "tmp" / "review_diagnostics"),
        help="Output directory path.",
    )
    args = parser.parse_args()

    if args.generation_id is not None:
        review_summary, meta = _load_summary_by_generation_id(int(args.generation_id))
        payload = _build_output_payload(mode="generation_id", meta=meta, review_summary=review_summary)
        out_path = _write_output(Path(args.out_dir), payload, suffix=f"generation_{int(args.generation_id)}")
    else:
        review_summary, meta = _run_simulated_fallback(case_count=int(args.case_count), keep_count=int(args.keep_count))
        payload = _build_output_payload(mode="simulate_primary_schema_error", meta=meta, review_summary=review_summary)
        out_path = _write_output(Path(args.out_dir), payload, suffix="simulated")

    metrics = dict(payload.get("metrics") or {})
    acceptance = dict(payload.get("acceptance") or {})
    checks = dict(acceptance.get("checks") or {})

    print("[OK] fallback reason coverage validation finished")
    print(f"  mode: {payload.get('mode')}")
    if args.generation_id is not None:
        print(f"  generation_id: {int(args.generation_id)}")
    print(f"  out: {out_path}")
    print("  metrics:")
    for key in (
        "final_source",
        "retry_parse_success",
        "fallback_reason_incomplete",
        "fallback_dropped_reason_count",
        "fallback_dropped_reason_mapped_count",
        "fallback_reason_coverage_ratio",
        "llm_reason_coverage_ratio",
        "deterministic_backfill_ratio",
    ):
        print(f"    {key}: {metrics.get(key)}")
    print("  acceptance:")
    for key, value in checks.items():
        print(f"    {key}: {bool(value)}")
    print(f"  passed: {bool(acceptance.get('passed'))}")

    return 0 if bool(acceptance.get("passed")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
