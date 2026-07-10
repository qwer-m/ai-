from __future__ import annotations

from copy import deepcopy

from modules.test_generation_components.postprocess.streaming_case_keys import case_signature
from modules.test_generation_components.postprocess.streaming_review_selection import (
    apply_append_target_cap,
    build_review_decision_summary_payload,
    build_review_selection_constraints,
    enforce_review_selection_constraints,
    is_high_signal,
    merge_review_selection_candidates,
    rank_review_case_for_fill,
    recover_post_rerank_shortfall,
    recover_review_selection_shortfall,
    resolve_review_llm_drop_reason_maps,
    resolve_review_post_rerank_floor_count,
    review_llm_drop_summary_fields,
    review_must_keep_reasons,
    split_review_candidate_pool,
    summarize_review_decision_counts,
    summarize_review_drop_reason_counts,
    summarize_review_drop_stage_counts,
    summarize_review_llm_drop_diagnostics,
    summarize_review_must_keep_and_signal_counts,
)


def _case(case_id: str, *, rank: int = 0, domain_noise: bool = False) -> dict[str, object]:
    return {
        "id": case_id,
        "description": f"case {case_id}",
        "expected_result": f"case {case_id} succeeds",
        "priority": "P1",
        "rank": rank,
        "domain_noise": domain_noise,
    }


def _review_case(
    case_id: str,
    *,
    priority: str = "P2",
    module: str = "order",
    description: str | None = None,
    test_input: str | None = None,
    expected_result: str | None = None,
    steps: list[str] | None = None,
    confirmed_fact_hits: list[str] | None = None,
    meta: dict[str, object] | None = None,
) -> dict[str, object]:
    case: dict[str, object] = {
        "id": case_id,
        "test_module": module,
        "description": description or f"{module} verifies {case_id}",
        "test_input": test_input or f"payload {case_id}",
        "expected_result": expected_result or f"{case_id} succeeds",
        "priority": priority,
        "steps": steps or ["open page", "submit form", "check result"],
    }
    if confirmed_fact_hits is not None:
        case["confirmed_fact_hits"] = confirmed_fact_hits
    if meta is not None:
        case["meta"] = meta
    return case


def _coverage_context(*entries: tuple[dict[str, object], dict[str, object]]) -> dict[str, object]:
    return {
        "case_rule_map": {
            case_signature(case): dict(rule_info)
            for case, rule_info in entries
        },
        "rule_meta": {
            "REQ-100": {"rule_id": "REQ-100", "rule_is_core_workflow": True},
            "REQ-200": {"rule_id": "REQ-200", "rule_is_core_workflow": True},
            "REQ-300": {"rule_id": "REQ-300", "rule_is_security_sensitive": True},
            "REQ-901": {"rule_id": "REQ-901", "rule_is_core_workflow": True},
        },
    }


def _rank_case(case: dict[str, object], **_: object) -> tuple[int]:
    return (int(case.get("rank") or 0),)


def _count_dict_cases(value: object) -> int:
    return int(sum(1 for item in (value or []) if isinstance(item, dict)))


def test_merge_review_selection_candidates_prefers_must_keep_on_duplicate_signature() -> None:
    description = "teacher approves personalized recommendation release"
    test_input = "teacher submits approval payload for student recommendation"
    expected_result = "recommendation release is approved and recorded"
    must_keep_case = _review_case(
        "TC-MERGE-001",
        priority="P0",
        module="recommendation",
        description=description,
        test_input=test_input,
        expected_result=expected_result,
    )
    selected_duplicate = _review_case(
        "TC-MERGE-002",
        priority="P2",
        module="recommendation",
        description=description,
        test_input=test_input,
        expected_result=expected_result,
    )
    selected_unique = _review_case(
        "TC-MERGE-003",
        priority="P1",
        module="recommendation",
        description="student views released personalized recommendation",
    )

    merged = merge_review_selection_candidates([must_keep_case], [selected_duplicate, selected_unique])

    assert [case["id"] for case in merged] == ["TC-MERGE-001", "TC-MERGE-003"]
    assert merged[0] is must_keep_case
    assert case_signature(must_keep_case) == case_signature(selected_duplicate)


def test_merge_review_selection_candidates_skips_non_dict_and_preserves_order() -> None:
    must_keep_first = _review_case(
        "TC-MERGE-010",
        priority="P0",
        module="recommendation",
        description="teacher keeps recommendation approval main path",
    )
    must_keep_second = _review_case(
        "TC-MERGE-011",
        priority="P1",
        module="recommendation",
        description="teacher keeps recommendation rejection exception path",
    )
    selected_first = _review_case(
        "TC-MERGE-012",
        priority="P2",
        module="student",
        description="student checks recommendation history after release",
    )
    selected_second = _review_case(
        "TC-MERGE-013",
        priority="P2",
        module="student",
        description="student refreshes recommendation details after release",
    )

    merged = merge_review_selection_candidates(
        [None, must_keep_first, "not-a-case", must_keep_second],
        [selected_first, 7, selected_second],
    )

    assert [case["id"] for case in merged] == [
        "TC-MERGE-010",
        "TC-MERGE-011",
        "TC-MERGE-012",
        "TC-MERGE-013",
    ]


def test_split_review_candidate_pool_routes_must_keep_and_llm_pool_cases() -> None:
    must_keep = _review_case("TC-SPLIT-001", priority="P0")
    reuse_risk = _review_case("TC-SPLIT-002", priority="P1")
    ordinary = _review_case("TC-SPLIT-003", priority="P2")

    def score_case_priority_fn(case: dict[str, object], **_: object) -> dict[str, object]:
        return {"reuse_risk_hit": case["id"] == "TC-SPLIT-002"}

    split = split_review_candidate_pool(
        [must_keep, "not-a-case", reuse_risk, ordinary],
        coverage_context={"coverage": "context"},
        rule_diagnostics={"rule": "diagnostics"},
        must_cover_rule_set={"REQ-100"},
        score_case_priority_fn=score_case_priority_fn,
    )

    assert split.must_keep_cases == [must_keep, reuse_risk]
    assert split.llm_pool_cases == [ordinary]
    assert split.must_keep_signatures == {
        case_signature(must_keep),
        case_signature(reuse_risk),
    }
    assert split.must_keep_reason_map[case_signature(must_keep)] == ["priority_p0"]
    assert split.must_keep_reason_map[case_signature(reuse_risk)] == ["reuse_risk_hit"]


