"""以真实历史原始响应重新验收首轮规划，准备或执行补充真实范围的定向复核。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.request_diagnostics import safe_exception_evidence
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_planning import current_planning_support_decisions
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_agent_real_model import source_fingerprint
from scripts.qa.verify.verify_planning_support_contract_real_data import (
    POSTPROCESSOR, REVIEWER, prepare_context, production_hashes, require_rejected, save_report,
)
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript


def current_decisions(context):
    state = context.action_state
    return current_planning_support_decisions(context, {
        "plan": state["plan"], "prepared_items": state["route_inputs"],
        "route_records": state["route_records"], "repair_records": state["gap_records"],
        "review_revisions": state["route_review_revisions"],
    })


def evidence_selection(value):
    try:
        module_index, design_index = (int(part) for part in value.split(":"))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("真实证据设计项须填写 module_index:design_index") from error
    return module_index, design_index


def check_projection(definition, item):
    """逐事实核对模型引用能无损回到可信原文，预算视图不成为验收输入。"""
    original = deepcopy(item)
    projected, diagnostics = _project_agent_map_input(definition=definition, raw_item=item)
    assert diagnostics["applied"] and item == original
    assert "source_contexts" not in projected
    assert len(projected["candidate_facts"]) == len(item["candidate_facts"])
    context_facts = 0
    for full, view in zip(item["candidate_facts"], projected["candidate_facts"], strict=True):
        assert view["fact_id"] == full["fact_id"]
        assert all(full[key] == value for key, value in view.items())
        assert "source_context_ref" not in view and "source_anchor" not in view
        if "source_context" in full:
            context_facts += 1
            assert view["source_context"] == full["source_context"]
        else:
            assert "source_context" not in view
    return projected, {
        **diagnostics, "full_input_sha256": action_state_hash(item),
        "model_input_sha256": action_state_hash(projected),
        "candidate_fact_count": len(item["candidate_facts"]), "source_context_fact_count": context_facts,
        "fact_order_and_retained_fields_unchanged": True,
        "all_model_inline_contexts_equal_full_context": True,
        "full_input_unchanged": True,
    }


def check_feedback_references(state):
    """仅验真实已落入状态的回执引用，不制造新增事实或未决结论。"""
    feedback = planning._route_feedback(state)
    facts_checked, contexts_checked = 0, 0
    for entry in feedback:
        inputs = [item for item in state["gap_inputs"] if item["module_index"] == entry["module_index"]]
        inputs += [revision["input"] for revision in state["route_review_revisions"]
                   if revision["input"]["module_index"] == entry["module_index"]]
        candidates = {fact["fact_id"]: fact for item in inputs for fact in item["candidate_facts"]}
        cited = {fact_id for decision in entry["review"]["decisions"]
                 for fact_id in [*decision["fact_ids"], *(value for gap in decision["support_gaps"]
                                                        for value in gap["evidence_fact_ids"]) ]}
        assert cited == {fact["fact_id"] for fact in entry["supporting_facts"]}
        for fact in entry["supporting_facts"]:
            full = candidates[fact["fact_id"]]
            assert all(fact[key] == full[key] for key in ("fact_id", "scope_id", "assertion"))
            assert fact["scope_id"] in entry["scope_ids"]
            assert fact.get("source_context") == full.get("source_context")
            assert "source_context_ref" not in fact
            facts_checked += 1
            contexts_checked += "source_context" in full
    return {"feedback_module_count": len(feedback), "actual_cited_facts_checked": facts_checked,
            "actual_cited_context_facts_checked": contexts_checked,
            "all_feedback_references_use_complete_real_facts": True,
            "no_model_projection_reference_leaks_to_feedback": True}


async def verify(options):
    started = perf_counter()
    source_bytes = options.support_report.read_bytes()
    source = json.loads(source_bytes)
    assert source["status"] == "passed" and source["all_initial_modules_applied"]
    frozen_code = production_hashes()
    report = {
        "status": "preparing", "started_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": source["source_run_id"], "support_report": str(options.support_report.resolve()),
        "support_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "historical_data_usage": "只读历史原始响应按当前契约重新结构验收；不是恢复当前执行身份或复用运行缓存",
        "historical_semantic_reassessment": False, "runtime_cache_restored": False,
        "formal_workflow_run": False, "natural_parent_action_selection": False,
        "online_database_writes": 0, "model_requests_sent": 0, "production_sha256": frozen_code,
        "scope": "首轮真实规划历史契约验收及一项 additional_scope_ids 定向复核组件",
    }
    diagnostics = SupportTranscript(options.output.with_suffix(".requests.jsonl"), options.module_index)
    hooks = None
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            repo = AgentRunRepository(db)
            run = repo.get_run(run_id=source["source_run_id"])
            assert run is not None
            node = repo.latest_node_run(run_id=run.id, node_key="resolve_generation")
            assert node is not None and node.id == source["source_node_run_id"]
            raw_input = repo.read_node_input(node)
            checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
            run_hash = action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload])
            node_hash = source_fingerprint(repo, node)
            assert action_state_hash(raw_input) == source["raw_input_sha256"]
            assert action_state_hash(checkpoint) == source["source_checkpoint_sha256"]
            parent, initial_action, plan_proofs, _ = prepare_context(db, run, node, raw_input, checkpoint)
            context = generation._planning_context(parent, parent.action_state)
            assert initial_action["items"] == source["prepared_inputs"]
            assert not any(context.action_state["gap_records"])
            assert context.action_state["route_review_revisions"] == []
            snapshots, _, descriptions = _capture_current_definitions(db, run, REVIEWER)
            definition, tools = snapshots[REVIEWER]
            historical_inputs = {action_state_hash(item): item for item in source["prepared_inputs"]}
            responses = [entry for entry in source["responses"] if entry["input_sha256"] in historical_inputs]
            assert len(responses) == len(historical_inputs) == source["all_module_count"]
            assert len({entry["input_sha256"] for entry in responses}) == len(responses)
            by_hash = {entry["input_sha256"]: entry for entry in responses}
            outputs, checks = [], []
            for old_item, item in zip(source["prepared_inputs"], initial_action["items"], strict=True):
                historical = by_hash[action_state_hash(old_item)]
                contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor=POSTPROCESSOR))
                normalized = contract.validate(definition, context, deepcopy(historical["raw_output"]))
                assert normalized == historical["validated_output"]
                _, projection_check = check_projection(definition, item)
                outputs.append(normalized)
                checks.append({"module_index": item["module_index"], "input_sha256": action_state_hash(item),
                    "historical_input_sha256": action_state_hash(old_item), "projection": projection_check,
                    "historical_raw_sha256": action_state_hash(historical["raw_output"]),
                    "current_contract_output_sha256": action_state_hash(normalized),
                    "historical_normalized_output_unchanged": True})
            initial_observation = planning.apply_route_gap_review(context, initial_action, outputs)
            initial_state = deepcopy(context.action_state)
            initial_feedback_checks = check_feedback_references(initial_state)
            current = current_decisions(context)
            target = current[options.module_index][options.design_index]
            assert target["disposition"] == "unsupported", "本次诊断必须选真实未决意图，不制造缺口"
            original = next(item for item in initial_action["items"] if item["module_index"] == options.module_index)
            original_scopes = {fact["scope_id"] for fact in original["candidate_facts"]}
            originals_by_id = {fact["fact_id"]: fact for fact in original["candidate_facts"]}
            canonical = {fact["fact_id"]: fact for fact in initial_state["source_semantics"]["authoritative_facts"]}
            sources = {item["module_index"]: item for item in initial_action["items"]}
            evidence, chosen_scopes = [], set()
            for module_index, design_index in options.evidence_design:
                decision = current[module_index][design_index]
                assert decision["disposition"] == "supported", "选取依据须来自真实已通过回执"
                source_item = sources[module_index]
                by_id = {fact["fact_id"]: fact for fact in source_item["candidate_facts"]}
                facts = [deepcopy(by_id[fact_id]) for fact_id in decision["fact_ids"]]
                assert facts
                for fact in facts:
                    assert canonical[fact["fact_id"]]["assertion"] == fact["assertion"]
                    chosen_scopes.add(fact["scope_id"])
                evidence.append({"module_index": module_index, "design_index": design_index,
                    "design": deepcopy(next(value for value in source_item["missing_test_design_items"]
                                            if value["test_design_item_index"] == design_index)),
                    "historical_decision_revalidated": deepcopy(decision),
                    "facts": [{**fact, "source_anchor": deepcopy(canonical[fact["fact_id"]]["source_anchor"]),
                               "is_new_scope_for_target": fact["scope_id"] not in original_scopes} for fact in facts]})
            additional_scopes = sorted(chosen_scopes - original_scopes)
            assert additional_scopes, "真实证据选择没有提供目标模块之外的新来源范围"
            arguments = {
                "design_selections": [{"module_index": options.module_index,
                                       "test_design_item_indexes": [options.design_index]}],
                "additional_scope_ids": additional_scopes, "question": options.question,
            }
            action = planning.prepare_route_gap_review(context, arguments)
            assert len(action["items"]) == 1
            item = action["items"][0]
            reversed_action = planning.prepare_route_gap_review(context,
                {**arguments, "additional_scope_ids": list(reversed(additional_scopes))})
            assert action["items"] == reversed_action["items"]
            assert item["_planning_feedback"]["previous_decisions"] == [target]
            candidates = {fact["fact_id"]: fact for fact in item["candidate_facts"]}
            added = {fact_id: fact for fact_id, fact in candidates.items() if fact_id not in originals_by_id}
            assert added and {fact["scope_id"] for fact in added.values()} == set(additional_scopes)
            assert all(candidates[fact_id] == value for fact_id, value in originals_by_id.items())
            model_input, projection_check = check_projection(definition, item)
            budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
            report.update(definition_snapshots=descriptions, source_node_run_id=node.id,
                raw_input_sha256=action_state_hash(raw_input), source_checkpoint_sha256=action_state_hash(checkpoint),
                historical_planner_router_checks=plan_proofs, historical_initial_support_checks=checks,
                historical_targeted_responses_excluded=len(source["responses"]) - len(responses),
                initial_observation=initial_observation, initial_planning_state=initial_state,
                initial_feedback_reference_checks=initial_feedback_checks,
                evidence_selection=evidence, original_scope_ids=sorted(original_scopes),
                additional_scope_ids=additional_scopes, new_candidate_facts=list(added.values()),
                input=deepcopy(item), input_sha256=action_state_hash(item), arguments=arguments,
                model_input=deepcopy(model_input), model_input_sha256=action_state_hash(model_input),
                input_projection=projection_check, request_budget=budget,
                request_budget_input="model_input", contract_and_apply_input="complete_input",
                previous_decisions_match_actual_history=True, additional_scope_order_identity_verified=True,
                original_candidate_facts_unchanged=True)
            if options.context_report:
                # 目标旧来源没有表格上下文时，另用真实完成报告检验非空目录，不能虚构候选填满分支。
                context_bytes = options.context_report.read_bytes()
                context_report = json.loads(context_bytes)
                assert context_report["source_run_id"] == run.id
                assert context_report["status"] == "contract_passed"
                context_item = deepcopy(context_report["input"])
                _, context_projection = check_projection(definition, context_item)
                context_contract = AgentOutputContract(deepcopy(context_item),
                    AgentMapConfig(item_postprocessor=POSTPROCESSOR))
                context_output = context_contract.validate(definition, context, deepcopy(context_report["raw_output"]))
                assert context_output == context_report["normalized_output"]
                cited = {fact_id for decision in context_output["decisions"]
                         for fact_id in [*decision["fact_ids"], *(value for gap in decision["support_gaps"]
                                                                for value in gap["evidence_fact_ids"]) ]}
                context_candidates = {fact["fact_id"]: fact for fact in context_item["candidate_facts"]}
                assert cited <= context_candidates.keys()
                cited_contexts = [context_candidates[fact_id] for fact_id in cited
                                  if "source_context" in context_candidates[fact_id]]
                assert cited_contexts, "非空目录检查必须实际覆盖真实回执引用的表格事实"
                report["separate_real_context_projection_check"] = {
                    "source_report": str(options.context_report.resolve()),
                    "source_report_sha256": hashlib.sha256(context_bytes).hexdigest(),
                    "projection": context_projection, "actual_cited_context_facts_checked": len(cited_contexts),
                    "historical_normalized_output_unchanged": True,
                    "historical_semantic_reassessment": False, "planning_state_applied": False,
                    "scope": "另一真实已完成组件输入及回执的共享目录投影检查，不作为本次新增范围复核或语义通过",
                }
                assert options.context_report.read_bytes() == context_bytes
            assert production_hashes() == frozen_code
            if options.execute:

                def persist_budget(snapshot):
                    report["budget_usage"] = snapshot
                    save_report(options.output, report)

                hooks = AutonomousBudgetHooks(budget, on_change=persist_budget)
                contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor=POSTPROCESSOR),
                    candidate_validator=partial(planning._validate_single_candidate,
                                                planning.apply_route_gap_review, context, action))
                report.update(status="running", request_budget=budget)
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
                observation = planning.apply_route_gap_review(context, action, [response.validated_output])
                assert context.action_state["gap_records"] == initial_state["gap_records"]
                assert context.action_state["route_records"] == initial_state["route_records"]
                assert context.action_state["plan"] == initial_state["plan"]
                decision = current_decisions(context)[options.module_index][options.design_index]
                assert decision["review_input_sha256"] == action_state_hash(item)
                cited = set(decision["fact_ids"]) | {fact_id for gap in decision["support_gaps"]
                                                       for fact_id in gap["evidence_fact_ids"]}
                assert cited <= candidates.keys()
                new_citations = sorted(cited & added.keys())
                feedback = planning._route_feedback(context.action_state)
                for entry in feedback:
                    for fact in entry["supporting_facts"]:
                        assert fact["assertion"] == canonical[fact["fact_id"]]["assertion"]
                target_feedback = next((entry for entry in feedback if entry["module_index"] == options.module_index), None)
                returned_ids = {fact["fact_id"] for fact in (target_feedback or {}).get("supporting_facts", [])}
                if decision["disposition"] == "unsupported":
                    assert target_feedback is not None and cited <= returned_ids
                    for fact_id in new_citations:
                        assert candidates[fact_id]["scope_id"] in target_feedback["scope_ids"]
                else:
                    assert target_feedback is None or options.design_index not in {
                        value["test_design_item_index"] for value in target_feedback["review"]["decisions"]}
                duplicate = require_rejected(lambda: planning.prepare_route_gap_review(context, arguments),
                                             "真实复核后同一完整问题不能重复抽取")
                report.update(status="contract_passed", final_decision=decision, final_observation=observation,
                    final_feedback_reference_checks=check_feedback_references(context.action_state),
                    final_feedback=feedback, final_planning_state=deepcopy(context.action_state),
                    observed_additional_scope_citations=new_citations,
                    revision_feedback_projection_exercised=bool(new_citations and decision["disposition"] == "unsupported"),
                    revision_feedback_projection_note=("实际未决回执新增引用已回投影" if new_citations and
                        decision["disposition"] == "unsupported" else
                        "本次输出未产生带新增引用的未决反馈，不伪造结论以覆盖该分支"),
                    initial_plan_routes_and_receipts_unchanged=True, duplicate_question_rejected=duplicate)
            else:
                report["status"] = "prepared"
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            db.refresh(node)
            assert action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload]) == run_hash
            assert source_fingerprint(repo, node) == node_hash
            assert options.support_report.read_bytes() == source_bytes
            assert production_hashes() == frozen_code
            report["source_report_run_checkpoint_and_production_unchanged"] = True
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__, exception_chain=safe_exception_evidence(error),
                error_message=str(error) if isinstance(error, (ValueError, AssertionError, OutputValidationError))
                    else "异常详情见结构化诊断", provider_error_categories=provider_error_categories(error),
                provider_error_message_redacted=provider_error_message(error))
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
    parser.add_argument("--support-report", type=Path, required=True)
    parser.add_argument("--context-report", type=Path,
                        help="可选真实已完成表格复核报告，只验非空共享目录的数据投影，不调用模型或改规划状态")
    parser.add_argument("--module-index", type=int, required=True)
    parser.add_argument("--design-index", type=int, required=True)
    parser.add_argument("--evidence-design", type=evidence_selection, action="append", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if len(set(options.evidence_design)) != len(options.evidence_design):
        parser.error("真实证据设计项不能重复")
    if options.output.exists() or options.output.with_suffix(".requests.jsonl").exists():
        raise FileExistsError("报告或模型请求证据已存在，请使用新路径")
    options.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(options))
    print(json.dumps({key: report.get(key) for key in ("status", "source_run_id", "original_scope_ids",
        "additional_scope_ids", "model_requests_sent", "observed_additional_scope_citations",
        "revision_feedback_projection_exercised", "error_type", "error_message")}, ensure_ascii=False))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
