"""用真实成功规划和失败候选验证同会话反馈边界；不调用模型、不修改线上库。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.tool_context import ToolContext
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract, _function_tool
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_planning import validate_business_plan_output
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def validate(context, source, candidate):
    return validate_business_plan_output(context, {"input_payload": source, "output": candidate})


def assert_locations(candidate: dict, details: dict) -> None:
    for group_id, positions in details["group_locations"].items():
        for position in positions:
            module = candidate["business_modules"][position["module_index"]]
            point = module["test_points"][position["point_index"]]
            design = point["test_designs"][position["design_index"]]
            assert design["coverage_items"][position["coverage_item_index"]] == group_id
            assert module["name"] == position["module_name"] and point["name"] == position["point_name"]
            assert design["technique"] == position["technique"]
            assert position["path"].endswith(f'.coverage_items[{position["coverage_item_index"]}]')


async def invoke_actual_tool(context, source, candidate) -> tuple[dict, AutonomousToolLoop]:
    definition = builtin_definition(AgentDefinition, next(item for item in BUILTIN_AGENT_SPECS
                                    if item["agent_key"] == "test_business_planner"),
                                    project_id=context.project_id, user_id=context.user_id)
    tool_definition = builtin_definition(AgentToolDefinition, next(item for item in BUILTIN_TOOL_SPECS
                                         if item["tool_key"] == "submit_business_plan"),
                                         project_id=context.project_id, user_id=context.user_id)
    contract = AgentOutputContract(deepcopy(source))
    input_schema_before = deepcopy(tool_definition.input_schema)
    output_schema_before = deepcopy(definition.output_schema)
    loop = AutonomousToolLoop(terminal_tool_names={"submit_business_plan"},
                              validate_submission=partial(contract.validate, definition, context),
                              recoverable_errors=(OutputValidationError,), max_tool_calls=6)
    tool = loop.wrap_tool(_function_tool(tool_definition, context), allow_feedback=True)
    assert tool_definition.input_schema == input_schema_before
    assert definition.output_schema == output_schema_before
    arguments = json.dumps(candidate, ensure_ascii=False)
    try:
        result = await tool.on_invoke_tool(ToolContext(context=context, tool_name=tool.name,
                                                     tool_call_id=uuid4().hex, tool_arguments=arguments), arguments)
    except Exception as error:
        if isinstance(error.__cause__, ValueError) and not isinstance(error.__cause__, OutputValidationError):
            assert loop.feedback_for_error(error) is None
        raise
    finally:
        assert tool_definition.input_schema == input_schema_before
        assert definition.output_schema == output_schema_before
    return json.loads(result), loop


async def verify(success_run_id: int, failed_run_id: int) -> dict:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        good = repo.latest_node_run(run_id=success_run_id, node_key="plan")
        bad_nodes = db.query(AgentNodeRun).filter(AgentNodeRun.run_id == failed_run_id,
                                                AgentNodeRun.node_key == "plan").order_by(AgentNodeRun.id).all()
        assert good is not None and good.status == "success" and len(bad_nodes) == 2
        tracked = [good, *bad_nodes]
        before = {node.id: action_state_hash([node.input_payload, node.output_payload, node.sdk_state]) for node in tracked}
        run = db.get(AgentRun, success_run_id)
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                       run_id=run.id, node_key="plan", run_input=deepcopy(run.input_payload))
        good_source = repo.read_node_input(good)
        good_candidate = next(call["arguments"] for call in good.sdk_state["tool_calls"]
                              if call["tool_key"] == "submit_business_plan")
        source_copy, candidate_copy = deepcopy(good_source), deepcopy(good_candidate)
        normalized = validate(context, good_source, good_candidate)
        assert normalized == good.output_payload
        _, loop = await invoke_actual_tool(context, good_source, good_candidate)
        assert loop.events[-1]["status"] == "accepted"
        assert loop.require_submission().normalized_output == good.output_payload
        assert good_source == source_copy and good_candidate == candidate_copy
        bad_reports = []
        for node in bad_nodes:
            source = repo.read_node_input(node)
            candidate = deepcopy(node.sdk_state["repair_context"]["candidate_output"])
            unchanged = deepcopy(candidate)
            try:
                validate(context, source, candidate)
            except OutputValidationError as error:
                details = deepcopy(error.details)
            else:
                raise AssertionError("真实失败候选必须被拒绝")
            assert details["reason"] == "coverage_group_assignment"
            assert details["original_group_ids"] == [item["coverage_group_id"] for item in source["coverage_group_catalog"]]
            assert details["duplicate_group_ids"]
            assert_locations(candidate, details)
            feedback, rejected_loop = await invoke_actual_tool(context, source, candidate)
            assert rejected_loop.events[-1]["status"] == feedback["status"] == "rejected"
            assert feedback["feedback"]["details"] == details and candidate == unchanged
            bad_reports.append({"node_run_id": node.id, "attempt": node.attempt, "error_type": "OutputValidationError",
                                "actual_function_tool_feedback": True, "details": details})

        # 负向契约只修改真实对象副本；不伪造模型正确答案或替换处理函数。
        candidate_checks = []
        for mode in ("missing", "unknown"):
            changed = deepcopy(good_candidate)
            ids = changed["business_modules"][0]["test_points"][0]["test_designs"][0]["coverage_items"]
            removed_id = ids.pop(0)
            if mode == "unknown":
                ids.append(good_source["coverage_group_catalog"][0]["name"])
            try:
                validate(context, good_source, changed)
            except OutputValidationError as error:
                assert removed_id in error.details["missing_group_ids"]
                assert bool(error.details["unknown_group_ids"]) == (mode == "unknown")
                assert_locations(changed, error.details)
                candidate_checks.append(mode)
            else:
                raise AssertionError("损坏真实候选副本必须被拒绝")

        # 容量回归仍使用真实成功规划，只在输入副本把一项容量收紧到实际值以下。
        modules = normalized["business_modules"]
        points = [point for module in modules for point in module["test_points"]]
        designs = [design for point in points for design in point["test_designs"]]
        actual_counts = {"max_business_modules": len(modules), "max_test_points": len(points),
                         "max_test_designs": len(designs),
                         "max_coverage_items": sum(len(design["coverage_items"]) for design in designs)}
        capacity_checks = []
        for field, count in actual_counts.items():
            assert count > 1
            limited_source = deepcopy(good_source)
            limited_source["planning_limits"][field] = count - 1
            try:
                validate(context, limited_source, good_candidate)
            except OutputValidationError as error:
                assert error.details["actual_counts"] == actual_counts
                assert error.details["planning_limits"] == limited_source["planning_limits"]
                assert good_candidate == candidate_copy
                capacity_checks.append(field)
            else:
                raise AssertionError("真实规划超出容量必须拒绝，不能截断覆盖项")

        corrupt_sources = {}
        missing_catalog = deepcopy(good_source)
        missing_catalog.pop("coverage_group_catalog")
        corrupt_sources["missing_catalog"] = missing_catalog
        duplicate_catalog = deepcopy(good_source)
        duplicate_catalog["coverage_group_catalog"].append(deepcopy(duplicate_catalog["coverage_group_catalog"][0]))
        corrupt_sources["duplicate_catalog_id"] = duplicate_catalog
        broken_draft = deepcopy(good_source)
        broken_draft["partial_plans"][0]["draft"] = []
        corrupt_sources["invalid_partial_plan"] = broken_draft
        broken_limits = deepcopy(good_source)
        broken_limits["planning_limits"]["max_test_designs"] = 0
        corrupt_sources["invalid_limits"] = broken_limits
        broken_metadata = deepcopy(good_source)
        broken_metadata["planning_metadata"]["coverage_focus"] = {}
        corrupt_sources["invalid_metadata"] = broken_metadata
        broken_binding = deepcopy(good_source)
        broken_binding["partial_plans"][0]["draft"]["module_candidates"][0]["coverage_topics"] = []
        corrupt_sources["source_group_binding_missing"] = broken_binding
        for source in corrupt_sources.values():
            try:
                validate(context, source, bad_nodes[0].sdk_state["repair_context"]["candidate_output"])
            except ValueError as error:
                assert not isinstance(error, OutputValidationError)
            else:
                raise AssertionError("来源损坏必须先于候选校验硬失败")
        try:
            await invoke_actual_tool(context, missing_catalog, good_candidate)
        except Exception as error:
            assert type(error).__name__ == "OutputPostprocessingError"
            assert isinstance(error.__cause__, ValueError) and not isinstance(error.__cause__, OutputValidationError)
        else:
            raise AssertionError("实际 SDK 工具不能吞掉来源损坏")
        for node in tracked:
            db.refresh(node)
            assert action_state_hash([node.input_payload, node.output_payload, node.sdk_state]) == before[node.id]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        return {"status": "passed", "success_run_id": success_run_id, "success_node_run_id": good.id,
                "historical_normalized_output_identical": True, "historical_output_sha256": action_state_hash(normalized),
                "actual_success_function_tool_accepted": True, "failed_run_id": failed_run_id,
                "actual_failed_candidates": bad_reports, "candidate_copy_checks": candidate_checks,
                "capacity_copy_checks": capacity_checks,
                "source_corruption_hard_failures": list(corrupt_sources), "actual_tool_source_error_not_feedback": True,
                "actual_tool_schema_unchanged": True,
                "inputs_and_candidates_unchanged": True, "automatic_deduplication_or_reassignment": False,
                "model_requests": 0, "online_database_writes": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--success-run-id", type=int, default=28)
    parser.add_argument("--failed-run-id", type=int, default=29)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    report = asyncio.run(verify(options.success_run_id, options.failed_run_id))
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "actual_failed_candidates"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
