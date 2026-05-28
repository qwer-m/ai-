import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import (
    classify_case_scenario_key,
    govern_cases_by_flow_structure,
)
from modules.test_generation_components.coverage import scenario_registry
from modules.test_generation_components.coverage.scenario_registry import (
    infer_domain_tags,
    scenario_registry_meta,
)


def test_scenario_registry_is_loaded_from_data_file() -> None:
    data_path = Path(scenario_registry.__file__).with_name("scenario_registry_data.json")
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    meta = scenario_registry_meta()

    assert payload["version"] == meta["scenario_policy_registry_version"]
    assert len(payload["domains"]) == meta["domain_policy_registered_count"]
    assert len(payload["scenarios"]) == meta["scenario_policy_registered_count"]
    assert meta["scenario_policy_sources"]["manual_seed"] >= 1
    assert "\u8bb2\u9519\u9898\u63a5\u5165AI.pdf" in meta["scenario_policy_documents"]
    assert "ai_answer_irrelevant_score_zero" in {
        scenario["key"] for scenario in payload["scenarios"]
    }
    assert {
        "pdf_download_content",
        "essay_empty_state",
        "delete_restore_unsubmitted",
        "featured_sorting",
        "essay_submission_failure_reason",
        "essay_polish_copy",
        "essay_critique_button_availability",
        "my_essay_limit",
        "nonlinear_course_unlock",
        "free_first_lesson",
        "secret_overlay",
        "essay_submission_success_state",
        "critique_limit",
        "star_rating",
        "sentence_comment_jump",
        "hot_recommend_entry",
        "secret_entry_list",
        "essay_sample_numbering",
        "original_image_toggle",
    } <= {
        scenario["key"] for scenario in payload["scenarios"]
    }


def test_scenario_registry_loader_skips_disabled_entries() -> None:
    payload = {
        "domains": [
            {
                "key": "enabled_domain",
                "hints": ["enabled"],
                "status": "active",
            },
            {
                "key": "disabled_domain",
                "hints": ["disabled"],
                "status": "disabled",
            },
        ],
        "scenarios": [
            {
                "key": "enabled_scenario",
                "keywords": ["enabled"],
                "status": "active",
            },
            {
                "key": "disabled_scenario",
                "keywords": ["disabled"],
                "status": "disabled",
            },
        ],
    }

    domains = scenario_registry._build_domain_policies(payload)
    scenarios = scenario_registry._build_scenario_policies(payload)

    assert [policy.key for policy in domains] == ["enabled_domain"]
    assert [policy.key for policy in scenarios] == ["enabled_scenario"]


def test_scenario_registry_loads_judge_duplicate_policy_fields() -> None:
    payload = {
        "scenarios": [
            {
                "key": "custom_judge_policy",
                "keywords": ["custom"],
                "judge_threshold": {"score": 0.42, "overlap": 9},
                "cross_module": False,
            }
        ],
    }

    scenarios = scenario_registry._build_scenario_policies(payload)

    assert scenarios[0].judge_score_threshold == 0.42
    assert scenarios[0].judge_overlap_threshold == 9
    assert scenarios[0].cross_module is False


def test_scenario_registry_rejects_duplicate_active_keys() -> None:
    payload = {
        "domains": [
            {"key": "same_domain", "hints": ["a"]},
            {"key": "same_domain", "hints": ["b"]},
        ],
        "scenarios": [
            {"key": "same_scenario", "keywords": ["a"]},
            {"key": "same_scenario", "keywords": ["b"]},
        ],
    }

    with pytest.raises(ValueError, match="duplicate domain policy key"):
        scenario_registry._build_domain_policies(payload)

    with pytest.raises(ValueError, match="duplicate scenario family policy key"):
        scenario_registry._build_scenario_policies(payload)


