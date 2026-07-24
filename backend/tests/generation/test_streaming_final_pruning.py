from __future__ import annotations

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.test_generation_components.postprocess.json_validator import reorder_cases_by_closed_loop
from modules.test_generation_components.postprocess.streaming_final_pruning import (
    apply_post_judge_final_pruning,
)
from modules.test_generation_components.postprocess.streaming_review_selection import (
    rank_review_case_for_fill,
)


def _schedule_case(
    case_id: str,
    *,
    description: str,
    expected_result: str,
    priority: str = "P1",
) -> dict[str, object]:
    return {
        "id": case_id,
        "test_module": "课程排课",
        "description": description,
        "test_input": f"{case_id} 的排课数据",
        "expected_result": expected_result,
        "priority": priority,
        "steps": ["打开课程排课页面", "填写课程时间", "保存排课"],
        "preconditions": ["教师已登录并具备排课权限"],
        "meta": {"source": "review"},
        "priority_conflict_reason": "debug-only",
    }


def test_post_judge_final_pruning_records_text_quality_diagnostic_without_deleting() -> None:
    duplicate_case = _schedule_case(
        "TC-SCHEDULE-002",
        description="保存课程排课后生成有效记录",
        expected_result="系统保存课程排课记录并展示保存成功提示",
    )
    low_quality_drop_details: list[dict[str, object]] = []

    result = apply_post_judge_final_pruning(
        requirement="课程排课系统需要支持保存排课，并在保存失败时展示明确错误原因。",
        parsed_result=[
            _schedule_case(
                "TC-SCHEDULE-001",
                description="保存课程排课后生成有效记录",
                expected_result="系统保存课程排课记录并展示保存成功提示",
                priority="P0",
            ),
            duplicate_case,
            _schedule_case(
                "TC-SCHEDULE-003",
                description="保存课程排课失败时展示错误原因",
                expected_result="系统阻止保存并展示具体失败原因",
                priority="P2",
            ),
            _schedule_case(
                "TC-SCHEDULE-004",
                description="保存课程排课返回泛化提示",
                expected_result="result is as configured",
            ),
        ],
        low_quality_drop_details=low_quality_drop_details,
        append_final_cap_count=0,
        start_id=10,
        analyze_coverage_fn=analyze_coverage,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        rank_case_fn=rank_review_case_for_fill,
    )

    assert result.final_quality_diagnostic_total == 1
    assert result.final_quality_drop_total == 0
    assert low_quality_drop_details[0]["stage"] == "post_judge_quality_diagnostic"
    assert low_quality_drop_details[0]["case_id"] == "TC-SCHEDULE-004"
    assert low_quality_drop_details[0]["diagnostic_only"] is True
    assert result.final_description_dedup_drop_signatures == set()
    assert result.append_cap_drop_total == 0
    assert result.append_cap_drop_signatures == set()
    assert len(result.cases) == 4
    assert result.pre_priority_coverage
    assert all("meta" not in case for case in result.cases)
    assert all("priority_conflict_reason" not in case for case in result.cases)
