"""用真实运行回放统一生成领域，不发模型请求，不修改线上记录。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform import test_generation_review_agent as review
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from scripts.qa.verify.generation_contract_history import project_historical_generation_inputs


def rejected(operation):
    try:
        operation()
    except (OutputValidationError, ValueError) as error:
        return str(error)
    raise AssertionError("旧输入、过期行动或重复审查必须被拒绝")


def _outputs(node, indexes):
    records = {record["item_index"]: record["output"] for record in node.output_payload["items"]}
    return [deepcopy(records[index]) for index in indexes]


def _input_items(repo, node):
    return deepcopy(repo.read_node_input(node)["items"])


def _apply_recorded(context, node, real_inputs, prepare, apply, indexes=None):
    indexes = list(range(len(real_inputs))) if indexes is None else indexes
    action = prepare(context, {"item_indexes": indexes})
    assert action["item_indexes"] == indexes
    assert action["items"] == [real_inputs[index] for index in indexes]
    apply(context, action, _outputs(node, indexes))
    return action


def verify(db, run_id):
    repo = AgentRunRepository(db)
    run = db.get(AgentRun, run_id)
    assert run is not None and run.status == "success"
    keys = ("resolve_planning", "authority", "effective_facts", "generation", "generated_cases",
            "prepare_final_review", "final_review_batches", "resolve_final_review")
    nodes = {key: repo.latest_node_run(run_id=run_id, node_key=key) for key in keys}
    assert all(node is not None and node.status == "success" for node in nodes.values())
    before = action_state_hash([run.run_context, run.output_payload,
                                [[node.input_payload, node.output_payload, node.sdk_state] for node in nodes.values()]])
    raw = deepcopy(repo.read_node_input(nodes["resolve_planning"]))
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key="unified_generation_readonly", run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context["artifacts"]),
    )
    generation.initialize_generation_state(context, raw)
    state = deepcopy(context.action_state)
    child = generation._planning_context(context, state)
    child.action_state = deepcopy(nodes["resolve_planning"].sdk_state["action_checkpoint"]["state"])
    # 历史独立协调器已完成；统一父目标继续工作，仅在本地取消子协调器的终止标记。
    child.action_state["completed"] = False
    planning._commit(child, child.action_state)
    assert planning._resolved_output(child) == nodes["resolve_planning"].output_payload
    state["planning"] = child.action_state
    generation._save(context, state)
    generation.accept_generation_plan(context, {})
    authority_inputs = _input_items(repo, nodes["authority"])
    assert context.action_state["authority_inputs"] == authority_inputs
    _apply_recorded(context, nodes["authority"], authority_inputs,
                    generation.prepare_authority_action, generation.apply_authority_action)
    assert context.action_state["effective_facts"] == nodes["effective_facts"].output_payload
    generation_inputs, contract_projection = project_historical_generation_inputs(_input_items(repo, nodes["generation"]))
    assert context.action_state["generation_inputs"] == generation_inputs
    before_generation = deepcopy(context.action_state)
    generation_action = _apply_recorded(context, nodes["generation"], generation_inputs,
                                       generation.prepare_generation_action, generation.apply_generation_action)
    assert context.action_state["generation"] == nodes["generated_cases"].output_payload
    review_inputs = _input_items(repo, nodes["final_review_batches"])
    assert context.action_state["review_inputs"] == review_inputs
    _apply_recorded(context, nodes["final_review_batches"], review_inputs,
                    generation.prepare_initial_review_action, generation.apply_initial_review_action)
    saved_review = nodes["resolve_final_review"].sdk_state["action_checkpoint"]["state"]
    assert saved_review["generation_version"] == 0, "本回放不伪造缺少的真实修复动作"

    def replay_global(child, arguments):
        action = review.prepare_global_review(child, arguments)
        assert action_state_hash(action["items"][0]) == saved_review["global_review"]["input_sha256"]
        return review.apply_global_review(child, action, [deepcopy(saved_review["global_review"]["output"])])

    asyncio.run(generation._review_action(context, {}, replay_global))
    expected = {**deepcopy(nodes["resolve_planning"].output_payload),
                "authoritative_facts": deepcopy(nodes["effective_facts"].output_payload["authoritative_facts"]),
                **deepcopy(nodes["resolve_final_review"].output_payload)}
    assert generation._resolved_output(context) == expected
    approved_state = deepcopy(context.action_state)
    generation.finish_generation(context, {})
    assert generation.validate_generation_resolution(context, {
        "input_payload": raw, "output": {"completed": True},
    }) == expected

    # 布局验证采用同一运行自然出现的数量拒绝，回到该候选被接受之前的真实输入阶段。
    rejected_item, rejected_observation = next(
        (item, observation)
        for item in nodes["generation"].sdk_state["items"]
        for observation in item["tool_observations"]
        if observation["status"] == "rejected"
        and "模型用例数量与平台编号契约不一致" in observation["feedback"]["message"]
    )
    index = rejected_item["item_index"]
    layout_context = replace(context, action_state=deepcopy(before_generation))
    _apply_recorded(layout_context, nodes["generation"], generation_inputs,
                    generation.prepare_generation_action, generation.apply_generation_action,
                    indexes=[value for value in range(len(generation_inputs)) if value != index])
    state = deepcopy(layout_context.action_state)
    original_layout = deepcopy(state["generation_layout"])
    generation._problem(
        state, origin="generation", input_hash=action_state_hash(generation_inputs[index]),
        payload=deepcopy(rejected_observation["feedback"]),
        fact_ids=[fact["fact_id"] for fact in generation_inputs[index]["authoritative_facts"]],
        batch_indexes=[index],
    )
    generation._save(layout_context, state)
    problems = generation.observe_generation_state(layout_context)["problems"]
    assert len(problems) == 1
    resolved_problem = replace(layout_context, action_state=deepcopy(layout_context.action_state))
    _apply_recorded(resolved_problem, nodes["generation"], generation_inputs,
                    generation.prepare_generation_action, generation.apply_generation_action, indexes=[index])
    assert generation.observe_generation_state(resolved_problem)["problems"] == []
    target = generation_inputs[index]["batch"]
    donor_index = next(value for value, item in enumerate(generation_inputs)
                       if item["case_budget"] > 1 and item["batch"]["module_index"] != target["module_index"])
    donor = generation_inputs[donor_index]["batch"]
    changed_layout = deepcopy(original_layout)
    changed_layout[target["module_index"]]["groups"][target["module_batch_index"]]["case_budget"] += 1
    changed_layout[donor["module_index"]]["groups"][donor["module_batch_index"]]["case_budget"] -= 1
    generation.revise_generation_layout(layout_context, {
        "problem_ids": [problems[0]["problem_id"]],
        "changes": [changed_layout[value] for value in sorted({target["module_index"], donor["module_index"]})],
    })
    changed_inputs = layout_context.action_state["generation_inputs"]
    assert len(changed_inputs) == len(generation_inputs)
    changed_indexes = [value for value, (old, new) in enumerate(zip(generation_inputs, changed_inputs, strict=True))
                       if old != new]
    assert changed_indexes and index in changed_indexes
    unchanged = [value for value in range(len(generation_inputs)) if value not in changed_indexes]
    assert all(layout_context.action_state["generation_records"][value] is not None for value in unchanged)
    assert all(layout_context.action_state["generation_records"][value] is None for value in changed_indexes)
    assert sum(item["case_budget"] for item in changed_inputs) == run.input_payload["case_budget"]
    assert layout_context.action_state["review"] is None
    stale_action_error = rejected(lambda: generation.apply_generation_action(
        layout_context, generation_action, _outputs(nodes["generation"], generation_action["item_indexes"]),
    ))
    stale_review = deepcopy(layout_context.action_state)
    stale_review["review"] = deepcopy(approved_state["review"])
    generation._save(layout_context, stale_review)
    stale_review_error = rejected(lambda: generation.finish_generation(layout_context, {}))

    # 单独验证全部生成记录已齐时，也不能把另一真实批次输入套在旧终审状态上。
    stale_binding = replace(context, action_state=deepcopy(approved_state))
    state = deepcopy(stale_binding.action_state)
    state["review"]["input"] = deepcopy(state["review"]["input"])
    state["review"]["input"]["generation_inputs"][0] = deepcopy(generation_inputs[-1])
    def submit_stale_binding():
        # 当前问题投影也复验来源范围；过期绑定可能在保存阶段即被拒绝。
        generation._save(stale_binding, state)
        generation.finish_generation(stale_binding, {})

    stale_review_binding_error = rejected(submit_stale_binding)

    # 原布局完整输入再次命中时，领域结论也必须命中，不能清掉全局审查历史再抽一次。
    restored = replace(context, action_state=deepcopy(approved_state))
    state = deepcopy(restored.action_state)
    generation._prepare_generation(restored, state)
    generation._save(restored, state)
    assert generation._resolved_output(restored) == expected
    current_review = generation._review_context(restored, restored.action_state)
    repeated_review_error = rejected(lambda: review.prepare_global_review(current_review, {}))
    assert restored.action_state["review"]["state"]["reviewed_global_scope_hashes"] == saved_review["reviewed_global_scope_hashes"]
    assert before == action_state_hash([run.run_context, run.output_payload,
                                       [[node.input_payload, node.output_payload, node.sdk_state] for node in nodes.values()]])
    return {
        "status": "passed", "observed_at": datetime.now(timezone.utc).isoformat(), "run_id": run.id,
        "source_nodes": {key: node.id for key, node in nodes.items()},
        "planning_checkpoint_contents_unchanged_except_local_completed": True,
        "authority_full_inputs_match": True, "effective_facts_match": True,
        "generation_full_inputs_match": True, "generation_merged_output_matches": True,
        "review_full_inputs_match": True, "full_finished_output_matches": True,
        "historical_generation_contract_projection": contract_projection,
        "layout_change": {"real_rejection_item_index": index,
                          "real_rejection": rejected_observation["feedback"]["message"],
                          "changed_indexes": changed_indexes, "unchanged_result_reuse_count": len(unchanged),
                          "user_case_budget": run.input_payload["case_budget"],
                          "old_action_rejection": stale_action_error, "old_review_rejection": stale_review_error},
        "resolved_generation_problem_not_current": True,
        "complete_generation_with_stale_review_input_rejection": stale_review_binding_error,
        "same_full_input_restores_prior_global_verdict": True,
        "repeated_global_review_rejection": repeated_review_error,
        "model_requests_sent": 0, "database_writes": 0, "persisted_records_unchanged": True,
        "boundary": "真实产物串接与领域回放；布局调整是基于真实拒绝的本地行动演练，不是模型自然重规划，也没有为新布局生成或审查候选。",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = verify(db, args.run_id)
        db.rollback()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
