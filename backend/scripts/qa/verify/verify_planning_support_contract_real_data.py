"""从真实首轮规划/路由回执准备新支持契约；显式执行时逐模块保留新模型证据。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
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
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, safe_exception_evidence
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_planning import (
    current_planning_support_decisions, planning_support_request_hash, postprocess_planning_route_repair_item,
)
from scripts.qa.verify.verify_generation_agent_real_model import (
    _capture_current_definitions, _definition_tree_identity, provider_error_categories, provider_error_message,
)
from scripts.qa.verify.verify_planning_agent_real_model import source_fingerprint
from scripts.qa.verify.verify_state_snapshot_filter_real_data import definition_for


REVIEWER = "test_planning_route_gap_reviewer"
POSTPROCESSOR = "testing.postprocess_planning_route_repair_item"


def production_hashes():
    root = Path(__file__).resolve().parents[3] / "modules" / "agent_platform"
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*.py"))}


def save_report(path, report):
    """每次真实预算或模块结果变化即保存；原报告始终保留可解析的完整 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".writing.json")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def prepare_context(db, run, node, raw_input, checkpoint):
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context["artifacts"]))
    generation.initialize_generation_state(context, deepcopy(raw_input))
    child = generation._planning_context(context, context.action_state)
    calls = list(checkpoint["validated_agent_calls"].values())
    proofs = []

    def actual_output(agent_key, item, postprocessor=None):
        matches = [call for call in calls if call["identity"]["agent_key"] == agent_key
                   and call["identity"]["input_hash"] == action_state_hash(item)]
        assert len(matches) == 1, (agent_key, action_state_hash(item), len(matches))
        saved = matches[0]
        assert action_state_hash(saved["raw_output"]) == saved["raw_output_hash"]
        contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor=postprocessor) if postprocessor else None)
        output = contract.validate(definition_for(agent_key), child, deepcopy(saved["raw_output"]))
        assert output == saved["output"]
        proofs.append({"agent_key": agent_key, "input_sha256": action_state_hash(item),
                       "raw_output_sha256": saved["raw_output_hash"], "validated_output_sha256": action_state_hash(output),
                       "source_parent_tool_call_id": saved["parent_tool_call_id"]})
        return output

    action = planning.prepare_business_plan_rebuild(child, {})
    planning.apply_business_plan_rebuild(child, action, [actual_output("test_business_planner", action["items"][0])])
    action = planning.prepare_scope_routing(child, {})
    planning.apply_scope_routing(child, action, [actual_output("test_planning_scope_router", item,
        "testing.postprocess_planning_scope_routing_item") for item in action["items"]])
    context.action_state["planning"] = deepcopy(child.action_state)
    generation._save(context, context.action_state)
    action = planning.prepare_route_gap_review(child, {})
    # 旧支持输出只做历史问题定位，不转换为新 support_gaps，不参与当前状态。
    first_review = next(value for value in checkpoint["receipts"].values()
                        if value["tool_key"] == "review_planning_route_gaps" and value["status"] == "completed")
    historical = [{"module_index": call["output"]["module_index"], "decision": deepcopy(decision)}
        for call in calls if call["identity"]["agent_key"] == REVIEWER
        and call["parent_tool_call_id"] == first_review["tool_call_id"]
        for decision in call["output"]["decisions"] if decision["disposition"] == "unsupported"]
    assert not any(child.action_state["gap_records"])
    return context, action, proofs, historical


def require_rejected(callback, label):
    try:
        callback()
    except (ValueError, OutputValidationError) as error:
        return {"check": label, "error_type": type(error).__name__, "message": str(error)[:500]}
    raise AssertionError(f"应拒绝真实输出的反事实篡改: {label}")