def test_split_review_candidate_pool_preserves_first_duplicate_must_keep_case_and_latest_reasons() -> None:
    description = "teacher approves recommendation publish"
    test_input = "teacher submits recommendation publish approval"
    expected_result = "recommendation publish approval is recorded"
    first = _review_case(
        "TC-SPLIT-010",
        priority="P0",
        description=description,
        test_input=test_input,
        expected_result=expected_result,
    )
    duplicate = _review_case(
        "TC-SPLIT-011",
        priority="P1",
        description=description,
        test_input=test_input,
        expected_result=expected_result,
    )
    seen_cases: list[str] = []

    def score_case_priority_fn(case: dict[str, object], **_: object) -> dict[str, object]:
        seen_cases.append(str(case.get("id") or ""))
        return {"case_id": case.get("id")}

    def must_keep_reasons_fn(
        case: dict[str, object],
        score_profile: dict[str, object],
        **_: object,
    ) -> list[str]:
        return [f"reason_{score_profile['case_id']}"]

    split = split_review_candidate_pool(
        [first, duplicate],
        coverage_context={},
        rule_diagnostics={},
        must_cover_rule_set=set(),
        score_case_priority_fn=score_case_priority_fn,
        must_keep_reasons_fn=must_keep_reasons_fn,
    )

    signature = case_signature(first)
    assert seen_cases == ["TC-SPLIT-010", "TC-SPLIT-011"]
    assert case_signature(duplicate) == signature
    assert split.must_keep_cases == [first]
    assert split.must_keep_signatures == {signature}
    assert split.must_keep_reason_map == {signature: ["reason_TC-SPLIT-011"]}


def test_apply_append_target_cap_noops_when_under_cap_without_running_rankers() -> None:
    cases = [_case("TC-001", rank=1), _case("TC-002", rank=2)]

    def unexpected_call(*_: object, **__: object) -> object:
        raise AssertionError("no-op cap path should not calculate coverage or ranking")

    capped, drop_signatures, drop_total = apply_append_target_cap(
        requirement="real requirement",
        parsed_cases=cases,
        append_final_cap_count=3,
        analyze_coverage_fn=unexpected_call,
        rule_diagnostics_fn=unexpected_call,
        rank_case_fn=unexpected_call,
        signature_fn=case_signature,
    )

    assert capped == cases
    assert capped is not cases
    assert drop_signatures == set()
    assert drop_total == 0


def test_apply_append_target_cap_keeps_best_ranked_cases_in_original_order() -> None:
    cases = [
        _case("TC-001", rank=1),
        _case("TC-002", rank=8),
        _case("TC-003", rank=5),
        _case("TC-004", rank=9),
    ]

    def analyze_coverage(requirement: str, candidate_cases: list[dict[str, object]]) -> dict[str, object]:
        assert requirement == "real requirement"
        return {"case_count": len(candidate_cases), "coverage_marker": "cap"}

    def rule_diagnostics(coverage_context: dict[str, object]) -> dict[str, object]:
        assert coverage_context["coverage_marker"] == "cap"
        return {"diagnostics_marker": "rank"}

    def rank_case(
        case: dict[str, object],
        *,
        coverage_context: dict[str, object],
        rule_diagnostics: dict[str, object],
    ) -> tuple[int]:
        assert coverage_context["case_count"] == 4
        assert rule_diagnostics["diagnostics_marker"] == "rank"
        return (int(case.get("rank") or 0),)

    capped, drop_signatures, drop_total = apply_append_target_cap(
        requirement="real requirement",
        parsed_cases=cases,
        append_final_cap_count=2,
        analyze_coverage_fn=analyze_coverage,
        rule_diagnostics_fn=rule_diagnostics,
        rank_case_fn=rank_case,
        signature_fn=case_signature,
    )

    assert [item["id"] for item in capped] == ["TC-002", "TC-004"]
    assert drop_signatures == {case_signature(cases[0]), case_signature(cases[2])}
    assert drop_total == 2


def test_apply_append_target_cap_returns_fresh_lists_and_drop_sets() -> None:
    cases = [_case("TC-001", rank=1), _case("TC-002", rank=9)]

    def analyze_coverage(_: str, candidate_cases: list[dict[str, object]]) -> dict[str, object]:
        return {"case_count": len(candidate_cases)}

    first_capped, first_drops, _first_total = apply_append_target_cap(
        requirement="real requirement",
        parsed_cases=cases,
        append_final_cap_count=1,
        analyze_coverage_fn=analyze_coverage,
        rule_diagnostics_fn=dict,
        rank_case_fn=_rank_case,
        signature_fn=case_signature,
    )
    first_capped.append(_case("TC-999", rank=99))
    first_drops.add("changed")

    second_capped, second_drops, second_total = apply_append_target_cap(
        requirement="real requirement",
        parsed_cases=cases,
        append_final_cap_count=1,
        analyze_coverage_fn=analyze_coverage,
        rule_diagnostics_fn=dict,
        rank_case_fn=_rank_case,
        signature_fn=case_signature,
    )

    assert first_capped is not cases
    assert second_capped is not cases
    assert [item["id"] for item in second_capped] == ["TC-002"]
    assert "changed" not in second_drops
    assert second_drops == {case_signature(cases[0])}
    assert second_total == 1
    assert [item["id"] for item in cases] == ["TC-001", "TC-002"]


