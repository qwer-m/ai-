"""回放真实审查/修补结果，并在独立 SQLite 验证行动检查点；不调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.tool_context import ToolContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentApproval, AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent, AgentWorkflowDefinition
from modules.agent_platform.action_journal import (
    ActionInProgress, ActionStateConflict, AgentActionJournal, action_state_hash,
)
from modules.agent_platform.autonomous_budget import (
    AgentExecutionBudgetExceeded, AutonomousBudgetHooks, model_request_reservation,
)
from modules.agent_platform.lifecycle import RunLeaseLost, new_claim_token, transition_run
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import _model_settings, _sdk_output_type
from modules.agent_platform.test_generation_review_agent import (
    apply_review_recheck, apply_review_repair, finish_review, initialize_review_state,
    observe_review_state, prepare_review_recheck, prepare_review_repair,
    validate_review_resolution,
    _new_review_candidates, read_review_evidence, validate_review_repair_candidate,
)
from modules.agent_platform.test_generation_review import merge_final_review_recheck_records, postprocess_final_review_batch_item
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, sqlite_longtext as _sqlite_longtext


def rejected(operation, error_type=OutputValidationError) -> Exception:
    """运行真实拒绝分支，不替换函数或创建模型响应。"""
    try:
        operation()
    except error_type as error:
        return error
    raise AssertionError(f"本次操作必须被 {error_type.__name__} 拒绝")


def load_nodes(db: Session, run_id: int) -> dict[str, AgentNodeRun]:
    keys = {
        "effective_facts", "prepare_generation", "generated_cases", "prepare_final_review",
        "final_review_batches", "prepare_final_review_repairs", "final_review_repairs",
        "repaired_cases", "prepare_final_review_rechecks", "final_review_rechecks",
        "global_review_input", "global_review",
    }
    result: dict[str, AgentNodeRun] = {}
    # 先排序主键，避免 MySQL 对完整 JSON 产物做排序。
    ids = db.query(AgentNodeRun.id).filter(
        AgentNodeRun.run_id == run_id, AgentNodeRun.node_key.in_(keys), AgentNodeRun.status == "success",
    ).order_by(AgentNodeRun.id).all()
    for (node_id,) in ids:
        node = db.get(AgentNodeRun, node_id)
        result[node.node_key] = node
    if result.keys() != keys:
        raise ValueError(f"真实运行缺少完整审查与修补链: {sorted(keys - result.keys())}")
    return result


def review_arguments(run: AgentRun, nodes: dict[str, AgentNodeRun]) -> dict[str, Any]:
    return {
        "generation": deepcopy(nodes["generated_cases"].output_payload),
        "generation_inputs": deepcopy(nodes["prepare_generation"].output_payload["items"]),
        "review_inputs": deepcopy(nodes["prepare_final_review"].output_payload["items"]),
        "review_records": deepcopy(nodes["final_review_batches"].output_payload["items"]),
        "authoritative_facts": deepcopy(nodes["effective_facts"].output_payload["authoritative_facts"]),
        "case_budget": run.input_payload["case_budget"],
    }


def recorded_outputs(action: dict, inputs: list[dict], records: list[dict]) -> list[dict]:
    """按真实批次和用例集合关联历史结果，避免凭数组位置给新动作塞结果。"""
    outputs = []
    for item in action["items"]:
        batch_id = item["review_batch"]["batch_id"]
        case_ids = [case["case_id"] for case in item["test_cases"]]
        matches = [
            index for index, source in enumerate(inputs)
            if source["review_batch"]["batch_id"] == batch_id
            and [case["case_id"] for case in source["test_cases"]] == case_ids
        ]
        assert len(matches) == 1, (batch_id, case_ids, matches)
        index = matches[0]
        record = next(record for record in records if record["item_index"] == index)
        outputs.append(deepcopy(record["output"]))
    return outputs


def verify_global_error_boundary(context: ToolExecutionContext, nodes: dict[str, AgentNodeRun]) -> dict:
    """仅损坏真实产物副本检查错误分类，不把负向契约样例称为模型拒绝结论。"""
    from modules.agent_platform.test_generation_review import postprocess_global_final_review_output

    source = deepcopy(nodes["global_review_input"].output_payload)
    output = deepcopy(nodes["global_review"].output_payload)
    real_difference = next(
        difference for record in nodes["final_review_batches"].output_payload["items"]
        for difference in record["output"]["differences"] if difference.get("case_id")
    )
    checked_fields = []
    for field in ("case_id", "field_path", "repair_instruction"):
        damaged = deepcopy(output)
        damaged["approved"] = False
        damaged["differences"] = [deepcopy(real_difference)]
        difference = damaged["differences"][0]
        # 全局输出不携带事实正文引用；此处只隔离待验证的模型文本字段错误。
        difference["related_fact_ids"] = []
        difference["field_path"] = None
        difference[field] = ""
        error = rejected(lambda: postprocess_global_final_review_output(context, {
            "input_payload": source, "output": damaged,
        }))
        assert error.details["field"] == f"difference.{field}"
        assert isinstance(error.__cause__, ValueError)
        checked_fields.append(error.details["field"])
    damaged_source = deepcopy(source)
    damaged_source["case_index"][0].pop("case_id")
    source_error = rejected(lambda: postprocess_global_final_review_output(context, {
        "input_payload": damaged_source, "output": output,
    }), ValueError)
    assert not isinstance(source_error, OutputValidationError)
    assert source == nodes["global_review_input"].output_payload
    assert output == nodes["global_review"].output_payload
    return {
        "invalid_output_text_fields_are_repairable": checked_fields,
        "invalid_source_case_index_is_hard_failure": True,
        "scope": "negative_contract_checks_from_real_output_copies_not_model_rejection_results",
    }


def replay_global_review(context: ToolExecutionContext, nodes: dict[str, AgentNodeRun],
                         transitions: list[dict]) -> dict:
    """用真实全局审查输入与结论验证当前版本；负向检查只改变真实对象副本。"""

    from modules.agent_platform.test_generation_review_agent import apply_global_review, prepare_global_review

    before = deepcopy(context.action_state)
    rejected(lambda: finish_review(context, {}))
    assert context.action_state == before
    action = prepare_global_review(context, {})
    historical_input = deepcopy(nodes["global_review_input"].output_payload)
    assert len(action["items"]) == 1
    current_input = deepcopy(action["items"][0])
    same_full_input = current_input == historical_input
    # 背景摘要改为当前行动描述；其它完整输入必须逐字段一致，不将此回放冒充相同输入的模型执行。
    current_context = deepcopy(current_input)
    historical_context = deepcopy(historical_input)
    current_summary = current_context["batch_review"].pop("summary")
    historical_summary = historical_context["batch_review"].pop("summary")
    assert current_context == historical_context, "真实全局输入存在背景摘要以外的差异"
    historical_output = deepcopy(nodes["global_review"].output_payload)
    damaged = deepcopy(historical_output)
    damaged.pop("approved")
    rejected(lambda: apply_global_review(context, action, [damaged]))
    assert context.action_state == before

    # 完整动作已为最终候选构建，将状态换成真实旧候选后不能接受这个迟到结论。
    old_state = deepcopy(before)
    old_state["generation"] = deepcopy(transitions[0]["before"]["generation"])
    old_state["generation_sha256"] = transitions[0]["before"]["generation_sha256"]
    old_state["generation_version"] = transitions[0]["before"]["generation_version"]
    assert old_state["generation_sha256"] != before["generation_sha256"]
    context.action_state = old_state
    rejected(lambda: apply_global_review(context, action, [historical_output]))
    assert context.action_state == old_state
    context.action_state = deepcopy(before)

    result = apply_global_review(context, action, [historical_output])
    approved = deepcopy(context.action_state)
    assert approved["global_review"]["generation_sha256"] == approved["generation_sha256"]
    assert approved["global_review"]["input_sha256"] == action_state_hash(current_input)
    assert observe_review_state(context)["can_finish"] is True
    rejected(lambda: prepare_global_review(context, {}))
    assert context.action_state == approved

    # 保留真实已通过结论，故意换回旧用例，直接终止必须被当前内容版本校验阻止。
    changed_after_approval = deepcopy(approved)
    for key in ("generation", "generation_sha256", "generation_version"):
        changed_after_approval[key] = deepcopy(old_state[key])
    assert changed_after_approval["global_review"]["generation_sha256"] != changed_after_approval["generation_sha256"]
    context.action_state = changed_after_approval
    rejected(lambda: finish_review(context, {}))
    assert context.action_state == changed_after_approval
    context.action_state = deepcopy(approved)
    transitions.append({
        "tool_key": "review_all_cases", "arguments": {}, "before": before,
        "after": deepcopy(approved), "result": deepcopy(result),
    })
    return {
        "historical_global_input_exactly_equal": same_full_input,
        "historical_global_input_difference": "none" if same_full_input else "context_summary_only",
        "historical_context_summary": historical_summary,
        "current_context_summary": current_summary,
        "all_non_summary_global_input_fields_equal": True,
        "validation_scope": "historical_output_domain_replay_not_same_input_model_run",
        "global_review_required_before_finish": True,
        "invalid_global_output_did_not_change_state": True,
        "stale_global_action_rejected": True,
        "same_visible_input_review_rejected": True,
        "changed_generation_invalidates_global_approval": True,
        "error_boundary": verify_global_error_boundary(context, nodes),
        "global_rejection_repair_success_path": "no_real_historical_sample",
    }


def replay_domain(db: Session, run: AgentRun, nodes: dict[str, AgentNodeRun], arguments: dict) -> tuple[dict, list[dict], Exception]:
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=nodes["prepare_final_review"].node_key, run_input=deepcopy(run.input_payload),
        action_input=deepcopy(arguments),
    )
    context.action_state = initialize_review_state(context, deepcopy(arguments))
    initial_state = deepcopy(context.action_state)
    initial_observation = observe_review_state(context, {})
    repairable = list(initial_observation["repairable_batch_ids"])
    assert repairable, "真实运行必须含有实际修补批次"
    rejected(lambda: finish_review(context, {}))
    assert context.action_state == initial_state
    transitions = []
    invalid_error = None
    for batch_id in reversed(repairable):
        # 故意采用另一合法批次顺序，并在每批修补后立即复审，验证没有固定轮次依赖。
        action = prepare_review_repair(context, {"batch_id": batch_id})
        outputs = recorded_outputs(
            action, nodes["prepare_final_review_repairs"].output_payload["items"],
            nodes["final_review_repairs"].output_payload["items"],
        )
        before = deepcopy(context.action_state)
        if invalid_error is None:
            unchanged = []
            for item in action["items"]:
                ids = set(item["target_case_ids"])
                unchanged.append({
                    "test_cases": deepcopy([case for case in before["generation"]["test_cases"] if case["case_id"] in ids]),
                    "case_fact_bindings": deepcopy([
                        binding for binding in before["generation"]["case_fact_bindings"] if binding["case_id"] in ids
                    ]),
                })
            unchanged_error = rejected(lambda: apply_review_repair(context, action, unchanged))
            assert unchanged_error.details["reason"] == "unchanged_candidate"
            assert context.action_state == before
            unchanged_child = rejected(lambda: validate_review_repair_candidate(context, action, 0, unchanged[0]))
            assert unchanged_child.details["reason"] == "unchanged_candidate"
            assert context.action_state == before
            damaged = deepcopy(outputs)
            damaged[0]["case_fact_bindings"].pop()
            invalid_error = rejected(lambda: apply_review_repair(context, action, damaged))
            assert context.action_state == before
        for index, candidate in enumerate(outputs):
            validate_review_repair_candidate(context, action, index, candidate)
            assert context.action_state == before
        receipt = apply_review_repair(context, action, outputs)
        transitions.append({
            "tool_key": "repair_review_batch", "arguments": {"batch_id": batch_id},
            "before": before, "after": deepcopy(context.action_state), "result": deepcopy(receipt),
        })
        after = deepcopy(context.action_state)
        rejected(lambda: apply_review_repair(context, action, outputs))
        assert context.action_state == after
        rejected(lambda: finish_review(context, {}))
        action = prepare_review_recheck(context, {"batch_id": batch_id})
        outputs = recorded_outputs(
            action, nodes["prepare_final_review_rechecks"].output_payload["items"],
            nodes["final_review_rechecks"].output_payload["items"],
        )
        before = deepcopy(context.action_state)
        receipt = apply_review_recheck(context, action, outputs)
        transitions.append({
            "tool_key": "recheck_review_batch", "arguments": {"batch_id": batch_id},
            "before": before, "after": deepcopy(context.action_state), "result": deepcopy(receipt),
        })
    first_batch_id = transitions[0]["arguments"]["batch_id"]
    scopes = transitions[0]["after"]["pending_rechecks"][first_batch_id]["scopes"]
    before_guard = deepcopy(context.action_state)
    cycled = rejected(lambda: _new_review_candidates(
        context, deepcopy(context.action_state), first_batch_id, initial_state["generation"], scopes,
    ))
    assert cycled.details["reason"] == "previously_reviewed_candidate"
    assert context.action_state == before_guard
    global_checks = replay_global_review(context, nodes, transitions)
    completed = finish_review(context, {})
    final = validate_review_resolution(context, {"input_payload": arguments, "output": completed})
    assert final["final_review"]["approved"] is True
    assert final["audit_summary"]["approved"] is True
    historical = nodes["repaired_cases"].output_payload
    assert final["generation"]["test_cases"] == historical["test_cases"]
    assert final["generation"]["case_fact_bindings"] == historical["case_fact_bindings"]
    return {
        "initial_batch_count": len(arguments["review_inputs"]), "repaired_batch_ids": list(reversed(repairable)),
        "action_count": len(transitions), "premature_finish_rejected": True,
        "stale_action_rejected": True, "invalid_result_did_not_change_state": True,
        "unchanged_candidate_rejected": True,
        "historical_candidate_cycle_guard_rejected": True,
        "specialist_candidate_validation_kept_parent_state": True,
        "final_generation_matches_history": True,
        "global_review": global_checks,
    }, transitions, invalid_error


async def verify_evidence_state(db: Session, run: AgentRun, nodes: dict[str, AgentNodeRun], arguments: dict) -> dict:
    """以真实事实核验分页、权限、目标绑定及实际工具在 SQLite 重开后的继续读取。"""
    from core.db.model_defs import AgentToolDefinition
    from modules.agent_platform.autonomous_tools import AutonomousToolLoop
    from modules.agent_platform.contracts import parse_execution_definition
    from modules.agent_platform.sdk_adapter import AgentOutputContract, _function_tool
    from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS
    from scripts.qa.verify.verify_review_agent_real_model import builtin_definition, isolated_journal

    workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
    graph_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key)
    node = next(item for item in parse_execution_definition(graph_spec["definition"]).nodes if item.node_key == "resolve_final_review")
    definition = builtin_definition(AgentDefinition, next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == node.reference_key),
                                    project_id=run.project_id, user_id=run.user_id)
    tool_definition = builtin_definition(AgentToolDefinition, next(item for item in BUILTIN_TOOL_SPECS if item["tool_key"] == "read_review_evidence"),
                                         project_id=run.project_id, user_id=run.user_id)
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                                   node_key=node.node_key, run_input=deepcopy(run.input_payload))
    initialize_review_state(context, arguments)
    initial = deepcopy(context.action_state)
    catalog = []
    offset = 0
    while True:
        part = read_review_evidence(context, {"fact_id": None, "offset": offset, "max_chars": 4000})
        assert len(part["content"]) <= 4000
        catalog.append(part["content"])
        if part["next_offset"] is None:
            break
        offset = part["next_offset"]
    assert [item["fact_id"] for item in json.loads("".join(catalog))] == [fact["fact_id"] for fact in arguments["authoritative_facts"]]
    fact = max(arguments["authoritative_facts"], key=lambda value: len(json.dumps(value, ensure_ascii=False)))
    canonical = json.dumps(fact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    before = deepcopy(context.action_state)
    rejected(lambda: read_review_evidence(context, {"fact_id": arguments["review_inputs"][0]["review_batch"]["batch_id"], "offset": 0, "max_chars": 4000}))
    rejected(lambda: read_review_evidence(context, {"fact_id": fact["fact_id"], "offset": len(canonical), "max_chars": 4000}))
    rejected(lambda: read_review_evidence(context, {"fact_id": fact["fact_id"], "offset": 0, "max_chars": 4001}))
    assert context.action_state == before
    changed_goal = deepcopy(before)
    changed_goal["goal"]["case_count"] += 1
    context.action_state = changed_goal
    rejected(lambda: observe_review_state(context), ValueError)
    context.action_state = deepcopy(before)
    broken = deepcopy(arguments)
    broken["authoritative_facts"][0]["source_anchor"]["asset_source_sha256"] = fact["source_anchor"]["page_image_sha256"]
    rejected(lambda: initialize_review_state(replace(context, action_state={}), broken), ValueError)
    assert context.action_state == before

    async def invoke_read(current: ToolExecutionContext, offset: int, limit: int) -> dict:
        contract = AgentOutputContract(deepcopy(arguments), node.map_config)
        loop = AutonomousToolLoop(terminal_tool_names={"finish_review"},
                                  validate_submission=partial(contract.validate, definition, current),
                                  recoverable_errors=(OutputValidationError,), max_tool_calls=int(definition.runtime_config["max_turns"]),
                                  action_context=current)
        tool = loop.wrap_tool(_function_tool(tool_definition, current), allow_feedback=True)
        payload = json.dumps({"fact_id": fact["fact_id"], "offset": offset, "max_chars": limit})
        result = await tool.on_invoke_tool(ToolContext(context=current, tool_name=tool.name, tool_call_id=uuid4().hex,
                                                       tool_arguments=payload), payload)
        assert loop.events[0]["status"] == "observation"
        return json.loads(result)

    with TemporaryDirectory(prefix="qoder-review-evidence-") as directory:
        path = Path(directory) / "actions.sqlite"
        with isolated_journal(path, run=run, workflow=workflow, source_node=nodes["final_review_batches"],
                              graph_spec=graph_spec, node_spec=node, raw_input=arguments, timeout=900) as (journal, local_node, token):
            context.action_journal, context.claim_token = journal, token
            journal.init(state=context.action_state, input_hash=action_state_hash(arguments))
            first = await invoke_read(context, 0, 128)
            saved = journal.load()
            node_run_id = local_node.id
            assert saved["state"]["evidence_reads"]["facts"][fact["fact_id"]]["ranges"] == [[0, 128]]
        engine = create_engine("sqlite+pysqlite:///" + path.as_posix())
        try:
            with Session(engine, expire_on_commit=False) as reopened:
                journal = AgentActionJournal(AgentRunRepository(reopened), run_id=run.id,
                                             node_run_id=node_run_id, expected_claim_token=token)
                assert journal.load() == saved
                restored = replace(context, action_state=deepcopy(saved["state"]), action_journal=journal,
                                   executed_tools=[], tool_calls=[])
                parts = [first["content"]]
                offset = first["next_offset"]
                while offset is not None:
                    part = await invoke_read(restored, offset, 4000)
                    parts.append(part["content"])
                    offset = part["next_offset"]
                assert "".join(parts) == canonical and json.loads("".join(parts)) == fact
                fully_read = deepcopy(restored.action_state)
                duplicate = await invoke_read(restored, 0, 128)
                assert duplicate["new_evidence_read"] is False and restored.action_state == fully_read
                assert restored.action_state["evidence_reads"]["facts"][fact["fact_id"]]["ranges"] == [[0, len(canonical)]]
                assert {key: value for key, value in restored.action_state.items() if key != "evidence_reads"} == {
                    key: value for key, value in initial.items() if key != "evidence_reads"}
        finally:
            engine.dispose()
    return {"fact_count": len(arguments["authoritative_facts"]), "catalog_pages": len(catalog),
            "bounded_json_fragments_reconstructed_exactly": True, "unknown_id_and_invalid_page_rejected": True,
            "goal_or_source_version_change_rejected": True, "actual_function_tool_invoked": True,
            "sqlite_reopened_and_read_progress_restored": True, "duplicate_read_did_not_change_progress": True,
            "generation_and_reviews_unchanged": True, "model_requests": 0}


def verify_review_error_boundary(db: Session, run: AgentRun, nodes: dict[str, AgentNodeRun]) -> dict:
    """损坏真实候选与真实输入的副本，区分可修正模型输出和不可继续的来源错误。"""
    record = next(item for item in nodes["final_review_batches"].output_payload["items"]
                  if item["output"]["differences"] and item["output"]["differences"][0].get("case_id"))
    source = deepcopy(nodes["prepare_final_review"].output_payload["items"][record["item_index"]])
    output = deepcopy(record["output"])
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=nodes["final_review_batches"].node_key, run_input=deepcopy(run.input_payload),
    )
    batch_case_ids = {case["case_id"] for case in source["test_cases"]}
    outside_case_id = next(case["case_id"] for case in nodes["generated_cases"].output_payload["test_cases"]
                           if case["case_id"] not in batch_case_ids)
    bad_output = deepcopy(output)
    bad_output["differences"][0]["case_id"] = outside_case_id
    feedback = rejected(lambda: postprocess_final_review_batch_item(context, {
        "item_input": source, "item_output": bad_output,
    }))
    assert feedback.details["field"] == "difference.case_id"
    bad_source = deepcopy(source)
    selected_case_id = output["differences"][0]["case_id"]
    bad_source["case_fact_bindings"] = [binding for binding in bad_source["case_fact_bindings"]
                                        if binding["case_id"] != selected_case_id]
    fault = rejected(lambda: postprocess_final_review_batch_item(context, {
        "item_input": bad_source, "item_output": output,
    }), ValueError)
    assert not isinstance(fault, OutputValidationError)
    assert source == nodes["prepare_final_review"].output_payload["items"][record["item_index"]]
    assert output == record["output"]
    return {"outside_case_feedback_is_repairable": True, "missing_source_binding_is_hard_failure": True}


def verify_partial_recheck(db: Session, run: AgentRun, nodes: dict[str, AgentNodeRun]) -> dict:
    """缩小真实已复审范围，验证同批次 ID 不会错误清除未触及用例的历史差异。"""
    baseline_inputs = deepcopy(nodes["prepare_final_review"].output_payload["items"])
    baseline_records = deepcopy(nodes["final_review_batches"].output_payload["items"])
    for index, source in enumerate(nodes["prepare_final_review_rechecks"].output_payload["items"]):
        batch_id = source["review_batch"]["batch_id"]
        baseline_index = next(i for i, item in enumerate(baseline_inputs) if item["review_batch"]["batch_id"] == batch_id)
        baseline = next(record["output"] for record in baseline_records if record["item_index"] == baseline_index)
        case_id = source["test_cases"][0]["case_id"]
        remaining = [difference for difference in baseline["differences"] if difference.get("case_id") != case_id]
        if not remaining or len(remaining) == len(baseline["differences"]):
            continue
        narrowed = deepcopy(source)
        narrowed["test_cases"] = [case for case in narrowed["test_cases"] if case["case_id"] == case_id]
        narrowed["case_fact_bindings"] = [item for item in narrowed["case_fact_bindings"] if item["case_id"] == case_id]
        narrowed["review_batch"]["case_ids"] = [case_id]
        record = next(item for item in nodes["final_review_rechecks"].output_payload["items"] if item["item_index"] == index)
        assert record["output"]["approved"] is True
        context = ToolExecutionContext(
            db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key=nodes["final_review_rechecks"].node_key, run_input=deepcopy(run.input_payload),
        )
        merged = merge_final_review_recheck_records(context, {
            "baseline_inputs": baseline_inputs, "baseline_records": baseline_records,
            "replacement_inputs": [narrowed],
            "replacement_records": [{"item_index": 0, "output": deepcopy(record["output"])}],
        })
        result = merged["items"][baseline_index]["output"]
        assert result["approved"] is False and result["differences"] == remaining
        return {"batch_id": batch_id, "narrowed_case_id": case_id, "untouched_difference_count": len(remaining)}
    raise AssertionError("真实运行缺少能验证局部复审保留其他差异的批次")


async def pending_budget(definition: AgentDefinition, input_payload: dict, journal: AgentActionJournal) -> tuple[dict, dict]:
    """以真实定义和输入计量一次请求准入；不发送请求，保留真实未结算状态。"""
    agent = Agent(
        name=definition.name, instructions=definition.instructions,
        model_settings=_model_settings(dict(definition.runtime_config or {})),
        output_type=_sdk_output_type(definition, has_tools=False),
    )
    inputs = [{"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)}]
    limits = model_request_reservation(agent=agent, system_prompt=definition.instructions, input_items=inputs)
    hooks = AutonomousBudgetHooks(limits, on_change=journal.save_budget)
    await hooks.on_llm_start(RunContextWrapper(context=input_payload), agent, definition.instructions, inputs)
    pending = hooks.snapshot()
    assert pending["pending_requests"] == 1
    restored = AutonomousBudgetHooks(limits, checkpoint=pending)
    assert restored.snapshot()["charged"] == pending["charged"]
    assert restored.snapshot()["interrupted_requests"] == 1
    assert restored.snapshot()["pending_requests"] == 0
    try:
        await restored.on_llm_start(RunContextWrapper(context=input_payload), agent, definition.instructions, inputs)
    except AgentExecutionBudgetExceeded:
        pass
    else:
        raise AssertionError("恢复不能把没有回执的已预留请求退款并重新发送")
    return limits, pending


def replay_journal(run: AgentRun, workflow: AgentWorkflowDefinition, node: AgentNodeRun,
                   arguments: dict, transitions: list[dict], failure: Exception,
                   definition: AgentDefinition, model_input: dict) -> dict:
    with TemporaryDirectory(prefix="qoder-review-journal-") as directory:
        uri = "sqlite+pysqlite:///" + (Path(directory) / "actions.sqlite").as_posix()
        engine = create_engine(uri)
        for model in (AgentWorkflowDefinition, AgentRun, AgentNodeRun, AgentRunEvent, AgentApproval):
            model.__table__.create(engine)
        copied_run, copied_node = copy_record(run), copy_record(node)
        copied_run.status, copied_run.finished_at, copied_run.claim_token = "pending", None, None
        copied_node.status, copied_node.finished_at = "running", None
        copied_node.input_payload, copied_node.sdk_state = deepcopy(arguments), {}
        token = new_claim_token()
        first = transitions[0]
        call_id = f"replay-{node.id}-action-1"
        args = {"arguments_json": json.dumps(first["arguments"], ensure_ascii=False)}
        try:
            with Session(engine, expire_on_commit=False) as db:
                db.add_all([copy_record(workflow), copied_run, copied_node])
                db.commit()
                repo = AgentRunRepository(db)
                transition_run(repo, copied_run, "running", event_type="run_started", payload={"source_run_id": run.id})
                copied_run.claim_token = token
                copied_run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
                db.commit()
                journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=token)
                checkpoint = journal.init(state=first["before"], input_hash=action_state_hash(arguments))
                _, budget = asyncio.run(pending_budget(definition, model_input, journal))
                journal.begin(tool_call_id=call_id, tool_key=first["tool_key"], args=args, prehash=checkpoint["state_hash"])
            # 关闭数据库连接后重新打开文件，恢复完全来自落盘数据，不保留 journal 对象。
            engine.dispose()
            engine = create_engine(uri)
            with Session(engine, expire_on_commit=False) as db:
                repo = AgentRunRepository(db)
                journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=token)
                loaded = journal.load()
                assert loaded["state"] == first["before"] and loaded["budget"] == budget
                rejected(lambda: journal.begin(
                    tool_call_id=call_id, tool_key=first["tool_key"], args=args, prehash=loaded["state_hash"],
                ), ActionInProgress)
                rejected(lambda: journal.begin(
                    tool_call_id=call_id + "-other", tool_key=first["tool_key"], args=args, prehash=loaded["state_hash"],
                ), ActionInProgress)
                journal.begin(tool_call_id=call_id, tool_key=first["tool_key"], args=args,
                              prehash=loaded["state_hash"], resume_started=True)
                before_commit = journal.load()
                before_events = db.query(AgentRunEvent).count()
                table_name = AgentRunEvent.__table__.name
                db.execute(text(f'''CREATE TRIGGER fail_action_commit BEFORE INSERT ON "{table_name}"
                    WHEN NEW.event_type = 'agent_action_completed'
                    BEGIN SELECT RAISE(ABORT, 'review action persistence interruption'); END'''))
                db.commit()
                fault = rejected(lambda: journal.complete(
                    tool_call_id=call_id, nextstate=first["after"], result=first["result"], budget=budget,
                ), DBAPIError)
                assert "review action persistence interruption" in str(fault)
                assert journal.load() == before_commit
                assert db.query(AgentRunEvent).count() == before_events
                db.execute(text("DROP TRIGGER fail_action_commit"))
                db.commit()
                journal.complete(tool_call_id=call_id, nextstate=first["after"], result=first["result"], budget=budget)
            engine.dispose()
            engine = create_engine(uri)
            with Session(engine, expire_on_commit=False) as db:
                repo = AgentRunRepository(db)
                journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=token)
                loaded = journal.load()
                assert loaded["state"] == first["after"]
                events_before_replay = db.query(AgentRunEvent).count()
                receipt = journal.begin(tool_call_id=call_id, tool_key=first["tool_key"], args=args,
                                        prehash=action_state_hash(first["before"]))
                assert receipt.replayed and receipt.result == first["result"]
                receipt = journal.complete(tool_call_id=call_id, nextstate=first["after"], result=first["result"])
                assert receipt.replayed and db.query(AgentRunEvent).count() == events_before_replay
                rejected(lambda: journal.begin(
                    tool_call_id=call_id, tool_key=transitions[1]["tool_key"], args=args, prehash=loaded["state_hash"],
                ), ActionStateConflict)
                rejected(lambda: journal.begin(
                    tool_call_id=call_id + "-stale", tool_key=first["tool_key"], args=args,
                    prehash=action_state_hash(first["before"]),
                ), ActionStateConflict)
                failed_id = call_id + "-rejected"
                journal.begin(tool_call_id=failed_id, tool_key=first["tool_key"], args=args, prehash=loaded["state_hash"])
                journal.fail(tool_call_id=failed_id, error={
                    "error_type": type(failure).__name__, "message": str(failure),
                    "details": deepcopy(getattr(failure, "details", {})),
                }, budget=budget)
                assert journal.load()["state"] == first["after"]
                local_node = db.get(AgentNodeRun, node.id)
                original_error = local_node.error_message
                local_node.error_message = str(original_error or "") + "（未提交）"
                rejected(journal.load, RuntimeError)
                assert local_node in db.dirty and local_node.error_message.endswith("（未提交）")
                db.rollback()
                assert db.get(AgentNodeRun, node.id).error_message == original_error
                altered_input = deepcopy(arguments)
                altered_input["case_budget"] -= 1
                local_node.input_payload = altered_input
                db.commit()
                rejected(journal.load, ActionStateConflict)
                local_node.input_payload = deepcopy(arguments)
                db.commit()
                assert journal.load()["state"] == first["after"]
                stale_run = repo.get_run_for_update(run_id=run.id)
                stale_run.claim_token = new_claim_token()
                db.commit()
                rejected(journal.load, RunLeaseLost)
                active = AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=stale_run.claim_token)
                assert active.load()["state"] == first["after"]
                transition_run(repo, stale_run, "cancelled", event_type="run_cancelled", payload={})
                db.commit()
                rejected(active.load, RunLeaseLost)
                events = db.query(AgentRunEvent).filter(AgentRunEvent.run_id == run.id).all()
                action_events = [event for event in events if event.event_type.startswith("agent_action_")]
                assert all("arguments" not in event.payload and "state" not in event.payload for event in action_events)
                return {
                    "durable_sqlite_reopen_count": 2, "started_action_requires_explicit_resume": True,
                    "completed_action_replayed_from_receipt": True, "stale_state_rejected": True,
                    "failure_kept_last_committed_state": True, "pending_budget_preserved": True,
                    "transaction_failure_rolled_back_state_receipt_events": True,
                    "dirty_session_not_committed_or_rolled_back": True,
                    "completed_action_did_not_duplicate_events": True, "input_change_rejected": True,
                    "stale_worker_rejected": True, "cancelled_run_rejected": True,
                    "action_event_count": len(action_events), "mysql_lock_concurrency": "not_tested",
                }
        finally:
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    options = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("必须提供真实成功运行")
        nodes = load_nodes(db, run.id)
        before = {key: deepcopy((node.input_payload, node.output_payload, node.sdk_state)) for key, node in nodes.items()}
        arguments = review_arguments(run, nodes)
        domain, transitions, failure = replay_domain(db, run, nodes, arguments)
        domain["partial_recheck_preserved_other_differences"] = verify_partial_recheck(db, run, nodes)
        domain["review_error_boundary"] = verify_review_error_boundary(db, run, nodes)
        domain["persistent_goal_and_evidence"] = asyncio.run(verify_evidence_state(db, run, nodes, arguments))
        definition = db.get(AgentDefinition, nodes["final_review_repairs"].agent_definition_id)
        journal = replay_journal(
            run, db.get(AgentWorkflowDefinition, run.workflow_definition_id), nodes["prepare_final_review"],
            arguments, transitions, failure, definition,
            nodes["prepare_final_review_repairs"].output_payload["items"][0],
        )
        for key, node in nodes.items():
            db.refresh(node)
            assert (node.input_payload, node.output_payload, node.sdk_state) == before[key]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        print(json.dumps({
            "run_id": run.id, "domain": domain, "journal": journal,
            "model_calls": 0, "online_database_unchanged": True,
            "validation_scope": "真实历史结果回放、领域状态和 SQLite 行动日志恢复；未执行模型或操作系统进程杀停",
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
