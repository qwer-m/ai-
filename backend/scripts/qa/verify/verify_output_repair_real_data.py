"""只读回放真实运行的规划与生成数据，对副本注入缺口验证结构化修复策略。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputRepairError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import (
    _agent_map_item_retry_feedback,
    _agent_map_repair_context,
)
from modules.agent_platform.sdk_adapter import OutputPostprocessingError, postprocess_agent_map_output
from modules.agent_platform.test_generation_batching import postprocess_generation_batch_item
from modules.agent_platform.test_generation_review import _inline_repair_cases
from modules.agent_platform.test_generation_planning import (
    postprocess_planning_scope_routing_item,
)
from scripts.qa.verify.generation_contract_history import project_historical_generation_inputs


def repair_context(source: dict, candidate: dict, error: OutputRepairError) -> dict:
    wrapped = OutputPostprocessingError(output=candidate, postprocessor=error.strategy_key, cause=error)
    wrapped.__cause__ = error
    feedback = _agent_map_item_retry_feedback(previous_feedback=None, exc=wrapped, item_input=source)
    repair = _agent_map_repair_context(result=None, exc=wrapped, validation_feedback=feedback, item_input=source)
    assert repair is not None and repair["mode"] == "minimal_patch"
    assert repair["strategy_key"] == error.strategy_key
    return repair


def runtime_validator(db, run: AgentRun, node: AgentNodeRun, *, item_postprocessor: str):
    """读取真实 Agent 定义，经当前领域后处理入口回放；不恢复已退役工作流。"""

    definition = db.get(AgentDefinition, node.agent_definition_id)
    if definition is None:
        raise ValueError("真实运行缺少 Agent 定义")
    config = AgentMapConfig(item_postprocessor=item_postprocessor)
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=node.node_key, run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context.get("artifacts") or {}),
    )

    def validate(source: dict, output: dict) -> dict:
        return postprocess_agent_map_output(
            config=config, definition=definition, execution_context=context,
            item_input=source, item_output=output,
        )

    return validate


def assert_runtime_rejects(validate, source: dict, output: dict) -> None:
    try:
        validate(source, output)
    except OutputPostprocessingError:
        return
    raise AssertionError("修补目标之外的新错误必须由完整后处理拒绝，不能回填旧候选掩盖错误")


def verify_planning(node: AgentNodeRun, validate, node_input: dict) -> dict:
    samples = []
    for record in node.output_payload["items"]:
        source = deepcopy(node_input["items"][record["item_index"]])
        original = deepcopy(record["output"])
        assert postprocess_planning_scope_routing_item(None, {"item_input": source, "item_output": original}) == original
        samples.append((source, original, [record["item_index"]]))
    # 历史运行可能只产生单范围批次；重组同一目录下的真实记录验证完整范围校验，明确记录来源。
    if not any(len(output["routes"]) >= 2 for _, output, _ in samples):
        for left_index, (left_input, left_output, left_items) in enumerate(samples):
            for right_input, right_output, right_items in samples[left_index + 1:]:
                if left_input["business_modules"] == right_input["business_modules"]:
                    samples.append((
                        {"business_modules": deepcopy(left_input["business_modules"]), "scopes": deepcopy(left_input["scopes"] + right_input["scopes"])},
                        {"routes": deepcopy(left_output["routes"] + right_output["routes"])},
                        left_items + right_items,
                    ))
                    break
            if len(samples[-1][1]["routes"]) >= 2:
                break
    for source, original, source_items in samples:
        if len(original["routes"]) < 2:
            continue
        assert postprocess_planning_scope_routing_item(None, {"item_input": source, "item_output": original}) == original
        candidate = deepcopy(original)
        target_id = candidate["routes"][0]["scope_id"]
        candidate["routes"][0]["assignments"][0]["module_routes"] = []
        try:
            postprocess_planning_scope_routing_item(None, {"item_input": source, "item_output": candidate})
        except OutputRepairError as error:
            assert error.details["scope_ids"] == [target_id]
            repair = repair_context(source, candidate, error)
        else:
            raise AssertionError("真实路由副本缺失模块映射却通过校验")
        assert not any(key.startswith("protected_") for key in repair)
        assert repair["route_repair_targets"] == [{"scope_id": target_id}]
        response = deepcopy(original)
        response["routes"].reverse()
        assert validate(source, response) == original
        other_scope_id = original["routes"][1]["scope_id"]
        for route in response["routes"]:
            if route["scope_id"] == other_scope_id:
                route["assignments"] = []
        assert_runtime_rejects(validate, source, response)
        omitted = {"routes": [deepcopy(original["routes"][0])]}
        assert_runtime_rejects(validate, source, omitted)
        if len(original["routes"]) >= 2:
            candidate["routes"][1]["assignments"][0]["module_routes"] = []
            try:
                postprocess_planning_scope_routing_item(None, {"item_input": source, "item_output": candidate})
            except OutputRepairError as error:
                assert set(error.details["scope_ids"]) == {route["scope_id"] for route in original["routes"][:2]}
                all_targets = repair_context(source, candidate, error)
                assert len(all_targets["route_repair_targets"]) == 2
            else:
                raise AssertionError("多个范围缺口必须一起提交给修复策略")
        return {
            "node_run_id": node.id, "source_item_indexes": source_items,
            "regrouped_real_batches": len(source_items) > 1, "target_scope_id": target_id,
            "non_target_scope_id": other_scope_id, "non_target_errors_rejected": True,
            "missing_scope_rejected": True, "all_scope_errors_targeted": True,
        }
    raise AssertionError("当前真实运行没有可验证完整范围校验的多 scope 路由批次")


def verify_generation(node: AgentNodeRun, validate, node_input: dict) -> dict:
    current_inputs, projection = project_historical_generation_inputs(node_input["items"])
    for record in node.output_payload["items"]:
        source = deepcopy(current_inputs[record["item_index"]])
        normalized = record["output"]
        original = {"test_cases": _inline_repair_cases(test_cases=normalized["test_cases"], bindings=normalized["case_fact_bindings"])}
        case_ids = source["case_fact_contract"]["target_case_ids"]
        if len(case_ids) < 2:
            continue
        assert postprocess_generation_batch_item(None, {"item_input": source, "item_output": original}) == normalized
        for fact_id in source["case_fact_contract"]["required_fact_ids"]:
            candidate = deepcopy(original)
            required_groups = []
            optional_groups = []
            for case in candidate["test_cases"]:
                required_groups.extend(item["fact_ids"] for item in case["preconditions"])
                required_groups.extend(step["fact_bindings"]["expected"] for step in case["steps"])
                optional_groups.extend(step["fact_bindings"]["action"] for step in case["steps"])
                optional_groups.append(case["test_input"]["fact_ids"])
            if any(group == [fact_id] for group in required_groups):
                continue
            for group in [*required_groups, *optional_groups]:
                group[:] = [value for value in group if value != fact_id]
            for case in candidate["test_cases"]:
                if not case["test_input"]["fact_ids"]:
                    case["test_input"]["text"] = ""
            try:
                postprocess_generation_batch_item(None, {"item_input": source, "item_output": candidate})
            except OutputRepairError as error:
                assert error.details["missing_fact_ids"] == [fact_id]
                repair = repair_context(source, candidate, error)
            else:
                raise AssertionError("真实用例副本缺失事实引用却通过校验")
            assert repair["repair_targets"] == [], "全批缺失事实不得按退役槽位强派给某条用例"
            assert repair["batch_coverage_gaps"]["missing_fact_ids"] == [fact_id]
            assert set(repair["candidate_fact_ids_by_case"]) == set(case_ids)
            assert all(fact_id not in values for values in repair["candidate_fact_ids_by_case"].values())
            assert not any(key.startswith("protected_") for key in repair)
            # 缺口没有预定承载用例；用另一个真实用例标题改坏副本，检验完整新候选仍能纠正旧内容。
            target_index, other_index = next(
                (left, right)
                for left, first in enumerate(original["test_cases"])
                for right, second in enumerate(original["test_cases"])
                if left != right and first["title"] != second["title"]
            )
            stale_candidate = repair["candidate_output"]
            stale_candidate["test_cases"][other_index]["title"] = original["test_cases"][target_index]["title"]
            assert stale_candidate["test_cases"][other_index]["title"] != original["test_cases"][other_index]["title"]
            response = deepcopy(original)
            checked = validate(source, response)
            assert checked == normalized, "新结果必须完整采用并校验，不能让未验证旧标题覆盖本次纠正"
            response["test_cases"][other_index] = {}
            assert_runtime_rejects(validate, source, response)
            return {
                "node_run_id": node.id, "item_index": record["item_index"], "missing_fact_id": fact_id,
                "target_case_ids": case_ids,
                "repair_targets": repair["repair_targets"],
                "batch_coverage_gaps": repair["batch_coverage_gaps"],
                "candidate_fact_ids_by_case": repair["candidate_fact_ids_by_case"],
                "edited_candidate_case_id": case_ids[other_index],
                "full_candidate_title_correction_preserved": True,
                "other_case_errors_rejected": True,
                "historical_contract_projection": projection,
            }
    raise AssertionError("当前真实运行没有可独立注入覆盖缺口的多用例批次")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("需要真实成功的运行编号")
        node_rows = db.query(AgentNodeRun).filter(
            AgentNodeRun.run_id == run.id,
            AgentNodeRun.node_key.in_(["plan_routes", "generation"]),
            AgentNodeRun.status == "success",
        ).all()
        nodes = {node.node_key: node for node in sorted(node_rows, key=lambda node: (node.attempt, node.id))}
        snapshots = {key: (deepcopy(node.input_payload), deepcopy(node.output_payload)) for key, node in nodes.items()}
        report = {
            "run_id": run.id,
            "validation_scope": "current_postprocessor_replay_not_retired_workflow_replay",
            "planning": verify_planning(nodes["plan_routes"], runtime_validator(
                db, run, nodes["plan_routes"],
                item_postprocessor="testing.postprocess_planning_scope_routing_item",
            ), AgentRunRepository(db).read_node_input(nodes["plan_routes"])),
            "generation": verify_generation(nodes["generation"], runtime_validator(
                db, run, nodes["generation"],
                item_postprocessor="testing.postprocess_generation_batch_item",
            ), AgentRunRepository(db).read_node_input(nodes["generation"])),
        }
        for key, node in nodes.items():
            db.refresh(node)
            assert (node.input_payload, node.output_payload) == snapshots[key]
        report["database_unchanged"] = True
        report["model_requests_sent"] = 0
        db.rollback()
        if options.output:
            options.output.parent.mkdir(parents=True, exist_ok=True)
            options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