def test_recover_review_selection_shortfall_fills_ranked_unique_cases() -> None:
    selected = [_case("TC-001", rank=1)]
    candidates = [
        _case("TC-001", rank=100),
        _case("TC-002", rank=2),
        _case("TC-003", rank=5),
    ]

    recovered, reason_map, recovered_count = recover_review_selection_shortfall(
        selection_input=selected,
        candidate_cases=candidates,
        target_min_count=3,
        constraint_reason_map={},
        coverage_context={},
        rule_diagnostics={},
        rank_case_fn=_rank_case,
    )

    assert [item["id"] for item in recovered] == ["TC-001", "TC-003", "TC-002"]
    assert recovered_count == 2
    assert reason_map[case_signature(candidates[2])] == "retained_by_shortfall_recovery"
    assert reason_map[case_signature(candidates[1])] == "retained_by_shortfall_recovery"


def test_recover_review_selection_shortfall_applies_domain_guard_when_available() -> None:
    noisy = _case("TC-002", rank=10, domain_noise=True)
    guarded = _case("TC-003", rank=5)

    recovered, reason_map, recovered_count = recover_review_selection_shortfall(
        selection_input=[_case("TC-001", rank=1)],
        candidate_cases=[noisy, guarded],
        target_min_count=2,
        constraint_reason_map={},
        domain_guard_active=True,
        cross_domain_noise_fn=lambda item: bool(item.get("domain_noise")),
        coverage_context={},
        rule_diagnostics={},
        rank_case_fn=_rank_case,
    )

    assert [item["id"] for item in recovered] == ["TC-001", "TC-003"]
    assert recovered_count == 1
    assert case_signature(guarded) in reason_map
    assert case_signature(noisy) not in reason_map


def test_recover_post_rerank_shortfall_appends_from_selection_then_candidates() -> None:
    parsed = [_case("TC-001", rank=1)]
    selection_input = [_case("TC-002", rank=3)]
    candidate_cases = [_case("TC-003", rank=7), _case("TC-002", rank=100)]

    recovered, recovered_count = recover_post_rerank_shortfall(
        parsed_cases=parsed,
        review_selection_input=selection_input,
        candidate_cases=candidate_cases,
        floor_count=3,
        coverage_context={},
        rule_diagnostics={},
        rank_case_fn=_rank_case,
    )

    assert [item["id"] for item in recovered] == ["TC-001", "TC-003", "TC-002"]
    assert recovered_count == 2


def test_resolve_review_post_rerank_floor_count_matches_modes() -> None:
    assert resolve_review_post_rerank_floor_count(
        candidate_count_before_review=50,
        reference_count_effective=20,
        generation_coverage_mode="expanded_regression",
    ) == 16
    assert resolve_review_post_rerank_floor_count(
        candidate_count_before_review=50,
        reference_count_effective=20,
        generation_coverage_mode="full_functional_regression",
    ) == 7
    assert resolve_review_post_rerank_floor_count(
        candidate_count_before_review=3,
        reference_count_effective=2,
        generation_coverage_mode="core_smoke",
    ) == 2


def test_summarize_review_decision_counts_matches_review_summary_fields() -> None:
    review_decision_table = [
        {
            "must_keep_candidate": True,
            "retained_final": True,
            "retained_reason": "retained_due_to_coverage_value",
            "hit_must_cover_rule": True,
            "hits_soft_constraint": True,
            "satisfies_quality_hint": True,
        },
        {
            "must_keep_candidate": True,
            "retained_final": False,
            "violates_forbidden_pattern": True,
        },
        {
            "retained_reason": "retained_due_to_coverage_value",
            "hit_must_cover_rule": True,
            "hits_soft_constraint": True,
        },
    ]
    dropped_rows = [
        {
            "dropped_stage": "review_gate",
            "dropped_reason": "drop_no_new_rule_no_new_bucket_no_high_signal",
            "model_priority_current": "P0",
            "core_rule_hits": ["R-001"],
            "high_signal": True,
            "has_coverage_value": True,
        },
        {
            "dropped_stage": "review_dedup_pre_gate",
            "dropped_reason": "drop_rule_cap",
            "model_priority_current": "p1",
            "missing_rule_hits": ["R-002"],
        },
        {
            "dropped_stage": "post_review_dedup_or_reorder",
            "dropped_reason": "drop_ui_like_redundant_case",
        },
        {"dropped_reason": "drop_ui_like_ratio_cap"},
        {"dropped_reason": "drop_outside_target_window"},
    ]

    assert summarize_review_decision_counts(
        review_decision_table,
        dropped_rows,
        ui_like_ratio_postprocess_drop_count=5,
        final_description_dedup_drop_signatures={"sig-a", "sig-b"},
        drop_by_review_llm_count=3,
        drop_by_review_selector_count=4,
    ) == {
        "must_keep_candidate_count": 2,
        "must_keep_retained_count": 1,
        "must_keep_dropped_count": 1,
        "drop_by_review_llm_count": 3,
        "drop_by_review_selector_count": 4,
        "drop_by_review_gate_count": 1,
        "drop_by_pre_gate_dedup_count": 1,
        "drop_by_post_review_dedup_count": 1,
        "drop_no_new_signal_count": 1,
        "drop_rule_cap_count": 1,
        "drop_ui_like_redundant_count": 1,
        "drop_ui_like_ratio_cap_count": 1,
        "drop_outside_target_window_count": 1,
        "drop_by_rerank_low_signal_count": 1,
        "dropped_model_priority_p0_p1_count": 2,
        "dropped_core_rule_hit_count": 1,
        "dropped_missing_rule_hit_count": 1,
        "dropped_high_signal_count": 1,
        "dropped_has_coverage_value_count": 1,
        "retained_due_to_coverage_value_count": 2,
        "must_cover_rule_hit_count": 2,
        "forbidden_pattern_violation_count": 1,
        "soft_constraint_hit_count": 2,
        "quality_hint_satisfied_count": 1,
        "drop_ui_like_ratio_postprocess_count": 5,
        "drop_final_description_duplicate_count": 2,
    }


