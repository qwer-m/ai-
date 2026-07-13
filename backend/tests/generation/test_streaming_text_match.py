from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_text_match import (
    CaseGovernanceMatcher,
    build_quality_hint_keywords,
    normalize_match_patterns,
    normalize_match_text,
)


def test_normalize_match_text_keeps_ascii_digits_underscore_and_cjk() -> None:
    assert normalize_match_text("  P0-main path: 保存/提交 #1  ") == "p0mainpath保存提交1"


def test_normalize_match_patterns_deduplicates_after_normalization() -> None:
    assert normalize_match_patterns(["保存-提交", "保存 提交", "", None, "权限"]) == [
        "保存提交",
        "权限",
    ]


def test_build_quality_hint_keywords_extracts_unique_tokens_in_order() -> None:
    assert build_quality_hint_keywords(["保存提交后验证下游展示", "P0 main-flow state sync"]) == [
        "保存提交后验证下游展示",
        "p0mainflowstatesync",
    ]


def test_case_governance_matcher_matches_control_state_patterns() -> None:
    matcher = CaseGovernanceMatcher.from_raw(
        forbidden_patterns=["只检查静态文案"],
        reuse_risks=["旧链路返回风险"],
        soft_constraints=["避免重复提交"],
        quality_fix_hints=["保存提交后验证下游展示"],
    )

    assert matcher.violates_forbidden_pattern({"description": "只检查静态文案和布局"}) is True
    assert matcher.hits_soft_constraint({"expected_result": "避免重复提交导致重复生成"}) is True
    assert matcher.hits_reuse_risk({"steps": ["覆盖旧链路返回风险"]}) is True
    assert matcher.satisfies_quality_hint({"expected_result": "保存提交后验证下游展示最新状态"}) is True


def test_case_governance_matcher_accepts_priority_score_reuse_risk() -> None:
    matcher = CaseGovernanceMatcher.from_raw()

    assert matcher.hits_reuse_risk({}, {"reuse_risk_hit": True}) is True
    assert matcher.hits_reuse_risk({}, {"reuse_risk_hit": False}) is False
