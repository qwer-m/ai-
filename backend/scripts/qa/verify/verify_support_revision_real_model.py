"""用真实矛盾复核回执执行一次定向专业复核，并检查完整支持布局；不声称自然父决策或全流程成功。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import validate
from sqlalchemy import text

from core.db.database import SessionLocal
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import safe_exception_evidence
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_batching import generation_layout_from_inputs, prepare_test_case_batches
from modules.agent_platform.test_generation_schemas import GENERATION_BATCH_ITEM_SCHEMA, PLAN_SCHEMA
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions, provider_error_categories


async def verify(seed_path: Path):
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    started = perf_counter()
    result = {"status": "failed", "scope": "targeted_specialist_and_pre_authority_layout",
              "natural_parent_action_selection": False, "formal_workflow_run": False,
              "online_database_writes": 0, "seed_report": str(seed_path.resolve())}
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            repo = AgentRunRepository(db)
            source_report = json.loads(Path(seed["source_report"]).read_text(encoding="utf-8"))
            run = repo.get_run(run_id=source_report["source_run_id"])
            node = repo.latest_node_run(run_id=run.id, node_key="resolve_generation")
            raw_input = repo.read_node_input(node)
            assert action_state_hash(raw_input) == seed["raw_input_sha256"]
            original_run_hash = action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(run.run_context["artifacts"]), action_input=deepcopy(raw_input),
                action_state=deepcopy(seed["current_initial_planning_state"]))
            observation = planning.observe_planning_state(context)
            by_module = {}
            for decision in observation["unsupported_designs"]:
                by_module.setdefault(decision["module_index"], []).append(decision["test_design_item_index"])
            arguments = {"design_selections": [{"module_index": index, "test_design_item_indexes": indexes}
                         for index, indexes in by_module.items()], "problem_ids": [],
                         "question": "请重新核实以下真实结论及其理由是否一致；依据候选原文决定完整支持集合，不预设结果："
                                     + json.dumps(observation["unsupported_designs"], ensure_ascii=False)}
            action = planning.prepare_route_gap_review(context, arguments)
            assert len(action["items"]) == 1, "本诊断只执行真实未决模块的一次复核"
            snapshots, originals, descriptions = _capture_current_definitions(db, run, "test_planning_route_gap_reviewer")
            definition, tools = snapshots["test_planning_route_gap_reviewer"]
            item = action["items"][0]
            budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=item)
            contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"),
                candidate_validator=partial(planning._validate_single_candidate, planning.apply_route_gap_review, context, action))
            result.update(input=deepcopy(item), definition_snapshots=descriptions,
                          prior_unsupported=observation["unsupported_designs"], request_budget=budget)
            response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                execution_context=context, input_payload=item,
                request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                disable_server_output_schema=True, skip_output_postprocessor=True,
                output_contract=contract, request_budget=budget, captured_definitions=snapshots)
            assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
            result.update(candidate=deepcopy(response.output), validated_output=deepcopy(response.validated_output),
                          model_usage=response.usage, model_requests=response.model_requests, budget_usage=response.budget_usage)
            previous_records = deepcopy(context.action_state["gap_records"])
            planning.apply_route_gap_review(context, action, [response.validated_output])
            assert context.action_state["gap_records"] == previous_records
            result["original_review_receipts_preserved"] = True
            result["planning_state"] = deepcopy(context.action_state)
            resolved = planning._resolved_output(context)
            validate(resolved["plan"], PLAN_SCHEMA)
            result["resolved_plan"] = resolved["plan"]
            # 验证布局使用真实提取的 effective 事实；权威协调尚未执行，不能记成生成阶段验收。
            batch_args = {"plan": resolved["plan"], "effective_facts": [fact for fact in resolved["source_semantics"]["authoritative_facts"]
                         if fact["status"] == "effective"], "case_budget": raw_input["case_budget"],
                         "batch_case_limit": raw_input["batch_case_limit"]}
            batches = prepare_test_case_batches(context, batch_args)["items"]
            for item in batches:
                validate(item, GENERATION_BATCH_ITEM_SCHEMA)
            layout = generation_layout_from_inputs(batches)
            assert prepare_test_case_batches(context, {**batch_args, "generation_layout": layout})["items"] == batches
            result.update(status="passed", pre_authority_generation_inputs=batches, layout_roundtrip=True,
                          batch_count=len(batches), case_count=sum(item["case_budget"] for item in batches))
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload]) == original_run_hash
            result["source_run_unchanged"] = True
        except Exception as error:
            result.update(error_type=type(error).__name__, exception_chain=safe_exception_evidence(error),
                          provider_error_categories=provider_error_categories(error),
                          error_message=str(error) if isinstance(error, (ValueError, AssertionError, OutputValidationError)) else "异常详情见结构化诊断")
        finally:
            db.rollback()
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("诊断报告已存在")
    result = asyncio.run(verify(args.seed_report))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result.get(key) for key in ("status", "model_usage", "batch_count", "case_count", "error_type", "error_message")}, ensure_ascii=False))
    if result["status"] != "passed":
        raise SystemExit(1)
