"""只读第二轮真实检查点与专业回执，验证问题投影、批次映射及当前候选读取。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from functools import partial
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy.orm import Session

from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_review_agent as review
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import AgentOutputContract, _revalidate_managed_agent_call
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from modules.agent_platform.test_generation_review import refresh_final_review_input
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine


def by_batch(state):
    result = {}
    for problem in state["problems"].values():
        result.setdefault(problem["review_provenance"]["batch_id"], {})[problem["problem_id"]] = problem
    return result


def specialist_outputs(saved, action, agent_key, handler, context, validator=None):
    spec = next(value for value in BUILTIN_AGENT_SPECS if value["agent_key"] == agent_key)
    definition = AgentDefinition(**{key: deepcopy(value) for key, value in spec.items() if key != "version"})
    outputs = []
    for index, item in enumerate(action["items"]):
        record = next(record for record in saved["validated_agent_calls"].values()
                      if record["identity"]["agent_key"] == agent_key
                      and record["identity"]["input_hash"] == action_state_hash(item))
        assert record["identity"]["execution_semantic_hash"] == saved["execution_semantic_hash"]
        contract = AgentOutputContract(item, AgentMapConfig(item_postprocessor=handler),
            candidate_validator=partial(validator, context, action, index) if validator else None)
        outputs.append(_revalidate_managed_agent_call(saved=record, contract=contract,
                                                     definition=definition, context=context))
    return outputs


def verify(source_report, output):
    paths = [source_report, source_report.with_suffix(".sqlite"), source_report.with_suffix(".checkpoint.json")]
    source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = json.loads(source_report.read_text(encoding="utf-8"))
    saved = json.loads(paths[2].read_text(encoding="utf-8"))["checkpoint"]
    assert report["model_usage"]["requests"] == 92
    engine = sqlite_engine(paths[1])
    with Session(engine) as db:
        run = db.get(AgentRun, report["source_run_id"])
        node = db.get(AgentNodeRun, report["isolated_node_run_id"])
        assert node.sdk_state["action_checkpoint"] == saved
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(node.input_payload), action_state=deepcopy(saved["state"]),
            artifacts=deepcopy(run.run_context["artifacts"]))
        original_count = len(context.action_state["problems"])
        generation._save(context, context.action_state)
        final_projection = deepcopy(context.action_state["problems"])
        final_count = len(final_projection)
        generation._save(context, context.action_state)
        assert context.action_state["problems"] == final_projection
        final_observation = generation.observe_generation_state(context)
        generation_batch = context.action_state["generation_inputs"][14]["batch"]["batch_id"]
        target = next(item for item in final_observation["initial_review_batches"]
                      if generation_batch in item["generation_batch_ids"])
        assert target["item_index"] == 10 and target["batch_id"] == "R-011"
        assert target["case_ids"] == ["TC-035", "TC-036"]
        inspected = generation.inspect_generation_scope(context, {"item_index": 14, "problem_id": None})
        assert inspected["candidate"] is None
        assert inspected["review_scopes"][0]["item_index"] == 10
        assert inspected["review_scopes"][0]["input"] == refresh_final_review_input(
            context.action_state["review_inputs"][10], generation=context.action_state["review"]["state"]["generation"],
            generation_inputs=context.action_state["generation_inputs"])

        # 初审输入和输出均取自真实嵌套领域源；重放首个真实修复，不重发模型。
        initial = deepcopy(saved["state"])
        child = review.compose_review_context(context, input_payload=initial["review"]["input"])
        initial["review"]["state"] = deepcopy(child.action_state)
        generation._save(context, initial)
        before = by_batch(context.action_state)
        receipt = next(value for value in saved["receipts"].values()
                       if value["tool_key"] == "repair_review_batch" and value["status"] == "completed")
        arguments = json.loads(receipt["arguments"]["arguments_json"])
        batch_id = arguments["batch_id"]
        action = review.prepare_review_repair(child, arguments)
        repair_outputs = specialist_outputs(saved, action, "test_generation_batch_repairer",
            "testing.postprocess_final_review_repair_item", child, review.validate_review_repair_candidate)
        review.apply_review_repair(child, action, repair_outputs)
        state = deepcopy(context.action_state)
        state["review"]["state"] = deepcopy(child.action_state)
        generation._save(context, state)
        pending = by_batch(context.action_state)
        assert batch_id not in pending
        assert {key: value for key, value in before.items() if key != batch_id} == pending
        invalidated_ids = list(before[batch_id])
        try:
            generation._feedback(context.action_state, [invalidated_ids[0]])
        except OutputValidationError:
            pass
        else:
            raise AssertionError("尚未复审的新候选接受了旧问题身份")
        scope = next(item for item in initial["review"]["input"]["review_inputs"] if item["review_batch"]["batch_id"] == batch_id)
        generation_index = next(index for index, item in enumerate(initial["generation_inputs"])
                                if item["batch"]["batch_id"] in scope["review_batch"]["generation_batch_ids"])
        pending_inspect = generation.inspect_generation_scope(context, {"item_index": generation_index, "problem_id": None})
        pending_scope = next(item for item in pending_inspect["review_scopes"] if item["input"]["review_batch"]["batch_id"] == batch_id)
        assert pending_scope["status"] == "awaiting_recheck" and pending_scope["review_result"] is None
        assert pending_scope["input"]["test_cases"] != scope["test_cases"]
        assert pending_scope["input"]["test_cases"] == [case for case in child.action_state["generation"]["test_cases"]
                                                        if case["case_id"] in scope["review_batch"]["case_ids"]]
        old_identity = review.review_input_identity(scope, generation_inputs=initial["generation_inputs"],
            authoritative_facts=initial["effective_facts"]["authoritative_facts"])
        new_identity = review.review_input_identity(pending_scope["input"], generation_inputs=initial["generation_inputs"],
            authoritative_facts=initial["effective_facts"]["authoritative_facts"])
        assert old_identity != new_identity
        recheck = review.prepare_review_recheck(child, arguments)
        recheck_outputs = specialist_outputs(saved, recheck, "test_generation_final_reviewer",
            "testing.postprocess_final_review_batch_item", child)
        review.apply_review_recheck(child, recheck, recheck_outputs)
        state = deepcopy(context.action_state)
        state["review"]["state"] = deepcopy(child.action_state)
        generation._save(context, state)
        after = by_batch(context.action_state)
        assert {key: value for key, value in after.items() if key != batch_id} == pending
        assert not set(invalidated_ids) & set(context.action_state["problems"])
        assert not db.new and not db.dirty and not db.deleted
        historical_outputs = [json.loads(value["result"]["output"]) for value in saved["receipts"].values()
                              if value["status"] == "completed"]
        initial_payloads = [problem["payload"] for values in before.values() for problem in values.values()]
        history_has_problems = any(value.get("problems") for value in historical_outputs)
        assert history_has_problems and all(any(problem["reported_problem"] == payload
            for value in historical_outputs for problem in value.get("problems", [])) for payload in initial_payloads)
    engine.dispose()
    assert source_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result = {
        "status": "passed", "source_report": str(source_report.resolve()), "source_hashes": source_hashes,
        "source_real_model_requests": 92, "historical_problem_count": original_count, "current_problem_count": final_count,
        "projection_idempotent": True, "replayed_actual_repair_batch_id": batch_id,
        "revalidated_real_repair_candidates": len(repair_outputs), "revalidated_real_recheck_candidates": len(recheck_outputs),
        "unrelated_problem_ids_stable": True, "changed_scope_input_identity_invalidated": True,
        "pending_recheck_hides_old_problems_and_verdict": True, "old_problem_ids_rejected": True,
        "generation_14_maps_to_review": target, "inspect_reads_actual_repaired_candidate": True,
        "historical_problem_payloads_traceable_in_original_journal": True,
        "global_dependency_validation": "第二轮没有自然global_review，原global input/scope身份及stale_global分支未声称真实样本执行覆盖",
        "model_requests_sent": 0, "database_writes": 0,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("验证报告已存在，禁止覆盖")
    print(json.dumps(verify(args.source_report, args.output), ensure_ascii=False))
