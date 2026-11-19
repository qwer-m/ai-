"""只读审计正式运行的规划行动、最终版本和下游完整输入，不调用模型。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent, AgentWorkflowDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.sdk_adapter import AgentOutputContract
from modules.agent_platform.test_generation_planning_agent import validate_planning_resolution


def verify(db, run_id):
    repo = AgentRunRepository(db)
    run = db.get(AgentRun, run_id)
    assert run and run.status == "success", "正式运行尚未成功"
    graph = parse_execution_definition(db.get(AgentWorkflowDefinition, run.workflow_definition_id).definition)
    nodes = {spec.node_key: repo.latest_node_run(run_id=run_id, node_key=spec.node_key) for spec in graph.nodes}
    assert all(node is not None and node.status == "success" for node in nodes.values())
    outputs = {key: node.output_payload for key, node in nodes.items()}
    planning = nodes["resolve_planning"]
    saved = planning.sdk_state["action_checkpoint"]
    raw_input = repo.read_node_input(planning)
    before = action_state_hash([run.run_context, run.output_payload, planning.sdk_state, planning.output_payload])
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                   run_id=run.id, node_key=planning.node_key, run_input=deepcopy(run.input_payload),
                                   artifacts=deepcopy(run.run_context["artifacts"]),
                                   action_input=deepcopy(raw_input), action_state=deepcopy(saved["state"]))
    normalized = validate_planning_resolution(context, {"input_payload": deepcopy(raw_input), "output": {"completed": True}})
    assert normalized == planning.output_payload
    assert action_state_hash(saved["state"]) == saved["state_hash"]
    assert saved["budget"]["pending_requests"] == 0
    assert saved["budget"]["completed_requests"] + saved["budget"]["interrupted_requests"] == saved["budget"]["charged"]["attempted_requests"]
    attempt_ids = [value for (value,) in db.query(AgentNodeRun.id).filter(
        AgentNodeRun.run_id == run.id, AgentNodeRun.node_key == planning.node_key,
        AgentNodeRun.id <= planning.id,
    ).order_by(AgentNodeRun.id).all()]
    previous_attempts = []
    for identifier in attempt_ids[:-1]:
        previous = db.get(AgentNodeRun, identifier)
        checkpoint = (previous.sdk_state or {}).get("action_checkpoint")
        if checkpoint:
            previous_attempts.append({"node_run_id": identifier, "status": previous.status,
                                      "budget": deepcopy(checkpoint["budget"]),
                                      "error_diagnostic": deepcopy(previous.sdk_state.get("error_diagnostic"))})
    # 未返回用量的失败请求在恢复后继续计入 interrupted，不应被误判为仍在执行或要求清零。
    prior_budget = previous_attempts[-1]["budget"] if previous_attempts else {}
    expected_interrupted = prior_budget.get("interrupted_requests", 0) + prior_budget.get("pending_requests", 0)
    assert saved["budget"]["interrupted_requests"] == expected_interrupted
    downstream = []
    for key in ("prepare_authority", "effective_facts", "prepare_generation", "chain_context"):
        spec = next(spec for spec in graph.nodes if spec.node_key == key)
        assert "resolve_planning" in spec.depends_on
        assert "source_semantics" not in spec.depends_on and "routed_plan" not in spec.depends_on
        expected = _node_input(run, spec, outputs)
        actual = repo.read_node_input(nodes[key])
        assert expected == actual, f"{key} 没有消费当前正式规划输出"
        downstream.append({"node_key": key, "node_run_id": nodes[key].id,
                           "complete_input_sha256": action_state_hash(actual), "matches_current_graph_mapping": True})
    actions = []
    # MySQL 的 JSON 对象不保留写入顺序；行动时间线沿追加事件的主键恢复。
    event_ids = [value for (value,) in db.query(AgentRunEvent.id).filter(
        AgentRunEvent.run_id == run.id, AgentRunEvent.node_run_id.in_(attempt_ids),
        AgentRunEvent.event_type == "agent_action_started",
    ).order_by(AgentRunEvent.id).all()]
    action_ids = list(dict.fromkeys(db.get(AgentRunEvent, identifier).payload["tool_call_id"] for identifier in event_ids))
    assert set(action_ids) == set(saved["receipts"])
    for call_id in action_ids:
        receipt = saved["receipts"][call_id]
        result = (receipt.get("result") or {}).get("output")
        observation = json.loads(result) if isinstance(result, str) else (result or {})
        actions.append({"tool_key": receipt["tool_key"], "status": receipt["status"],
                        "pre_state_hash": receipt["pre_state_hash"], "post_state_hash": receipt.get("post_state_hash"),
                        "changed": observation.get("changed"),
                        "versions": {key: observation[key] for key in ("source_version", "draft_version", "plan_version") if key in observation},
                        "unsupported_count": len(observation.get("unsupported_designs") or []),
                        "evidence_id": observation.get("evidence_id"),
                        "error": deepcopy(receipt.get("error"))})
    assert actions[-1]["tool_key"] == "finish_planning" and actions[-1]["status"] == "completed"
    drafts = nodes["plan_batches"]
    draft_inputs = repo.read_node_input(drafts)["items"]
    draft_definition = db.get(AgentDefinition, drafts.agent_definition_id)
    draft_outputs = {item["item_index"]: item["output"] for item in drafts.output_payload["items"]}
    draft_submissions = []
    for item in drafts.sdk_state["items"]:
        index = item["item_index"]
        observations = item["tool_observations"]
        accepted = [value for value in observations if value["status"] == "accepted"]
        assert len(accepted) == 1 and observations[-1] == accepted[0]
        call = next(value for value in item["tool_calls"] if value["tool_call_id"] == accepted[0]["tool_call_id"])
        assert call["tool_key"] == "submit_result"
        candidate = AgentOutputContract(deepcopy(draft_inputs[index])).validate(draft_definition, context, deepcopy(call["arguments"]))
        assert candidate == draft_outputs[index]
        assert item["budget_usage"]["pending_requests"] == item["budget_usage"]["interrupted_requests"] == 0
        draft_submissions.append({"item_index": index, "statuses": [value["status"] for value in observations],
                                  "rejected_feedback": [deepcopy(value["feedback"]) for value in observations if value["status"] == "rejected"],
                                  "usage": item["usage"], "accepted_candidate_revalidated": True})
    after = action_state_hash([run.run_context, run.output_payload, planning.sdk_state, planning.output_payload])
    assert before == after
    return {"status": "passed", "observed_at": datetime.now(timezone.utc).isoformat(), "run_id": run.id,
            "node_run_id": planning.id, "graph_node_count": len(nodes),
            "final_versions": {key: saved["state"][key] for key in ("source_version", "draft_version", "plan_version")},
            "source_ref": saved["state"]["goal"]["source_ref"], "actions": actions,
            "budget": saved["budget"], "previous_planning_attempts": previous_attempts,
            "unanswered_requests_retained_after_restore": expected_interrupted,
            "full_domain_output_revalidated": True,
            "current_output_sha256": action_state_hash(normalized), "downstream_inputs": downstream,
            "initial_draft_node_run_id": drafts.id, "initial_draft_submissions": draft_submissions,
            "record_unchanged": True, "database_writes": 0, "model_requests_sent": 0,
            "boundary": "正式持久产物及完整输入复核；未验证HTTP、Celery或在线强杀恢复。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，请指定新路径")
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = verify(db, args.run_id)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "run_id": args.run_id, "report": str(args.output)}, ensure_ascii=False))
