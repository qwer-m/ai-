from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_case_keys import case_signature
from modules.test_generation_components.postprocess.streaming_review_mapping import (
    CANONICAL_REVIEW_DROP_REASONS,
    map_review_selection_with_reasons,
    map_review_to_candidates,
    normalize_review_llm_reason,
)


def _case(case_id: str, description: str) -> dict[str, str]:
    return {
        "id": case_id,
        "test_module": "课程排课",
        "description": description,
        "expected_result": f"{description}成功",
        "test_input": description,
    }


def test_map_review_to_candidates_matches_by_signature_and_deduplicates() -> None:
    first = _case("TC-001", "保存课程")
    second = _case("TC-002", "删除课程")
    reviewed = [dict(second), dict(second), {"description": "unknown"}]

    assert map_review_to_candidates([first, second], reviewed) == [second]


def test_map_review_selection_with_reasons_accepts_scalar_id_list() -> None:
    first = _case("TC-001", "保存课程")
    second = _case("TC-002", "删除课程")

    selected, signatures, reason_map, origin_map = map_review_selection_with_reasons(
        [first, second],
        ["TC-002", "TC-002", "missing"],
    )

    assert selected == [second]
    assert signatures == {case_signature(second)}
    assert reason_map == {}
    assert origin_map == {}


def test_map_review_selection_with_reasons_accepts_dict_kept_and_dropped_payload() -> None:
    first = _case("TC-001", "保存课程")
    second = _case("TC-002", "删除课程")

    selected, signatures, reason_map, origin_map = map_review_selection_with_reasons(
        [first, second],
        {
            "kept_case_ids": ["TC-001"],
            "dropped": [{"case_id": "TC-002", "reason": "coverage_redundant"}],
        },
        reason_origin="fallback_llm",
    )

    assert selected == [first]
    assert signatures == {case_signature(first)}
    assert reason_map == {case_signature(second): "coverage_redundant"}
    assert origin_map == {case_signature(second): "fallback_llm"}


def test_map_review_selection_with_reasons_falls_back_to_selected_objects() -> None:
    first = _case("TC-001", "保存课程")
    second = _case("TC-002", "删除课程")

    selected, signatures, _, _ = map_review_selection_with_reasons(
        [first, second],
        {"selected": [dict(second)]},
    )

    assert selected == [second]
    assert signatures == {case_signature(second)}


def test_map_review_selection_with_reasons_normalizes_unknown_reason_origin_to_llm() -> None:
    first = _case("TC-001", "保存课程")

    _, _, reason_map, origin_map = map_review_selection_with_reasons(
        [first],
        {"dropped": [{"case_id": "TC-001", "reason": "duplicate"}]},
        reason_origin="manual",
    )

    assert reason_map == {case_signature(first): "duplicate"}
    assert origin_map == {case_signature(first): "llm"}


def test_map_review_selection_with_reasons_accepts_alias_dropped_id() -> None:
    case = _case("TC-ALIAS", "drop alias")

    _, _, reason_map, origin_map = map_review_selection_with_reasons(
        [case],
        {"dropped": [{"caseId": "TC-ALIAS", "reason": "duplicate"}]},
        reason_origin="fallback_llm",
    )

    assert reason_map == {case_signature(case): "duplicate"}
    assert origin_map == {case_signature(case): "fallback_llm"}


def test_normalize_review_llm_reason_maps_common_reason_variants() -> None:
    assert "duplicate" in CANONICAL_REVIEW_DROP_REASONS
    assert normalize_review_llm_reason("coverage redundant") == "coverage_redundant"
    assert normalize_review_llm_reason("selection-tradeoff-omitted") == "selection_tradeoff_omitted"
    assert normalize_review_llm_reason("覆盖冗余") == "coverage_redundant"
    assert normalize_review_llm_reason("低价值") == "low_value"
    assert normalize_review_llm_reason("重复覆盖") == "duplicate"
    assert normalize_review_llm_reason("custom reason") == "custom reason"
    assert normalize_review_llm_reason("") == ""
