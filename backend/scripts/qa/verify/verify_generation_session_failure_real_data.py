"""只读真实失败运行，在领域提交边界复验成功前缀和专业会话失败的隔离。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from functools import partial
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from agents.exceptions import UserError

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import (
    AgentSubtreeRequestBudgetExceeded, AutonomousBudgetHooks,
)
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.request_diagnostics import unwrap_sdk_execution_error
from modules.agent_platform.sdk_adapter import AgentOutputContract, _revalidate_managed_agent_call
from modules.agent_platform.test_generation_escalation import validate_generation_scope_feedback


def fingerprint(run, node):
    return action_state_hash({
        "run": [run.status, run.input_payload, run.run_context, run.output_payload],
        "node": [node.status, node.input_payload, node.output_payload, node.sdk_state, node.error_message],
    })


def events_fingerprint(db, run_id):
    events = db.query(AgentRunEvent).filter(AgentRunEvent.run_id == run_id).all()
    return len(events), action_state_hash(sorted(
        ([event.id, event.event_type, event.payload] for event in events), key=lambda event: event[0],
    ))


def prepare_failed_generation_state(context, sdk_state, *, generator_definition, action_call_id=None):
    """仅用于新诊断种子：以当前合同复验真实旧产物，不冒充跨代码版本的普通恢复。"""
    assert context.action_journal is None
    checkpoint = deepcopy(sdk_state["action_checkpoint"])
    candidates = [receipt for receipt in checkpoint["receipts"].values()
                  if receipt["status"] == "failed" and receipt["tool_key"] == "generate_cases"
                  and (receipt.get("error", {}).get("error_type") == "AgentSubtreeRequestBudgetExceeded"
                       or (receipt.get("error", {}).get("error_type") == "UserError"
                           and any(item["error_type"] == "AgentSubtreeRequestBudgetExceeded"
                                   for item in sdk_state["error_diagnostic"]["exception_chain"])))]
    if action_call_id is None:
        assert len(candidates) == 1, "必须明确选择唯一的真实局部失败行动"
        action_call_id = candidates[0]["tool_call_id"]
    failed_action = checkpoint["receipts"][action_call_id]
    assert failed_action in candidates
    assert failed_action["pre_state_hash"] == failed_action["post_state_hash"]
    arguments = json.loads(failed_action["arguments"]["arguments_json"])
    context.action_state = deepcopy(checkpoint["state"])
    generation.observe_generation_state(context)
    assert set(context.action_state["generation_cache"]) == {
        record["input_hash"] for record in context.action_state["generation_records"] if record is not None
    }, "存在不属于当前输入的历史缓存，需要单独复验后才能作为新诊断种子"
    previous_indexes = []
    for index, record in enumerate(context.action_state["generation_records"]):
        if record is None:
            continue
        item = context.action_state["generation_inputs"][index]
        assert record["input_hash"] == action_state_hash(item)
        assert context.action_state["generation_cache"][record["input_hash"]] == record
        matched = []
        for receipt in checkpoint["validated_agent_calls"].values():
            if receipt["identity"]["agent_key"] != "test_case_generator" or receipt["output"] != record["output"]:
                continue
            parent = checkpoint["receipts"][receipt["parent_tool_call_id"]]
            assert parent["status"] == "completed" and parent["tool_key"] == "generate_cases"
            original_arguments = json.loads(parent["arguments"]["arguments_json"])
            assert index in original_arguments["item_indexes"]
            request = deepcopy(item)
            guidance = original_arguments.get("guidance") or ""
            if guidance:
                request["_planning_feedback"] = {"instruction": guidance, "source": "协调者建议，不是新增事实或预算"}
            if receipt["identity"]["input_hash"] != action_state_hash(request):
                continue
            assert receipt["identity"]["execution_semantic_hash"] == checkpoint["execution_semantic_hash"]
            assert receipt["raw_output_hash"] == action_state_hash(receipt["raw_output"])
            assert receipt["output_hash"] == action_state_hash(receipt["output"])
            contract = AgentOutputContract(request, AgentMapConfig(item_postprocessor="testing.postprocess_generation_batch_item"),
                alternate_output_validators={"planning_feedback": partial(validate_generation_scope_feedback, item)})
            assert _revalidate_managed_agent_call(saved=receipt, contract=contract,
                definition=generator_definition, context=context) == record["output"]
            matched.append(receipt)
        assert len(matched) == 1
        previous_indexes.append(index)
    action = generation.prepare_generation_action(context, arguments)
    receipts = list(checkpoint["validated_agent_calls"].values())
    child_calls = [call for call in sdk_state["tool_calls"] if call.get("tool_key") == "agent:test_case_generator"]
    outputs, evidence, failure_offset, failure_call = [], [], None, None
    for offset, (index, item, request) in enumerate(zip(
        action["item_indexes"], action["items"], action["requests"], strict=True,
    )):
        request_hash = action_state_hash(request)
        saved = [receipt for receipt in receipts if receipt["identity"]["agent_key"] == "test_case_generator"
                 and receipt["identity"]["input_hash"] == request_hash]
        calls = [call for call in child_calls if call["input_hash"] == request_hash]
        if failure_offset is not None:
            assert not saved and not calls
            evidence.append({"item_index": index, "request_hash": request_hash, "status": "not_started"})
            continue
        if not saved:
            assert len(calls) == 1 and calls[0]["status"] == "failed"
            failure_offset, failure_call = offset, calls[0]
            evidence.append({"item_index": index, "request_hash": request_hash, "status": "failed"})
            continue
        assert len(saved) == len(calls) == 1 and calls[0]["status"] == "validated"
        receipt = saved[0]
        assert receipt["parent_tool_call_id"] == action_call_id
        assert receipt["identity"]["execution_semantic_hash"] == checkpoint["execution_semantic_hash"]
        assert receipt["raw_output_hash"] == action_state_hash(receipt["raw_output"])
        assert receipt["output_hash"] == action_state_hash(receipt["output"])
        contract = AgentOutputContract(
            deepcopy(request), AgentMapConfig(item_postprocessor="testing.postprocess_generation_batch_item"),
            alternate_output_validators={"planning_feedback": partial(validate_generation_scope_feedback, item)},
        )
        normalized = _revalidate_managed_agent_call(saved=receipt, contract=contract, definition=generator_definition, context=context)
        assert normalized == receipt["output"] and normalized.get("result_type") != "planning_feedback"
        outputs.append(normalized)
        evidence.append({"item_index": index, "request_hash": request_hash, "status": "validated",
                         "raw_output_hash": receipt["raw_output_hash"], "output_hash": receipt["output_hash"]})
    assert failure_offset is not None and failure_call is not None and failure_offset == len(outputs)
    successful_indexes = action["item_indexes"][:failure_offset]
    failed_index = action["item_indexes"][failure_offset]
    unstarted_indexes = action["item_indexes"][failure_offset + 1:]
    failed_item, failed_request = action["items"][failure_offset], action["requests"][failure_offset]
    # 从持久化异常与真实工具回执恢复内部失败类型，不替换 invoke_agent，也不编造模型产物。
    if failed_action["error"]["error_type"] == "UserError":
        # 严格按真实失败工具记录还原 SDK 包装关系，不按错误文案猜测类型。
        nested = [item for item in failure_call["tool_observations"]
                  if item.get("status") == "error" and item.get("error_type") == "AgentSubtreeRequestBudgetExceeded"]
        assert len(nested) == 1
        wrapped = UserError(failed_action["error"]["message"])
        wrapped.__cause__ = AgentSubtreeRequestBudgetExceeded(nested[0]["message"])
        error = unwrap_sdk_execution_error(wrapped)
        assert error is wrapped.__cause__
    else:
        error = AgentSubtreeRequestBudgetExceeded(failed_action["error"]["message"])
    error.diagnostic = {
        "budget_usage": deepcopy(checkpoint["budget"]),
        "exception_chain": deepcopy(sdk_state["error_diagnostic"]["exception_chain"]),
        "tool_observations": deepcopy(failure_call["tool_observations"]),
    }
    error.tool_calls = deepcopy(failure_call["submissions"])
    outputs.append(generation._GenerationSessionFailure(error=error))
    completed_arguments = {**arguments, "item_indexes": action["item_indexes"][:failure_offset + 1]}
    completed_action = generation.prepare_generation_action(context, completed_arguments)
    observation = generation.apply_generation_action(context, completed_action, outputs)
    assert all(context.action_state["generation_records"][index] == checkpoint["state"]["generation_records"][index]
               for index in previous_indexes)
    return {
        "stage": "persisted_generation_failure", "source_execution_semantic_hash": checkpoint["execution_semantic_hash"],
        "parent_action_call_id": action_call_id, "previous_validated_indexes": previous_indexes,
        "validated_success_prefix_indexes": successful_indexes, "failed_index": failed_index,
        "unstarted_indexes": unstarted_indexes, "items": evidence,
        "source_budget": deepcopy(checkpoint["budget"]), "seed_state_hash": action_state_hash(context.action_state),
        "failure_problem_ids": [item["problem_id"] for item in observation["problems"]
                                if item["reported_problem"].get("result_type") == "specialist_failure"],
    }


def verify(run_id, node_id, action_call_id):
    source_files = [Path("modules/agent_platform") / name for name in (
        "test_generation_agent.py", "autonomous_budget.py", "sdk_adapter.py", "action_journal.py",
    )]
    source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run, node = db.get(AgentRun, run_id), db.get(AgentNodeRun, node_id)
        assert run is not None and node is not None and node.run_id == run_id
        assert run.status == node.status == "failed"
        original = fingerprint(run, node)
        event_count, event_hash = events_fingerprint(db, run_id)
        checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
        initial_state = deepcopy(checkpoint["state"])
        failed_action = checkpoint["receipts"][action_call_id]
        assert failed_action["status"] == "failed" and failed_action["tool_key"] == "generate_cases"
        assert failed_action["error"]["error_type"] in {"AgentSubtreeRequestBudgetExceeded", "UserError"}
        assert failed_action["pre_state_hash"] == failed_action["post_state_hash"]
        arguments = json.loads(failed_action["arguments"]["arguments_json"])
        context = ToolExecutionContext(
            db=db, user_id=run.user_id, project_id=run.project_id, run_id=run_id,
            node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=AgentRunRepository(db).read_node_input(node),
            action_state=deepcopy(initial_state), artifacts=deepcopy(run.run_context["artifacts"]),
        )
        assert context.invoke_agent is None and context.action_journal is None
        budget = AutonomousBudgetHooks(checkpoint["budget"]["limits"], checkpoint=deepcopy(checkpoint["budget"]))
        before_budget = budget.snapshot()
        child = AgentDefinitionRepository(db).get_agent(project_id=run.project_id, agent_key="test_case_generator")
        seed = prepare_failed_generation_state(context, node.sdk_state, generator_definition=child, action_call_id=action_call_id)
        successful_indexes, failed_index, unstarted_indexes = (seed[key] for key in
            ("validated_success_prefix_indexes", "failed_index", "unstarted_indexes"))
        assert successful_indexes
        evidence = seed["items"]
        failure_offset = len(successful_indexes)
        original_context = generation._isolated(context, state=initial_state)
        action = generation.prepare_generation_action(original_context, arguments)
        failed_item, failed_request = action["items"][failure_offset], action["requests"][failure_offset]
        failure_call = next(call for call in node.sdk_state["tool_calls"]
            if call.get("tool_key") == "agent:test_case_generator" and call.get("input_hash") == action_state_hash(failed_request))
        observation = generation.observe_generation_state(context)
        state = context.action_state
        for offset, index in enumerate(successful_indexes):
            record = state["generation_records"][index]
            source_receipt = next(receipt for receipt in checkpoint["validated_agent_calls"].values()
                if receipt["identity"]["agent_key"] == "test_case_generator"
                and receipt["identity"]["input_hash"] == action_state_hash(action["requests"][offset]))
            assert record == generation._record(action["items"][offset], source_receipt["output"], index)
            assert state["generation_cache"][record["input_hash"]] == record
        for index, record in enumerate(initial_state["generation_records"]):
            if index not in successful_indexes:
                assert state["generation_records"][index] == record
        for index in [failed_index, *unstarted_indexes]:
            assert state["generation_records"][index] is None
            assert index in observation["generation"]["pending_indexes"]
            assert action_state_hash(state["generation_inputs"][index]) not in state["generation_cache"]
        failed_request_hash = action_state_hash(failed_request)
        assert state["generation_requests"][failed_request_hash]["result_type"] == "specialist_failure"
        for request in action["requests"][failure_offset + 1:]:
            assert action_state_hash(request) not in state["generation_requests"]
        problems = [problem for problem in state["problems"].values()
                    if problem["input_hash"] == action_state_hash(failed_item)
                    and problem["payload"].get("result_type") == "specialist_failure"]
        assert len(problems) == 1
        problem = problems[0]
        payload = problem["payload"]
        assert payload["error_type"] == "AgentSubtreeRequestBudgetExceeded"
        expected_message = (failed_action["error"]["message"] if failed_action["error"]["error_type"] == payload["error_type"]
                            else next(item["message"] for item in failure_call["tool_observations"]
                                      if item.get("error_type") == payload["error_type"]))
        assert payload["message"] == expected_message
        assert payload["tool_observations"] == failure_call["tool_observations"]
        assert payload["budget_usage"] == checkpoint["budget"]
        submission_attempts = [call for call in failure_call["submissions"] if call["tool_key"].startswith("submit_")]
        assert len(payload["submission_attempts"]) == len(submission_attempts) > 0
        for attempt, original_attempt in zip(payload["submission_attempts"], submission_attempts, strict=True):
            assert all(attempt[key] == value for key, value in original_attempt.items())
            assert attempt["evidence_role"] == "提交尝试，不能据此认定候选已验收"
        assert payload["consultation_results"] == []
        assert payload["tool_observations"]
        assert problem["generation_indexes"] == [failed_index]
        assert any(issue["reported_problem"] == payload for issue in observation["problems"])
        assert generation.inspect_generation_scope(context, {"problem_id": problem["problem_id"]})["problem"] == problem
        assert state["accepted_plan"] == initial_state["accepted_plan"]
        assert state["effective_facts"] == initial_state["effective_facts"]
        assert state["generation_layout"] == initial_state["generation_layout"]
        assert state["generation"] is None and state["review"] is None
        state_hash = action_state_hash(state)
        try:
            generation.prepare_generation_action(context, {**arguments, "item_indexes": [failed_index]})
        except OutputValidationError as rejection:
            same_request_rejection = str(rejection)
        else:
            raise AssertionError("同一完整输入及指导语被允许重试")
        # 新指导只引用实际校验拒绝，不添加业务事实；准备请求不会自动调用模型。
        rejection_messages = [event["feedback"]["message"] for event in failure_call["tool_observations"]
                              if event.get("status") == "rejected"]
        assert rejection_messages
        revised_guidance = arguments["guidance"] + "\n请针对本次真实校验反馈修正工具参数：\n" + "\n".join(rejection_messages)
        new_action = generation.prepare_generation_action(context, {"item_indexes": [failed_index], "guidance": revised_guidance})
        assert action_state_hash(new_action["requests"][0]) != failed_request_hash
        assert action_state_hash(context.action_state) == state_hash
        layout_split_checked = False
        # 单用例批次不能直接拆分并维持本批额度；不伪造可拆分的测试前提。
        if failed_item["case_budget"] > 1:
            # 在隔离分支中按当前失败范围拆分既有事实，检查布局决策能力和用户总预算约束。
            layout_context = generation._isolated(context, state=state)
            module_index = failed_item["batch"]["module_index"]
            change = deepcopy(state["generation_layout"][module_index])
            fact_ids = failed_item["case_fact_contract"]["required_fact_ids"]
            group_index = next(index for index, group in enumerate(change["groups"]) if set(group["fact_ids"]) == set(fact_ids))
            group = change["groups"][group_index]
            assert len(group["fact_ids"]) > 1 and group["case_budget"] > 1
            split = len(group["fact_ids"]) // 2
            change["groups"][group_index:group_index + 1] = [
                {"fact_ids": group["fact_ids"][:split], "case_budget": 1},
                {"fact_ids": group["fact_ids"][split:], "case_budget": group["case_budget"] - 1},
            ]
            revised_observation = generation.revise_generation_layout(layout_context, {"problem_ids": [problem["problem_id"]], "changes": [change]})
            assert layout_context.action_state["effective_facts"] == state["effective_facts"]
            assert layout_context.action_state["accepted_plan"] == state["accepted_plan"]
            assert sum(item["case_budget"] for item in layout_context.action_state["generation_inputs"]) == context.action_input["case_budget"]
            assert revised_observation["generation"]["layout_version"] == observation["generation"]["layout_version"] + 1
            layout_split_checked = True
        assert action_state_hash(context.action_state) == state_hash
        assert budget.snapshot() == before_budget == checkpoint["budget"]
        reopened_budget = AutonomousBudgetHooks(checkpoint["budget"]["limits"], checkpoint=budget.snapshot())
        assert reopened_budget.snapshot() == checkpoint["budget"]
        assert initial_state == checkpoint["state"]
        db.expire_all()
        assert fingerprint(db.get(AgentRun, run_id), db.get(AgentNodeRun, node_id)) == original
        assert events_fingerprint(db, run_id) == (event_count, event_hash)
        assert not db.new and not db.dirty and not db.deleted
        result = {
            "status": "passed", "source_run_id": run_id, "source_node_id": node_id,
            "parent_action_call_id": action_call_id, "original_failure": node.error_message,
            "validated_success_prefix_indexes": successful_indexes, "failed_index": failed_index,
            "unstarted_indexes": unstarted_indexes, "full_request_and_saved_semantic_matched": True,
            "raw_revalidated_by_current_full_contract": True, "success_cache_excludes_failed_and_unstarted": True,
            "same_full_request_rejection": same_request_rejection, "new_guidance_prepared_without_model": True,
            "layout_split_prepared_without_model": layout_split_checked, "case_budget_preserved": context.action_input["case_budget"],
            "failure_problem_id": problem["problem_id"], "failure_payload": payload, "seed_metadata": seed,
            "budget_unchanged_after_apply_and_reopen": budget.snapshot(),
            "original_db_state_receipts_budget_failure_events_unchanged": True,
            "source_fingerprint": original, "source_event_count": event_count, "source_event_hash": event_hash,
            "production_source_hashes": source_hashes, "items": evidence,
            "model_requests_sent": 0, "database_writes": 0, "mysql_transaction": "READ ONLY",
            "verification_limits": [
                "直接回放真实回执至领域提交边界，未替换 invoke_agent，未执行新的模型对话或宣称父 Agent 已自然恢复",
                "局部六次逐请求 usage 未保存；恢复真实聚合预算，不伪造逐请求用量或宣称重放该子树网络调用",
                "原始失败保留父共享预算，未据错误文本合成缺失的局部预算快照",
                "旧失败摘要未保存咨询后处理结果，只恢复 submission_attempts 与真实观察哈希，不补造 consultation_results",
            ],
        }
    assert source_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--action-call-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("验证报告已存在，禁止覆盖")
    result = verify(args.run_id, args.node_id, args.action_call_id)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "validated_success_prefix_indexes",
        "failed_index", "unstarted_indexes", "case_budget_preserved", "model_requests_sent")}, ensure_ascii=False))
