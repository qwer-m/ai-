from __future__ import annotations

from core.db.database import SessionLocal
from modules.agent_platform.automation_evaluation_workflow import (
    BUILTIN_WORKFLOW_SPECS,
    persist_automation_evaluation,
)
from modules.agent_platform.contracts import WorkflowGraph
from modules.agent_platform.registry import ToolExecutionContext


def test_automation_evaluation_workflows_have_agent_and_artifact_nodes() -> None:
    assert {spec["workflow_key"] for spec in BUILTIN_WORKFLOW_SPECS} == {
        "ui_automation_evaluation",
        "api_automation_evaluation",
    }
    for spec in BUILTIN_WORKFLOW_SPECS:
        graph = WorkflowGraph.model_validate(spec["definition"])
        assert [node.node_key for node in graph.execution_order()] == [
            "evaluate",
            "persist",
        ]
        assert graph.output_node_key == "persist"


def test_automation_evaluation_persists_structured_run_artifact() -> None:
    db = SessionLocal()
    context = ToolExecutionContext(
        db=db,
        user_id=1,
        project_id=7,
        run_id=21,
        node_key="persist",
        run_input={"source_execution_id": 42},
    )
    evaluation = {
        "summary": "真实执行成功，核心断言有效。",
        "overall_score": 8.5,
        "execution_status": "success",
        "criteria": [
            {"key": key, "name": name, "score": score, "analysis": analysis}
            for key, name, score, analysis in (
                ("structure", "脚本结构", 9, "测试职责划分清晰。"),
                ("assertions", "断言", 9, "响应状态和业务字段均有断言。"),
                ("error_handling", "错误处理", 8, "错误响应会保留诊断信息。"),
                ("coverage", "测试覆盖", 8, "覆盖了输入规范中的目标端点。"),
                ("execution", "执行成功", 8.5, "真实执行结果显示测试通过。"),
            )
        ],
        "coverage": {
            "rate": 1.0,
            "covered_items": ["POST /projects"],
            "missing_items": [],
            "explanation": "脚本覆盖输入规范中的唯一端点。",
        },
        "risks": [],
        "recommendations": ["补充重复项目名的失败断言。"],
    }
    try:
        result = persist_automation_evaluation(
            context,
            {"evaluation_type": "api", "evaluation": evaluation},
        )
        artifact = context.artifacts["automation_evaluation"]
        assert result == {
            "status": "persisted",
            "run_id": 21,
            "artifact_key": "automation_evaluation",
            "evaluation_type": "api",
            "overall_score": 8.5,
        }
        assert artifact["source_execution_id"] == 42
        assert artifact["result"] == evaluation
    finally:
        db.close()