def check_response(context, item, raw, output):
    assert all("disposition" not in decision for decision in raw["decisions"])
    assert all(decision["disposition"] == ("unsupported" if decision["support_gaps"] else "supported")
               for decision in output["decisions"])
    assert postprocess_planning_route_repair_item(context, {"item_input": item, "item_output": deepcopy(output)}) == output
    mutations = []
    flipped = deepcopy(output)
    decision = flipped["decisions"][0]
    decision["disposition"] = "supported" if decision["disposition"] == "unsupported" else "unsupported"
    mutations.append(require_rejected(lambda: postprocess_planning_route_repair_item(context,
        {"item_input": item, "item_output": flipped}), "派生状态不能被反向改写"))
    empty = deepcopy(raw)
    empty["decisions"][0].update(fact_ids=[], support_gaps=[])
    mutations.append(require_rejected(lambda: postprocess_planning_route_repair_item(context,
        {"item_input": item, "item_output": empty}), "无支持事实且无缺口不能通过"))
    gaps = [(di, gi) for di, value in enumerate(raw["decisions"]) for gi, _ in enumerate(value["support_gaps"])]
    if gaps:
        corrupted = deepcopy(raw)
        di, gi = gaps[0]
        corrupted["decisions"][di]["support_gaps"][gi]["coverage_fragment"] += "【反事实篡改片段】"
        mutations.append(require_rejected(lambda: postprocess_planning_route_repair_item(context,
            {"item_input": item, "item_output": corrupted}), "缺口片段必须逐字来自当前意图"))
    return {"module_index": item["module_index"], "derived_disposition_matches_gaps": True,
            "normalized_receipt_roundtrip": True, "counterfactual_rejections": mutations}


def check_full_state(context):
    child = generation._planning_context(context, context.action_state)
    state = child.action_state
    arguments = {"plan": state["plan"], "prepared_items": state["route_inputs"], "route_records": state["route_records"],
                 "repair_records": state["gap_records"], "review_revisions": state["route_review_revisions"]}
    current = current_planning_support_decisions(child, arguments)
    expected_current = {}
    for item, record in zip(state["gap_inputs"], state["gap_records"], strict=True):
        for decision in record["output"]["decisions"]:
            expected_current.setdefault(item["module_index"], {})[decision["test_design_item_index"]] = {
                **decision, "review_input_sha256": action_state_hash(item)}
    for revision in state["route_review_revisions"]:
        for decision in revision["output"]["decisions"]:
            expected_current[revision["input"]["module_index"]][decision["test_design_item_index"]] = {
                **decision, "review_input_sha256": revision["input_hash"]}
    assert current == expected_current
    observation = planning.observe_planning_state(child)
    blocker = observation["rebuild_blocker"]
    check = {"all_current_decisions_match_actual_receipts": True,
             "support_version": state["support_version"], "can_finish": observation["can_finish"],
             "unsupported_designs": observation["unsupported_designs"],
             "parent_planning_has_no_persisted_downstream_feedback": "downstream_feedback" not in state}
    if observation["unsupported_designs"]:
        assert blocker is not None and observation["can_rebuild"] is False
        check["rebuild_blocker"] = require_rejected(lambda: planning.prepare_business_plan_rebuild(child, {}),
                                                    "纯支持缺口不能用全局重分组重抽结论")
    else:
        check["rebuild_blocker"] = {"exercised": False, "reason": "本次真实输出没有支持缺口，不伪造被拒结论"}
    candidates = observation["unsupported_designs"] or [
        {"module_index": item["module_index"], **design}
        for item in state["gap_inputs"] for design in item["missing_test_design_items"]]
    origins = planning._coverage_origins(state)

    def origin_scope_difference(target):
        original = next(item for item in state["gap_inputs"] if item["module_index"] == target["module_index"])
        design = next(design for design in original["missing_test_design_items"]
                      if design["test_design_item_index"] == target["test_design_item_index"])
        scope_ids = set(origins.get(design["coverage_intent"], {}).get("scope_ids", []))
        return sorted(scope_ids - {fact["scope_id"] for fact in original["candidate_facts"]})

    selected = max(candidates, key=lambda target: len(origin_scope_difference(target)))
    module_index, design_index = selected["module_index"], selected["test_design_item_index"]
    original_scopes = {fact["scope_id"] for item in state["gap_inputs"] if item["module_index"] == module_index
                       for fact in item["candidate_facts"]}
    extra_scopes = origin_scope_difference(selected)
    base = {"design_selections": [{"module_index": module_index, "test_design_item_indexes": [design_index]}],
            "question": "请依据完整候选事实核对本项真实结论中的缺口及其依据；补充范围来自本意图草案实际引用的来源，"
                        "不预设支持或不支持。当前记录：" + json.dumps(current[module_index][design_index], ensure_ascii=False),
            "additional_scope_ids": extra_scopes}
    targeted = planning.prepare_route_gap_review(child, base)
    reverse = planning.prepare_route_gap_review(child, {**base, "additional_scope_ids": list(reversed(extra_scopes))})
    assert targeted["items"] == reverse["items"]
    item = targeted["items"][0]
    assert item["_planning_feedback"]["previous_decisions"] == [current[module_index][design_index]]
    changed_history = deepcopy(item)
    changed_history["_planning_feedback"]["previous_decisions"] = []
    assert planning_support_request_hash(changed_history) == planning_support_request_hash(item)
    assert action_state_hash(changed_history) != action_state_hash(item)
    check.update(targeted_input=item, targeted_arguments=base, extra_scope_order_has_same_input_identity=True,
                 extra_scope_order_check_scope_count=len(extra_scopes),
                 additional_scopes_come_from_actual_target_draft=True,
                 previous_decisions_are_current_actual_receipts=True, previous_decisions_do_not_create_new_question_identity=True,
                 targeted_review_executed=False,
                 revision_additional_fact_feedback={"exercised": False,
                     "reason": "本脚本只执行初次审查；未生成定向复核响应，不声称新增引用回投影已真实触发"})
    return check


