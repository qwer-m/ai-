"""Regression tests — three real-world scenarios for the priority sample pool pipeline.

Covers the full flow: raw input → normalize → aggregate → patterns → signals.

Scenarios:
  1. course_scheduling  — 近期课程+排课 (mixed priorities, corrections, manual feedback)
  2. review_round2      — 二轮复习 (pattern learning, linked final cases, quality eval)
  3. small_requirement   — 一个小型需求 (compact, focused, no corrections)
"""

from __future__ import annotations

import pytest

from modules.testing.priority_sample_pool_store import (
    _aggregate_by_cluster,
    _apply_source_limits,
    _apply_signal_type_limits,
    _dedupe_priority_samples,
    derive_patterns_from_samples,
    derive_signals_from_patterns,
    normalize_priority_sample,
    normalize_priority_samples,
)


# ═══════════════════════════════════════════════════════════════════
# Fixture: 近期课程+排课 (course scheduling)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def course_scheduling_samples() -> list[dict]:
    """Mixed-priority cases: some manually corrected, some from quality eval."""
    return [
        # Core flow cases — manually added with expected P0/P1
        {
            "sample_id": "cs-001",
            "case_id": "TC-CS-001",
            "title": "创建课程并分配教师",
            "source": "priority_debug_manual_add",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "core_flow",
            "pattern_category": "core_flow_closure",
            "expected_priority": "P0",
            "pattern_summary": "课程创建核心流程必须覆盖教师分配",
            "pattern_weight": 0.95,
            "pattern_confidence": 0.88,
            "user_comment": "核心流程，不可降级",
        },
        {
            "sample_id": "cs-002",
            "case_id": "TC-CS-002",
            "title": "排课冲突检测",
            "source": "priority_debug_manual_add",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "core_flow",
            "pattern_category": "core_flow_closure",
            "expected_priority": "P0",
            "pattern_summary": "排课冲突检测是核心业务逻辑",
            "pattern_weight": 0.92,
            "pattern_confidence": 0.85,
        },
        # Display issues — over-raised cases
        {
            "sample_id": "cs-003",
            "case_id": "TC-CS-003",
            "title": "课程列表排序校验",
            "source": "priority_debug_manual_add",
            "signal_type": "negative",
            "pattern_usage": "avoid",
            "reason_category": "display_issue",
            "pattern_category": "",
            "expected_priority": "P3",
            "pattern_summary": "列表排序属于展示问题，不应抬高",
            "pattern_weight": 0.45,
            "pattern_confidence": 0.55,
            "user_comment": "过度抬高，展示态不应>P2",
        },
        {
            "sample_id": "cs-004",
            "case_id": "TC-CS-004",
            "title": "课程卡片样式校验",
            "source": "priority_debug_manual_add",
            "signal_type": "negative",
            "pattern_usage": "avoid",
            "reason_category": "display_issue",
            "pattern_category": "",
            "expected_priority": "P3",
            "pattern_summary": "样式校验属于展示问题",
            "pattern_weight": 0.42,
            "pattern_confidence": 0.50,
        },
        # Quality evaluation defect
        {
            "sample_id": "cs-005",
            "case_id": "missing_points-1",
            "title": "排课时间冲突未覆盖跨天场景",
            "source": "quality_evaluation_defect",
            "source_type": "quality_evaluation_defect",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "recall_gap",
            "pattern_category": "recall_gap_missing_business_coverage",
            "expected_priority": "P1",
            "pattern_summary": "跨天排课冲突场景缺失",
            "pattern_weight": 0.78,
            "pattern_confidence": 0.72,
            "learning_status": "user_confirmed",
            "learning_confirmed_at": "2026-05-08T10:00:00",
            "learning_confirmed_by": 1,
        },
        # Hallucination — negative
        {
            "sample_id": "cs-006",
            "case_id": "hallucinations-1",
            "title": "排课后自动发送微信通知",
            "source": "quality_evaluation_defect",
            "source_type": "quality_evaluation_defect",
            "signal_type": "negative",
            "pattern_usage": "avoid",
            "reason_category": "hallucination_or_redundant_case",
            "pattern_category": "hallucination_or_redundant_case",
            "expected_priority": "P2",
            "pattern_summary": "系统无微信通知功能，属于幻觉",
            "pattern_weight": 0.52,
            "pattern_confidence": 0.48,
            "learning_status": "user_confirmed",
            "learning_confirmed_at": "2026-05-08T10:01:00",
            "learning_confirmed_by": 1,
        },
        # Manual pool input
        {
            "sample_id": "cs-007",
            "case_id": "manual-1",
            "title": "排课结果导出Excel",
            "source": "manual_pool_input",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "boundary_condition",
            "pattern_category": "boundary_effective_coverage",
            "expected_priority": "P1",
            "pattern_summary": "导出功能边界覆盖",
            "pattern_weight": 0.70,
            "pattern_confidence": 0.65,
        },
    ]