def test_summarize_review_must_keep_and_signal_counts_filters_non_dict_rows() -> None:
    result = summarize_review_must_keep_and_signal_counts(
        [
            {
                "must_keep_candidate": True,
                "retained_final": True,
                "retained_reason": "retained_due_to_coverage_value",
                "hit_must_cover_rule": True,
                "hits_soft_constraint": True,
                "satisfies_quality_hint": True,
            },
            {
                "must_keep_candidate": True,
                "retained_final": False,
                "violates_forbidden_pattern": True,
            },
            "ignored",
        ]
    )

    assert result == {
        "must_keep_candidate_count": 2,
        "must_keep_retained_count": 1,
        "must_keep_dropped_count": 1,
        "retained_due_to_coverage_value_count": 1,
        "must_cover_rule_hit_count": 1,
        "forbidden_pattern_violation_count": 1,
        "soft_constraint_hit_count": 1,
        "quality_hint_satisfied_count": 1,
    }


def test_summarize_review_drop_stage_counts_uses_explicit_llm_and_selector_counts() -> None:
    result = summarize_review_drop_stage_counts(
        [
            {"dropped_stage": "review_gate"},
            {"dropped_stage": "review_dedup_pre_gate"},
            {"dropped_stage": "post_review_dedup_or_reorder"},
            {"dropped_stage": "review_llm"},
            "ignored",
        ],
        drop_by_review_llm_count=7,
        drop_by_review_selector_count=8,
    )

    assert result == {
        "drop_by_review_llm_count": 7,
        "drop_by_review_selector_count": 8,
        "drop_by_review_gate_count": 1,
        "drop_by_pre_gate_dedup_count": 1,
        "drop_by_post_review_dedup_count": 1,
    }


def test_summarize_review_drop_reason_counts_counts_signals_and_final_duplicates() -> None:
    duplicate_signatures = (item for item in ["sig-a", "sig-b"])

    result = summarize_review_drop_reason_counts(
        [
            {
                "dropped_reason": "drop_no_new_rule_no_new_bucket_no_high_signal",
                "model_priority_current": "P0",
                "core_rule_hits": ["R-001"],
                "high_signal": True,
                "has_coverage_value": True,
            },
            {
                "dropped_reason": "drop_rule_cap",
                "model_priority_current": "p1",
                "missing_rule_hits": ["R-002"],
            },
            {"dropped_reason": "drop_ui_like_redundant_case"},
            {"dropped_reason": "drop_ui_like_ratio_cap"},
            {"dropped_reason": "drop_outside_target_window"},
            "ignored",
        ],
        ui_like_ratio_postprocess_drop_count=5,
        final_description_dedup_drop_signatures=duplicate_signatures,
    )

    assert result == {
        "drop_no_new_signal_count": 1,
        "drop_rule_cap_count": 1,
        "drop_ui_like_redundant_count": 1,
        "drop_ui_like_ratio_cap_count": 1,
        "drop_outside_target_window_count": 1,
        "drop_by_rerank_low_signal_count": 1,
        "dropped_model_priority_p0_p1_count": 2,
        "dropped_core_rule_hit_count": 1,
        "dropped_missing_rule_hit_count": 1,
        "dropped_high_signal_count": 1,
        "dropped_has_coverage_value_count": 1,
        "drop_ui_like_ratio_postprocess_count": 5,
        "drop_final_description_duplicate_count": 2,
    }


def test_summarize_review_decision_counts_ignores_non_dict_rows() -> None:
    result = summarize_review_decision_counts(
        [
            {"must_keep_candidate": True, "retained_final": True},
            "not-a-row",
            None,
        ],
        [
            {"dropped_stage": "review_gate", "dropped_reason": "drop_rule_cap"},
            1,
            None,
        ],
    )

    assert result["must_keep_candidate_count"] == 1
    assert result["must_keep_retained_count"] == 1
    assert result["drop_by_review_gate_count"] == 1
    assert result["drop_rule_cap_count"] == 1


def test_summarize_review_decision_counts_does_not_mutate_inputs() -> None:
    review_decision_table = [{"must_keep_candidate": True, "retained_final": True}]
    dropped_rows = [{"dropped_stage": "review_gate"}]
    final_duplicate_signatures = {"sig-a", "sig-b"}
    original_review_rows = [dict(row) for row in review_decision_table]
    original_dropped_rows = [dict(row) for row in dropped_rows]

    result = summarize_review_decision_counts(
        review_decision_table,
        dropped_rows,
        final_description_dedup_drop_signatures=final_duplicate_signatures,
    )

    assert result["drop_final_description_duplicate_count"] == 2
    assert review_decision_table == original_review_rows
    assert dropped_rows == original_dropped_rows
    assert final_duplicate_signatures == {"sig-a", "sig-b"}


def test_summarize_review_decision_counts_prefers_passed_llm_and_selector_counts() -> None:
    result = summarize_review_decision_counts(
        [],
        [
            {"dropped_stage": "review_llm"},
            {"dropped_stage": "review_llm"},
            {"dropped_stage": "review_selector"},
        ],
        drop_by_review_llm_count=7,
        drop_by_review_selector_count=8,
    )

    assert result["drop_by_review_llm_count"] == 7
    assert result["drop_by_review_selector_count"] == 8


def test_summarize_review_llm_drop_diagnostics_counts_omitted_signatures_without_final_drop() -> None:
    result = summarize_review_llm_drop_diagnostics(
        review_llm_applied=True,
        review_llm_omitted_signatures={"sig-a"},
        dropped_rows=[],
        review_llm_drop_reason_map={"sig-a": "low_value"},
        review_llm_drop_reason_raw_map={"sig-a": "LOW VALUE"},
        review_llm_drop_reason_source_map={"sig-a": "llm"},
        review_llm_drop_reason_evidence_map={},
        review_llm_runtime_debug={"final_source": "mapped_valid_payload"},
    )

    assert result["drop_by_review_llm_count"] == 1
    assert result["review_llm_drop_reason_breakdown"] == {"low_value": 1}
    assert result["review_llm_drop_reason_raw_breakdown"] == {"LOW VALUE": 1}
    assert result["review_llm_drop_reason_source_breakdown"] == {"llm": 1}
    assert result["llm_reason_coverage_ratio"] == 1.0


