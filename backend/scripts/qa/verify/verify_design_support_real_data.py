"""只读真实模型产物，验证完整支持派生及当前规划的批次承接；不制造模型结论。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import validate
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_batching import (
    _generation_fact_groups, _test_design_items_by_module, generation_layout_from_inputs,
    prepare_test_case_batches,
)
from modules.agent_platform.test_generation_facts import bound_fact_ids, derive_test_design_item_ids, validate_generation_design_supports
from modules.agent_platform.test_generation_planning import current_planning_support_decisions
from modules.agent_platform.test_generation_schemas import GENERATION_BATCH_ITEM_SCHEMA, PLAN_SCHEMA
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine


def verify(historical: Path, current: Path | None):
    historical_bytes = historical.read_bytes()
    state = json.loads(historical_bytes)["checkpoint"]["state"]
    modules = state["accepted_plan"]["plan"]["business_modules"]
    designs = _test_design_items_by_module(modules=modules)
    results, rejected_claims = [], []
    for index, item in enumerate(state["generation_inputs"]):
        record = state["generation_records"][index]
        if not record or record["output"].get("result_type") == "planning_feedback":
            continue
        module_index = item["batch"]["module_index"]
        module = modules[module_index]
        # 历史候选相关边只用于验证集合算法，不冒充新版独立支持审查的结论。
        routes = {route["fact_id"]: [designs[module_index][value]["test_design_item_id"]
                                     for value in route["test_design_item_indexes"]]
                  for route in module["fact_design_routes"]}
        order = [item["test_design_item_id"] for item in designs[module_index]]
        derived = derive_test_design_item_ids(case_fact_bindings=record["output"]["case_fact_bindings"],
            fact_design_item_ids=routes, required_fact_ids=list(routes), available_fact_ids=list(routes),
            required_design_item_ids=order)
        support = {key: {fact_id for fact_id, values in routes.items() if key in values} for key in order}
        for case, binding in zip(record["output"]["test_cases"], record["output"]["case_fact_bindings"], strict=True):
            expected = [key for key in order if support[key] <= bound_fact_ids(binding)]
            assert derived[case["case_id"]] == expected
            lost = sorted(set(case["test_design_item_ids"]) - set(expected))
            if lost:
                rejected_claims.append({"item_index": index, "case_id": case["case_id"],
                                        "partial_support_design_ids": lost})
        results.append({"item_index": index, "case_count": len(record["output"]["test_cases"])})
    assert rejected_claims, "真实旧产物中必须出现局部支持被错误计为完整覆盖的案例"
    report = {"status": "passed", "model_requests": 0, "online_database_writes": 0,
              "historical_source_sha256": hashlib.sha256(historical_bytes).hexdigest(),
              "algorithm_only_not_semantic_support_approval": True,
              "historical_batches": results, "rejected_partial_support_claims": rejected_claims}
    if current is not None:
        engine = sqlite_engine(current)
        try:
            with Session(engine) as db:
                with sqlite3.connect("file:" + current.resolve().as_posix() + "?mode=ro", uri=True) as connection:
                    raw_state, raw_input, run_id = connection.execute(
                        "SELECT sdk_state, input_payload, run_id FROM agent_node_runs ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                checkpoint = json.loads(raw_state)["action_checkpoint"]
                current_state = checkpoint["state"]
                run = db.get(AgentRun, run_id)
                context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                    run_id=run.id, node_key="resolve_generation", run_input=deepcopy(run.input_payload),
                    action_input=json.loads(raw_input), action_state=deepcopy(current_state),
                    artifacts=deepcopy(run.run_context["artifacts"]))
                planning = current_state["planning"]
                report["current_checkpoint_sha256"] = action_state_hash(checkpoint)
                if planning["gap_inputs"]:
                    decisions = current_planning_support_decisions(context, {
                        "plan": planning["plan"], "prepared_items": planning["route_inputs"],
                        "route_records": planning["route_records"], "repair_records": planning["gap_records"],
                        "review_revisions": planning["route_review_revisions"],
                    })
                    report["actual_review_decisions_revalidated"] = sum(len(value) for value in decisions.values())
                if current_state["accepted_plan"] and current_state["generation_inputs"]:
                    plan = current_state["accepted_plan"]["plan"]
                    validate(plan, PLAN_SCHEMA)
                    inputs = current_state["generation_inputs"]
                    layout = generation_layout_from_inputs(inputs)
                    rebuilt = prepare_test_case_batches(context, {
                        "plan": plan, "effective_facts": current_state["effective_facts"]["effective_facts"],
                        "case_budget": context.action_input["case_budget"],
                        "batch_case_limit": context.action_input["batch_case_limit"], "generation_layout": layout,
                    })["items"]
                    assert rebuilt == inputs
                    assigned = Counter()
                    required_by_module = {}
                    for item in inputs:
                        validate(item, GENERATION_BATCH_ITEM_SCHEMA)
                        assert len(_generation_fact_groups(item["authoritative_facts"])) == 1
                        contract = item["case_fact_contract"]
                        assigned.update(contract["required_test_design_item_ids"])
                        required_by_module.setdefault(item["batch"]["module_index"], []).extend(contract["required_fact_ids"])
                        routes = contract["fact_design_item_ids"]
                        module = plan["business_modules"][item["batch"]["module_index"]]
                        catalog = _test_design_items_by_module(modules=plan["business_modules"])[item["batch"]["module_index"]]
                        for support in module["design_supports"]:
                            design_id = catalog[support["test_design_item_index"]]["test_design_item_id"]
                            if design_id in contract["required_test_design_item_ids"]:
                                assert set(support["fact_ids"]) == {key for key, values in routes.items() if design_id in values}
                    assert assigned and all(count == 1 for count in assigned.values())
                    assert all(len(values) == len(set(values)) for values in required_by_module.values())
                    report["current_complete_support_batching"] = {"batch_count": len(inputs),
                        "design_count": len(assigned), "unique_design_ownership": True,
                        "complete_support_context": True, "layout_roundtrip": True}
                else:
                    report["current_complete_support_batching"] = "尚无本版真实已验收规划及生成输入，不伪造通过结果"
                assert not db.new and not db.dirty and not db.deleted
        finally:
            engine.dispose()
    assert historical.read_bytes() == historical_bytes
    return report


async def verify_generated_supports(plan_path: Path, module_index: int, *, execute: bool, recorded_path: Path | None = None):
    """真实支持规划的一整个模块组件验证；不冒充权威重跑或正式流程。"""
    from sqlalchemy import text
    from core.db.database import SessionLocal
    from modules.agent_platform.contracts import AgentMapConfig
    from modules.agent_platform.output_repair import OutputValidationError
    from modules.agent_platform.run_repository import AgentRunRepository
    from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
    from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
    from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, safe_exception_evidence
    from scripts.qa.verify.verify_generation_agent_real_model import (
        _capture_current_definitions, provider_error_categories, provider_error_message,
    )
    from modules.agent_platform.test_generation_batching import merge_grounded_generation_batches

    source_bytes = plan_path.read_bytes()
    source = json.loads(source_bytes)
    assert source["status"] == "passed"
    inputs = [item for item in source["pre_authority_generation_inputs"] if item["batch"]["module_index"] == module_index]
    assert inputs, "必须选择真实规划中的完整模块"
    seed = json.loads(Path(source["seed_report"]).read_text(encoding="utf-8"))
    seed_source = json.loads(Path(seed["source_report"]).read_text(encoding="utf-8"))
    report = {"status": "prepared", "scope": "real_support_plan_module_generation_component",
              "formal_workflow_run": False, "authority_reexecuted": False, "natural_parent_action_selection": False,
              "online_database_writes": 0, "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "module_index": module_index, "inputs": inputs, "responses": []}
    diagnostics = ModelRequestDiagnostics()
    recorded_bytes = recorded_path.read_bytes() if recorded_path is not None else None
    recorded = json.loads(recorded_bytes) if recorded_bytes is not None else None
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            repo = AgentRunRepository(db)
            run = repo.get_run(run_id=seed_source["source_run_id"])
            node = repo.latest_node_run(run_id=run.id, node_key="resolve_generation")
            assert action_state_hash(repo.read_node_input(node)) == seed["raw_input_sha256"]
            run_hash = action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload])
            snapshots, _, descriptions = _capture_current_definitions(db, run, "test_case_generator")
            definition, tools = snapshots["test_case_generator"]
            report["definition_snapshots"] = descriptions
            if recorded is not None:
                assert not execute and recorded["inputs"] == inputs and recorded["definition_snapshots"] == descriptions
                assert len(recorded["responses"]) == len(inputs)
                report["recorded_model_report"] = str(recorded_path.resolve())
                report["recorded_model_report_sha256"] = hashlib.sha256(recorded_bytes).hexdigest()
            if execute or recorded is not None:
                report["status"] = "running"
                for item_index, item in enumerate(inputs):
                    validate(item, GENERATION_BATCH_ITEM_SCHEMA)
                    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                        node_key="complete-support-component", run_input=deepcopy(run.input_payload),
                        artifacts=deepcopy(run.run_context["artifacts"]))
                    model_input, _ = _project_agent_map_input(definition=definition, raw_item=item)
                    contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor="testing.postprocess_generation_batch_item"))
                    if recorded is not None:
                        saved = recorded["responses"][item_index]
                        assert saved["batch_id"] == item["batch"]["batch_id"]
                        assert contract.validate(definition, context, deepcopy(saved["candidate"])) == saved["validated_output"]
                        report["responses"].append(deepcopy(saved))
                        continue
                    budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
                    response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                        execution_context=context, input_payload=model_input,
                        request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                        disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                        request_budget=budget, captured_definitions=snapshots, request_diagnostics=diagnostics)
                    assert response.validated_output == contract.validate(definition, context, deepcopy(response.output))
                    report["responses"].append({"batch_id": item["batch"]["batch_id"], "candidate": response.output,
                        "validated_output": response.validated_output, "usage": response.usage,
                        "budget_usage": response.budget_usage, "tool_observations": response.tool_observations})
                generation = merge_grounded_generation_batches(context, {"generation_inputs": inputs,
                    "generation_records": [{"item_index": index, "output": response["validated_output"]}
                                           for index, response in enumerate(report["responses"])],
                    "case_budget": sum(item["case_budget"] for item in inputs)})
                validate_generation_design_supports(generation, inputs)
                routes = {}
                for item in inputs:
                    for fact_id, design_ids in item["case_fact_contract"]["fact_design_item_ids"].items():
                        routes[fact_id] = list(dict.fromkeys([*routes.get(fact_id, []), *design_ids]))
                derived = derive_test_design_item_ids(case_fact_bindings=generation["case_fact_bindings"],
                    fact_design_item_ids=routes, available_fact_ids=list(routes),
                    required_design_item_ids=[value for item in inputs for value in item["case_fact_contract"]["required_test_design_item_ids"]])
                report["extra_supported_unclaimed_designs"] = [{"case_id": case["case_id"],
                    "design_ids": sorted(set(derived[case["case_id"]]) - set(case["test_design_item_ids"]))}
                    for case in generation["test_cases"] if set(derived[case["case_id"]]) - set(case["test_design_item_ids"])]
                # 只破坏真实已通过产物的覆盖声明，验证最终关口仍阻止遗漏。
                missing = inputs[0]["case_fact_contract"]["required_test_design_item_ids"][0]
                damaged = deepcopy(generation)
                for case in damaged["test_cases"]:
                    case["test_design_item_ids"] = [value for value in case["test_design_item_ids"] if value != missing]
                try:
                    validate_generation_design_supports(damaged, inputs)
                except OutputValidationError as error:
                    assert missing in error.details["missing_test_design_item_ids"]
                else:
                    raise AssertionError("实际产物遗漏完整覆盖声明却被通过")
                unsupported_claim = next((
                    (case["case_id"], design_id) for case in generation["test_cases"]
                    for design_ids in derived.values() for design_id in design_ids
                    if design_id not in derived[case["case_id"]]
                ), None)
                if unsupported_claim is not None:
                    damaged = deepcopy(generation)
                    case = next(case for case in damaged["test_cases"] if case["case_id"] == unsupported_claim[0])
                    case["test_design_item_ids"].append(unsupported_claim[1])
                    try:
                        validate_generation_design_supports(damaged, inputs)
                    except OutputValidationError as error:
                        assert error.details["module_index"] == module_index
                    else:
                        raise AssertionError("实际用例冒领其他意图却被通过")
                    report["unsupported_claim_rejected"] = {"case_id": unsupported_claim[0], "design_id": unsupported_claim[1]}
                report.update(status="passed", case_count=len(generation["test_cases"]),
                              actual_generated_module_support_gate=True, missing_claim_rejected=True)
            db.refresh(run)
            assert action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload]) == run_hash
            assert not db.new and not db.dirty and not db.deleted
            report["source_run_unchanged"] = True
        except Exception as error:
            diagnostics.record_failure(error)
            report.update(status="failed", error_type=type(error).__name__, exception_chain=safe_exception_evidence(error),
                provider_error_categories=provider_error_categories(error), provider_error_message_redacted=provider_error_message(error),
                error_message=str(error) if isinstance(error, (ValueError, AssertionError, OutputValidationError)) else "异常见结构化诊断")
        finally:
            report["model_requests"] = diagnostics.requests
            db.rollback()
    assert plan_path.read_bytes() == source_bytes
    if recorded_path is not None:
        assert recorded_path.read_bytes() == recorded_bytes
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path)
    parser.add_argument("--current-sqlite", type=Path)
    parser.add_argument("--supported-plan-report", type=Path)
    parser.add_argument("--module-index", type=int)
    parser.add_argument("--generated-report", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("请使用新路径保存独立验证证据")
    if args.supported_plan_report is not None:
        if args.module_index is None or args.historical is not None or args.current_sqlite is not None:
            parser.error("模块组件诊断需要 --module-index，不能混入历史回放参数")
        if args.generated_report is not None and args.execute:
            parser.error("真实回执复验不能同时重新调用模型")
        result = asyncio.run(verify_generated_supports(args.supported_plan_report, args.module_index,
                                                      execute=args.execute, recorded_path=args.generated_report))
    else:
        if args.historical is None or args.execute or args.module_index is not None or args.generated_report is not None:
            parser.error("只读回放需要 --historical；模型执行请明确指定真实支持规划报告")
        result = verify(args.historical, args.current_sqlite)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "real_batch_count": len(result.get("historical_batches", [])),
                      "partial_support_case_count": len(result.get("rejected_partial_support_claims", [])),
                      "case_count": result.get("case_count"), "error_type": result.get("error_type"),
                      "current_complete_support_batching": result.get("current_complete_support_batching")}, ensure_ascii=False))
    if result["status"] not in {"passed", "prepared"}:
        raise SystemExit(1)
