from modules.test_generation_components.postprocess.streaming_shortfall_supplement import (
    resolve_final_shortfall_supplement_size,
    should_attempt_final_shortfall_supplement,
)


def test_should_attempt_final_shortfall_supplement_requires_floor_and_shortfall() -> None:
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="full_functional_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=False,
        current_count=12,
    ) is True
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="standard_regression",
        expected_count_value=30,
        final_target_floor_count=30,
        append=False,
        current_count=20,
    ) is True


def test_should_attempt_final_shortfall_supplement_blocks_append_and_non_shortfall() -> None:
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="full_functional_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=True,
        current_count=12,
    ) is False
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="full_functional_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=False,
        current_count=40,
    ) is False
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="standard_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=False,
        current_count=12,
    ) is False


def test_resolve_final_shortfall_supplement_size_keeps_existing_buffer_policy() -> None:
    assert resolve_final_shortfall_supplement_size(current_count=38, target_floor_count=40) == {
        "shortfall": 2,
        "buffer": 3,
        "needed": 5,
    }
    assert resolve_final_shortfall_supplement_size(current_count=10, target_floor_count=40) == {
        "shortfall": 30,
        "buffer": 8,
        "needed": 30,
    }
