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


def test_append_mode_caps_final_new_cases_to_requested_delta() -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {"id":"TC-095","description":"新增学习计划入口跳转验证","test_module":"学习计划","preconditions":["用户已登录"],"steps":["点击学习计划入口"],"test_input":"无","expected_result":"页面跳转到学习计划首页并显示本周任务模块","priority":"P1"},
      {"id":"TC-096","description":"新增排行榜标题展示验证","test_module":"学习计划","preconditions":["用户已登录"],"steps":["打开学习计划首页"],"test_input":"无","expected_result":"页面固定展示本周学习时长排行榜标题和Top5列表","priority":"P2"},
      {"id":"TC-097","description":"新增历史课程复习入口验证","test_module":"学习计划","preconditions":["存在历史课程"],"steps":["点击历史课程复习按钮"],"test_input":"无","expected_result":"页面进入所选历史课程的复习流程","priority":"P2"}
    ]
    """

    gen = stream_postprocess_cases(
        client=client,
        requirement="学习计划首页需要支持入口跳转、排行榜展示、历史课程复习",
        base_prompt="BASE",
        kb_context="",
        full_content=full_content,
        expected_count=95,
        append=True,
        existing_cases=[
            {
                "id": "TC-001",
                "description": "已有用例",
                "test_module": "已有模块",
                "preconditions": [],
                "steps": ["执行已有流程"],
                "test_input": "无",
                "expected_result": "已有流程结果可验证",
                "priority": "P1",
            }
        ],
        existing_unique_count=94,
        start_id=95,
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

    cases = list((result or {}).get("cases") or [])
    convergence_debug = dict((result or {}).get("convergence_debug") or {})
    review_table = [row for row in (result or {}).get("review_decision_table") or [] if isinstance(row, dict)]

    assert len(cases) == 1
    assert convergence_debug.get("append_target_count") == 1
    assert convergence_debug.get("append_final_cap_count") == 1
    assert convergence_debug.get("append_cap_drop_count") >= 1
    assert any(row.get("dropped_stage") == "append_target_cap" for row in review_table)


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


def test_hidden_wrong_question_entry_does_not_trigger_wrong_collection_p0_hard_drop() -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {
        "id":"TC-001",
        "description":"验证二轮复习课程详情页隐藏讲知识错题入口",
        "test_module":"二轮复习课程详情页",
        "preconditions":["用户已进入二轮复习课程详情页"],
        "steps":["打开左侧导航","检查导航入口列表"],
        "test_input":"二轮复习课程",
        "expected_result":"左侧导航不显示讲知识错题入口，仅保留学&练相关课程目录节点",
        "priority":"P1"
      }
    ]
    """

    gen = stream_postprocess_cases(
        client=client,
        requirement="二轮复习课程详情页隐藏讲知识错题等流程入口，仅保留学&练。",
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
    assert convergence_debug.get("governance_hard_drop_count") == 0


def test_missing_required_p0_group_does_not_hard_drop_all_candidates() -> None:
    client = _SinglePassClient()
    full_content = """
    [
      {
        "id":"TC-001",
        "description":"Validate general course page entry layout remains available",
        "test_module":"course page",
        "preconditions":["User has logged in and can open the course page"],
        "steps":["Open the course page","Check the primary entry area"],
        "test_input":"normal course page",
        "expected_result":"The primary entry area is visible and links to the course detail page",
        "priority":"P1"
      }
    ]
    """

    gen = stream_postprocess_cases(
        client=client,
        requirement="The payment gate and subscription flow must be covered for unpaid users.",
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
    assert len(cases) == 1
    convergence_debug = dict((result or {}).get("convergence_debug") or {})
    assert convergence_debug.get("governance_hard_drop_count") == 0