def check_targeted_state(context, initial_records, arguments):
    child = generation._planning_context(context, context.action_state)
    state = child.action_state
    assert state["gap_records"] == initial_records
    revision = state["route_review_revisions"][-1]
    inputs = {"plan": state["plan"], "prepared_items": state["route_inputs"], "route_records": state["route_records"],
              "repair_records": state["gap_records"], "review_revisions": state["route_review_revisions"]}
    current = current_planning_support_decisions(child, inputs)
    for decision in revision["output"]["decisions"]:
        assert current[revision["input"]["module_index"]][decision["test_design_item_index"]] == {
            **decision, "review_input_sha256": revision["input_hash"]}
    broken = deepcopy(inputs)
    changed = broken["review_revisions"][-1]
    changed["input"]["_planning_feedback"]["previous_decisions"] = []
    changed["input_hash"] = action_state_hash(changed["input"])
    rejected_history = require_rejected(lambda: current_planning_support_decisions(child, broken),
                                       "重新计算输入摘要也不能伪造定向复核的先前结论")
    rejected_duplicate = require_rejected(lambda: planning.prepare_route_gap_review(child, arguments),
                                         "复核改变先前结论后同一问题仍不能重复抽取")
    original_item = next(item for item in state["gap_inputs"] if item["module_index"] == revision["input"]["module_index"])
    original_ids = {fact["fact_id"] for fact in original_item["candidate_facts"]}
    new_cited = []
    for feedback in planning.observe_planning_state(child)["route_gaps"]:
        if feedback["review"] is None:
            continue
        cited = {fact_id for decision in feedback["review"]["decisions"]
                 for fact_id in [*decision["fact_ids"], *(value for gap in decision["support_gaps"]
                                                       for value in gap["evidence_fact_ids"]) ]}
        assert cited <= {fact["fact_id"] for fact in feedback["supporting_facts"]}
        if feedback["module_index"] == revision["input"]["module_index"]:
            new_cited = sorted(cited - original_ids)
    return {"initial_receipts_preserved": True, "current_projection_uses_actual_revision": True,
            "previous_decisions_tampering_rejected": rejected_history, "duplicate_question_rejected": rejected_duplicate,
            "all_feedback_citations_keep_actual_facts": True, "observed_additional_scope_citations": new_cited,
            "parent_planning_has_no_persisted_downstream_feedback": "downstream_feedback" not in state}


