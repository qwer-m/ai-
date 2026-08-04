from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentToolDefinition
from modules.agent_platform.contracts import WorkflowGraph, WorkflowNode
from modules.agent_platform.registry import (
    BUILTIN_AGENT_SPECS,
    BUILTIN_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS,
    ToolExecutionContext,
)
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.sdk_adapter import _function_tool


def test_builtin_workflow_is_data_driven_and_references_registered_definitions() -> None:
    agent_keys = {str(item["agent_key"]) for item in BUILTIN_AGENT_SPECS}
    tool_keys = {str(item["tool_key"]) for item in BUILTIN_TOOL_SPECS}

    for spec in BUILTIN_WORKFLOW_SPECS:
        graph = WorkflowGraph.model_validate(spec["definition"])
        assert graph.execution_order()
        for node in graph.nodes:
            allowed = agent_keys if node.node_type == "agent" else tool_keys
            assert node.reference_key in allowed

    graph = WorkflowGraph.model_validate(BUILTIN_WORKFLOW_SPECS[0]["definition"])
    assert [node.node_key for node in graph.execution_order()] == [
        "evidence",
        "plan",
        "generate",
        "ground",
        "validate",
        "persist",
    ]
    ground = next(node for node in graph.nodes if node.node_key == "ground")
    assert ground.input_mapping == {
        "requirement": "dependencies.evidence.requirement",
        "draft_test_cases": "dependencies.generate.test_cases",
        "case_budget": "input.case_budget",
    }
    run = AgentRun(
        id=19,
        project_id=66,
        user_id=1,
        workflow_definition_id=1,
        input_payload={
            "requirement": "冲突的直接输入",
            "requirement_doc_id": 70,
            "case_budget": 3,
        },
    )
    ground_input = _node_input(
        run,
        ground,
        {
            "evidence": {
                "requirement": "文档中的真实需求",
                "source": {"document_id": 70},
                "linked_examples": [{"content": "不应进入事实审查"}],
            },
            "generate": {"test_cases": [{"case_id": "TC-001"}]},
            "plan": {"roles": ["不应进入事实审查"]},
        },
    )
    assert ground_input == {
        "requirement": "文档中的真实需求",
        "draft_test_cases": [{"case_id": "TC-001"}],
        "case_budget": 3,
    }


def test_workflow_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="循环依赖"):
        WorkflowGraph.model_validate(
            {
                "nodes": [
                    {
                        "node_key": "a",
                        "node_type": "agent",
                        "reference_key": "agent_a",
                        "depends_on": ["b"],
                    },
                    {
                        "node_key": "b",
                        "node_type": "agent",
                        "reference_key": "agent_b",
                        "depends_on": ["a"],
                    },
                ],
                "output_node_key": "b",
            }
        )


def test_node_input_mapping_reads_run_and_dependency_data() -> None:
    run = AgentRun(
        id=18,
        project_id=66,
        user_id=1,
        workflow_definition_id=1,
        input_payload={"requirement": "真实需求"},
    )
    node = WorkflowNode(
        node_key="review",
        node_type="tool",
        reference_key="review_cases",
        depends_on=["generate"],
        input_mapping={
            "requirement": "input.requirement",
            "cases": "dependencies.generate.cases",
            "project_id": "run.project_id",
        },
    )

    value = _node_input(run, node, {"generate": {"cases": [{"title": "登录"}]}})

    assert value == {
        "requirement": "真实需求",
        "cases": [{"title": "登录"}],
        "project_id": 66,
    }


def test_builtin_tool_can_be_constructed_as_strict_sdk_function_tool() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "validate_test_cases"
    )
    definition = AgentToolDefinition(
        project_id=66,
        user_id=1,
        tool_key=spec["tool_key"],
        name=spec["name"],
        description=spec["description"],
        handler_key=spec["handler_key"],
        input_schema=spec["input_schema"],
        output_schema=spec["output_schema"],
    )
    db = SessionLocal()
    try:
        context = ToolExecutionContext(
            db=db,
            user_id=1,
            project_id=66,
            run_id=1,
            node_key="validate",
            run_input={},
        )

        tool = _function_tool(definition, context)

        assert tool.name == "validate_test_cases"
        assert set(tool.params_json_schema["required"]) == {
            "requirement",
            "case_budget",
            "test_cases",
        }
    finally:
        db.close()
