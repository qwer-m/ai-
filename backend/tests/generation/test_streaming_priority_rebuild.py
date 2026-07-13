from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_priority_rebuild import (
    preserve_review_priority_demotions,
    rebuild_priority_by_semantics,
)


def _case_signature(case: dict[str, object]) -> str:
    return str(case.get("case_signature") or "")


def test_preserve_review_priority_demotions_restores_matching_review_downgrades_to_p1() -> None:
    parsed_result = [
        {"case_signature": "case-a", "priority": "P0", "priority_final": "P0"},
        {"case_signature": "case-b", "priority": "P0", "priority_final": "P0"},
    ]
    review_candidate_cases = [
        {
            "case_signature": "case-a",
            "priority_final": "P2",
            "priority_decision_source": "model_p0_guard_downgrade",
        },
        {
            "case_signature": "case-b",
            "priority_final": "p1",
            "priority_decision_source": "main_path_anchor_demoted_non_blocking",
        },
    ]

    restored = preserve_review_priority_demotions(
        parsed_result,
        review_candidate_cases,
        case_signature_fn=_case_signature,
    )

    assert [case["priority"] for case in restored] == ["P1", "P1"]
    assert [case["priority_final"] for case in restored] == ["P1", "P1"]
    assert {case["priority_decision_state"] for case in restored} == {"overridden"}
    assert {case["priority_decision_source"] for case in restored} == {
        "review_model_p0_demotion_preserved"
    }


def test_preserve_review_priority_demotions_returns_dict_copies_without_matches() -> None:
    parsed_result = [
        {"case_signature": "case-a", "priority": "P0", "priority_final": "P0"},
        {"case_signature": "case-b", "priority": "P1", "priority_final": "P1"},
    ]
    review_candidate_cases = [
        {
            "case_signature": "case-a",
            "priority_final": "P0",
            "priority_decision_source": "model_p0_guard_downgrade",
        },
        {
            "case_signature": "case-b",
            "priority_final": "P2",
            "priority_decision_source": "manual_review",
        },
    ]

    restored = preserve_review_priority_demotions(
        parsed_result,
        review_candidate_cases,
        case_signature_fn=_case_signature,
    )

    assert restored == parsed_result
    assert restored[0] is not parsed_result[0]
    assert restored[1] is not parsed_result[1]


def test_preserve_review_priority_demotions_does_not_mutate_inputs() -> None:
    parsed_case = {"case_signature": "case-a", "priority": "P0", "priority_final": "P0"}
    review_candidate = {
        "case_signature": "case-a",
        "priority_final": "P2",
        "priority_decision_source": "model_p0_guard_downgrade",
    }
    parsed_result = [parsed_case]
    review_candidate_cases = [review_candidate]

    restored = preserve_review_priority_demotions(
        parsed_result,
        review_candidate_cases,
        case_signature_fn=_case_signature,
    )

    assert parsed_case == {"case_signature": "case-a", "priority": "P0", "priority_final": "P0"}
    assert review_candidate == {
        "case_signature": "case-a",
        "priority_final": "P2",
        "priority_decision_source": "model_p0_guard_downgrade",
    }
    assert restored[0] is not parsed_case


def test_preserve_review_priority_demotions_ignores_non_dict_items() -> None:
    restored = preserve_review_priority_demotions(
        [
            {"case_signature": "case-a", "priority": "P0", "priority_final": "P0"},
            "bad parsed case",
        ],
        [
            "bad review case",
            {
                "case_signature": "case-a",
                "priority_final": "P2",
                "priority_decision_source": "model_p0_guard_downgrade",
            },
        ],
        case_signature_fn=_case_signature,
    )

    assert restored == [
        {
            "case_signature": "case-a",
            "priority": "P1",
            "priority_final": "P1",
            "priority_decision_state": "overridden",
            "priority_decision_source": "review_model_p0_demotion_preserved",
        }
    ]


def test_rebuild_priority_by_semantics_promotes_registered_p0_group_tokens() -> None:
    cases = [
        {"id": "pay", "priority": "P2", "description": "付费拦截提示", "expected_result": "无法进入"},
        {"id": "ai", "priority": "P2", "description": "auto score result", "expected_result": "score is visible"},
    ]

    rebuilt = rebuild_priority_by_semantics(cases)

    assert [case["priority"] for case in rebuilt] == ["P0", "P0"]


def test_rebuild_priority_by_semantics_promotes_p0_extra_tokens() -> None:
    rebuilt = rebuild_priority_by_semantics(
        [{"id": "main", "priority": "P2", "description": "主流程闭环", "expected_result": "完成"}]
    )

    assert rebuilt[0]["priority"] == "P0"


def test_rebuild_priority_by_semantics_applies_p1_and_p2_fallback_tokens() -> None:
    rebuilt = rebuild_priority_by_semantics(
        [
            {"id": "nav", "priority": "P2", "description": "页面跳转交互", "expected_result": "跳转成功"},
            {"id": "copy", "priority": "P1", "description": "UI文案展示", "expected_result": "文案正确"},
        ]
    )

    assert [case["priority"] for case in rebuilt] == ["P1", "P2"]


def test_rebuild_priority_by_semantics_normalizes_unknown_priority_and_skips_non_dict() -> None:
    rebuilt = rebuild_priority_by_semantics(
        [
            {"id": "plain", "priority": "unknown", "description": "普通用例", "expected_result": "保存成功"},
            "bad",
        ]  # type: ignore[list-item]
    )

    assert rebuilt == [{"id": "plain", "priority": "P2", "description": "普通用例", "expected_result": "保存成功"}]
