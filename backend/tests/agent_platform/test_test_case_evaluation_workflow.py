from modules.agent_platform.contracts import WorkflowGraph
from modules.agent_platform.test_case_evaluation_workflow import BUILTIN_WORKFLOW_SPECS


def test_test_case_evaluation_uses_native_agent_workflow() -> None:
    assert len(BUILTIN_WORKFLOW_SPECS) == 1
    spec = BUILTIN_WORKFLOW_SPECS[0]
    assert spec["workflow_key"] == "test_case_evaluation"

    graph = WorkflowGraph.model_validate(spec["definition"])
    assert [node.node_key for node in graph.execution_order()] == [
        "evaluate",
        "persist",
    ]
    assert graph.output_node_key == "persist"
