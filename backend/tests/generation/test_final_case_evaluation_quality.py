from __future__ import annotations

from modules.test_generation_components.services.final_case_evaluation_learning import (
    _evaluation_learning_candidate_quality_gate as legacy_quality_gate,
)
from modules.test_generation_components.services.final_case_evaluation_quality import (
    _evaluation_learning_candidate_quality_gate,
    _filter_quality_evaluation_sample_for_apply,
)


def test_evaluation_quality_gate_rejects_case_id_only_text() -> None:
    assert legacy_quality_gate is _evaluation_learning_candidate_quality_gate

    result = _evaluation_learning_candidate_quality_gate(
        text="TC-039修改CASE-49",
        source_field="modifications",
        candidate_type="quality_fix_hint",
        signal_type="positive",
    )

    assert result == {
        "status": "rejected",
        "reason": "case_identifier_label_only",
    }


def test_quality_evaluation_sample_filter_keeps_reusable_sample() -> None:
    sample = {
        "source": "quality_evaluation_defect",
        "source_type": "quality_evaluation_defect",
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": "quality_fix_hint",
        "learning_signal_source": "defect_analysis.modifications",
        "title": (
            "modified final case should assert persisted progress, generated case only checked generic success"
        ),
        "user_comment": (
            "modified final case should assert persisted progress, generated case only checked generic success"
        ),
        "pattern_summary": "prefer | quality_fix_hint | assert persisted progress",
    }

    filtered = _filter_quality_evaluation_sample_for_apply(sample)

    assert filtered is not None
    assert filtered["quality_gate_status"] == "auto_select"
    assert filtered["quality_gate_policy"] == "evaluation_defect_reusable_pattern_v1"
