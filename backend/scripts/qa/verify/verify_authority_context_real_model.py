"""从真实支持裁决选取同模块事实，准备或执行现有来源权威专家的独立组件验证。"""

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
from modules.agent_platform.test_generation_semantics import (
    merge_authority_reconciliation, postprocess_source_semantics_item, prepare_authority_reconciliation,
)
from modules.knowledge_base_components.document.document_asset_service import load_document_manifest
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_support_contract_real_data import production_hashes, save_report
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript


def source_evidence_model_view(model_input):
    """仅诊断候选摘要是否干扰原文核对，不改变完整事实和验收契约。"""
    view = deepcopy(model_input)
    removed_ids = []
    for fact in view["authoritative_facts"]:
        assert fact["source_anchor"].get("table_cell")
        assert fact.get("source_context", {}).get("table_contexts")
        del fact["assertion"]
        removed_ids.append(fact["fact_id"])
    return view, removed_ids


async def verify(options):
    started = perf_counter()
    support_bytes = options.support_report.read_bytes()
    support = json.loads(support_bytes)
    assert support["status"] == "contract_passed"
    source_path = Path(support["source_report"])
    source_bytes = source_path.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == support["source_report_sha256"]
    source = json.loads(source_bytes)
    assert source["status"] == "passed" and source["source_run_id"] == support["source_run_id"]
    input_path = Path(source["input_path"])
    input_bytes = input_path.read_bytes()
    saved = json.loads(input_bytes)
    fragments = {item["source_item_index"]: item["input"] for item in saved["source_fragments"]}
    assert action_state_hash(list(fragments.values())) == source["source_input_sha256"]
    assert load_document_manifest(source["document_id"])["source_sha256"] == source["source_sha256"]
    original_item = support["input"]
    decisions = support["normalized_output"]["decisions"]
    assert decisions and support["normalized_output"]["module_index"] == original_item["module_index"]
    selected_ids = sorted({fact_id for decision in decisions for fact_id in decision["fact_ids"]})
    candidate_ids = {fact["fact_id"] for fact in original_item["candidate_facts"]}
    assert selected_ids and set(selected_ids) <= candidate_ids
    assert {fact_id for decision in decisions for gap in decision.get("support_gaps", [])
            for fact_id in gap["evidence_fact_ids"]} <= set(selected_ids)
    frozen_code = production_hashes()
    report = {
        "status": "preparing", "source_run_id": source["source_run_id"],
        "started_at": datetime.now(timezone.utc).isoformat(), "production_sha256": frozen_code,
        "source_report": str(source_path.resolve()), "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_inputs": str(input_path.resolve()), "source_inputs_sha256": hashlib.sha256(input_bytes).hexdigest(),
        "support_report": str(options.support_report.resolve()), "support_report_sha256": hashlib.sha256(support_bytes).hexdigest(),
        "scope": "同一真实模块支持裁决引用事实的来源权威组件选择，不是正式已批准的事实路由",
        "formal_workflow_run": False, "parent_state_applied": False, "approved_fact_route": False,
        "online_database_writes": 0, "model_requests_sent": 0,
        "source_module_index": original_item["module_index"],
        "selected_fact_ids": selected_ids,
        "selection_evidence": [{"test_design_item_index": item["test_design_item_index"],
            "disposition": item["disposition"], "fact_ids": item["fact_ids"]} for item in decisions],
        "semantic_quality_assessment": "待核对真实表头与正文冲突的模型裁决，不以契约通过代替语义结论",
    }
    hooks = None
    diagnostics = SupportTranscript(options.output.with_suffix(".requests.jsonl"), original_item["module_index"])
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source["source_run_id"])
            assert run is not None
            run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key="source_text", run_input=deepcopy(run.input_payload), artifacts=deepcopy(saved["source_artifacts"]))
            canonical = {}
            checks = []
            for record in source["fragments"]:
                item = fragments[record["source_item_index"]]
                assert action_state_hash(item) == record["input_sha256"]
                normalized = postprocess_source_semantics_item(context, {
                    "item_input": deepcopy(item), "item_output": deepcopy(record["candidate"])})
                assert normalized == record["normalized_output"]
                assert action_state_hash(normalized) == record["normalized_sha256"]
                for fact in normalized["authoritative_facts"]:
                    assert fact["fact_id"] not in canonical
                    canonical[fact["fact_id"]] = deepcopy(fact)
                checks.append({"source_item_index": record["source_item_index"],
                    "input_sha256": action_state_hash(item), "normalized_sha256": action_state_hash(normalized)})
            assert set(selected_ids) <= canonical.keys()
            facts = [canonical[fact_id] for fact_id in selected_ids]
            support_by_id = {fact["fact_id"]: fact for fact in original_item["candidate_facts"]}
            for fact in facts:
                assert all(support_by_id[fact["fact_id"]][key] == value for key, value in fact.items())
            module = {"name": original_item["module_name"], "objective": original_item["module_objective"],
                      "fact_ids": selected_ids}
            prepared = prepare_authority_reconciliation(context, {
                "plan": {"business_modules": [module]}, "authoritative_facts": facts})
            assert prepared["review_module_count"] == 1
            reviewer_input = prepared["items"][0]
            assert {fact["fact_id"] for fact in reviewer_input["authoritative_facts"]} == set(selected_ids)
            assert {fact["fact_id"]: fact for fact in reviewer_input["authoritative_facts"]} == {
                fact["fact_id"]: fact for fact in facts}
            snapshots, _, descriptions = _capture_current_definitions(db, run, "test_authority_reconciliation_reviewer")
            definition, tools = snapshots["test_authority_reconciliation_reviewer"]
            model_input, projection = _project_agent_map_input(definition=definition, raw_item=reviewer_input)
            assert model_input["authoritative_facts"] == reviewer_input["authoritative_facts"]
            contract = AgentOutputContract(deepcopy(reviewer_input),
                AgentMapConfig(item_postprocessor="testing.postprocess_authority_reconciliation_item"))
            production_model_input_hash = action_state_hash(model_input)
            removed_assertion_ids = []
            if options.source_evidence_only:
                model_input, removed_assertion_ids = source_evidence_model_view(model_input)
                # 模型视图裁掉摘要，契约及其身份仍使用完整原始输入，不能把实验变成生产路由。
                assert contract.input_payload == reviewer_input
                assert removed_assertion_ids == [fact["fact_id"] for fact in reviewer_input["authoritative_facts"]]
            report["projection_experiment"] = {
                "enabled": options.source_evidence_only,
                "non_production_projection": options.source_evidence_only,
                "mode": "source_evidence_only" if options.source_evidence_only else "production",
                "description": "仅从模型视图删除真实表格事实的 assertion；完整原输入、来源锚点和上下文保持不变",
                "removed_assertion_fact_ids": removed_assertion_ids,
                "production_model_input_sha256": production_model_input_hash,
                "model_view_sha256": action_state_hash(model_input),
                "full_input_sha256": action_state_hash(reviewer_input),
                "contract_input_sha256": action_state_hash(contract.input_payload),
                "contract_identity_uses_full_input": contract.input_payload == reviewer_input,
            }
            reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
            report.update(source_normalization_checks=checks, input=reviewer_input,
                input_sha256=action_state_hash(reviewer_input), model_input=model_input,
                model_input_sha256=action_state_hash(model_input), input_projection=projection,
                definition_snapshots=descriptions, request_budget=reservation,
                fact_count=len(facts), source_context_fact_count=sum("source_context" in fact for fact in facts),
                authority_prepare=deepcopy(context.artifacts["authority_reconciliation_prepare"]),
                selected_subscope_coverage_validated=True, full_source_context_preserved=True)
            assert production_hashes() == frozen_code
            if options.execute:
                context.node_key = "authority-context-component"

                def persist_budget(snapshot):
                    report["budget_usage"] = snapshot
                    save_report(options.output, report)

                hooks = AutonomousBudgetHooks(reservation, on_change=persist_budget)
                report["status"] = "running"
                save_report(options.output, report)
                response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                    execution_context=context, input_payload=deepcopy(model_input),
                    request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                    disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                    request_budget=reservation, shared_budget_hooks=hooks, require_validated_submission=True,
                    captured_definitions=snapshots, request_diagnostics=diagnostics)
                assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
                merged = merge_authority_reconciliation(context, {
                    "authoritative_facts": facts, "prepared_items": [reviewer_input],
                    "reconciliation_records": [{"item_index": 0, "output": deepcopy(response.validated_output)}]})
                report.update(status="contract_passed", raw_output=deepcopy(response.output),
                    normalized_output=deepcopy(response.validated_output), merged_output=merged,
                    raw_output_sha256=action_state_hash(response.output),
                    normalized_output_sha256=action_state_hash(response.validated_output),
                    model_usage=response.usage, tool_calls=deepcopy(context.tool_calls),
                    tool_observations=deepcopy(response.tool_observations),
                    status_transitions=[{"fact_id": fact["fact_id"], "before": canonical[fact["fact_id"]]["status"],
                                         "after": fact["status"]} for fact in merged["authoritative_facts"]
                                        if fact["status"] != canonical[fact["fact_id"]]["status"]])
            else:
                report["status"] = "prepared"
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert run_hash == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            assert production_hashes() == frozen_code
            assert options.support_report.read_bytes() == support_bytes and source_path.read_bytes() == source_bytes
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support-report", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-evidence-only", action="store_true",
        help="非生产投影诊断：仅从模型视图删除表格事实 assertion，完整验收输入不变")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists() or options.output.with_suffix(".requests.jsonl").exists():
        raise FileExistsError("报告或模型请求证据已存在，请使用新路径")
    options.output.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(verify(options))
    print(json.dumps({key: result.get(key) for key in ("status", "fact_count", "source_context_fact_count",
        "model_requests_sent", "error_type", "error_message")}, ensure_ascii=False))
    if result["status"] == "failed":
        raise SystemExit(1)
