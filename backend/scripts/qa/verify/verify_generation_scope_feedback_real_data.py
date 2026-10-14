"""用持久真实批次和明确的人工诊断标注验证范围报告；不发模型请求或写线上库。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.test_generation_escalation import (
    report_generation_scope_problem,
    validate_generation_scope_feedback,
)


def verify(db, options: argparse.Namespace) -> dict:
    node = db.get(AgentNodeRun, options.node_id)
    if node is None or node.node_key != "generation":
        raise ValueError("必须引用真实生成节点")
    run = db.get(AgentRun, node.run_id)
    raw_inputs = AgentRunRepository(db).read_node_input(node)["items"]
    item_input = raw_inputs[options.item_index]
    item_state = next(item for item in node.sdk_state["items"] if item["item_index"] == options.item_index)
    before = action_state_hash({"input": raw_inputs, "sdk_state": node.sdk_state,
                                "output": node.output_payload, "run_context": run.run_context})
    facts = {fact["fact_id"]: fact for fact in item_input["authoritative_facts"]}
    if len(options.fact_id) < 2 or len(set(options.fact_id)) != len(options.fact_id):
        raise ValueError("诊断必须选择至少两条互不重复的真实事实")
    if not set(options.fact_id).issubset(facts):
        raise ValueError("诊断选择了该真实批次之外的事实")
    arguments = {
        "issues": [{
            "kind": options.kind,
            "explanation": options.explanation,
            "scenario_groups": [{
                "fact_ids": [fact_id],
                "test_design_item_ids": item_input["case_fact_contract"]["fact_design_item_ids"][fact_id],
                "condition": {"fact_id": fact_id, "quote": facts[fact_id]["assertion"]},
            } for fact_id in options.fact_id],
            "requested_change": options.requested_change,
        }],
    }
    original_arguments = deepcopy(arguments)
    envelope = report_generation_scope_problem(None, arguments)
    result = validate_generation_scope_feedback(item_input, envelope)
    assert validate_generation_scope_feedback(item_input, result) == result
    assert arguments == original_arguments
    assert result["planning_feedback"]["case_budget"] == item_input["case_budget"]
    assert result["planning_feedback"]["assessment_status"] == "unreviewed"
    assert "test_cases" not in result

    # 换用另一个实际批次而非伪造编号，确认范围外原文不能迁入当前批次报告。
    other_index = next(index for index, item in enumerate(raw_inputs)
                       if not set(options.fact_id).issubset(
                           fact["fact_id"] for fact in item["authoritative_facts"]))
    rejected: list[dict] = []
    for label, target_input, candidate in [
        ("real_report_against_other_real_batch", raw_inputs[other_index], envelope),
        ("stale_identity_against_other_real_batch", raw_inputs[other_index], result),
    ]:
        try:
            validate_generation_scope_feedback(target_input, candidate)
        except OutputValidationError as error:
            rejected.append({"check": label, "error": str(error), "details": error.details})
        else:
            raise AssertionError(f"领域报告接受了错误范围: {label}")
    original_submissions = [call for call in item_state["tool_calls"]
                            if call["tool_key"] == "submit_generation_batch"]
    for call in original_submissions:
        try:
            validate_generation_scope_feedback(item_input, call["arguments"])
        except OutputValidationError:
            pass
        else:
            raise AssertionError("普通用例候选被混入范围反馈通道")
    assert original_submissions
    assert before == action_state_hash({"input": raw_inputs, "sdk_state": node.sdk_state,
                                       "output": node.output_payload, "run_context": run.run_context})
    assert not db.new and not db.dirty and not db.deleted
    return {
        "status": "passed", "source_run_id": run.id, "source_node_id": node.id,
        "source_item_index": options.item_index, "comparison_item_index": other_index,
        "verification_scope": "domain_validation_of_analyst_annotation_on_persisted_real_evidence",
        "feedback_author": "analyst_annotation_not_model_output",
        "accepted_feedback": result,
        "actual_submission_case_counts": [len(call["arguments"]["test_cases"]) for call in original_submissions],
        "ordinary_candidates_not_accepted_as_feedback": len(original_submissions),
        "rejections": rejected, "validation_idempotent": True,
        "raw_inputs_candidates_and_budget_unchanged": True, "source_database_evidence_unchanged": True,
        "model_requests_sent": 0, "online_database_writes": 0, "mysql_transaction": "READ ONLY",
        "natural_model_escalation_verified": False, "semantic_conflict_independently_reviewed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--item-index", type=int, required=True)
    parser.add_argument("--fact-id", action="append", required=True)
    parser.add_argument("--kind", required=True, choices=[
        "mutually_exclusive_preconditions", "lifecycle_discontinuity", "batch_capacity_pressure",
    ])
    parser.add_argument("--explanation", required=True)
    parser.add_argument("--requested-change", choices=["repartition", "reallocate", "replan"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("报告已存在，拒绝覆盖历史证据")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = verify(db, options)
        db.rollback()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in [
        "status", "source_run_id", "source_item_index", "actual_submission_case_counts",
        "ordinary_candidates_not_accepted_as_feedback", "natural_model_escalation_verified",
    ]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