def test_summarize_review_llm_drop_diagnostics_marks_fallback_reason_incomplete_without_drop_reason() -> None:
    result = summarize_review_llm_drop_diagnostics(
        review_llm_applied=True,
        review_llm_omitted_signatures={"sig-a"},
        dropped_rows=[],
        review_llm_drop_reason_map={},
        review_llm_drop_reason_raw_map={},
        review_llm_drop_reason_source_map={},
        review_llm_drop_reason_evidence_map={},
        review_llm_runtime_debug={
            "final_source": "fallback_llm",
            "final_dropped_reason_payload_count": 0,
            "final_dropped_reason_count": 0,
        },
    )

    assert result["fallback_reason_incomplete"] is True
    assert result["runtime_debug_updates"]["fallback_reason_incomplete"] is True


def test_summarize_review_llm_drop_diagnostics_reports_repaired_reason_ratios() -> None:
    result = summarize_review_llm_drop_diagnostics(
        review_llm_applied=True,
        review_llm_omitted_signatures={"sig-a", "sig-b", "sig-c", "sig-d"},
        dropped_rows=[],
        review_llm_drop_reason_map={
            "sig-a": "low_value",
            "sig-b": "duplicate",
            "sig-c": "coverage_redundant",
            "sig-d": "high_signal_omitted",
        },
        review_llm_drop_reason_raw_map={
            "sig-a": "low_value",
            "sig-b": "duplicate",
            "sig-c": "",
            "sig-d": "coverage_redundant",
        },
        review_llm_drop_reason_source_map={
            "sig-a": "llm",
            "sig-b": "fallback_llm",
            "sig-c": "deterministic_backfill",
            "sig-d": "deterministic_backfill",
        },
        review_llm_drop_reason_evidence_map={},
        review_llm_runtime_debug={"final_source": "mapped_valid_payload"},
    )

    assert result["reason_source_breakdown"] == {
        "primary": 1,
        "fallback": 1,
        "backfill": 2,
    }
    assert result["llm_reason_coverage_ratio"] == 0.5
    assert result["fallback_reason_coverage_ratio"] == 0.25
    assert result["deterministic_backfill_ratio"] == 0.5
    assert result["final_reason_incomplete"] is False


def test_summarize_review_llm_drop_diagnostics_returns_fresh_mutable_values() -> None:
    runtime_debug = {"final_source": "mapped_valid_payload", "applied_reason": "mapped_valid_payload"}
    raw_reason_map = {"sig-a": "fallback"}

    first = summarize_review_llm_drop_diagnostics(
        review_llm_applied=True,
        review_llm_omitted_signatures={"sig-a"},
        dropped_rows=[],
        review_llm_drop_reason_map={"sig-a": "fallback_unspecified"},
        review_llm_drop_reason_raw_map=raw_reason_map,
        review_llm_drop_reason_source_map={"sig-a": "deterministic_backfill"},
        review_llm_drop_reason_evidence_map={"sig-a": {"has_positive_evidence": True}},
        review_llm_runtime_debug=runtime_debug,
    )
    first["review_llm_drop_reason_breakdown"]["fallback_unspecified"] = 99
    first["reason_source_breakdown"]["backfill"] = 99
    first["runtime_debug_updates"]["final_reason_incomplete"] = "changed"

    second = summarize_review_llm_drop_diagnostics(
        review_llm_applied=True,
        review_llm_omitted_signatures={"sig-a"},
        dropped_rows=[],
        review_llm_drop_reason_map={"sig-a": "fallback_unspecified"},
        review_llm_drop_reason_raw_map=raw_reason_map,
        review_llm_drop_reason_source_map={"sig-a": "deterministic_backfill"},
        review_llm_drop_reason_evidence_map={"sig-a": {"has_positive_evidence": True}},
        review_llm_runtime_debug=runtime_debug,
    )

    assert raw_reason_map == {"sig-a": "fallback"}
    assert runtime_debug == {"final_source": "mapped_valid_payload", "applied_reason": "mapped_valid_payload"}
    assert second["review_llm_drop_reason_breakdown"] == {"fallback_unspecified": 1}
    assert second["reason_source_breakdown"] == {"primary": 0, "fallback": 0, "backfill": 1}
    assert second["runtime_debug_updates"]["final_reason_incomplete"] is True
    assert second["fallback_with_positive_evidence_count"] == 1


def test_review_llm_drop_summary_fields_returns_review_decision_summary_fields() -> None:
    result = review_llm_drop_summary_fields(
        {
            "review_llm_drop_reason_breakdown": {"low_value": 2},
            "review_llm_drop_reason_raw_breakdown": {"LOW VALUE": 2},
            "review_llm_drop_reason_source_breakdown": {"llm": 1, "fallback_llm": 1},
            "fallback_reason_incomplete": True,
            "final_reason_incomplete": False,
            "final_reason_coverage_ratio": "0.75",
            "fallback_dropped_reason_count": "4",
            "fallback_dropped_reason_mapped_count": "3",
            "fallback_dropped_reason_unmapped_count": "1",
            "fallback_reason_coverage_ratio": "0.5",
            "llm_reason_coverage_ratio": "0.75",
            "deterministic_backfill_ratio": "0.25",
            "reason_source_breakdown": {"primary": 1, "fallback": 1, "backfill": 1},
            "fallback_with_positive_evidence_count": "2",
            "fallback_without_positive_evidence_count": "1",
            "runtime_debug_updates": {"final_reason_incomplete": True},
        },
        {
            "primary_reason_incomplete": 1,
            "primary_dropped_reason_count": "3",
            "primary_dropped_reason_payload_count": "5",
            "primary_reason_coverage_ratio": "0.6",
        },
    )

    assert result == {
        "review_llm_drop_reason_breakdown": {"low_value": 2},
        "review_llm_drop_reason_raw_breakdown": {"LOW VALUE": 2},
        "review_llm_drop_reason_source_breakdown": {"llm": 1, "fallback_llm": 1},
        "fallback_reason_incomplete": True,
        "final_reason_incomplete": False,
        "final_reason_coverage_ratio": 0.75,
        "fallback_dropped_reason_count": 4,
        "fallback_dropped_reason_mapped_count": 3,
        "fallback_dropped_reason_unmapped_count": 1,
        "fallback_reason_coverage_ratio": 0.5,
        "llm_reason_coverage_ratio": 0.75,
        "deterministic_backfill_ratio": 0.25,
        "reason_source_breakdown": {"primary": 1, "fallback": 1, "backfill": 1},
        "primary_reason_incomplete": True,
        "primary_dropped_reason_count": 3,
        "primary_dropped_reason_payload_count": 5,
        "primary_reason_coverage_ratio": 0.6,
        "fallback_with_positive_evidence_count": 2,
        "fallback_without_positive_evidence_count": 1,
    }
    assert "runtime_debug_updates" not in result