def test_scenario_registry_rejects_unknown_scenario_domains() -> None:
    domains = scenario_registry._build_domain_policies(
        {"domains": [{"key": "known_domain", "hints": ["known"]}]}
    )
    scenarios = scenario_registry._build_scenario_policies(
        {"scenarios": [{"key": "case_family", "keywords": ["case"], "domain": "missing_domain"}]}
    )

    with pytest.raises(ValueError, match="unknown domains"):
        scenario_registry._validate_registry_links(domains, scenarios)


def _project_profile(label: str, mode: str = "full_functional_regression") -> dict:
    return {
        "confidence": 0.9,
        "flow_outline": {
            "flow_order": ["stage"],
            "flow_labels": {"stage": label},
            "cross_cutting": [],
            "cross_cutting_labels": {},
        },
        "scenario_cluster_policy": {"coverage_mode": mode},
    }


def test_ai_wrong_question_duplicate_families_use_registry_caps() -> None:
    cases = [
        {
            "id": "TC-016",
            "description": "\u9a8c\u8bc1\u5b66\u5458\u56de\u7b54\u7b54\u975e\u6240\u95ee\u65f6\uff0c\u51c6\u786e\u6027\u76f4\u63a5\u8bb0\u4e3a0\u5206",
            "test_module": "\u5b66\u5458\u7aefAI\u8bc4\u5206",
            "steps": ["\u5b66\u5458\u8f93\u5165\u4eca\u5929\u5929\u6c14\u5f88\u597d"],
            "expected_result": "\u8bc4\u5206\u7ed3\u679c\u663e\u793a\u51c6\u786e\u6027\u5206\u6570\u4e3a0\u5206\uff0c\u5176\u4ed6\u7ef4\u5ea6\u6b63\u5e38\u8bc4\u5206",
            "priority": "P0",
        },
        {
            "id": "TC-024",
            "description": "\u9a8c\u8bc1\u5b66\u5458\u7aef\u56de\u7b54\u7b54\u975e\u6240\u95ee\u65f6\u51c6\u786e\u6027\u81ea\u52a80\u5206\uff0c\u4e14\u603b\u5206\u6309\u89c4\u5219\u8ba1\u7b97",
            "test_module": "\u5b66\u5458\u7aefAI\u8bb2\u9519\u9898\u8bc4\u5206",
            "steps": ["\u8f93\u5165\u201c\u4eca\u5929\u5929\u6c14\u4e0d\u9519\u201d"],
            "expected_result": "\u51c6\u786e\u6027\u7ef4\u5ea6\u5f970\u5206\uff0c\u7efc\u5408\u8bc4\u5206\u4f4e\u4e8e8\u5206",
            "priority": "P1",
        },
        {
            "id": "TC-037",
            "description": "\u9a8c\u8bc1\u8bc4\u5206\u89c4\u5219\uff1a\u7b54\u975e\u6240\u95ee\u65f6\uff0c\u51c6\u786e\u6027\u76f4\u63a50\u5206",
            "test_module": "\u5b66\u5458\u7aefAI\u8bb2\u9519\u9898\u4ea4\u4e92-\u8bc4\u5206\u673a\u5236",
            "steps": ["\u56de\u7b54\u4e0e\u95ee\u9898\u65e0\u5173\u7684\u5185\u5bb9"],
            "expected_result": "\u51c6\u786e\u6027\u5f97\u5206\u4e3a0\u5206\uff0c\u5e76\u6807\u6ce8\u7b54\u975e\u6240\u95ee",
            "priority": "P2",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "\u8bb2\u9519\u9898AI\u8bc4\u5206",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("\u5b66\u5458\u7aefAI\u8bc4\u5206"),
    )

    assert summary["scenario_policy_registry_version"] == 1
    assert summary["scenario_cap_policy"]["ai_answer_irrelevant_score_zero"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 2
    assert [case["id"] for case in governed] == ["TC-016"]


def test_ai_completed_reentry_variants_collapse_to_readonly_family() -> None:
    cases = [
        {
            "id": "TC-022",
            "description": "\u9a8c\u8bc1\u5b66\u5458\u7aef\u91cd\u590d\u8fdb\u5165\u5df2\u5b8c\u6210\u7684\u8bb2\u9519\u9898\u9875\u9762\uff0c\u663e\u793a\u5386\u53f2\u5bf9\u8bdd\u4e14\u6240\u6709\u4ea4\u4e92\u6309\u94ae\u7f6e\u7070\u4e0d\u53ef\u64cd\u4f5c",
            "test_module": "\u5b66\u5458\u7aefAI\u8bb2\u9519\u9898-\u91cd\u590d\u8fdb\u5165",
            "expected_result": "\u9875\u9762\u4fdd\u7559\u6240\u6709\u5386\u53f2\u5bf9\u8bdd\u5185\u5bb9\uff1b\u8f93\u5165\u6846\u3001\u5f55\u97f3\u6309\u94ae\u5747\u7f6e\u7070",
            "priority": "P1",
        },
        {
            "id": "TC-056",
            "description": "\u9a8c\u8bc1\u5df2\u5b8c\u6210\u8bb2\u9519\u9898\u540e\u5b66\u5458\u518d\u6b21\u8fdb\u5165\u8be5\u8bb2\u9519\u9898\u9875\u9762\uff0c\u65e0\u6cd5\u91cd\u590d\u6253\u5206\u4e14\u4e0d\u80fd\u7ee7\u7eed\u56de\u7b54",
            "test_module": "\u5b66\u5458\u7aefAI\u8bb2\u9519\u9898\u91cd\u590d\u8fdb\u5165",
            "expected_result": "\u5c55\u793a\u4e4b\u524d\u7684\u5b8c\u6574\u5386\u53f2\u5bf9\u8bdd\uff0c\u8f93\u5165\u6846\u548c\u8bed\u97f3\u5f55\u5236\u6309\u94ae\u7f6e\u7070\u6216\u79fb\u9664",
            "priority": "P2",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "\u8bb2\u9519\u9898\u5df2\u5b8c\u6210\u91cd\u590d\u8fdb\u5165",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("\u5b66\u5458\u7aefAI\u8bb2\u9519\u9898", "expanded_regression"),
    )

    assert summary["scenario_cap_policy"]["ai_completed_reentry_readonly"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-022"]


def test_schedule_duplicate_families_are_specific_before_generic_empty_state() -> None:
    cases = [
        {
            "id": "TC-007",
            "description": "\u9996\u9875-\u672c\u5468\u8fdb\u5ea6\u6a21\u5757\uff1a\u5f53\u672c\u5468\u65e0\u8bfe\u7a0b\u65f6\uff0c\u7a7a\u72b6\u6001\u5c55\u793a'\u672c\u5468\u6682\u65e0\u5b66\u4e60\u8ba1\u5212'",
            "test_module": "\u9996\u9875-\u672c\u5468\u8fdb\u5ea6\u6a21\u5757",
            "expected_result": "\u672c\u5468\u8fdb\u5ea6\u533a\u57df\u663e\u793a\u6587\u6848'\u672c\u5468\u6682\u65e0\u5b66\u4e60\u8ba1\u5212'",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "\u9996\u9875-\u672c\u5468\u8fdb\u5ea6\u6a21\u5757\uff1a\u7a7a\u72b6\u6001\uff08\u65e0\u8bfe\u7a0b\uff09\u65e7\u7248\u672c\u663e\u793a\u201c\u672c\u5468\u6682\u65e0\u5b66\u4e60\u8ba1\u5212\u201d",
            "test_module": "\u9996\u9875-\u672c\u5468\u8fdb\u5ea6\u6a21\u5757",
            "expected_result": "\u672c\u5468\u8fdb\u5ea6\u6a21\u5757\u5c55\u793a\u201c\u672c\u5468\u6682\u65e0\u5b66\u4e60\u8ba1\u5212\u201d\uff0c\u4e0d\u663e\u793a\u6392\u884c\u699c\u548c\u8fdb\u5ea6\u6761",
            "priority": "P1",
        },
    ]

    scenario_keys = [
        classify_case_scenario_key(case, "stage:\u9996\u9875-\u672c\u5468\u8fdb\u5ea6\u6a21\u5757")
        for case in cases
    ]
    governed, summary = govern_cases_by_flow_structure(
        "\u8fd1\u671f\u8bfe\u7a0b \u9996\u9875 \u672c\u5468\u8fdb\u5ea6",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("\u9996\u9875-\u672c\u5468\u8fdb\u5ea6\u6a21\u5757"),
    )

    assert all("schedule_week_progress_empty" in key for key in scenario_keys)
    assert summary["scenario_cap_policy"]["schedule_week_progress_empty"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-007"]


def test_domain_registry_keeps_only_dominant_domain_for_mixed_requirement_text() -> None:
    ai_text = (
        "\u8bb2\u9519\u9898\u63a5\u5165AI\uff0c\u8986\u76d6\u70b9\u51fb\u5bf9\u8bdd\u3001"
        "\u7ee7\u7eed\u5f55\u97f3\u3001\u5b66\u5458\u56de\u7b54\u3001\u8ffd\u95ee\u3001"
        "\u56db\u8f6e\u5bf9\u8bdd\u3001\u8bc4\u5206\u5f39\u7a97\u3001\u7efc\u5408\u8bc4\u5206\u3001"
        "\u7b54\u975e\u6240\u95ee\u3001\u53bb\u65e5\u6e05\uff0c\u4f46\u9875\u9762\u4e2d\u5076\u7136\u63d0\u5230\u5b66\u4e60\u8ba1\u5212\u5165\u53e3\u3002"
    )
    schedule_text = (
        "\u8fd1\u671f\u8bfe\u7a0b\u548c\u6392\u8bfe\uff1a\u65b0\u589e\u8ba1\u5212\u3001\u5df2\u6709\u8ba1\u5212\u3001"
        "\u7f16\u8f91\u8ba1\u5212\u3001\u8bfe\u7a0b\u89c4\u5212\u3001\u8bfe\u7a0b\u7ba1\u7406\u3001"
        "\u5b66\u4e60\u8ba1\u5212\u3001\u672c\u5468\u8fdb\u5ea6\u3001\u672c\u5468\u4efb\u52a1\u3001\u8bfe\u5802\u7ba1\u7406\u3001"
        "\u987a\u5ef6\u548c\u9632\u6284\u7b54\u6848\uff0c\u4f46\u67d0\u4e2a\u6309\u94ae\u53ef\u80fd\u8df3\u5230\u8bb2\u9519\u9898\u3002"
    )

    assert infer_domain_tags(ai_text) == {"ai_wrong_question_teaching"}
    assert infer_domain_tags(schedule_text) == {"recent_course_scheduling"}


def test_duplicate_policy_runs_even_without_flow_outline() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "\u9a8c\u8bc1\u5b66\u5458\u56de\u7b54\u7b54\u975e\u6240\u95ee\u65f6\u51c6\u786e\u6027\u76f4\u63a50\u5206",
            "test_module": "\u5b66\u5458\u7aefAI\u8bc4\u5206",
            "expected_result": "\u51c6\u786e\u6027\u5f970\u5206",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "\u7b54\u975e\u6240\u95ee\u65f6\u51c6\u786e\u6027\u8ba1\u4e3a0\u5206\u5e76\u5f71\u54cd\u603b\u5206",
            "test_module": "\u8bc4\u5206\u89c4\u5219",
            "expected_result": "\u51c6\u786e\u6027\u4e3a0\u5206\uff0c\u5176\u4ed6\u7ef4\u5ea6\u6b63\u5e38",
            "priority": "P1",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "",
        cases,
        renumber_ids=False,
        project_profile={},
    )

    assert summary["applied"] is True
    assert summary["flow_reordered"] is False
    assert summary["scenario_cap_policy"]["ai_answer_irrelevant_score_zero"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-001"]
