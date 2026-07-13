from __future__ import annotations

from modules.test_generation_components.postprocess import postprocess_priority_config as config


def test_priority_config_facade_uses_default_anchor_rules_when_no_data_file() -> None:
    families = config.p0_critical_families()

    assert families
    assert all(isinstance(name, str) and isinstance(tokens, tuple) for name, tokens in families)
    assert "main_workflow_hit" in config.scoring_deltas()


def test_priority_config_keeps_default_category_and_quality_contracts() -> None:
    categories = config.preferred_pattern_categories()

    assert isinstance(categories, set)
    assert categories
    assert config.invalid_case_quality_markers() == (
        "invalid",
        "invalid_case",
        "reject",
        "rejected",
    )
    assert config.quality_check_fields() == ("case_quality", "quality")