def test_review_llm_drop_summary_fields_uses_review_summary_defaults() -> None:
    result = review_llm_drop_summary_fields({}, None)

    assert result == {
        "review_llm_drop_reason_breakdown": {},
        "review_llm_drop_reason_raw_breakdown": {},
        "review_llm_drop_reason_source_breakdown": {},
        "fallback_reason_incomplete": False,
        "final_reason_incomplete": False,
        "final_reason_coverage_ratio": 0.0,
        "fallback_dropped_reason_count": 0,
        "fallback_dropped_reason_mapped_count": 0,
        "fallback_dropped_reason_unmapped_count": 0,
        "fallback_reason_coverage_ratio": 0.0,
        "llm_reason_coverage_ratio": 0.0,
        "deterministic_backfill_ratio": 0.0,
        "reason_source_breakdown": {},
        "primary_reason_incomplete": False,
        "primary_dropped_reason_count": 0,
        "primary_dropped_reason_payload_count": 0,
        "primary_reason_coverage_ratio": 0.0,
        "fallback_with_positive_evidence_count": 0,
        "fallback_without_positive_evidence_count": 0,
    }


def test_review_llm_drop_summary_fields_converts_primary_runtime_debug_fields() -> None:
    result = review_llm_drop_summary_fields(
        None,
        {
            "primary_reason_incomplete": "true",
            "primary_dropped_reason_count": "7",
            "primary_dropped_reason_payload_count": "9",
            "primary_reason_coverage_ratio": "0.7777",
        },
    )

    assert result["primary_reason_incomplete"] is True
    assert result["primary_dropped_reason_count"] == 7
    assert result["primary_dropped_reason_payload_count"] == 9
    assert result["primary_reason_coverage_ratio"] == 0.7777


def test_review_llm_drop_summary_fields_does_not_mutate_inputs() -> None:
    diagnostics = {
        "review_llm_drop_reason_breakdown": {"fallback_unspecified": 1},
        "review_llm_drop_reason_raw_breakdown": {"": 1},
        "review_llm_drop_reason_source_breakdown": {"deterministic_backfill": 1},
        "reason_source_breakdown": {"primary": 0, "fallback": 0, "backfill": 1},
        "fallback_with_positive_evidence_count": 1,
    }
    runtime_debug = {
        "primary_reason_incomplete": False,
        "primary_dropped_reason_count": 1,
        "primary_dropped_reason_payload_count": 2,
        "primary_reason_coverage_ratio": 0.5,
    }
    original_diagnostics = deepcopy(diagnostics)
    original_runtime_debug = deepcopy(runtime_debug)

    result = review_llm_drop_summary_fields(diagnostics, runtime_debug)
    result["review_llm_drop_reason_breakdown"]["fallback_unspecified"] = 99
    result["reason_source_breakdown"]["backfill"] = 99

    assert diagnostics == original_diagnostics
    assert runtime_debug == original_runtime_debug


def test_review_must_keep_reasons_collects_priority_rule_reuse_and_fact_reasons() -> None:
    case = _review_case(
        "TC-MUST-001",
        priority="P0",
        module="permission",
        description="REQ-100 permission approval release blocking main workflow",
        confirmed_fact_hits=["teacher role can approve"],
        meta={"confirmed_fact_hits": ["approval audit fact"]},
    )

    assert review_must_keep_reasons(
        case,
        {
            "reuse_risk_hit": True,
            "covered_rule_ids": ["REQ-100"],
            "core_rule_hits": ["REQ-100"],
            "missing_rule_hits": [],
            "unique_coverage_hits": [],
        },
        must_cover_rule_set={"REQ-100"},
    ) == [
        "priority_p0",
        "reuse_risk_hit",
        "must_cover_rule_hit",
        "confirmed_fact_hit",
    ]


def test_rank_review_case_for_fill_marks_real_rule_coverage_as_high_signal() -> None:
    covered_case = _review_case(
        "TC-RANK-001",
        priority="P2",
        module="order",
        description="order submit main workflow covers REQ-200",
    )
    low_signal_case = _review_case(
        "TC-RANK-002",
        priority="P2",
        module="profile",
        description="profile label display check",
        steps=["open profile", "check label"],
    )
    coverage_context = _coverage_context(
        (
            covered_case,
            {
                "covered_rule_ids": ["REQ-200"],
                "core_rule_hits": ["REQ-200"],
                "missing_rule_hits": [],
                "unique_coverage_hits": ["REQ-200"],
                "rule_risk_reasons": ["high"],
            },
        )
    )

    covered_rank = rank_review_case_for_fill(
        covered_case,
        coverage_context=coverage_context,
        rule_diagnostics={},
    )
    low_rank = rank_review_case_for_fill(
        low_signal_case,
        coverage_context=coverage_context,
        rule_diagnostics={},
    )

    assert is_high_signal(
        covered_case,
        {
            "missing_rule_hits": [],
            "core_rule_hits": ["REQ-200"],
            "unique_coverage_hits": ["REQ-200"],
            "rule_risk_reasons": ["high"],
            "coverage_gain_score": 13,
        },
    ) is True
    assert is_high_signal(
        low_signal_case,
        {
            "missing_rule_hits": [],
            "core_rule_hits": [],
            "unique_coverage_hits": [],
            "rule_risk_reasons": [],
            "coverage_gain_score": 0,
        },
    ) is False
    assert covered_rank[0] == 1
    assert covered_rank[2] == 1
    assert covered_rank[4] > 0
    assert covered_rank > low_rank


