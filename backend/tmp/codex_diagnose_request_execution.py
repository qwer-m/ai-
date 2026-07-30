from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.database import SessionLocal
from core.db.models import LogEntry
from modules.test_generation_components.postprocess.streaming_execution_plan_metadata import (
    apply_execution_plan_metadata,
    evaluate_required_stage_candidate_coverage,
)


def _payload_by_kind(db: Any, request_id: str, kind: str) -> dict[str, Any]:
    rows = (
        db.query(LogEntry)
        .filter(LogEntry.message.like(f'%"request_id": "{request_id}"%'))
        .order_by(LogEntry.id.desc())
        .all()
    )
    for row in rows:
        message = str(row.message or "")
        if not message.startswith("GEN_DIAG:"):
            continue
        try:
            payload = json.loads(message.removeprefix("GEN_DIAG:"))
        except Exception:
            continue
        if str(payload.get("kind") or "") == kind:
            return dict(payload)
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        judge = _payload_by_kind(db, args.request_id, "judge_decision_table")
        gate = _payload_by_kind(db, args.request_id, "persistence_gate")
        cases = [
            dict(row.get("before_case_snapshot") or {})
            for row in (judge.get("rows") or [])
            if isinstance(row, dict)
            and isinstance(row.get("before_case_snapshot"), dict)
        ]
        metrics = dict(
            ((gate.get("execution_plan_validation") or {}).get("metrics") or {})
        )
        workflow_closure = dict(metrics.get("workflow_closure") or {})
        blueprint = dict(workflow_closure.get("declared_workflow_contract") or {})
        if blueprint:
            blueprint["primary"] = True
        blueprints = [blueprint] if blueprint else []
        annotated, summary = apply_execution_plan_metadata(
            cases,
            workflow_blueprints=blueprints,
            current_requirement_workflow_blueprints=blueprints,
            coverage_mode="core_smoke",
        )
        print(
            json.dumps(
                {
                    "case_count": len(cases),
                    "case_semantic_summaries": [
                        {
                            "case_id": case.get("id"),
                            "description": case.get("description"),
                            "workflow_stage_candidates": [
                                {
                                    "workflow_id": candidate.get("workflow_id"),
                                    "stage_id": candidate.get("stage_id"),
                                    "stage_kind": candidate.get("stage_kind"),
                                    "confidence": candidate.get("confidence"),
                                    "evidence_verified": candidate.get("evidence_verified"),
                                }
                                for candidate in (
                                    (case.get("_semantic") or {}).get(
                                        "workflow_stage_candidates"
                                    )
                                    or []
                                )
                                if isinstance(candidate, dict)
                            ],
                            "precondition_states": (
                                (case.get("_semantic") or {}).get(
                                    "precondition_states"
                                )
                                or []
                            ),
                            "produced_states": (
                                (case.get("_semantic") or {}).get("produced_states")
                                or []
                            ),
                        }
                        for case in cases
                    ],
                    "candidate_coverage": evaluate_required_stage_candidate_coverage(
                        cases,
                        workflow_blueprints=blueprints,
                    ),
                    "main_chain_incomplete_reason": summary.get(
                        "main_chain_incomplete_reason"
                    ),
                    "publishable_main_chain": summary.get(
                        "publishable_main_chain"
                    ),
                    "global_stage_assignment": summary.get(
                        "global_stage_assignment"
                    ),
                    "best_assignment_state_conflicts": summary.get(
                        "best_assignment_state_conflicts"
                    ),
                    "main_chain_excluded_candidates": summary.get(
                        "main_chain_excluded_candidates"
                    ),
                    "main_cases": [
                        {
                            "id": item.get("id"),
                            "stage": item.get("main_chain_stage"),
                            "source_state": item.get("source_state"),
                            "target_state": item.get("target_state"),
                        }
                        for item in annotated
                        if item.get("execution_group") == "main_smoke"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
