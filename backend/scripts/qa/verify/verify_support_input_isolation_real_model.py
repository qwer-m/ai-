"""保持真实已执行输入不变，独立检验 reviewer 投影隔离；不恢复旧来源或规划状态。"""

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
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import safe_exception_evidence
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_support_contract_real_data import POSTPROCESSOR, REVIEWER, production_hashes, save_report
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript


def inspect_isolation(definition, trusted_input):
    """只核对真实输入的字段投影，不修改候选事实或伪造非空权威反馈。"""
    original = deepcopy(trusted_input)
    projected, diagnostics = _project_agent_map_input(definition=definition, raw_item=trusted_input)
    assert trusted_input == original and diagnostics["applied"]
    full_feedback = trusted_input["_planning_feedback"]
    model_feedback = projected["_planning_feedback"]
    assert full_feedback["previous_decisions"]
    assert "previous_decisions" not in model_feedback
    assert set(model_feedback) == {"question", "downstream_problems"}
    assert all(model_feedback[key] == full_feedback[key] for key in model_feedback)
    assert "source_contexts" not in projected
    assert len(projected["candidate_facts"]) == len(trusted_input["candidate_facts"])
    routed_facts = 0
    context_facts = 0
    for full, view in zip(trusted_input["candidate_facts"], projected["candidate_facts"], strict=True):
        assert full["fact_id"] == view["fact_id"]
        assert all(full[key] == value for key, value in view.items())
        assert "current_test_design_item_indexes" not in view
        assert "source_context_ref" not in view and "source_anchor" not in view
        routed_facts += bool(full.get("current_test_design_item_indexes"))
        if "source_context" in full:
            context_facts += 1
            assert view["source_context"] == full["source_context"]
        else:
            assert "source_context" not in view
    assert routed_facts, "真实基线必须实际含路由标签，不能用不存在的字段声称完成隔离"
    authority_problems = [value for value in full_feedback["downstream_problems"]
                          if value.get("origin") == "preparation"
                          and value.get("payload", {}).get("kind") == "authority_support_gap"]
    authority_inactive = {value for problem in authority_problems
                          for value in problem["payload"]["authority_inactive_fact_ids"]}
    assert definition.runtime_config["input_projection"]["_planning_feedback"]["downstream_problems"] is True
    return projected, {
        **diagnostics, "complete_input_sha256": action_state_hash(trusted_input),
        "model_input_sha256": action_state_hash(projected),
        "candidate_fact_count": len(trusted_input["candidate_facts"]),
        "original_nonempty_routing_label_count": routed_facts,
        "original_previous_decision_count": len(full_feedback["previous_decisions"]),
        "routing_labels_removed_from_model": True, "previous_decisions_removed_from_model": True,
        "original_question_and_downstream_problems_unchanged": True,
        "candidate_order_and_projected_fact_fields_unchanged": True,
        "source_context_fact_count": context_facts,
        "authority_problem_count": len(authority_problems), "authority_inactive_fact_count": len(authority_inactive),
        "authority_fields_retained_by_projection_spec": True,
        "nonempty_authority_constraints_exercised": bool(authority_problems),
        "authority_verification_limit": "原输入权威反馈为空时仅验证字段保留规则，不声称真实非空排除约束已经执行",
        "complete_input_unchanged": True,
    }