def test_resolve_review_llm_drop_reason_maps_protects_omitted_coverage_signal() -> None:
    selected_case = _review_case(
        "TC-LLM-001",
        priority="P1",
        module="permission",
        description="permission approval happy path is retained",
    )
    omitted_case = _review_case(
        "TC-LLM-002",
        priority="P1",
        module="permission",
        description="permission approval covers REQ-300 security audit",
    )
    omitted_signature = case_signature(omitted_case)
    coverage_context = _coverage_context(
        (
            omitted_case,
            {
                "covered_rule_ids": ["REQ-300"],
                "core_rule_hits": [],
                "missing_rule_hits": [],
                "unique_coverage_hits": ["REQ-300"],
                "rule_risk_reasons": ["high"],
            },
        )
    )

    reason_map, source_map, evidence_map = resolve_review_llm_drop_reason_maps(
        pool_cases=[selected_case, omitted_case],
        selected_cases=[selected_case],
        raw_drop_reason_map={omitted_signature: "low_value"},
        raw_drop_reason_origin_map={omitted_signature: "fallback_llm"},
        coverage_context=coverage_context,
        rule_diagnostics={},
    )

    assert reason_map[omitted_signature] == "coverage_protected_omitted"
    assert source_map[omitted_signature] == "fallback_llm"
    assert evidence_map[omitted_signature]["reason_adjusted_from"] == "low_value"
    assert evidence_map[omitted_signature]["reason_adjustment_rule"] == "llm_low_value_with_coverage_signal"
    assert evidence_map[omitted_signature]["has_coverage_signal"] is True
    assert evidence_map[omitted_signature]["unique_coverage_hits"] == ["REQ-300"]


def test_build_review_selection_constraints_derives_diversity_minima_from_cases() -> None:
    cases = [
        _review_case("TC-CON-001", priority="P0", module="permission", description="permission invalid access error"),
        _review_case("TC-CON-002", priority="P1", module="report", description="report dashboard state transition"),
        _review_case("TC-CON-003", priority="P1", module="order", description="order submit happy path"),
        _review_case("TC-CON-004", priority="P2", module="report", description="report export happy path"),
        _review_case("TC-CON-005", priority="P2", module="order", description="order min boundary validation"),
        _review_case("TC-CON-006", priority="P1", module="permission", description="role permission grant happy path"),
        _review_case("TC-CON-007", priority="P2", module="order", description="order cancel state transition"),
        _review_case("TC-CON-008", priority="P2", module="profile", description="profile invalid email error"),
        _review_case("TC-CON-009", priority="P1", module="report", description="analytics metric normal refresh"),
        _review_case("TC-CON-010", priority="P2", module="order", description="order normal query success"),
    ]

    constraints = build_review_selection_constraints(
        cases,
        reference_count=10,
        generation_profile={"coverage_mode": "standard_regression"},
    )

    assert constraints["priority_min"] == {"P0": 1, "P1": 2, "P2": 1}
    assert constraints["scenario_min"] == {"happy": 1, "state": 1, "exception": 1}
    assert constraints["domain_min"] == {"permission": 1, "report": 1}
    assert constraints["target_min_count"] == 10
    assert constraints["target_max_count"] == 10


def test_enforce_review_selection_constraints_fills_priority_scenario_and_domain_gaps() -> None:
    selected_case = _review_case(
        "TC-ENF-001",
        priority="P1",
        module="order",
        description="order submit happy path",
    )
    permission_exception_case = _review_case(
        "TC-ENF-002",
        priority="P0",
        module="permission",
        description="permission invalid access exception error",
    )
    report_state_case = _review_case(
        "TC-ENF-003",
        priority="P2",
        module="report",
        description="report dashboard state transition after refresh",
    )
    filler_case = _review_case(
        "TC-ENF-004",
        priority="P2",
        module="profile",
        description="profile label display check",
    )
    constraints = {
        "target_min_count": 3,
        "target_max_count": 3,
        "priority_min": {"P0": 1, "P1": 1, "P2": 1},
        "scenario_min": {"exception": 1, "state": 1},
        "domain_min": {"permission": 1, "report": 1},
    }

    enforced, reason_map = enforce_review_selection_constraints(
        selected_cases=[selected_case],
        pool_cases=[selected_case, filler_case, report_state_case, permission_exception_case],
        constraints=constraints,
        coverage_context=_coverage_context(),
        rule_diagnostics={},
        rank_case_fn=rank_review_case_for_fill,
    )

    assert [case["id"] for case in enforced] == ["TC-ENF-001", "TC-ENF-002", "TC-ENF-003"]
    assert reason_map[case_signature(permission_exception_case)] == "retained_by_constraint_priority_P0"
    assert reason_map[case_signature(report_state_case)] == "retained_by_constraint_priority_P2"
    assert case_signature(filler_case) not in reason_map


