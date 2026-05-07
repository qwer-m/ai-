from __future__ import annotations

from modules.testing.test_generation_components.judge.test_case_judge import judge_cases
from modules.testing.test_generation_components.judge.test_case_repairer import repair_cases


def test_judge_repairer_does_not_append_untyped_batch_gap_cases() -> None:
    semantics = {
        "hard_flow_constraints": [
            "\u4e8c\u8f6e\u590d\u4e60\u8bfe\u7a0b\u8be6\u60c5\u9875\u4ec5\u4fdd\u7559\u5b66\u0026\u7ec3\u6d41\u7a0b"
        ],
        "reuse_risks": [
            "\u6253\u5370\u5f39\u7a97\u4fdd\u7559\u6559\u6750\u548c\u7b54\u6848\u53cc\u9009\u9879"
        ],
    }

    judged = judge_cases([], semantics)
    repaired = repair_cases(judged, semantics)

    assert repaired.appended_case_count == 0
    assert repaired.repaired_case_count == 0
    assert repaired.repairable_count == 2
    for item in repaired.cases:
        if item.status != "REPAIRABLE":
            continue
        assert item.repaired_pass is False
        assert item.after_case == {}
        assert item.reject_reason == "requires_typed_requirement_unit"
        assert "untyped_batch_gap_not_auto_repaired" in item.signals.notes


def test_judge_repairer_does_not_append_when_missing_payload_is_empty() -> None:
    judged = judge_cases([], {"hard_flow_constraints": []})
    repaired = repair_cases(judged, {"hard_flow_constraints": []})

    assert repaired.appended_case_count == 0
    assert all(not item.after_case for item in repaired.cases if item.status == "REPAIRABLE")