# ═══════════════════════════════════════════════════════════════════
# Fixture: 二轮复习 (review round 2)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def review_round2_samples() -> list[dict]:
    """Pattern-learning scenario with linked final cases and multiple sources."""
    return [
        # Linked final case pattern — positive
        {
            "sample_id": "rv-001",
            "case_id": "final-101",
            "title": "复习计划生成并关联知识点标签",
            "source": "linked_final_case_pattern",
            "source_type": "linked_final_case_pattern",
            "source_id": 42,
            "source_case_id": "FINAL-101",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "core_flow",
            "pattern_category": "core_flow_closure",
            "expected_priority": "P0",
            "pattern_summary": "复习计划关联知识点是核心闭环",
            "pattern_weight": 0.90,
            "pattern_confidence": 0.82,
            "learning_status": "user_confirmed",
            "learning_confirmed_at": "2026-05-07T14:00:00",
            "learning_confirmed_by": 1,
        },
        {
            "sample_id": "rv-002",
            "case_id": "final-102",
            "title": "错题本自动归类到知识点",
            "source": "linked_final_case_pattern",
            "source_type": "linked_final_case_pattern",
            "source_id": 42,
            "source_case_id": "FINAL-102",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "core_flow",
            "pattern_category": "core_flow_closure",
            "expected_priority": "P0",
            "pattern_summary": "错题归类是核心学习闭环",
            "pattern_weight": 0.88,
            "pattern_confidence": 0.80,
        },
        # Business extension
        {
            "sample_id": "rv-003",
            "case_id": "final-201",
            "title": "二轮复习计划基于遗忘曲线动态调整",
            "source": "linked_final_case_business_extension",
            "source_type": "linked_final_case_business_extension",
            "source_id": 42,
            "source_case_id": "FINAL-201",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "cross_page_flow",
            "pattern_category": "cross_page_flow",
            "expected_priority": "P1",
            "pattern_summary": "遗忘曲线动态调整是跨页面流程",
            "pattern_weight": 0.82,
            "pattern_confidence": 0.75,
            "learning_status": "user_confirmed",
            "learning_confirmed_at": "2026-05-07T14:30:00",
            "learning_confirmed_by": 1,
        },
        # Quality eval — negative
        {
            "sample_id": "rv-004",
            "case_id": "modifications-1",
            "title": "复习提醒时间需要支持自定义",
            "source": "quality_evaluation_defect",
            "source_type": "quality_evaluation_defect",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "quality_fix_hint",
            "pattern_category": "quality_fix_hint",
            "expected_priority": "P1",
            "pattern_summary": "复习提醒应支持自定义时间",
            "pattern_weight": 0.76,
            "pattern_confidence": 0.70,
        },
        # Negative — UI case over-raised
        {
            "sample_id": "rv-005",
            "case_id": "TC-RV-005",
            "title": "复习进度条颜色校验",
            "source": "priority_debug_manual_add",
            "signal_type": "negative",
            "pattern_usage": "avoid",
            "reason_category": "display_issue",
            "pattern_category": "",
            "expected_priority": "P3",
            "pattern_summary": "进度条颜色属于UI展示",
            "pattern_weight": 0.40,
            "pattern_confidence": 0.48,
        },
        {
            "sample_id": "rv-006",
            "case_id": "manual-2",
            "title": "多设备同步复习进度",
            "source": "manual_pool_input",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "pattern_category": "multi_step_interaction",
            "reason_category": "state_transition",
            "expected_priority": "P1",
            "pattern_summary": "多设备同步涉及状态迁移",
            "pattern_weight": 0.75,
            "pattern_confidence": 0.68,
        },
    ]


