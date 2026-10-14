"""复用真实被预算拒绝的视觉复查输入，验证图片计量及真实模型完整提交。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash, ordered_action_receipts
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks, input_protocol_reservation
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from scripts.qa.verify.prepare_agent_workflow_e2e_real_data import source_evidence
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions


async def verify(options):
    report = {"status": "preparing", "source_run_id": options.run_id,
              "formal_workflow_run": False, "online_database_writes": 0}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        ids = db.query(AgentNodeRun.id).filter_by(run_id=run.id, node_key="resolve_generation").all()
        node = db.get(AgentNodeRun, max(i for i, in ids))
        checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
        before = source_evidence(db, run.id)
        receipt = next(item for item in reversed(ordered_action_receipts(checkpoint))
                       if item["tool_key"] == "reinspect_planning_source" and item["status"] == "failed")
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            artifacts=deepcopy(run.run_context.get("artifacts") or {}),
            action_input=AgentRunRepository(db).read_node_input(node), action_state=deepcopy(checkpoint["state"]))
        child = generation._planning_context(context, context.action_state)
        action = planning.prepare_source_reinspection(child, json.loads(receipt["arguments"]["arguments_json"]))
        trusted = deepcopy(action["items"][0])
        failed = next(item for item in reversed(node.sdk_state["tool_calls"])
                      if item["tool_key"] == "agent:test_source_semantics_analyst" and item.get("status") == "failed")
        assert failed["input_hash"] == action_state_hash(trusted)
        snapshots, _, _ = _capture_current_definitions(db, run, "test_source_semantics_analyst")
        definition, tools = snapshots["test_source_semantics_analyst"]
        contract = AgentOutputContract(trusted, AgentMapConfig(item_postprocessor="testing.postprocess_source_semantics_item"),
            candidate_validator=partial(planning._validate_single_candidate, planning.apply_source_reinspection, child, action))
        model_input, projection = _project_agent_map_input(definition=definition, raw_item=trusted)
        budget_before = deepcopy(checkpoint["budget"])
        hooks = AutonomousBudgetHooks(budget_before["limits"], checkpoint=budget_before)
        diagnostics = ModelRequestDiagnostics()
        state_hash = action_state_hash(context.action_state)
        report.update(source_node_run_id=node.id, source_evidence=before, input_sha256=action_state_hash(trusted),
                      input_projection=projection, budget_before=budget_before, source_arguments=receipt["arguments"],
                      source_page=trusted["page_number"])
        options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                execution_context=context, input_payload=model_input,
                request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                skip_output_postprocessor=True, output_contract=contract,
                request_budget=budget_before["limits"], shared_budget_hooks=hooks,
                require_validated_submission=True, captured_definitions=snapshots, request_diagnostics=diagnostics)
            request = diagnostics.requests[0]
            prior = budget_before["charged"]["input_tokens"]
            old_reservation = request["measured_context_bytes"] + input_protocol_reservation()
            current_reservation = request["reserved_input_tokens"]
            limit = budget_before["limits"]["input_tokens"]
            assert prior + current_reservation <= limit < prior + old_reservation
            assert 0 < response.usage["input_tokens"] <= current_reservation
            assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
            report.update(status="passed", old_input_reservation=old_reservation,
                current_input_reservation=current_reservation, usage=response.usage,
                normalized_output=response.validated_output, raw_output=response.output,
                source_contract_passed=True, original_limits_preserved=True)
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__,
                          diagnostic=deepcopy(getattr(error, "diagnostic", None)))
        finally:
            if diagnostics.requests:
                request = diagnostics.requests[0]
                prior = budget_before["charged"]["input_tokens"]
                old_reservation = request["measured_context_bytes"] + input_protocol_reservation()
                current_reservation = request["reserved_input_tokens"]
                limit = budget_before["limits"]["input_tokens"]
                usage = request.get("usage") or {}
                report.update(old_input_reservation=old_reservation,
                    current_input_reservation=current_reservation,
                    budget_admission_passed=(prior + current_reservation <= limit < prior + old_reservation),
                    real_usage_within_reservation=(0 < usage.get("input_tokens", 0) <= current_reservation),
                    source_contract_passed=report.get("source_contract_passed", False),
                    original_limits_preserved=(hooks.snapshot()["limits"] == budget_before["limits"]))
            assert state_hash == action_state_hash(context.action_state)
            assert not db.new and not db.dirty and not db.deleted
            db.rollback()
            db.execute(text("SET TRANSACTION READ ONLY"))
            assert before == source_evidence(db, run.id)
            db.rollback()
            report.update(model_requests=diagnostics.requests, budget_after=hooks.snapshot(),
                          source_database_unchanged=True, state_unchanged=True)
            options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("status", "source_run_id", "old_input_reservation",
        "current_input_reservation", "usage", "source_contract_passed")}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    report = asyncio.run(verify(options))
    if report["status"] != "passed":
        sys.exit(1)
