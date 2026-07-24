from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_execution_plan_assignment import (
    maximum_weight_stage_assignment as _maximum_weight_stage_assignment,
)


def maximum_weight_stage_assignment(
    stages: list[dict[str, object]],
    edges: list[dict[str, object]],
) -> dict[str, object]:
    normalized_edges = [
        {
            **edge,
            "candidate_key": str(edge.get("candidate_key") or edge.get("case_signature") or ""),
        }
        for edge in edges
    ]
    return _maximum_weight_stage_assignment(stages, normalized_edges)


def _stages() -> list[dict[str, object]]:
    return [
        {"stage_key": "entry", "stage_order": 1, "required": True},
        {"stage_key": "configure", "stage_order": 2, "required": True},
    ]


def test_maximum_weight_assignment_beats_stage_local_greedy_choice() -> None:
    result = maximum_weight_stage_assignment(
        _stages(),
        [
            {"stage_key": "entry", "case_signature": "case-a", "score": 90},
            {"stage_key": "entry", "case_signature": "case-b", "score": 85},
            {"stage_key": "configure", "case_signature": "case-a", "score": 80},
            {"stage_key": "configure", "case_signature": "case-b", "score": 30},
        ],
    )

    assert [
        (item["stage_key"], item["case_signature"])
        for item in result["selected"]
    ] == [("entry", "case-b"), ("configure", "case-a")]
    assert result["total_score"] == 165
    assert result["required_gap_count"] == 0


def test_assignment_uses_required_and_optional_virtual_gaps() -> None:
    result = maximum_weight_stage_assignment(
        [
            {"stage_key": "entry", "stage_order": 1, "required": True},
            {"stage_key": "preview", "stage_order": 2, "optional": True},
        ],
        [],
    )

    assert result["selected"] == []
    assert result["required_gap_count"] == 1
    assert result["optional_gap_count"] == 1
    assert result["gaps"] == [
        {
            "stage_key": "entry",
            "stage_label": "entry",
            "required": True,
            "reason": "no_globally_selected_candidate",
        },
        {
            "stage_key": "preview",
            "stage_label": "preview",
            "required": False,
            "reason": "no_globally_selected_candidate",
        },
    ]


def test_assignment_is_stable_when_candidate_edge_order_changes() -> None:
    edges = [
        {"stage_key": "entry", "case_signature": "case-a", "score": 50},
        {"stage_key": "entry", "case_signature": "case-b", "score": 50},
        {"stage_key": "configure", "case_signature": "case-a", "score": 50},
        {"stage_key": "configure", "case_signature": "case-b", "score": 50},
    ]

    first = maximum_weight_stage_assignment(_stages(), edges)
    second = maximum_weight_stage_assignment(_stages(), list(reversed(edges)))

    assert first["selected"] == second["selected"]
    assert [item["case_signature"] for item in first["selected"]] == ["case-a", "case-b"]


def test_assignment_deduplicates_stage_case_signature_edges_by_highest_score() -> None:
    result = maximum_weight_stage_assignment(
        [{"stage_key": "entry", "required": True}],
        [
            {"stage_key": "entry", "case_signature": "same-case", "score": 10},
            {"stage_key": "entry", "case_signature": "same-case", "score": 70},
        ],
    )

    assert result["candidate_edge_count"] == 1
    assert result["selected"][0]["score"] == 70
    assert result["total_score"] == 70


def test_required_stage_prefers_explicit_gap_to_non_positive_quality_candidate() -> None:
    for score in (-15, 0):
        result = maximum_weight_stage_assignment(
            [{"stage_key": "commit", "required": True}],
            [{"stage_key": "commit", "case_signature": "unrelated-case", "score": score}],
        )

        assert result["selected"] == []
        assert result["required_gap_count"] == 1


def test_duplicate_equal_score_edge_diagnostic_is_stable_across_input_order() -> None:
    edges = [
        {
            "stage_key": "entry",
            "case_signature": "same-case",
            "case_id": "case-b",
            "score": 40,
        },
        {
            "stage_key": "entry",
            "case_signature": "same-case",
            "case_id": "case-a",
            "score": 40,
        },
    ]

    first = maximum_weight_stage_assignment([{"stage_key": "entry", "required": True}], edges)
    second = maximum_weight_stage_assignment(
        [{"stage_key": "entry", "required": True}],
        list(reversed(edges)),
    )

    assert first["selected"] == second["selected"]
    assert first["selected"][0]["case_id"] == "case-a"


def test_stage_without_required_declaration_is_not_guessed_as_required() -> None:
    result = maximum_weight_stage_assignment([{"stage_key": "undeclared"}], [])

    assert result["required_gap_count"] == 0
    assert result["optional_gap_count"] == 1


def test_optional_stage_does_not_take_the_only_positive_required_candidate() -> None:
    result = maximum_weight_stage_assignment(
        [
            {"stage_key": "commit", "stage_order": 1, "required": True},
            {"stage_key": "preview", "stage_order": 2, "required": False},
        ],
        [
            {"stage_key": "commit", "case_signature": "shared-case", "score": 1},
            {"stage_key": "preview", "case_signature": "shared-case", "score": 100},
        ],
    )

    assert [
        (item["stage_key"], item["case_signature"])
        for item in result["selected"]
    ] == [("commit", "shared-case")]
    assert result["required_gap_count"] == 0
    assert result["optional_gap_count"] == 1
    assert result["total_score"] == 1


def test_required_stage_competition_minimizes_gaps_then_maximizes_quality() -> None:
    result = maximum_weight_stage_assignment(
        [
            {"stage_key": "entry", "stage_order": 1, "required": True},
            {"stage_key": "commit", "stage_order": 2, "required": True},
            {"stage_key": "visible", "stage_order": 3, "required": True},
        ],
        [
            {"stage_key": "entry", "case_signature": "case-x", "score": 100},
            {"stage_key": "entry", "case_signature": "case-y", "score": 99},
            {"stage_key": "commit", "case_signature": "case-x", "score": 98},
            {"stage_key": "visible", "case_signature": "case-y", "score": 1},
        ],
    )

    assert [
        (item["stage_key"], item["case_signature"])
        for item in result["selected"]
    ] == [("entry", "case-y"), ("commit", "case-x")]
    assert result["required_gap_count"] == 1
    assert result["gaps"][0]["stage_key"] == "visible"
    assert result["total_score"] == 197


def test_assignment_capacity_uses_unique_candidate_key_not_text_signature() -> None:
    result = _maximum_weight_stage_assignment(
        _stages(),
        [
            {
                "stage_key": "entry",
                "candidate_key": "TC-001::entry-state",
                "case_signature": "same-visible-text",
                "case_id": "TC-001",
                "score": 90,
            },
            {
                "stage_key": "configure",
                "candidate_key": "TC-002::configure-state",
                "case_signature": "same-visible-text",
                "case_id": "TC-002",
                "score": 85,
            },
        ],
    )

    assert [item["case_id"] for item in result["selected"]] == ["TC-001", "TC-002"]
    assert result["required_gap_count"] == 0
