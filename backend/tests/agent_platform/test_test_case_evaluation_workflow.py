from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from modules.agent_platform.contracts import WorkflowGraph
from modules.agent_platform.test_case_evaluation_workflow import BUILTIN_WORKFLOW_SPECS, _build_evaluation_result, _validate_assertion


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

def test_real_assertions_distinguish_the_same_transition_across_two_deliveries():
    path = Path(__file__).with_name("fixtures") / "run92_run93_assertion_evidence_real.json"
    receipts = json.loads(path.read_text(encoding="utf-8"))
    assert len({receipt["group"]["point"] for receipt in receipts}) == 1
    assert {row["source_run_id"]: row["group"]["assertion"]["verdict"] for row in receipts} == {
        84: "missing_trigger", 89: "detects_fault",
    }
    for receipt in receipts:
        group = receipt["group"]
        cases = {case["case_id"]: case for case in receipt["generated_cases"]}
        _validate_assertion(group, cases)
        invalid = deepcopy(group)
        assertion = invalid["assertion"]
        assertion["steps"] = [len(cases[assertion["case_id"]]["steps"]) + 1]
        with pytest.raises(ValueError, match="存在的从 1 开始"):
            _validate_assertion(invalid, cases)
        if group["assertion"]["verdict"] == "missing_trigger":
            with pytest.raises(ValueError, match="不能判为完整覆盖"):
                _validate_assertion({**group, "agent": "full"}, cases)
