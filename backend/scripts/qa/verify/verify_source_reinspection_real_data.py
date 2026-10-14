"""只读真实检查点与来源候选，验证生成后回溯入口和来源版本的失效边界。"""

from __future__ import annotations

import argparse
import ast
import asyncio
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract, _managed_agent_call_identity
from scripts.qa.verify.verify_generation_action_availability_real_data import context_for, source_bundle
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine
from scripts.qa.verify.verify_state_snapshot_filter_real_data import definition_for


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def availability_guard(state, source_items):
    """旧检查点版本不能完整恢复；只执行真实生产来源入口谓词，不填造新版观察字段。"""
    tree = ast.parse(inspect.getsource(generation._available_actions))
    source_guard = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                        and any(isinstance(value, ast.Assign)
                                and any(isinstance(target, ast.Subscript)
                                        and isinstance(target.slice, ast.Constant)
                                        and target.slice.value == "reinspect_planning_source"
                                        for target in value.targets)
                                for value in node.body))
    expression = ast.Expression(body=source_guard.test)
    return bool(eval(compile(ast.fix_missing_locations(expression), "<真实来源入口谓词>", "eval"),
                     {}, {"state": state, "plan": {"source_items": source_items}}))


def verify_generated_history(source_path: Path):
    bundle = source_bundle(source_path)
    checkpoint = bundle["checkpoint"]
    assert checkpoint["state_hash"] == action_state_hash(checkpoint["state"])
    engine = sqlite_engine(bundle["paths"][1])
    try:
        with Session(engine) as db:
            context, _, node = context_for(db, bundle)
            state = context.action_state
            assert state["generation_requests"] and any(state["generation_records"])
            assert not state["completed"] and not state["planning"]["completed"]
            try:
                generation.observe_generation_state(context)
            except ValueError as error:
                rejection = str(error)
                assert "版本不受支持" in rejection
            else:
                raise AssertionError("旧规划版本不能被静默恢复成当前状态")
            source_items = planning._source_index(context)
            assert source_items and availability_guard(state, source_items)
            # 在真实持久生成后状态上比较旧谓词；不改历史状态或添加问题。
            assert not (source_items and not state["generation_requests"])
            original_requests = deepcopy(state["generation_requests"])
            original_caches = {key: deepcopy(state[key]) for key in
                               ("authority_cache", "generation_cache", "review_cache")}
            invalidated = deepcopy(state)
            generation._invalidate_downstream(invalidated)
            assert invalidated["generation_requests"] == original_requests
            assert all(invalidated[key] == value for key, value in original_caches.items())
            assert all(invalidated[key] is None for key in
                       ("accepted_plan", "effective_facts", "generation", "review"))
            assert all(invalidated[key] == [] for key in
                       ("authority_inputs", "authority_records", "generation_inputs", "generation_records",
                        "review_inputs", "review_records"))
            # 当前旧输入仍可精确找到其候选；失效不会删除历史成功产物或已记账请求。
            retained = generation._retain(state["generation_inputs"], state["generation_cache"])
            for index, record in enumerate(state["generation_records"]):
                if record is not None:
                    assert retained[index] == record
            assert node.sdk_state["action_checkpoint"] == checkpoint
            assert not db.new and not db.dirty and not db.deleted
            result = {
                "source_report": str(source_path.resolve()), "source_file_hashes": bundle["hashes"],
                "source_run_id": bundle["report"]["source_run_id"],
                "checkpoint_state_sha256": checkpoint["state_hash"],
                "historical_planning_schema_version": state["planning"]["schema_version"],
                "current_planning_schema_version": planning._STATE_VERSION,
                "generation_requests": len(original_requests),
                "generated_records": sum(item is not None for item in state["generation_records"]),
                "persisted_real_problems": len(state["problems"]),
                "old_source_guard_enabled": False, "current_source_guard_enabled": True,
                "full_checkpoint_restore_rejection": rejection,
                "request_history_and_success_caches_preserved_on_invalidation": True,
                "persisted_budget_sha256": action_state_hash(checkpoint["budget"]),
                "scope": "真实旧生成状态的生产入口谓词及下游失效函数回放，不是新版完整恢复",
            }
    finally:
        engine.dispose()
    assert all(file_hash(path) == bundle["hashes"][str(path.resolve())] for path in bundle["paths"])
    return result


