"""只读提取真实失败支持会话，验证当前提交契约并可显式执行真实模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.models.chatcmpl_converter import Converter
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, _output_submission_tool, _parse_function_arguments, run_agent_async,
)
from scripts.qa.verify.prepare_agent_workflow_e2e_real_data import source_evidence
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_support_contract_real_data import production_hashes, save_report
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript


class SubmissionTranscript(SupportTranscript):
    """同时保存实际工具声明，核对 Schema 是否在模型边界被额外包装。"""

    def start(self, context, agent, system_prompt, input_items):
        super().start(context, agent, system_prompt, input_items)
        self._record("sdk_tools", {"tools": [Converter.tool_to_openai(tool) for tool in agent.tools]})


async def verify(options):
    started = perf_counter()
    report = {"source_run_id": options.run_id, "status": "preparing", "online_database_writes": 0,
              "formal_workflow_run": False, "started_at": datetime.now(timezone.utc).isoformat()}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        ids = db.query(AgentNodeRun.id).filter_by(run_id=run.id, node_key="resolve_generation").all()
        node = db.get(AgentNodeRun, max(identifier for (identifier,) in ids))
        before = source_evidence(db, run.id)
        checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
        failed = next(call for call in reversed(node.sdk_state["tool_calls"])
                      if call.get("status") == "failed" and call["tool_key"] == "agent:test_planning_route_gap_reviewer")
        trusted = next(deepcopy(item) for item in checkpoint["state"]["planning"]["gap_inputs"]
                       if action_state_hash(item) == failed["input_hash"])
        snapshots, _, descriptions = _capture_current_definitions(db, run, "test_planning_route_gap_reviewer")
        definition, tools = snapshots["test_planning_route_gap_reviewer"]
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key="support-submission-verification", run_input=deepcopy(run.input_payload),
            artifacts=deepcopy(run.run_context.get("artifacts") or {}))
        source_path = options.output.with_suffix(".source.json")
        source_path.write_text(json.dumps({"run_id": run.id, "node_id": node.id,
            "project_id": run.project_id, "user_id": run.user_id, "run_input": run.input_payload,
            "artifacts": context.artifacts, "node_input": AgentRunRepository(db).read_node_input(node),
            "checkpoint": checkpoint, "failed_call": failed, "source_evidence": before,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        model_input, projection = _project_agent_map_input(definition=definition, raw_item=trusted)
        reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
        tool = _output_submission_tool(definition, context)
        assert tool.strict_json_schema
        assert tool.params_json_schema["properties"].keys() == definition.output_schema["properties"].keys()
        checks = []
        for record in checkpoint["validated_agent_calls"].values():
            if record["identity"]["agent_key"] != definition.agent_key:
                continue
            item = next(item for item in checkpoint["state"]["planning"]["gap_inputs"]
                        if action_state_hash(item) == record["identity"]["input_hash"])
            contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"))
            raw = _parse_function_arguments("submit_result", tool.params_json_schema,
                                            json.dumps(record["raw_output"], ensure_ascii=False))
            assert contract.validate(definition, context, raw) == record["output"]
            checks.append({"module_index": item["module_index"], "input_sha256": action_state_hash(item),
                           "output_sha256": record["output_hash"]})
        frozen = production_hashes()
        report.update(source_evidence=before, source_path=str(source_path.resolve()),
            input=trusted, input_sha256=action_state_hash(trusted), input_projection=projection,
            definition_snapshots=descriptions, request_budget=reservation, production_sha256=frozen,
            strict_submission=Converter.tool_to_openai(tool), historical_success_revalidation=checks,
            historical_failure=failed, historical_run_modified=False)
        hooks = AutonomousBudgetHooks(reservation)
        diagnostics = SubmissionTranscript(options.output.with_suffix(".requests.jsonl"), trusted["module_index"])
        save_report(options.output, report)
        try:
            if options.execute:
                contract = AgentOutputContract(deepcopy(trusted), AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"))
                response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                    execution_context=context, input_payload=model_input,
                    request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                    disable_server_output_schema=True, skip_output_postprocessor=True,
                    output_contract=contract, request_budget=reservation, shared_budget_hooks=hooks,
                    require_validated_submission=True, captured_definitions=snapshots, request_diagnostics=diagnostics)
                assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
                report.update(status="contract_passed", raw_output=response.output,
                    normalized_output=response.validated_output, usage=response.usage,
                    tool_observations=response.tool_observations)
            else:
                report["status"] = "prepared"
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__,
                diagnostic=deepcopy(getattr(error, "diagnostic", None)),
                provider_error_categories=provider_error_categories(error),
                provider_error_message=provider_error_message(error))
        finally:
            assert not db.new and not db.dirty and not db.deleted
            db.rollback()
            db.execute(text("SET TRANSACTION READ ONLY"))
            assert before == source_evidence(db, run.id)
            assert frozen == production_hashes()
            report.update(budget=hooks.snapshot(), requests=diagnostics.requests,
                          elapsed_seconds=round(perf_counter()-started, 3))
            save_report(options.output, report)
            db.rollback()
    print(json.dumps({key: report[key] for key in ("status", "source_run_id", "elapsed_seconds")}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    options = parser.parse_args()
    for path in (options.output, options.output.with_suffix(".source.json"), options.output.with_suffix(".requests.jsonl")):
        if path.exists():
            raise FileExistsError(path)
    result = asyncio.run(verify(options))
    if result["status"] == "failed":
        sys.exit(1)