async def verify(options):
    started = perf_counter()
    report = {"status": "preparing", "source_run_id": options.run_id,
              "started_at": datetime.now(timezone.utc).isoformat(), "online_database_writes": 0,
              "formal_workflow_run": False, "natural_parent_action_selection": False,
              "old_support_outputs_converted": False, "responses": [], "model_requests_sent_this_execution": 0}
    saved = json.loads(options.resume_report.read_text(encoding="utf-8")) if options.resume_report else None
    diagnostics = ModelRequestDiagnostics()
    frozen_code = production_hashes()
    hooks = None
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            repo = AgentRunRepository(db)
            run = repo.get_run(run_id=options.run_id)
            if run is None:
                raise ValueError("真实运行不存在")
            node = repo.latest_node_run(run_id=run.id, node_key="resolve_generation")
            if node is None:
                raise ValueError("真实运行缺少统一协调节点")
            raw_input = repo.read_node_input(node)
            checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
            original_run_hash = action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload])
            original_node_hash = source_fingerprint(repo, node)
            context, action, proofs, historical = prepare_context(db, run, node, raw_input, checkpoint)
            snapshots, originals, descriptions = _capture_current_definitions(db, run, REVIEWER)
            definition, tools = snapshots[REVIEWER]
            selected = set(options.module_indexes or [item["module_index"] for item in action["items"]])
            assert selected and selected <= {item["module_index"] for item in action["items"]}
            inputs = [item for item in action["items"] if item["module_index"] in selected]
            report.update(source_node_run_id=node.id, source_checkpoint_sha256=action_state_hash(checkpoint),
                raw_input_sha256=action_state_hash(raw_input), validated_historical_calls=proofs,
                historical_first_round_unsupported=historical, definition_snapshots=descriptions,
                definition_tree_sha256=_definition_tree_identity(descriptions), production_sha256=frozen_code,
                prepared_inputs=inputs, all_module_count=len(action["items"]), selected_module_count=len(inputs),
                design_item_count=sum(len(item["missing_test_design_items"]) for item in inputs),
                initial_planning_state=deepcopy(context.action_state["planning"]))
            if saved is not None:
                for key in ("source_run_id", "source_node_run_id", "source_checkpoint_sha256", "raw_input_sha256",
                            "definition_tree_sha256", "production_sha256", "prepared_inputs"):
                    assert saved[key] == report[key], f"恢复身份已变化: {key}"
                report["responses"] = deepcopy(saved["responses"])
                report["resume_report"] = str(options.resume_report.resolve())
                report["resume_report_sha256"] = hashlib.sha256(options.resume_report.read_bytes()).hexdigest()
            if not options.execute:
                report["status"] = "prepared"
                return report
            limits = {name: run.run_context["execution_limits"][limit] for name, limit in (
                ("attempted_requests", "max_requests"), ("input_tokens", "max_input_tokens"),
                ("output_tokens", "max_output_tokens"), ("total_tokens", "max_total_tokens"))}

            def persist_budget(snapshot):
                report["budget_usage"] = deepcopy(snapshot)
                report["model_requests"] = deepcopy(diagnostics.requests)
                save_report(options.output, report)

            hooks = AutonomousBudgetHooks(limits, checkpoint=saved.get("budget_usage") if saved else None,
                                          on_change=persist_budget)
            report.update(status="running", budget_usage=hooks.snapshot())
            save_report(options.output, report)
            reusable = {response["input_sha256"]: response for response in report["responses"]}
            assert len(reusable) == len(report["responses"])
            by_hash = {action_state_hash(item): item for item in inputs}

            async def invoke_actual(*, agent_key, input_payload, item_postprocessor=None, **kwargs):
                assert agent_key == REVIEWER and item_postprocessor == POSTPROCESSOR and not kwargs
                assert production_hashes() == frozen_code, "执行中生产代码变化，不能继续混用契约"
                identity = action_state_hash(input_payload)
                assert identity in by_hash and input_payload == by_hash[identity]
                child_context = replace(context, action_state={}, action_input={}, action_journal=None,
                                        artifacts=deepcopy(context.artifacts), tool_calls=[], executed_tools=[])
                contract = AgentOutputContract(deepcopy(input_payload), AgentMapConfig(item_postprocessor=POSTPROCESSOR))
                if identity in reusable:
                    previous = reusable[identity]
                    assert contract.validate(definition, child_context, deepcopy(previous["raw_output"])) == previous["validated_output"]
                    return deepcopy(previous["validated_output"])
                request_start = len(diagnostics.requests)
                response = await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                    execution_context=child_context, input_payload=input_payload,
                    request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                    disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                    request_budget=limits, shared_budget_hooks=hooks, request_diagnostics=diagnostics,
                    require_validated_submission=True, captured_definitions=snapshots)
                assert response.validated_output == contract.validate(definition, child_context, deepcopy(response.output))
                record = {"module_index": input_payload["module_index"], "input_sha256": identity,
                          "raw_output": deepcopy(response.output), "validated_output": deepcopy(response.validated_output),
                          "usage": deepcopy(response.usage), "model_requests": deepcopy(diagnostics.requests[request_start:]),
                          "budget_after_module": hooks.snapshot(),
                          "tool_observations": deepcopy(response.tool_observations)}
                report["responses"].append(record)
                reusable[identity] = record
                report["budget_usage"] = hooks.snapshot()
                report["model_requests_sent_this_execution"] = len(diagnostics.requests)
                save_report(options.output, report)
                print(json.dumps({"module_index": input_payload["module_index"], "completed_modules": len(reusable),
                                  "selected_modules": len(inputs), "budget": hooks.snapshot()["charged"]}, ensure_ascii=False), flush=True)
                return deepcopy(response.validated_output)

            context.invoke_agent = invoke_actual
            if len(inputs) == len(action["items"]):
                report["parent_action_result"] = await generation.review_planning_route_gaps(context, {})
                report["full_state_checks"] = check_full_state(context)
                report["all_initial_modules_applied"] = True
                if options.execute_targeted:
                    initial_records = deepcopy(context.action_state["planning"]["gap_records"])
                    targeted_args = deepcopy(report["full_state_checks"]["targeted_arguments"])
                    targeted = planning.prepare_route_gap_review(
                        generation._planning_context(context, context.action_state), targeted_args)
                    for item in targeted["items"]:
                        by_hash[action_state_hash(item)] = item
                    report["targeted_inputs"] = deepcopy(targeted["items"])
                    save_report(options.output, report)
                    report["targeted_parent_action_result"] = await generation.review_planning_route_gaps(context, targeted_args)
                    report["targeted_state_checks"] = check_targeted_state(context, initial_records, targeted_args)
                    report["full_state_checks"]["targeted_review_executed"] = True
                    report["full_state_checks"]["revision_additional_fact_feedback"] = {
                        "exercised": bool(report["targeted_state_checks"]["observed_additional_scope_citations"]),
                        "actual_additional_scope_citations": report["targeted_state_checks"]["observed_additional_scope_citations"]}
                report["planning_state"] = deepcopy(context.action_state["planning"])
            else:
                for item in inputs:
                    await invoke_actual(agent_key=REVIEWER, input_payload=item, item_postprocessor=POSTPROCESSOR)
                report["all_initial_modules_applied"] = False
            report["response_checks"] = [check_response(context, by_hash[response["input_sha256"]],
                response["raw_output"], response["validated_output"]) for response in report["responses"]]
            report["dispositions"] = dict(Counter(decision["disposition"] for response in report["responses"]
                                                  for decision in response["validated_output"]["decisions"]))
            assert production_hashes() == frozen_code
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            db.refresh(node)
            assert action_state_hash([run.status, run.input_payload, run.run_context, run.output_payload]) == original_run_hash
            assert source_fingerprint(repo, node) == original_node_hash
            report.update(status="passed", source_run_and_checkpoint_unchanged=True)
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__, exception_chain=safe_exception_evidence(error),
                error_message=str(error) if isinstance(error, (ValueError, AssertionError, OutputValidationError)) else "异常详情见结构化诊断",
                provider_error_categories=provider_error_categories(error), provider_error_message_redacted=provider_error_message(error))
        finally:
            if hooks is not None:
                report["budget_usage"] = hooks.snapshot()
            report["model_requests"] = deepcopy(diagnostics.requests)
            report["model_requests_sent_this_execution"] = len(diagnostics.requests)
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            assert not db.new and not db.dirty and not db.deleted
            db.rollback()
            save_report(options.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=39)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--execute-targeted", action="store_true",
                        help="全部初审完成后，再根据真实结论执行一项补充真实范围的定向复核")
    parser.add_argument("--module-index", type=int, action="append", dest="module_indexes")
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.execute_targeted and (not options.execute or options.module_indexes):
        parser.error("--execute-targeted 需要 --execute，且不能只选择部分模块")
    if options.output.exists():
        raise FileExistsError("报告已存在，请使用新路径保存本轮证据")
    report = asyncio.run(verify(options))
    print(json.dumps({key: report.get(key) for key in ("status", "source_run_id", "all_module_count", "selected_module_count",
        "design_item_count", "model_requests_sent_this_execution", "dispositions", "error_type", "error_message")}, ensure_ascii=False))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