async def verify_current_source_apply(support_path: Path, candidate_path: Path):
    support = json.loads(support_path.read_text("utf-8"))
    candidate_report = json.loads(candidate_path.read_text("utf-8"))
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, support["source_run_id"])
            node = db.get(AgentNodeRun, support["source_node_run_id"])
            raw_input = AgentRunRepository(db).read_node_input(node)
            persisted_before = action_state_hash([run.run_context, run.output_payload, run.status, node.sdk_state])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(run.run_context["artifacts"]))
            generation.initialize_generation_state(context, deepcopy(raw_input))
            # 此状态由真实 22 模块支持审查产生，沿用其原始输入及来源，不拼装生成问题。
            context.action_state["planning"] = deepcopy(support["planning_state"])
            generation._save(context, context.action_state)
            initial = deepcopy(context.action_state)
            observation = generation.observe_generation_state(context)
            assert "reinspect_planning_source" in observation["available_actions"]
            saved_fragment = candidate_report["fragments"][-1]
            saved_inputs = json.loads(Path(candidate_report["input_path"]).read_text("utf-8"))
            original_request = next(item["input"] for item in saved_inputs["source_fragments"]
                                    if item["source_item_index"] == saved_fragment["source_item_index"])
            # 以真实正文及块坐标匹配旧 Run 的分片；新增表格上下文使全局索引变化。
            def body_identity(item):
                pages = item.get("pages", [item])
                return [(page["document_id"], page["page_number"], page["page_text"], page["blocks"]) for page in pages]
            indexes = [index for index, item in enumerate(raw_input["source_text_inputs"])
                       if body_identity(item) == body_identity(original_request)]
            assert len(indexes) == 1
            index = indexes[0]
            arguments = {"source_kind": "text", "item_index": index,
                "question": "复核真实表格审查中已记录的列对应及完整条件，使用已保存的同源真实候选。"}
            prepared = planning.prepare_source_reinspection(generation._planning_context(context, initial), arguments)
            old_output = deepcopy(initial["planning"]["source_records"]["text"][index]["output"])

            def unchanged(child, args):
                action = planning.prepare_source_reinspection(child, args)
                return planning.apply_source_reinspection(child, action, [deepcopy(old_output)])

            unchanged_result = await generation._planning_action(
                context, arguments, "reinspect_planning_source", unchanged)
            assert unchanged_result["changed"] is False and unchanged_result["downstream_invalidated"] is False
            assert context.action_state == initial
            contract = AgentOutputContract(prepared["items"][0],
                AgentMapConfig(item_postprocessor="testing.postprocess_source_semantics_item"))
            semantic_hash = node.sdk_state["action_checkpoint"]["execution_semantic_hash"]
            identity_before = _managed_agent_call_identity(execution_semantic_hash=semantic_hash,
                agent_key="test_source_text_semantics_analyst", contract=contract)
            same_prepared = planning.prepare_source_reinspection(generation._planning_context(context, initial), arguments)
            assert _managed_agent_call_identity(execution_semantic_hash=semantic_hash,
                agent_key="test_source_text_semantics_analyst",
                contract=AgentOutputContract(same_prepared["items"][0], contract.map_config)) == identity_before
            normalized = []

            def changed(child, args):
                action = planning.prepare_source_reinspection(child, args)
                current_contract = AgentOutputContract(action["items"][0], contract.map_config)
                output = current_contract.validate(definition_for("test_source_text_semantics_analyst"),
                    child, deepcopy(saved_fragment["candidate"]))
                normalized.append(deepcopy(output))
                return planning.apply_source_reinspection(child, action, [output])

            changed_result = await generation._planning_action(
                context, arguments, "reinspect_planning_source", changed)
            updated = context.action_state
            assert changed_result["changed"] and changed_result["downstream_invalidated"]
            assert updated["planning"]["source_version"] == initial["planning"]["source_version"] + 1
            assert updated["generation_requests"] == initial["generation_requests"]
            assert updated["planning"]["plan"] is None
            assert len(updated["revision_history"]) == len(initial["revision_history"]) + 1
            old_inputs = initial["planning"]["planning_inputs"]
            new_inputs = updated["planning"]["planning_inputs"]
            old_hashes = {action_state_hash(item) for item in old_inputs}
            changed_indexes = []
            for current_index, item in enumerate(new_inputs):
                if action_state_hash(item) not in old_hashes:
                    changed_indexes.append(current_index)
                    assert updated["planning"]["planning_records"][current_index] is None
            assert changed_indexes
            expected_facts = {fact["fact_id"]: fact for fact in normalized[0]["authoritative_facts"]}
            merged_facts = {fact["fact_id"]: fact for fact in updated["planning"]["source_semantics"]["authoritative_facts"]}
            assert all(merged_facts[key] == value for key, value in expected_facts.items())
            again = planning.prepare_source_reinspection(generation._planning_context(context, updated), arguments)
            identity_after = _managed_agent_call_identity(execution_semantic_hash=semantic_hash,
                agent_key="test_source_text_semantics_analyst",
                contract=AgentOutputContract(again["items"][0], contract.map_config))
            assert identity_after != identity_before
            before_repeat = deepcopy(updated)
            repeat_result = await generation._planning_action(
                context, arguments, "reinspect_planning_source", changed)
            assert repeat_result["changed"] is False and not repeat_result["downstream_invalidated"]
            assert context.action_state == before_repeat
            assert persisted_before == action_state_hash([run.run_context, run.output_payload, run.status, node.sdk_state])
            assert not db.new and not db.dirty and not db.deleted
            return {
                "source_run_id": run.id, "source_node_run_id": node.id,
                "support_report": str(support_path.resolve()), "candidate_report": str(candidate_path.resolve()),
                "source_item_index": index, "candidate_original_source_item_index": saved_fragment["source_item_index"],
                "same_real_body_and_coordinates": True, "unchanged_action_result": unchanged_result,
                "changed_action_result": changed_result, "repeated_action_result": repeat_result,
                "changed_planning_input_indexes": changed_indexes,
                "unchanged_source_preserves_entire_state": True,
                "changed_source_invalidates_changed_planning_drafts": True,
                "updated_fact_ids": list(expected_facts), "canonical_facts_equal_revalidated_candidate": True,
                "same_source_request_identity_equal": True, "changed_previous_output_changes_request_identity": True,
                "initial_child_request_identity": identity_before, "updated_child_request_identity": identity_after,
                "historical_run_has_table_contexts": any("source_context" in fact for fact in merged_facts.values()),
                "source_context_flow_evidence": "backend/artifacts/verification/run39_source_context_flow_real_data.json",
                "source_run_and_checkpoint_unchanged": True,
            }
        finally:
            db.rollback()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-report", type=Path, required=True)
    parser.add_argument("--support-report", type=Path, required=True)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("拒绝覆盖真实回放报告")
    production = Path(generation.__file__)
    before = file_hash(production)
    report = {"status": "passed", "model_requests_sent": 0, "database_writes": 0,
        "generated_history": verify_generated_history(args.history_report),
        "current_source_apply": await verify_current_source_apply(args.support_report, args.candidate_report),
        "verification_limits": [
            "Run38 的真实生成后检查点是旧规划版本，只验证原样来源入口谓词和失效函数，未绕过版本守卫完整恢复。",
            "完整 prepare/postprocess/apply/父状态失效链使用 Run39 当前契约重建的真实支持状态，尚未生成。",
            "重放候选来自新增表格上下文后的同一真实正文；旧 Run39 本身无 table_contexts，未改写持久来源注入新上下文。",
            "真实候选没有新的 status 修订，未制造 uncertain 候选；context 流通另由真实20事实投影报告覆盖。",
            "本次未联网；重复请求仅核对生产身份一致，SDK 持久回执命中由已有 managed receipts QA 覆盖。",
        ]}
    assert before == file_hash(production), "验证期间生产代码变化"
    report["production_sha256"] = before
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "model_requests_sent": 0, "database_writes": 0,
                      "output": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
