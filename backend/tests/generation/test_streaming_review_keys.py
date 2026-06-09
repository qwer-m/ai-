from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_review_keys import (
    review_domain,
    review_scenario,
)


def test_review_scenario_maps_bucket_kinds_to_review_groups() -> None:
    assert review_scenario({"description": "normal path"}) == "happy"
    assert review_scenario({"description": "状态流转"}) == "state"
    assert review_scenario({"description": "异常拒绝"}) == "exception"
    assert review_scenario({"description": "最大值边界"}) == "exception"
    assert review_scenario({"description": "权限校验"}) == "exception"


def test_review_domain_detects_permission_report_and_general_text() -> None:
    assert review_domain({"description": "角色权限校验"}) == "permission"
    assert review_domain({"test_module": "Dashboard", "expected_result": "metric refreshes"}) == "report"
    assert review_domain({"description": "课程排课成功"}) == "general"


def test_review_domain_uses_steps_and_input_text() -> None:
    assert review_domain({"steps": ["admin authorize request"]}) == "permission"
    assert review_domain({"test_input": "报表日期范围"}) == "report"
