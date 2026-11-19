"""只回放已有真实用例评测，验证差异候选幂等与项目边界；没有评测时明确未验收。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select

from core.db.database import SessionLocal
from core.db.model_defs import AgentContextRecord, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_case_evaluation_workflow import persist_test_case_evaluation


def verify() -> dict:
    with SessionLocal() as db:
        before = db.query(AgentContextRecord).count()
        workflow_ids = list(db.execute(select(AgentWorkflowDefinition.id).where(
            AgentWorkflowDefinition.workflow_key == "test_case_evaluation",
        )).scalars())
        run_ids = list(db.execute(select(AgentRun.id).where(
            AgentRun.workflow_definition_id.in_(workflow_ids), AgentRun.status == "success",
        ).order_by(AgentRun.id.desc())).scalars())
        report = {
            "verification": "existing_real_evaluation_transaction_rollback",
            "model_requests_sent": 0,
            "persisted_memory_rows_before": before,
            "real_successful_evaluation_run_count": len(run_ids),
        }
        if not run_ids:
            db.rollback()
            return {**report, "status": "unverified", "reason": "no_real_test_case_evaluation_run",
                    "persisted_memory_rows_after": db.query(AgentContextRecord).count()}
        run = db.get(AgentRun, run_ids[0])
        node_id = db.execute(select(AgentNodeRun.id).where(
            AgentNodeRun.run_id == run.id, AgentNodeRun.node_key == "evaluate",
            AgentNodeRun.status == "success",
        ).order_by(AgentNodeRun.id.desc()).limit(1)).scalar()
        node = db.get(AgentNodeRun, node_id) if node_id is not None else None
        if node is None or not isinstance(node.output_payload, dict):
            db.rollback()
            return {**report, "status": "unverified", "reason": "real_evaluation_evidence_unavailable",
                    "persisted_memory_rows_after": db.query(AgentContextRecord).count()}
        context = ToolExecutionContext(
            db=db, user_id=int(run.user_id), project_id=int(run.project_id), run_id=int(run.id),
            node_key="persist", run_input=deepcopy(run.input_payload),
            artifacts=deepcopy((run.run_context or {}).get("artifacts") or {}),
        )
        arguments = {key: deepcopy(context.run_input[key])
                     for key in ("source_run_id", "requirement", "reference_content", "upload")}
        arguments["evaluation"] = deepcopy(node.output_payload)
        persist_test_case_evaluation(context, arguments)
        first_ids = list(context.artifacts["test_evaluation"]["feedback_record_ids"])
        persist_test_case_evaluation(context, arguments)
        second_ids = list(context.artifacts["test_evaluation"]["feedback_record_ids"])
        assert first_ids == second_ids
        records = [db.get(AgentContextRecord, record_id) for record_id in first_ids]
        assert all(record.scope == "project" and record.scope_id == str(run.project_id)
                   and record.project_id == run.project_id
                   and record.origin_run_id == arguments["source_run_id"] for record in records)
        report.update(status="passed", evaluation_run_id=int(run.id),
                      source_run_id=arguments["source_run_id"], project_id=int(run.project_id),
                      candidate_ids_in_transaction=first_ids, idempotent=True,
                      candidate_statuses=[record.status for record in records],
                      business_confirmation="unverified_no_human_decision_generated")
        db.rollback()
        report["persisted_memory_rows_after"] = db.query(AgentContextRecord).count()
        assert report["persisted_memory_rows_after"] == before
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
