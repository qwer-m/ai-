from __future__ import annotations

from modules.test_generation_components.services.final_case_learning_derivation import (
    build_learning_samples_from_final_cases,
)
from modules.test_generation_components.services.final_case_learning_service import (
    build_learning_samples_from_final_cases as service_build_learning_samples_from_final_cases,
)


def test_final_case_learning_derivation_builds_positive_sample_and_service_reexports() -> None:
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-001",
                "description": "Verify submitted order keeps payment state",
                "test_module": "order state",
                "steps": ["submit order", "open order detail"],
                "expected_result": "order detail shows paid state",
                "priority": "P1",
            }
        ],
        requirement_text="order payment state must be visible",
        generation_id=501,
    )

    assert service_build_learning_samples_from_final_cases is build_learning_samples_from_final_cases
    assert result["diagnostics"]["final_case_count"] == 1
    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["negative_samples"] == []
