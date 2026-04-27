from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db.database import SessionLocal
from core.db.models import TestGeneration
from modules.test_generation_components.control.build_feedback_control_state import (
    build_feedback_control_state,
)
from modules.test_generation_components.eval.case_distribution_classifier import (
    classify_case_distribution,
    classify_case_distributions,
)
from modules.test_generation_components.postprocess.result_postprocess_priority_semantics import (
    apply_priority_semantics_to_cases,
)
from modules.test_generation_components.prompting.prompt_orchestration_split_helpers import (
    build_closed_loop_base_prompt,
)
from modules.test_generation_components.prompting.structured_context import (
    build_structured_prompt_context,
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _parse_cases(generated_result: str) -> list[dict[str, Any]]:
    raw = str(generated_result or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        cases = payload.get("cases")
        if isinstance(cases, list):
            return [item for item in cases if isinstance(item, dict)]
    return []


def _get_recent_runs(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    limit: int,
) -> list[TestGeneration]:
    rows = (
        db.query(TestGeneration)
        .filter(
            TestGeneration.project_id == int(project_id),
            TestGeneration.user_id == int(user_id),
        )
        .order_by(TestGeneration.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    rows.reverse()
    return rows


def _choose_latest_run(
    *,
    db: Any,
    generation_id: int | None,
) -> TestGeneration | None:
    if generation_id:
        return db.query(TestGeneration).filter(TestGeneration.id == int(generation_id)).first()
    return db.query(TestGeneration).order_by(TestGeneration.id.desc()).first()


def _extract_section_body(text: str, section_title: str) -> str:
    normalized = str(text or "")
    if not normalized:
        return ""
    escaped = re.escape(section_title)
    pattern = re.compile(rf"### {escaped}\s*\n(?P<body>.*?)(?:\n### |\Z)", re.DOTALL)
    match = pattern.search(normalized)
    if not match:
        return ""
    return str(match.group("body") or "").strip()


def _is_nonempty_section(body: str) -> bool:
    normalized = str(body or "").strip()
    if not normalized:
        return False
    if normalized.startswith("* (none)"):
        return False
    return True


def _probe_prompt_injection(
    *,
    requirement: str,
    control_state: Any,
) -> dict[str, Any]:
    prompt_context = build_structured_prompt_context(
        requirement=str(requirement or ""),
        kb_context="",
        rag_result=None,
        existing_cases=[],
        current_biz_key="",
        only_current_biz=False,
        feedback_control_state=control_state,
        include_soft_constraints_in_prompt=False,
        include_quality_fix_hints_in_prompt=False,
    )
    control_summary = dict(prompt_context.get("control_summary") or {})
    control_context = str(prompt_context.get("control_context") or "")
    base_prompt = build_closed_loop_base_prompt(
        strategy_plan={},
        requirement_context=str(prompt_context.get("requirement_context") or "(empty)"),
        testcase_context=str(prompt_context.get("testcase_context") or "(empty)"),
        supplement_context=str(prompt_context.get("supplement_context") or "(empty)"),
        control_context=control_context,
        current_biz_key=str(prompt_context.get("current_biz_key") or "unknown"),
        doc_type="requirement",
    )

    forbidden_body = _extract_section_body(control_context, "FORBIDDEN PATTERNS")
    preferred_body = _extract_section_body(control_context, "PREFERRED PATTERNS")

    return {
        "preferred_patterns_count": _safe_int(control_summary.get("preferred_patterns_count")),
        "forbidden_patterns_count": _safe_int(control_summary.get("forbidden_patterns_count")),
        "control_context_has_preferred_section": bool("### PREFERRED PATTERNS" in control_context),
        "control_context_has_forbidden_section": bool("### FORBIDDEN PATTERNS" in control_context),
        "control_context_preferred_nonempty": bool(_is_nonempty_section(preferred_body)),
        "control_context_forbidden_nonempty": bool(_is_nonempty_section(forbidden_body)),
        "final_prompt_has_preferred_section": bool("### PREFERRED PATTERNS" in base_prompt),
        "final_prompt_has_forbidden_section": bool("### FORBIDDEN PATTERNS" in base_prompt),
        "final_prompt_preferred_nonempty": bool(_is_nonempty_section(preferred_body)),
        "final_prompt_forbidden_nonempty": bool(_is_nonempty_section(forbidden_body)),
        "control_context_chars": int(len(control_context)),
    }


def _build_table1_rows(runs: list[TestGeneration], db: Any) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for row in runs:
        state = build_feedback_control_state(
            db=db,
            project_id=int(row.project_id),
            user_id=int(row.user_id),
            requirement_text=str(row.requirement_text or ""),
            enable_priority_sample_pool=True,
            include_agent_learning=True,
        )
        source_meta = dict((state.to_dict() or {}).get("source_meta") or {})
        prompt_probe = _probe_prompt_injection(
            requirement=str(row.requirement_text or ""),
            control_state=state,
        )
        table.append(
            {
                "run_id": int(row.id),
                "selected_count": _safe_int(source_meta.get("priority_pool_selected_sample_count")),
                "positive_selected": _safe_int(source_meta.get("positive_selected_count")),
                "negative_selected": _safe_int(source_meta.get("negative_selected_count")),
                "retrieval_hit_count": _safe_int(source_meta.get("retrieval_hit_count")),
                "fallback": str(source_meta.get("retrieval_fallback") or ""),
                "preferred_patterns_count": _safe_int(prompt_probe.get("preferred_patterns_count")),
                "forbidden_patterns_count": _safe_int(prompt_probe.get("forbidden_patterns_count")),
                "final_prompt_preferred_nonempty": bool(prompt_probe.get("final_prompt_preferred_nonempty")),
                "final_prompt_forbidden_nonempty": bool(prompt_probe.get("final_prompt_forbidden_nonempty")),
                "positive_total_in_pool": _safe_int(
                    source_meta.get("priority_pool_total_positive_count"),
                    _safe_int(source_meta.get("retrieval_pool_positive_count")),
                ),
                "negative_total_in_pool": _safe_int(
                    source_meta.get("priority_pool_total_negative_count"),
                    _safe_int(source_meta.get("retrieval_pool_negative_count")),
                ),
                "positive_retrieved_raw": _safe_int(source_meta.get("retrieval_raw_positive_count")),
                "negative_retrieved_raw": _safe_int(source_meta.get("retrieval_raw_negative_count")),
                "positive_after_diversity": _safe_int(source_meta.get("retrieval_after_diversity_positive_count")),
                "negative_after_diversity": _safe_int(source_meta.get("retrieval_after_diversity_negative_count")),
                "positive_after_quota_merge": _safe_int(source_meta.get("retrieval_after_quota_merge_positive_count")),
                "negative_after_quota_merge": _safe_int(source_meta.get("retrieval_after_quota_merge_negative_count")),
                "positive_final_selected": _safe_int(
                    source_meta.get("retrieval_final_selected_positive_count"),
                    _safe_int(source_meta.get("positive_selected_count")),
                ),
                "negative_final_selected": _safe_int(
                    source_meta.get("retrieval_final_selected_negative_count"),
                    _safe_int(source_meta.get("negative_selected_count")),
                ),
            }
        )
    return table


def _build_table2_rows(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rescored = apply_priority_semantics_to_cases(cases, attach_debug=True)
    case_type_mapping = classify_case_distributions(rescored)
    rows: list[dict[str, Any]] = []
    total = len(rescored)
    flow_count = 0
    state_count = 0
    ui_count = 0
    workflow_signal_count = 0
    ui_like_count = 0
    cross_state_count = 0
    p1_uplift_count = 0
    p2_cap_count = 0
    p2_cap_main_workflow = 0
    p2_cap_preferred_pattern = 0
    p2_cap_cross_state = 0

    for case in rescored:
        case_id = str(case.get("id") or case.get("case_id") or case.get("caseId") or "")
        debug = dict((case.get("meta") or {}).get("priority_debug") or {})
        reasons = [str(x) for x in (debug.get("priority_reasons") or [])]
        case_type = case_type_mapping.get(case_id) or classify_case_distribution(case)

        main_workflow_hit = bool("main_workflow_hit" in reasons)
        preferred_pattern_hit = bool(debug.get("preferred_pattern_hit")) or bool(
            "preferred_pattern_hit" in reasons
        )
        cross_page_hit = bool(debug.get("cross_page_flow_hit")) or bool("cross_page_flow_hit" in reasons)
        state_transition_hit = bool(debug.get("state_transition_hit")) or bool(
            "state_transition_hit" in reasons
        )
        cross_or_state = bool(cross_page_hit or state_transition_hit)
        p1_uplifted = bool(debug.get("p1_uplifted"))
        p2_cap = bool(debug.get("p2_cap"))
        final_priority = str(case.get("priority") or debug.get("final_priority") or "")
        workflow_signal_hit = bool(main_workflow_hit or preferred_pattern_hit or cross_or_state)
        ui_like_case = bool(debug.get("ui_like_case"))

        if case_type == "FLOW":
            flow_count += 1
        elif case_type == "STATE":
            state_count += 1
        else:
            ui_count += 1
        if workflow_signal_hit:
            workflow_signal_count += 1
        if ui_like_case:
            ui_like_count += 1
        if cross_or_state:
            cross_state_count += 1
        if p1_uplifted:
            p1_uplift_count += 1
        if p2_cap:
            p2_cap_count += 1
            if main_workflow_hit:
                p2_cap_main_workflow += 1
            if preferred_pattern_hit:
                p2_cap_preferred_pattern += 1
            if cross_or_state:
                p2_cap_cross_state += 1

        rows.append(
            {
                "case_id": case_id,
                "case_type": case_type,
                "main_workflow_hit": bool(main_workflow_hit),
                "preferred_pattern_hit": bool(preferred_pattern_hit),
                "cross_page_state_transition": bool(cross_or_state),
                "workflow_signal_hit": bool(workflow_signal_hit),
                "ui_like_case": bool(ui_like_case),
                "p1_uplifted": bool(p1_uplifted),
                "p2_cap": bool(p2_cap),
                "final_priority": final_priority,
            }
        )

    flow_ratio = _safe_float(flow_count / total, 0.0) if total > 0 else 0.0
    state_ratio = _safe_float(state_count / total, 0.0) if total > 0 else 0.0
    ui_ratio = _safe_float(ui_count / total, 0.0) if total > 0 else 0.0
    workflow_signal_ratio = _safe_float(workflow_signal_count / total, 0.0) if total > 0 else 0.0
    ui_like_ratio = _safe_float(ui_like_count / total, 0.0) if total > 0 else 0.0
    summary = {
        "case_total": int(total),
        "flow_case_count": int(flow_count),
        "flow_ratio": round(float(flow_ratio), 4),
        "state_case_count": int(state_count),
        "state_ratio": round(float(state_ratio), 4),
        "ui_case_count": int(ui_count),
        "ui_ratio": round(float(ui_ratio), 4),
        "workflow_signal_case_count": int(workflow_signal_count),
        "workflow_signal_ratio": round(float(workflow_signal_ratio), 4),
        "ui_like_case_count": int(ui_like_count),
        "ui_like_ratio": round(float(ui_like_ratio), 4),
        "cross_page_state_transition_count": int(cross_state_count),
        "p1_uplifted_count": int(p1_uplift_count),
        "p2_cap_true_count": int(p2_cap_count),
        "p2_cap_main_workflow_hit_count": int(p2_cap_main_workflow),
        "p2_cap_preferred_pattern_hit_count": int(p2_cap_preferred_pattern),
        "p2_cap_cross_page_state_hit_count": int(p2_cap_cross_state),
    }
    return rows, summary


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _print_table1(rows: list[dict[str, Any]]) -> None:
    print(
        "run_id | selected_count | positive_selected | negative_selected | retrieval_hit_count | fallback | "
        "preferred_patterns_count | forbidden_patterns_count | final_prompt_preferred_nonempty | final_prompt_forbidden_nonempty"
    )
    for row in rows:
        print(
            f"{row['run_id']} | {row['selected_count']} | {row['positive_selected']} | "
            f"{row['negative_selected']} | {row['retrieval_hit_count']} | {row['fallback']} | "
            f"{row['preferred_patterns_count']} | {row['forbidden_patterns_count']} | "
            f"{row['final_prompt_preferred_nonempty']} | {row['final_prompt_forbidden_nonempty']}"
        )


def _print_table2(rows: list[dict[str, Any]]) -> None:
    print(
        "case_id | case_type | workflow_signal | ui_like_case | "
        "main_workflow_hit | preferred_pattern_hit | cross_page/state_transition | p1_uplifted | p2_cap | final_priority"
    )
    for row in rows:
        print(
            f"{row['case_id']} | {row['case_type']} | {row['workflow_signal_hit']} | {row['ui_like_case']} | "
            f"{row['main_workflow_hit']} | {row['preferred_pattern_hit']} | "
            f"{row['cross_page_state_transition']} | {row['p1_uplifted']} | {row['p2_cap']} | "
            f"{row['final_priority']}"
        )


def _print_funnel_table(rows: list[dict[str, Any]]) -> None:
    print(
        "run_id | positive_total_in_pool | negative_total_in_pool | "
        "positive_retrieved_raw | negative_retrieved_raw | "
        "positive_after_diversity | negative_after_diversity | "
        "positive_after_quota_merge | negative_after_quota_merge | "
        "positive_final_selected | negative_final_selected"
    )
    for row in rows:
        print(
            f"{row['run_id']} | {row['positive_total_in_pool']} | {row['negative_total_in_pool']} | "
            f"{row['positive_retrieved_raw']} | {row['negative_retrieved_raw']} | "
            f"{row['positive_after_diversity']} | {row['negative_after_diversity']} | "
            f"{row['positive_after_quota_merge']} | {row['negative_after_quota_merge']} | "
            f"{row['positive_final_selected']} | {row['negative_final_selected']}"
        )


def _evaluate_gate(table1_rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    cond1 = bool(table1_rows) and all(
        _safe_int(row.get("positive_selected")) >= 2
        and _safe_int(row.get("negative_selected")) <= 3
        and str(row.get("fallback") or "") == "none"
        for row in table1_rows
    )
    cond2 = (
        _safe_int(summary.get("workflow_signal_case_count")) > 0
        and _safe_int(summary.get("p2_cap_true_count")) > 0
        and (
            _safe_int(summary.get("p2_cap_main_workflow_hit_count")) > 0
            or _safe_int(summary.get("p2_cap_preferred_pattern_hit_count")) > 0
            or _safe_int(summary.get("p2_cap_cross_page_state_hit_count")) > 0
        )
    )
    return {
        "condition_1_positive_quota_stable_and_no_fallback": bool(cond1),
        "condition_2_flow_cases_still_blocked_by_p2_cap": bool(cond2),
        "ready_for_batch_b": bool(cond1 and cond2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe Batch A status: retrieval selection and flow-priority semantics.",
    )
    parser.add_argument("--generation-id", type=int, default=0, help="Target generation id; defaults to latest.")
    parser.add_argument("--window", type=int, default=5, help="Number of recent runs for table1 observation.")
    parser.add_argument(
        "--out-dir",
        type=str,
        default="tmp/batch_a_observation",
        help="Output directory for CSV + summary JSON.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        latest = _choose_latest_run(db=db, generation_id=(int(args.generation_id) or None))
        if latest is None:
            print("[ERROR] No generation found.")
            return 2

        runs = _get_recent_runs(
            db=db,
            project_id=int(latest.project_id),
            user_id=int(latest.user_id),
            limit=max(1, int(args.window)),
        )
        table1 = _build_table1_rows(runs, db)

        cases = _parse_cases(str(latest.generated_result or ""))
        table2, summary = _build_table2_rows(cases)
        gate = _evaluate_gate(table1, summary)

        out_dir = Path(args.out_dir)
        table1_path = out_dir / f"generation_{int(latest.id)}_table1_control_state.csv"
        table2_path = out_dir / f"generation_{int(latest.id)}_table2_flow_priority.csv"
        table3_path = out_dir / f"generation_{int(latest.id)}_table3_signal_funnel.csv"
        summary_path = out_dir / f"generation_{int(latest.id)}_summary.json"

        _write_csv(
            table1_path,
            table1,
            [
                "run_id",
                "selected_count",
                "positive_selected",
                "negative_selected",
                "retrieval_hit_count",
                "fallback",
                "preferred_patterns_count",
                "forbidden_patterns_count",
                "final_prompt_preferred_nonempty",
                "final_prompt_forbidden_nonempty",
            ],
        )
        _write_csv(
            table3_path,
            table1,
            [
                "run_id",
                "positive_total_in_pool",
                "negative_total_in_pool",
                "positive_retrieved_raw",
                "negative_retrieved_raw",
                "positive_after_diversity",
                "negative_after_diversity",
                "positive_after_quota_merge",
                "negative_after_quota_merge",
                "positive_final_selected",
                "negative_final_selected",
            ],
        )
        _write_csv(
            table2_path,
            table2,
            [
                "case_id",
                "case_type",
                "workflow_signal_hit",
                "ui_like_case",
                "main_workflow_hit",
                "preferred_pattern_hit",
                "cross_page_state_transition",
                "p1_uplifted",
                "p2_cap",
                "final_priority",
            ],
        )
        summary_obj = {
            "generation_id": int(latest.id),
            "project_id": int(latest.project_id or 0),
            "user_id": int(latest.user_id or 0),
            "created_at": str(latest.created_at or ""),
            "table1_rows": int(len(table1)),
            "table2_rows": int(len(table2)),
            "summary": summary,
            "batch_b_gate": gate,
        }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"[INFO] generation_id={int(latest.id)} project_id={int(latest.project_id)} user_id={int(latest.user_id)}")
        print("\n[TABLE1] control_state + prompt injection")
        _print_table1(table1)
        print("\n[TABLE3] signal funnel")
        _print_funnel_table(table1)
        print("\n[TABLE2] flow priority")
        _print_table2(table2)
        print("\n[SUMMARY]")
        for key, value in summary.items():
            print(f"{key}: {value}")
        print("\n[BATCH_B_GATE]")
        for key, value in gate.items():
            print(f"{key}: {value}")

        print("\n[FILES]")
        print(f"table1: {table1_path}")
        print(f"table3: {table3_path}")
        print(f"table2: {table2_path}")
        print(f"summary: {summary_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