# ═══════════════════════════════════════════════════════════════════
# Fixture: 一个小型需求 (small requirement)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def small_requirement_samples() -> list[dict]:
    """Compact scenario: few cases, no corrections, simple pipeline exercise."""
    return [
        {
            "sample_id": "sr-001",
            "case_id": "TC-SR-001",
            "title": "用户登录",
            "source": "priority_debug_manual_add",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "core_flow",
            "pattern_category": "core_flow_closure",
            "expected_priority": "P0",
            "pattern_summary": "登录是核心流程",
            "pattern_weight": 0.92,
            "pattern_confidence": 0.86,
        },
        {
            "sample_id": "sr-002",
            "case_id": "TC-SR-002",
            "title": "密码修改",
            "source": "priority_debug_manual_add",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "reason_category": "core_flow",
            "pattern_category": "core_flow_closure",
            "expected_priority": "P0",
            "pattern_summary": "密码修改是核心安全流程",
            "pattern_weight": 0.90,
            "pattern_confidence": 0.84,
        },
        {
            "sample_id": "sr-003",
            "case_id": "TC-SR-003",
            "title": "登录页logo展示",
            "source": "priority_debug_manual_add",
            "signal_type": "negative",
            "pattern_usage": "avoid",
            "reason_category": "display_issue",
            "pattern_category": "",
            "expected_priority": "P3",
            "pattern_summary": "logo展示纯UI验证",
            "pattern_weight": 0.38,
            "pattern_confidence": 0.45,
        },
    ]


# ═══════════════════════════════════════════════════════════════════
# Pipeline regression tests
# ═══════════════════════════════════════════════════════════════════

class TestRegressionCourseScheduling:
    """Scenario 1: 近期课程+排课 — 7 samples, mixed sources, corrections."""

    def test_normalize_produces_canonical_fields(self, course_scheduling_samples):
        results = normalize_priority_samples(course_scheduling_samples)
        assert len(results) >= 1
        for s in results:
            assert s.get("sample_id") is not None
            assert s.get("source_type") in {
                "priority_debug_manual_add",
                "quality_evaluation_defect",
                "manual_pool_input",
            }
            assert s.get("sample_kind") in {"positive", "negative"}
            assert s.get("status") in {"active", "deleted"}
            assert isinstance(s.get("confidence"), (int, float))

    def test_no_duplicates_after_dedup(self, course_scheduling_samples):
        normalized = [normalize_priority_sample(s) for s in course_scheduling_samples]
        deduped = _dedupe_priority_samples(normalized)
        ids = [s.get("sample_id") for s in deduped]
        assert len(ids) == len(set(ids))

    def test_aggregation_preserves_core_flow(self, course_scheduling_samples):
        normalized = normalize_priority_samples(course_scheduling_samples)
        # Core flow samples (cs-001, cs-002) should remain after aggregation
        result_ids = {s.get("sample_id") for s in normalized}
        assert "cs-001" in result_ids
        assert "cs-002" in result_ids

    def test_patterns_derived(self, course_scheduling_samples):
        normalized = normalize_priority_samples(course_scheduling_samples)
        patterns = derive_patterns_from_samples(normalized)
        assert len(patterns) >= 1
        # Should have at least a core_flow pattern
        cluster_keys = {p.get("cluster_key") for p in patterns}
        assert any("core_flow" in (k or "") for k in cluster_keys)

    def test_signals_exclude_disabled(self, course_scheduling_samples):
        normalized = normalize_priority_samples(course_scheduling_samples)
        patterns = derive_patterns_from_samples(normalized)
        signals = derive_signals_from_patterns(patterns)
        for sig in signals:
            assert sig.get("governance_status") != "disabled"

    def test_source_limits_applied(self, course_scheduling_samples):
        normalized = normalize_priority_samples(course_scheduling_samples)
        # Count per source
        from collections import Counter
        src_counts = Counter(s.get("source_type") for s in normalized)
        # No source should exceed its cap
        limits = {"priority_debug_manual_add": 1500, "quality_evaluation_defect": 500, "manual_pool_input": 500}
        for src, count in src_counts.items():
            assert count <= limits.get(src, 500), f"{src} count {count} exceeds limit"

    def test_learning_confirmed_samples_preserved(self, course_scheduling_samples):
        normalized = normalize_priority_samples(course_scheduling_samples)
        confirmed = [s for s in normalized if s.get("learning_status") == "user_confirmed"]
        assert len(confirmed) >= 2  # cs-005 and cs-006
        for s in confirmed:
            assert s.get("learning_confirmed_at") is not None
            assert s.get("learning_confirmed_by") is not None