async def verify(options):
    started = perf_counter()
    source_bytes = options.source_report.read_bytes()
    source = json.loads(source_bytes)
    assert source["status"] == "contract_passed"
    trusted_input = deepcopy(source["input"])
    assert action_state_hash(trusted_input) == source["input_sha256"]
    assert source["final_decision"]["review_input_sha256"] == source["input_sha256"]
    frozen_code = production_hashes()
    report = {
        "status": "preparing", "started_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": source["source_run_id"], "source_report": str(options.source_report.resolve()),
        "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(), "production_sha256": frozen_code,
        "scope": "同一真实输入在隔离历史字段投影下的独立支持判断对照组件",
        "source_state_restored": False, "planning_state_restored": False, "planning_state_applied": False,
        "runtime_cache_restored": False, "formal_workflow_run": False, "natural_parent_action_selection": False,
        "online_database_writes": 0, "model_requests_sent": 0,
        "input": trusted_input, "input_sha256": action_state_hash(trusted_input),
        "historical_response_for_comparison": deepcopy(source["normalized_output"]),
        "historical_model_input_sha256": source["model_input_sha256"],
    }
    hooks = None
    diagnostics = SupportTranscript(options.output.with_suffix(".requests.jsonl"), trusted_input["module_index"])
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source["source_run_id"])
            assert run is not None
            run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="support-input-isolation-component", run_input=deepcopy(run.input_payload))
            snapshots, _, descriptions = _capture_current_definitions(db, run, REVIEWER)
            definition, tools = snapshots[REVIEWER]
            model_input, isolation = inspect_isolation(definition, trusted_input)
            budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
            contract = AgentOutputContract(deepcopy(trusted_input), AgentMapConfig(item_postprocessor=POSTPROCESSOR))
            historical_validated = contract.validate(definition, context, deepcopy(source["raw_output"]))
            assert historical_validated == source["normalized_output"]
            report.update(definition_snapshots=descriptions, input_projection=isolation,
                model_input=deepcopy(model_input), model_input_sha256=action_state_hash(model_input),
                request_budget=budget, request_budget_input="model_input", output_contract_input="complete_input",
                historical_response_current_contract_revalidated=True, historical_semantic_reassessment=False)
            assert context.action_state == {} and context.action_input == {}
            if options.execute:
                assert production_hashes() == frozen_code

                def persist_budget(snapshot):
                    report["budget_usage"] = snapshot
                    save_report(options.output, report)

                hooks = AutonomousBudgetHooks(budget, on_change=persist_budget)
                report["status"] = "running"
                save_report(options.output, report)
                response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                    execution_context=context, input_payload=deepcopy(model_input),
                    request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                    disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                    request_budget=budget, shared_budget_hooks=hooks, require_validated_submission=True,
                    captured_definitions=snapshots, request_diagnostics=diagnostics)
                report.update(raw_output=deepcopy(response.output), normalized_output=deepcopy(response.validated_output),
                    model_usage=deepcopy(response.usage), tool_observations=deepcopy(response.tool_observations))
                assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
                assert production_hashes() == frozen_code
                report["status"] = "contract_passed"
            else:
                report["status"] = "prepared"
            assert action_state_hash(trusted_input) == source["input_sha256"]
            assert options.source_report.read_bytes() == source_bytes
            assert context.action_state == {} and context.action_input == {}
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert run_hash == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            report.update(original_report_input_and_online_run_unchanged=True,
                          production_unchanged_during_preflight=production_hashes() == frozen_code)
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__, exception_chain=safe_exception_evidence(error),
                provider_error_categories=provider_error_categories(error),
                provider_error_message_redacted=provider_error_message(error),
                error_message=str(error) if isinstance(error, (ValueError, AssertionError, OutputValidationError))
                    else "异常详情见结构化诊断")
        finally:
            if hooks is not None:
                report["budget_usage"] = hooks.snapshot()
            report["model_requests"] = deepcopy(diagnostics.requests)
            report["model_requests_sent"] = len(diagnostics.requests)
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            assert not db.new and not db.dirty and not db.deleted
            db.rollback()
            save_report(options.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists() or options.output.with_suffix(".requests.jsonl").exists():
        raise FileExistsError("报告或请求证据已存在，请使用新输出路径")
    options.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(options))
    print(json.dumps({key: report.get(key) for key in ("status", "source_run_id", "input_sha256",
        "model_input_sha256", "model_requests_sent", "error_type", "error_message")}, ensure_ascii=False))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
