"""将真实表格事实重新规范化后独立复核真实旧意图；不混用旧路由，不执行父状态或整轮规划。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import safe_exception_evidence
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_semantics import postprocess_source_semantics_item
from modules.knowledge_base_components.document.document_asset_service import load_document_manifest
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_support_contract_real_data import production_hashes, save_report
from scripts.qa.verify.verify_table_source_real_model import SourceTranscript


class SupportTranscript(SourceTranscript):
    """复用实际 SDK 正文记录，只将诊断对象标为当前真实模块。"""

    def _record(self, event, item):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"module_index": self.fragment_index, "event": event, **item}, ensure_ascii=False) + "\n")
        print(json.dumps({"module_index": self.fragment_index, "event": event,
                          "status": item.get("status"), "sequence": item.get("sequence")}, ensure_ascii=False), flush=True)


async def verify(options):
    started = perf_counter()
    source_bytes, support_bytes = options.source_report.read_bytes(), options.support_report.read_bytes()
    source, support = json.loads(source_bytes), json.loads(support_bytes)
    assert source["status"] == support["status"] == "passed"
    assert source["source_run_id"] == support["source_run_id"]
    input_path = Path(source["input_path"])
    input_bytes = input_path.read_bytes()
    saved = json.loads(input_bytes)
    fragments = {item["source_item_index"]: item["input"] for item in saved["source_fragments"]}
    assert action_state_hash(list(fragments.values())) == source["source_input_sha256"]
    assert load_document_manifest(source["document_id"])["source_sha256"] == source["source_sha256"]
    original_item = next(item for item in support["prepared_inputs"] if item["module_index"] == options.module_index)
    indexes = options.design_indexes or [0, 2, 3]
    assert len(indexes) == len(set(indexes))
    selected = [deepcopy(item) for item in original_item["missing_test_design_items"]
                if item["test_design_item_index"] in indexes]
    assert {item["test_design_item_index"] for item in selected} == set(indexes)
    frozen_code = production_hashes()
    report = {"status": "preparing", "source_run_id": source["source_run_id"],
        "source_report": str(options.source_report.resolve()), "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_inputs": str(input_path.resolve()), "source_inputs_sha256": hashlib.sha256(input_bytes).hexdigest(),
        "support_report": str(options.support_report.resolve()), "support_report_sha256": hashlib.sha256(support_bytes).hexdigest(),
        "production_sha256": frozen_code, "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "真实表格来源上下文与旧覆盖意图的独立专业复核组件",
        "online_database_writes": 0, "model_requests_sent": 0,
        "parent_state_applied": False, "full_planning_verified": False, "formal_workflow_run": False,
        "historical_fact_routes_injected": False, "semantic_quality_assessment": "待人工核对实际结论及理由"}
    hooks = None
    diagnostics = SupportTranscript(options.output.with_suffix(".requests.jsonl"), options.module_index)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source["source_run_id"])
            assert run is not None
            run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key="source_text", run_input=deepcopy(run.input_payload), artifacts=deepcopy(saved["source_artifacts"]))
            facts, checks = [], []
            for record in source["fragments"]:
                assert record["status"] == "passed"
                item = fragments[record["source_item_index"]]
                assert action_state_hash(item) == record["input_sha256"]
                assert action_state_hash(record["normalized_output"]) == record["normalized_sha256"]
                candidate = deepcopy(record["candidate"])
                normalized = postprocess_source_semantics_item(context, {"item_input": item, "item_output": candidate})
                prior = {fact["fact_id"]: fact for fact in record["normalized_output"]["authoritative_facts"]}
                for fact in normalized["authoritative_facts"]:
                    assert {key: value for key, value in fact.items() if key != "source_context"} == {
                        key: value for key, value in prior[fact["fact_id"]].items() if key != "source_context"}
                    assert fact["source_anchor"]["page_number"] == source["page_number"]
                    facts.append(deepcopy(fact))
                checks.append({"source_item_index": record["source_item_index"], "raw_candidate_sha256": action_state_hash(candidate),
                    "input_sha256": action_state_hash(item), "normalized_sha256": action_state_hash(normalized),
                    "fact_count": len(normalized["authoritative_facts"]), "assertion_status_anchor_unchanged": True})
            assert len({fact["fact_id"] for fact in facts}) == len(facts)
            reviewer_input = {key: deepcopy(original_item[key]) for key in ("module_index", "module_name", "module_objective")}
            candidates = [{**deepcopy(fact), "current_test_design_item_indexes": []} for fact in facts]
            reviewer_input.update(missing_test_design_items=selected,
                candidate_facts=candidates, additional_scope_ids=[])
            snapshots, _, descriptions = _capture_current_definitions(db, run, "test_planning_route_gap_reviewer")
            definition, tools = snapshots["test_planning_route_gap_reviewer"]
            model_input, projection = _project_agent_map_input(definition=definition, raw_item=reviewer_input)
            for original, projected in zip(candidates, model_input["candidate_facts"], strict=True):
                assert original["fact_id"] == projected["fact_id"]
                for key in ("assertion", "scope_id", "status", "value_policy", "governed_values", "governed_by"):
                    if key in original:
                        assert original[key] == projected[key]
                assert "current_test_design_item_indexes" not in projected
                if "source_context" in original:
                    assert original["source_context"] == projected["source_context"]
                else:
                    assert "source_context" not in projected
                assert "source_context_ref" not in projected
            assert "source_contexts" not in model_input
            report.update(source_normalization_checks=checks, fact_count=len(facts),
                source_context_fact_count=sum("source_context" in fact for fact in facts),
                candidate_facts_sha256=action_state_hash(facts), input=reviewer_input,
                input_sha256=action_state_hash(reviewer_input), definition_snapshots=descriptions,
                model_input=model_input, model_input_sha256=action_state_hash(model_input),
                input_projection=projection, source_context_projection_exact=True)
            assert production_hashes() == frozen_code
            if options.execute:
                context.node_key = "table-support-component"
                reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)

                def persist_budget(snapshot):
                    report["budget_usage"] = snapshot
                    save_report(options.output, report)

                hooks = AutonomousBudgetHooks(reservation, on_change=persist_budget)
                contract = AgentOutputContract(deepcopy(reviewer_input),
                    AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"))
                report.update(status="running", request_budget=reservation)
                save_report(options.output, report)
                response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                    execution_context=context, input_payload=deepcopy(model_input),
                    request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                    disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                    request_budget=reservation, shared_budget_hooks=hooks, require_validated_submission=True,
                    captured_definitions=snapshots, request_diagnostics=diagnostics)
                report.update(raw_output=deepcopy(response.output), normalized_output=deepcopy(response.validated_output),
                    model_usage=response.usage, tool_calls=deepcopy(context.tool_calls),
                    tool_observations=deepcopy(response.tool_observations))
                assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
                report.update(status="contract_passed", raw_output_sha256=action_state_hash(response.output),
                              normalized_output_sha256=action_state_hash(response.validated_output))
            else:
                report["status"] = "prepared"
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert run_hash == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            assert production_hashes() == frozen_code
            assert options.source_report.read_bytes() == source_bytes and options.support_report.read_bytes() == support_bytes
            assert input_path.read_bytes() == input_bytes
            report["source_files_and_online_run_unchanged"] = True
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__, exception_chain=safe_exception_evidence(error),
                provider_error_categories=provider_error_categories(error), provider_error_message_redacted=provider_error_message(error),
                error_message=str(error) if isinstance(error, (ValueError, AssertionError)) else "异常详情见结构化诊断")
            if "context" in locals():
                report["tool_calls"] = deepcopy(context.tool_calls)
        finally:
            if hooks is not None:
                report["budget_usage"] = hooks.snapshot()
            report["model_requests"] = deepcopy(diagnostics.requests)
            report["model_requests_sent"] = len(diagnostics.requests)
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            db.rollback()
            save_report(options.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--support-report", type=Path, required=True)
    parser.add_argument("--module-index", type=int, default=2)
    parser.add_argument("--design-index", type=int, action="append", dest="design_indexes")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists() or options.output.with_suffix(".requests.jsonl").exists():
        raise FileExistsError("报告或请求证据已存在，请使用新输出路径")
    options.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(options))
    print(json.dumps({key: report.get(key) for key in ("status", "fact_count", "source_context_fact_count",
        "model_requests_sent", "model_usage", "error_type", "error_message")}, ensure_ascii=False))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
