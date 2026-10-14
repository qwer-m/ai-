"""对真实支持复核输入执行当前 SDK 与契约；允许替换由原资产派生的上下文，不恢复线上状态。"""

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
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_support_contract_real_data import (
    POSTPROCESSOR, REVIEWER, production_hashes, save_report,
)
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript
from scripts.qa.verify.prepare_agent_workflow_e2e_real_data import digest as persisted_hash


async def verify(options):
    seed_bytes = options.inputs.read_bytes()
    seed = json.loads(seed_bytes)
    facts_bytes = options.facts.read_bytes()
    facts = json.loads(facts_bytes)
    assert seed["source_run_id"] == facts["source_run_id"]
    by_id = {fact["fact_id"]: fact for fact in facts["authoritative_facts"]}
    assert len(by_id) == len(facts["authoritative_facts"])
    frozen = production_hashes()
    report = {
        "status": "preparing", "started_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": seed["source_run_id"], "parent_call_id": seed["parent_call_id"],
        "inputs_file": str(options.inputs.resolve()), "facts_file": str(options.facts.resolve()),
        "production_sha256": frozen, "online_database_writes": 0,
        "formal_workflow_run": False, "natural_parent_action_selection": False,
        "parent_state_applied": False, "historical_rejected_candidates_accepted": False,
        "scope": "真实复核输入的上下文流通与提交协议组件；语义结论须单独核对",
        "results": [],
    }
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, seed["source_run_id"])
            assert run is not None
            before = persisted_hash({column.key: getattr(run, column.key) for column in run.__table__.columns})
            snapshots, _, descriptions = _capture_current_definitions(db, run, REVIEWER)
            definition, tools = snapshots[REVIEWER]
            report["definition_snapshots"] = descriptions
            for original in seed["items"]:
                started = perf_counter()
                trusted = deepcopy(original["input"])
                assert action_state_hash(trusted) == original["input_sha256"]
                changed = []
                for fact in trusted["candidate_facts"]:
                    current = by_id[fact["fact_id"]]
                    # 只搬运原资产派生的上下文，不能修改事实断言、状态或业务值。
                    for key in set(fact) & set(current) - {"source_context"}:
                        assert fact[key] == current[key], (fact["fact_id"], key)
                    if fact.get("source_context") != current.get("source_context"):
                        changed.append(fact["fact_id"])
                    if "source_context" in current:
                        fact["source_context"] = deepcopy(current["source_context"])
                    else:
                        fact.pop("source_context", None)
                model_input, projection = _project_agent_map_input(definition=definition, raw_item=trusted)
                budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
                context = ToolExecutionContext(
                    db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                    node_key="support-input-component", run_input=deepcopy(run.input_payload),
                )
                item = {
                    "status": "prepared", "module_index": trusted["module_index"],
                    "historically_executed": original["historically_executed"],
                    "original_input_sha256": original["input_sha256"],
                    "input": trusted, "input_sha256": action_state_hash(trusted),
                    "changed_context_fact_ids": changed, "input_projection": projection,
                    "model_input": model_input, "model_input_sha256": action_state_hash(model_input),
                    "request_budget": budget, "retry_feedback": original.get("retry_feedback"),
                }
                report["results"].append(item)
                save_report(options.output, report)
                if not options.execute:
                    continue
                diagnostics = SupportTranscript(
                    options.output.with_suffix(f".module-{trusted['module_index']}.requests.jsonl"),
                    trusted["module_index"],
                )

                def save_budget(snapshot):
                    item["budget_usage"] = snapshot
                    save_report(options.output, report)

                hooks = AutonomousBudgetHooks(budget, on_change=save_budget)
                contract = AgentOutputContract(deepcopy(trusted), AgentMapConfig(item_postprocessor=POSTPROCESSOR))
                try:
                    assert production_hashes() == frozen
                    response = await run_agent_async(
                        db=db, agent_definition=definition, tool_definitions=tools, execution_context=context,
                        input_payload=deepcopy(model_input),
                        request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                        retry_feedback=json.dumps(item["retry_feedback"], ensure_ascii=False) if item["retry_feedback"] else None,
                        disable_server_output_schema=True, skip_output_postprocessor=True,
                        output_contract=contract, request_budget=budget, shared_budget_hooks=hooks,
                        require_validated_submission=True, captured_definitions=snapshots,
                        request_diagnostics=diagnostics,
                    )
                    assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
                    item.update(status="contract_passed", raw_output=deepcopy(response.output),
                                normalized_output=deepcopy(response.validated_output),
                                model_usage=deepcopy(response.usage), tool_observations=deepcopy(response.tool_observations))
                except Exception as error:
                    item.update(status="failed", error_type=type(error).__name__,
                                exception_chain=safe_exception_evidence(error),
                                provider_error_categories=provider_error_categories(error),
                                provider_error_message_redacted=provider_error_message(error),
                                diagnostic=deepcopy(getattr(error, "diagnostic", None)))
                finally:
                    item.update(budget_usage=hooks.snapshot(), model_requests=deepcopy(diagnostics.requests),
                                elapsed_seconds=round(perf_counter() - started, 3))
                    assert production_hashes() == frozen
                    save_report(options.output, report)
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert before == persisted_hash({column.key: getattr(run, column.key) for column in run.__table__.columns})
            assert options.inputs.read_bytes() == seed_bytes and options.facts.read_bytes() == facts_bytes
            assert production_hashes() == frozen
            report["historical_run_and_input_files_unchanged"] = True
            report["status"] = "prepared" if not options.execute else (
                "contract_passed" if all(item["status"] == "contract_passed" for item in report["results"]) else "failed"
            )
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__)
            raise
        finally:
            db.rollback()
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            save_report(options.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    options = parser.parse_args()
    if options.output.exists() or list(options.output.parent.glob(options.output.stem + ".module-*.requests.jsonl")):
        raise FileExistsError("报告或请求证据已经存在")
    report = asyncio.run(verify(options))
    print(json.dumps({"status": report["status"], "results": [
        {key: item.get(key) for key in ("module_index", "status", "model_usage", "error_type")}
        for item in report["results"]
    ]}, ensure_ascii=False))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
