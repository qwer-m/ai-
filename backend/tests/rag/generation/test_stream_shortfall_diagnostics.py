from __future__ import annotations

from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    stream_postprocess_cases,
)
from modules.testing.test_generation_components.postprocess import result_postprocess_streaming_impl


class _SinglePassClient:
    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs):  # noqa: ANN001
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001
        yield "[]"


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def test_stream_postprocess_exposes_convergence_debug_when_under_reference_count() -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {"id":"TC-001","description":"case-1","test_module":"module-a","preconditions":[],"steps":["s1"],"test_input":"i1","expected_result":"ok1","priority":"P1"},
      {"id":"TC-002","description":"case-2","test_module":"module-a","preconditions":[],"steps":["s2"],"test_input":"i2","expected_result":"ok2","priority":"P1"}
    ]
    """

    gen = stream_postprocess_cases(
        client=client,
        requirement="",
        base_prompt="BASE",
        kb_context="",
        full_content=full_content,
        expected_count=5,
        append=False,
        existing_cases=[],
        existing_unique_count=0,
        start_id=1,
        db=None,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        count_unique_test_cases_fn=count_unique_test_cases,
        infer_case_kind_fn=infer_case_kind,
        build_supplement_closed_loop_instruction_fn=lambda **_: "",
        multi_pass=False,
        generation_mode="single_pass",
    )
    result = _drain_with_return(gen)

    assert isinstance(result, dict)
    convergence_debug = dict((result or {}).get("convergence_debug") or {})
    assert convergence_debug.get("suggested_count") == 5
    assert convergence_debug.get("final_count") == 2
    assert convergence_debug.get("reference_gap") == 3
    assert convergence_debug.get("converged") is True
    reasons = set(convergence_debug.get("reasons") or [])
    assert "quality_converged_before_reference_count" in reasons
    generation_summary = dict((result or {}).get("generation_summary") or {})
    assert generation_summary.get("recommended_range") == "30-50"
    assert generation_summary.get("final_count") == 2
    assert generation_summary.get("status") == "completed_with_optimal_set"
    stop_reason = set(generation_summary.get("stop_reason") or [])
    assert "coverage_satisfied" in stop_reason
    assert "stopped_due_to_diminishing_returns" in stop_reason
    assert "optimal_case_set_reached" in stop_reason
    review_summary = dict((result or {}).get("review_decision_summary") or {})
    review_table = list((result or {}).get("review_decision_table") or [])
    assert review_summary.get("candidate_total") == 2
    assert review_summary.get("retained_total") == 2
    assert review_summary.get("drop_no_new_signal_count") == 0
    assert review_summary.get("review_shortfall_detected") is True
    assert review_summary.get("review_post_rerank_floor_count") == 2
    assert review_summary.get("review_post_rerank_recovered_count") == 1
    assert review_summary.get("review_fill_source") == "post_rerank_recovery"
    assert len(review_table) == 2
    assert all("dropped_reason" in row for row in review_table if isinstance(row, dict))


def test_stream_postprocess_collects_review_drop_reasons() -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {"id":"TC-001","description":"happy case 1","test_module":"module-a","preconditions":[],"steps":["s1"],"test_input":"i1","expected_result":"ok1","priority":"P2"},
      {"id":"TC-002","description":"happy case 2","test_module":"module-a","preconditions":[],"steps":["s2"],"test_input":"i2","expected_result":"ok2","priority":"P2"},
      {"id":"TC-003","description":"happy case 3","test_module":"module-a","preconditions":[],"steps":["s3"],"test_input":"i3","expected_result":"ok3","priority":"P2"}
    ]
    """

    gen = stream_postprocess_cases(
        client=client,
        requirement="",
        base_prompt="BASE",
        kb_context="",
        full_content=full_content,
        expected_count=5,
        append=False,
        existing_cases=[],
        existing_unique_count=0,
        start_id=1,
        db=None,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        count_unique_test_cases_fn=count_unique_test_cases,
        infer_case_kind_fn=infer_case_kind,
        build_supplement_closed_loop_instruction_fn=lambda **_: "",
        multi_pass=False,
        generation_mode="single_pass",
    )
    result = _drain_with_return(gen)

    review_summary = dict((result or {}).get("review_decision_summary") or {})
    review_table = [row for row in (result or {}).get("review_decision_table") or [] if isinstance(row, dict)]
    assert review_summary.get("candidate_total") == 3
    assert review_summary.get("retained_total") == 2
    assert review_summary.get("drop_no_new_signal_count") == 1
    assert review_summary.get("drop_by_review_gate_count") == 1
    assert review_summary.get("review_shortfall_detected") is True
    assert review_summary.get("review_post_rerank_floor_count") == 2
    assert review_summary.get("review_post_rerank_recovered_count") == 1
    assert review_summary.get("review_fill_source") == "post_rerank_recovery"
    assert len(review_table) == 3
    dropped = [row for row in review_table if not bool(row.get("retained_final"))]
    assert len(dropped) == 1
    assert all(row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal" for row in dropped)


def test_review_gate_retains_case_when_coverage_value_exists(monkeypatch) -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {"id":"TC-001","description":"happy case 1","test_module":"module-a","preconditions":[],"steps":["s1"],"test_input":"i1","expected_result":"ok1","priority":"P2"},
      {"id":"TC-002","description":"happy case 2","test_module":"module-a","preconditions":[],"steps":["s2"],"test_input":"i2","expected_result":"ok2","priority":"P2"}
    ]
    """

    origin = result_postprocess_streaming_impl.score_case_priority

    def _fake_score(case, coverage_context=None, rule_diagnostics=None):  # noqa: ANN001
        _ = coverage_context, rule_diagnostics
        if str((case or {}).get("id") or "").strip() == "TC-002":
            return {
                "missing_rule_hits": [],
                "core_rule_hits": ["RULE-030"],
                "covered_rule_ids": ["RULE-030"],
                "coverage_gain_score": 18,
                "priority_score": 18,
            }
        return {
            "missing_rule_hits": [],
            "core_rule_hits": [],
            "covered_rule_ids": [],
            "coverage_gain_score": 0,
            "priority_score": 0,
        }

    monkeypatch.setattr(result_postprocess_streaming_impl, "score_case_priority", _fake_score)
    try:
        gen = stream_postprocess_cases(
            client=client,
            requirement="",
            base_prompt="BASE",
            kb_context="",
            full_content=full_content,
            expected_count=5,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
        )
        result = _drain_with_return(gen)
    finally:
        monkeypatch.setattr(result_postprocess_streaming_impl, "score_case_priority", origin)

    review_summary = dict((result or {}).get("review_decision_summary") or {})
    review_table = [row for row in (result or {}).get("review_decision_table") or [] if isinstance(row, dict)]
    assert review_summary.get("candidate_total") == 2
    assert review_summary.get("retained_total") == 2
    assert review_summary.get("drop_no_new_signal_count") == 0
    assert review_summary.get("retained_due_to_coverage_value_count") == 1
    retained_coverage_rows = [row for row in review_table if row.get("retained_reason") == "retained_due_to_coverage_value"]
    assert len(retained_coverage_rows) == 1
    assert retained_coverage_rows[0].get("case_id") == "TC-002"


def test_completion_word_does_not_trigger_week_flow_p0_hard_drop() -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {
        "id":"TC-001",
        "description":"验证完成选词填空后返回首页",
        "test_module":"学习路径",
        "preconditions":["用户已进入选词填空页面"],
        "steps":["完成选词填空","点击完成"],
        "test_input":"正常学习流程",
        "expected_result":"页面跳转至首页，且学习路径状态标记为已完成",
        "priority":"P1"
      }
    ]
    """

    gen = stream_postprocess_cases(
        client=client,
        requirement="用户完成选词填空后返回首页",
        base_prompt="BASE",
        kb_context="",
        full_content=full_content,
        expected_count=5,
        append=False,
        existing_cases=[],
        existing_unique_count=0,
        start_id=1,
        db=None,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        count_unique_test_cases_fn=count_unique_test_cases,
        infer_case_kind_fn=infer_case_kind,
        build_supplement_closed_loop_instruction_fn=lambda **_: "",
        multi_pass=False,
        generation_mode="single_pass",
    )
    result = _drain_with_return(gen)

    cases = [row for row in (result or {}).get("cases") or [] if isinstance(row, dict)]
    assert len(cases) >= 1
    convergence_debug = dict((result or {}).get("convergence_debug") or {})
    assert convergence_debug.get("low_quality_dropped_count") == 0