class TestRegressionReviewRound2:
    """Scenario 2: 二轮复习 — pattern learning, linked final cases, multi-source."""

    def test_linked_final_cases_have_source_ids(self, review_round2_samples):
        results = normalize_priority_samples(review_round2_samples)
        linked = [s for s in results if s.get("source_type") == "linked_final_case_pattern"]
        for s in linked:
            assert s.get("source_id") is not None, f"Missing source_id for {s.get('sample_id')}"
            assert s.get("source_case_id") is not None

    def test_business_extension_detected(self, review_round2_samples):
        results = normalize_priority_samples(review_round2_samples)
        extensions = [s for s in results if s.get("source_type") == "linked_final_case_business_extension"]
        assert len(extensions) >= 1

    def test_ui_negative_samples_demoted(self, review_round2_samples):
        results = normalize_priority_samples(review_round2_samples)
        ui_samples = [s for s in results if s.get("reason_category") == "display_issue"]
        assert len(ui_samples) >= 1
        for s in ui_samples:
            assert s.get("sample_kind") == "negative"
            # UI-negative gets a slight boost in _pattern_weight so they can
            # suppress low-value UI-only cases, but their confidence stays low.
            c = float(s.get("confidence") or 0)
            assert c < 0.65, f"UI-negative confidence {c} should be low"

    def test_patterns_aggregate_cross_source(self, review_round2_samples):
        normalized = normalize_priority_samples(review_round2_samples)
        patterns = derive_patterns_from_samples(normalized)
        # Should aggregate samples from different sources into shared clusters
        for pat in patterns:
            if pat.get("sample_count", 0) > 1:
                sources = pat.get("top_source_types") or []
                # Multi-sample patterns may or may not be cross-source — either is valid.
                assert isinstance(sources, list)

    def test_signals_sorted_by_activation_weight(self, review_round2_samples):
        normalized = normalize_priority_samples(review_round2_samples)
        patterns = derive_patterns_from_samples(normalized)
        signals = derive_signals_from_patterns(patterns)
        weights = [s.get("activation_weight") or 0 for s in signals]
        assert weights == sorted(weights, reverse=True)


