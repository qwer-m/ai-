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
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.output_repair import OutputRepairError
from modules.agent_platform.runtime import (
    _agent_map_item_retry_feedback,
    _agent_map_repair_context,
    _restore_protected_repair_slots,
)
from modules.agent_platform.sdk_adapter import OutputPostprocessingError
from modules.agent_platform.test_generation_batching import postprocess_generation_batch_item
from modules.agent_platform.test_generation_review import _inline_repair_cases
from modules.agent_platform.test_generation_workflow import postprocess_planning_scope_routing_item


def repair_context(source: dict, candidate: dict, error: OutputRepairError) -> dict:
    wrapped = OutputPostprocessingError(output=candidate, postprocessor=error.strategy_key, cause=error)
    wrapped.__cause__ = error
    feedback = _agent_map_item_retry_feedback(previous_feedback=None, exc=wrapped, item_input=source)
    repair = _agent_map_repair_context(result=None, exc=wrapped, validation_feedback=feedback, item_input=source)
    assert repair is not None and repair["mode"] == "minimal_patch"
    assert repair["strategy_key"] == error.strategy_key
    return repair


def verify_planning(node: AgentNodeRun) -> dict:
    samples = []
    for record in node.output_payload["items"]:
        source = deepcopy(node.input_payload["items"][record["item_index"]])
        original = deepcopy(record["output"])
        assert postprocess_planning_scope_routing_item(None, {"item_input": source, "item_output": original}) == original
        samples.append((source, original, [record["item_index"]]))
    # 历史运行可能只产生单范围批次；重组同一目录下的真实记录验证双范围保护，明确记录来源。
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
        assert repair["protected_scope_ids"]
        response = deepcopy(original)
        for route in response["routes"]:
            if route["scope_id"] in repair["protected_scope_ids"]:
                route["assignments"] = []
        response["routes"].reverse()
        restored = _restore_protected_repair_slots(item_output=response, repair_context=repair)
        assert restored == original, "模型重排并改写非目标范围时必须恢复原值"
        response["routes"] = [route for route in response["routes"] if route["scope_id"] == target_id]
        assert _restore_protected_repair_slots(item_output=response, repair_context=repair) == original
        if len(original["routes"]) >= 2:
            candidate["routes"][1]["assignments"][0]["module_routes"] = []
            try:
                postprocess_planning_scope_routing_item(None, {"item_input": source, "item_output": candidate})
            except OutputRepairError as error:
                assert set(error.details["scope_ids"]) == {route["scope_id"] for route in original["routes"][:2]}
            else:
                raise AssertionError("多个范围缺口必须一起提交给修复策略")
        return {"source_item_indexes": source_items, "regrouped_real_batches": len(source_items) > 1, "target_scope_id": target_id, "protected_scope_count": len(repair["protected_scope_ids"])}
    raise AssertionError("当前真实运行没有可验证保护范围的多 scope 路由批次")


def verify_generation(node: AgentNodeRun) -> dict:
    for record in node.output_payload["items"]:
        source = deepcopy(node.input_payload["items"][record["item_index"]])
        normalized = record["output"]
        original = {"test_cases": _inline_repair_cases(test_cases=normalized["test_cases"], bindings=normalized["case_fact_bindings"])}
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
            if not repair.get("protected_collections"):
                continue
            response = deepcopy(original)
            protected_indexes = repair["protected_collections"][0]["protected_indexes"]
            for index in protected_indexes:
                response["test_cases"][index] = {}
            restored = _restore_protected_repair_slots(item_output=response, repair_context=repair)
            for index in protected_indexes:
                assert restored["test_cases"][index] == candidate["test_cases"][index]
            postprocess_generation_batch_item(None, {"item_input": source, "item_output": restored})
            return {"item_index": record["item_index"], "missing_fact_id": fact_id, "protected_case_count": len(protected_indexes)}
    raise AssertionError("当前真实运行没有可独立注入覆盖缺口的多用例批次")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
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
        report = {"run_id": run.id, "planning": verify_planning(nodes["plan_routes"]), "generation": verify_generation(nodes["generation"])}
        for key, node in nodes.items():
            db.refresh(node)
            assert (node.input_payload, node.output_payload) == snapshots[key]
        report["database_unchanged"] = True
        db.rollback()
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
