from __future__ import annotations

from modules.testing.sample_case_access import sample_case_id, sample_case_steps, sample_case_text, sample_value


def test_sample_value_skips_empty_preferred_values() -> None:
    assert sample_value({"sourceCaseId": "", "caseId": "TC-001"}, "sourceCaseId", "caseId") == "TC-001"
    assert sample_value({"sourceCaseId": "SRC-001", "caseId": "TC-001"}, "sourceCaseId", "caseId") == "SRC-001"


def test_sample_case_fields_keep_source_specific_values_first() -> None:
    sample = {
        "caseId": "TC-GENERATED",
        "sourceCaseId": "SRC-001",
        "description": "Generated title",
        "sourceCaseTitle": "Curated title",
        "testModule": "Generated module",
        "sourceCaseModule": "Curated module",
    }

    assert sample_case_id(sample, "sourceCaseId") == "SRC-001"
    assert sample_case_id(sample) == "TC-GENERATED"
    assert sample_case_text(sample, "description", "sourceCaseTitle") == "Curated title"
    assert sample_case_text(sample, "test_module", "sourceCaseModule") == "Curated module"


def test_sample_case_id_can_include_plain_id_for_generated_cases() -> None:
    assert sample_case_id({"id": "TC-001"}) == "TC-001"


def test_sample_case_id_can_exclude_plain_sample_row_id() -> None:
    assert sample_case_id({"id": "row-1", "test_case_id": "TC-002"}, include_plain_id=False) == "TC-002"
    assert sample_case_id({"id": "row-1"}, include_plain_id=False) == ""


def test_sample_case_id_preferred_source_key_wins_when_plain_id_excluded() -> None:
    sample = {"id": "row-1", "sourceCaseId": "SRC-001", "test_case_id": "TC-002"}

    assert sample_case_id(sample, "sourceCaseId", include_plain_id=False) == "SRC-001"


def test_sample_case_steps_preserves_list_steps_before_flat_text() -> None:
    sample = {
        "sourceCaseSteps": ["Open plan page", {"action": "Save plan"}],
        "testSteps": "fallback step",
    }

    assert sample_case_steps(sample, "sourceCaseSteps") == ["Open plan page", "Save plan"]
    assert sample_case_text(sample, "steps", "sourceCaseSteps") == "Open plan page Save plan"