def test_build_review_decision_summary_payload_preserves_summary_counts_and_merge_priority() -> None:
    review_decision_table = [
        {
            "case_id": "TC-SUM-001",
            "retained_final": True,
            "case_quality": "valid_case",
            "invalid_case_reason": "",
        },
        {
            "case_id": "TC-SUM-002",
            "retained_final": False,
            "case_quality": "invalid_case",
            "invalid_case_reason": "reasoning_leakage",
        },
        {
            "case_id": "TC-SUM-003",
            "retained_final": True,
            "case_quality": "invalid_case",
            "invalid_case_reason": "missing_expected_result",
        },
    ]
    dropped_rows = [
        {"case_id": "TC-DROP-001", "dropped_stage": "review_gate"},
        {"case_id": "TC-DROP-002", "dropped_stage": "review_llm"},
    ]
    parsed_result = [
        {"case_id": "TC-FINAL-001", "description": "normal case"},
        {"case_id": "TC-FINAL-002", "description": "contains reasoning leakage marker"},
        "ignored non dict",
    ]
    runtime_debug = {"final_source": "review_selector", "nested": {"kept": True}}

    summary = build_review_decision_summary_payload(
        review_decision_table=review_decision_table,
        dropped_rows=dropped_rows,
        review_flow_summary_fields={"flow_conflict_count": 4},
        parsed_result=parsed_result,
        reasoning_leakage_hits_fn=lambda case: "leakage" in str(case.get("description") or ""),
        priority_summary_fields={"priority_conflict_count": 2},
        needs_priority_review=False,
        review_llm_applied=True,
        review_selection_input=[{"case_id": "IN-1"}, "ignored", {"case_id": "IN-2"}],
        dict_case_count_fn=_count_dict_cases,
        review_selected_count=2,
        review_target_min_count=None,
        review_target_max_count=None,
        review_shortfall_detected=True,
        review_shortfall_before_count=1,
        review_shortfall_recovered_count=1,
        review_post_rerank_floor_count=None,
        review_post_rerank_recovered_count=None,
        final_target_floor_count=None,
        final_floor_recovery_attempted=True,
        final_floor_recovery_applied=False,
        final_floor_recovered_count=None,
        final_floor_recovery_reason=None,
        final_confirmed_conflict_drop_count=None,
        final_shortfall_supplement_attempted=True,
        final_shortfall_supplement_applied=True,
        final_shortfall_supplement_count=3,
        final_shortfall_supplement_reason="expected_count_floor",
        final_shortfall_supplement_debug={"batches": [{"response_chars": 100}]},
        generation_mode="standard_regression",
        effective_generation_coverage_mode_source="explicit",
        explicit_generation_mode_override=True,
        explicit_expected_count_floor_preserved=True,
        review_fill_source=None,
        review_llm_selected_signatures={"sig-a", "sig-b"},
        review_llm_runtime_debug=runtime_debug,
        review_constraint_retained_signatures={"sig-c"},
        review_llm_summary_fields={"final_reason_incomplete": False},
        review_llm_pool_count=5,
        stage_counts={"primary": 6, "gap": 2},
        review_decision_counts={
            "candidate_total": 99,
            "retained_total": 88,
            "dropped_total": 77,
            "drop_by_review_gate_count": 1,
        },
    )

    assert summary["candidate_total"] == 99
    assert summary["retained_total"] == 88
    assert summary["dropped_total"] == 77
    assert summary["invalid_case_count"] == 2
    assert summary["reasoning_leakage_case_count"] == 1
    assert summary["final_reasoning_leakage_case_count"] == 1
    assert summary["review_input_size"] == 2
    assert summary["review_target_min_count"] == 1
    assert summary["review_target_max_count"] == 1
    assert summary["review_post_rerank_floor_count"] == 1
    assert summary["final_target_floor_count"] == 0
    assert summary["final_floor_recovery_reason"] == ""
    assert summary["final_confirmed_conflict_drop_count"] == 0
    assert summary["final_shortfall_supplement_debug"] == {"batches": [{"response_chars": 100}]}
    assert summary["review_fill_source"] == "none"
    assert summary["candidate_by_pass"] == {"primary": 6, "gap": 2}
    assert summary["drop_by_review_gate_count"] == 1
    assert summary["review_llm_runtime_debug"] == runtime_debug
    assert summary["review_llm_runtime_debug"] is not runtime_debug


def test_build_review_decision_summary_payload_preserves_explicit_target_max_and_runtime_debug_copy() -> None:
    runtime_debug = {"final_source": "fallback_llm", "final_dropped_reason_count": 2}

    summary = build_review_decision_summary_payload(
        review_decision_table=[{"case_id": "TC-SUM-010", "retained_final": True}],
        dropped_rows=[],
        review_flow_summary_fields={},
        parsed_result=[],
        reasoning_leakage_hits_fn=lambda case: bool(case.get("leakage")),
        priority_summary_fields={},
        needs_priority_review=True,
        review_llm_applied=False,
        review_selection_input=[{"case_id": "IN-10"}],
        dict_case_count_fn=_count_dict_cases,
        review_selected_count=None,
        review_target_min_count=3,
        review_target_max_count=5,
        review_shortfall_detected=False,
        review_shortfall_before_count=0,
        review_shortfall_recovered_count=0,
        review_post_rerank_floor_count=4,
        review_post_rerank_recovered_count=2,
        final_target_floor_count=6,
        final_floor_recovery_attempted=False,
        final_floor_recovery_applied=False,
        final_floor_recovered_count=0,
        final_floor_recovery_reason="",
        final_confirmed_conflict_drop_count=2,
        final_shortfall_supplement_attempted=False,
        final_shortfall_supplement_applied=False,
        final_shortfall_supplement_count=0,
        final_shortfall_supplement_reason="",
        final_shortfall_supplement_debug={},
        generation_mode=None,
        effective_generation_coverage_mode_source=None,
        explicit_generation_mode_override=False,
        explicit_expected_count_floor_preserved=False,
        review_fill_source="constraint",
        review_llm_selected_signatures=[],
        review_llm_runtime_debug=runtime_debug,
        review_constraint_retained_signatures=[],
        review_llm_summary_fields={},
        review_llm_pool_count=0,
        stage_counts={},
        review_decision_counts={},
    )

    runtime_debug["final_source"] = "mutated"

    assert summary["candidate_total"] == 1
    assert summary["retained_total"] == 1
    assert summary["dropped_total"] == 0
    assert summary["review_target_min_count"] == 3
    assert summary["review_target_max_count"] == 5
    assert summary["review_post_rerank_floor_count"] == 4
    assert summary["review_post_rerank_recovered_count"] == 2
    assert summary["final_target_floor_count"] == 6
    assert summary["final_confirmed_conflict_drop_count"] == 2
    assert summary["review_llm_runtime_debug"]["final_source"] == "fallback_llm"
