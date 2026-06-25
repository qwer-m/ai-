from __future__ import annotations

from modules.testing.sample_pool_shadow_store import _sample_to_row


def test_sample_to_row_reads_shared_case_aliases_and_camel_case_fields() -> None:
    row = _sample_to_row(
        {
            "sampleId": "sample-1",
            "sourceType": "quality_evaluation_defect",
            "sourceId": 99,
            "caseId": "TC-ALIAS",
            "description": "Create learning plan",
            "signalType": "positive",
            "patternUsage": "prefer",
            "expectedPriority": "P1",
            "reasonCategory": "core_flow",
            "patternCategory": "closure",
            "patternSummary": "Learning plan should save successfully",
            "patternCanonical": "learning plan save",
            "patternClusterKey": "learning_plan|save",
            "patternWeight": 0.8,
            "patternQualityScore": 0.75,
            "learningStatus": "user_confirmed",
            "learningConfirmedBy": 7,
        },
        project_id=1,
        user_id=2,
    )

    assert row.sample_id == "sample-1"
    assert row.source_type == "quality_evaluation_defect"
    assert row.source_id == 99
    assert row.source_case_id == "TC-ALIAS"
    assert row.case_id == "TC-ALIAS"
    assert row.title == "Create learning plan"
    assert row.sample_kind == "positive"
    assert row.pattern_usage == "prefer"
    assert row.expected_priority == "P1"
    assert row.reason_category == "core_flow"
    assert row.pattern_category == "closure"
    assert row.pattern_summary == "Learning plan should save successfully"
    assert row.pattern_canonical == "learning plan save"
    assert row.pattern_cluster_key == "learning_plan|save"
    assert row.pattern_weight == 0.8
    assert row.pattern_quality_score == 0.75
    assert row.learning_status == "user_confirmed"
    assert row.learning_confirmed_by == 7


def test_sample_to_row_keeps_source_case_fields_before_case_aliases() -> None:
    row = _sample_to_row(
        {
            "caseId": "TC-GENERATED",
            "sourceCaseId": "SRC-001",
            "description": "Generated title",
            "sourceCaseTitle": "Curated title",
        },
        project_id=1,
        user_id=None,
    )

    assert row.source_case_id == "SRC-001"
    assert row.case_id == "TC-GENERATED"
    assert row.title == "Curated title"
