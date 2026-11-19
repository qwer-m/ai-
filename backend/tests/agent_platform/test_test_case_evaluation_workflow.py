import gzip
import json
from pathlib import Path

import pytest

from modules.agent_platform.contracts import WorkflowGraph
from modules.agent_platform.test_case_evaluation_workflow import BUILTIN_WORKFLOW_SPECS, _build_evaluation_result


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


def test_real_run91_group_metrics_skip_and_receipt_completeness():
    fixture = Path(__file__).with_name("fixtures") / "run91_test_case_comparison_real.json.gz"
    data = json.loads(gzip.decompress(fixture.read_bytes()))
    inputs = {"baseline_groups": data["baseline_groups"], "reference_content": data["reference_content"],
              "generated_cases": [{"case_id": k} for k in data["case_ids"]]}
    evidence = data["evidence"]
    report = _build_evaluation_result(evidence, inputs)
    baseline = report["requirement_baseline"]
    assert len(baseline["groups"]) == 49 and len(baseline["requirement_points"]) == 47
    assert [g["manual_refs"] for g in baseline["groups"] if g["excluded"]] == [["H26"], ["H28"]]
    assert (baseline["generated_coverage_count"], baseline["modified_coverage_count"]) == (44, 42)
    assert report["metrics"] == {"precision": round(42 / 44, 4), "recall": 1.0,
                                 "f1_score": round(84 / 86, 4), "coverage_jaccard": round(42 / 44, 4)}
    assert len(report["defect_analysis"]["modifications"]) == 1
    with pytest.raises(ValueError, match="遗漏"):
        _build_evaluation_result({**evidence, "groups": evidence["groups"][:-1]}, inputs)