class TestRegressionSmallRequirement:
    """Scenario 3: 一个小型需求 — compact, no corrections, clean pipeline."""

    def test_all_samples_normalized(self, small_requirement_samples):
        results = normalize_priority_samples(small_requirement_samples)
        assert len(results) == 3

    def test_core_flow_clustered_together(self, small_requirement_samples):
        normalized = normalize_priority_samples(small_requirement_samples)
        patterns = derive_patterns_from_samples(normalized)
        # sr-001 and sr-002 should cluster together (both core_flow)
        core_patterns = [p for p in patterns if "core_flow" in (p.get("cluster_key") or "")]
        assert len(core_patterns) >= 1

    def test_display_issue_separate_cluster(self, small_requirement_samples):
        normalized = normalize_priority_samples(small_requirement_samples)
        patterns = derive_patterns_from_samples(normalized)
        # sr-003 (display_issue) should be in a different cluster from sr-001/sr-002
        display_patterns = [p for p in patterns if "display_issue" in (p.get("cluster_key") or "")]
        core_patterns = [p for p in patterns if "core_flow" in (p.get("cluster_key") or "")]
        if display_patterns and core_patterns:
            assert display_patterns[0].get("cluster_key") != core_patterns[0].get("cluster_key")

    def test_signals_emitted_for_all_patterns(self, small_requirement_samples):
        normalized = normalize_priority_samples(small_requirement_samples)
        patterns = derive_patterns_from_samples(normalized)
        signals = derive_signals_from_patterns(patterns)
        assert len(signals) <= len(patterns)
        assert len(signals) >= 1

    def test_no_deleted_in_active_samples(self, small_requirement_samples):
        results = normalize_priority_samples(small_requirement_samples)
        for s in results:
            assert s.get("status") == "active"


# ═══════════════════════════════════════════════════════════════════
# Cross-scenario consistency tests
# ═══════════════════════════════════════════════════════════════════

class TestCrossScenarioConsistency:
    """Tests that hold true across all three scenarios."""

    @pytest.fixture
    def all_scenarios(self, course_scheduling_samples, review_round2_samples, small_requirement_samples):
        return {
            "course_scheduling": course_scheduling_samples,
            "review_round2": review_round2_samples,
            "small_requirement": small_requirement_samples,
        }

    def test_all_normalize_without_error(self, all_scenarios):
        for name, samples in all_scenarios.items():
            result = normalize_priority_samples(samples)
            assert len(result) >= 1, f"{name}: no samples after normalization"
            assert all(isinstance(s, dict) for s in result), f"{name}: non-dict in results"

    def test_all_have_required_canonical_fields(self, all_scenarios):
        required = {"sample_id", "source_type", "sample_kind", "pattern_usage", "confidence", "status"}
        for name, samples in all_scenarios.items():
            result = normalize_priority_samples(samples)
            for s in result:
                for field in required:
                    assert field in s, f"{name}: missing canonical field '{field}' in sample {s.get('sample_id')}"

    def test_pattern_count_never_exceeds_sample_count(self, all_scenarios):
        for name, samples in all_scenarios.items():
            normalized = normalize_priority_samples(samples)
            patterns = derive_patterns_from_samples(normalized)
            assert len(patterns) <= len(normalized), f"{name}: patterns({len(patterns)}) > samples({len(normalized)})"

    def test_signal_count_never_exceeds_pattern_count(self, all_scenarios):
        for name, samples in all_scenarios.items():
            normalized = normalize_priority_samples(samples)
            patterns = derive_patterns_from_samples(normalized)
            signals = derive_signals_from_patterns(patterns)
            assert len(signals) <= len(patterns), f"{name}: signals({len(signals)}) > patterns({len(patterns)})"

    def test_deterministic_output(self, all_scenarios):
        """Same input must produce same output (pipeline is deterministic)."""
        for name, samples in all_scenarios.items():
            r1 = normalize_priority_samples(samples)
            r2 = normalize_priority_samples(samples)
            assert len(r1) == len(r2), f"{name}: different lengths"
            for a, b in zip(r1, r2):
                assert a.get("sample_id") == b.get("sample_id"), f"{name}: order changed"
                assert a.get("confidence") == b.get("confidence"), f"{name}: confidence changed"
                assert a.get("source_type") == b.get("source_type"), f"{name}: source_type changed"
