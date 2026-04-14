import sys
import importlib
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.control.feedback_control_state import (
    FeedbackControlState,
)
from modules.testing.priority_sample_pool_store import (
    normalize_priority_sample,
    normalize_priority_samples,
)

control_builder = importlib.import_module(
    "modules.test_generation_components.control.build_feedback_control_state"
)


def _state_with_rule(rule: str, source: str) -> FeedbackControlState:
    return FeedbackControlState(
        must_cover_rules=[rule],
        must_have_scenarios=[],
        forbidden_patterns=[],
        rule_quota={rule: 1},
        quality_fix_hints=[],
        source_meta={"sources": [source]},
    )


def test_priority_pool_feedback_disabled_skips_priority_source(monkeypatch) -> None:
    calls = {"priority": 0}

    def fake_priority_builder(**_: object) -> FeedbackControlState:
        calls["priority"] += 1
        return _state_with_rule("RULE-P", "priority")

    monkeypatch.setattr(control_builder, "_build_from_priority_sample_pool", fake_priority_builder)
    monkeypatch.setattr(control_builder, "_build_from_anomaly_pool", lambda **_: _state_with_rule("RULE-A", "anomaly"))
    monkeypatch.setattr(control_builder, "_build_from_reports", lambda **_: _state_with_rule("RULE-R", "report"))

    state = control_builder.build_feedback_control_state(
        db=object(),
        project_id=1,
        user_id=1,
        enable_priority_sample_pool=False,
        memory_fabric=None,
        memory_ctx=None,
    )

    assert calls["priority"] == 0
    assert "RULE-A" in state.must_cover_rules
    assert "RULE-R" in state.must_cover_rules
    assert "RULE-P" not in state.must_cover_rules


def test_priority_pool_feedback_enabled_reads_priority_source(monkeypatch) -> None:
    calls = {"priority": 0}

    def fake_priority_builder(**_: object) -> FeedbackControlState:
        calls["priority"] += 1
        return _state_with_rule("RULE-P", "priority")

    monkeypatch.setattr(control_builder, "_build_from_priority_sample_pool", fake_priority_builder)
    monkeypatch.setattr(control_builder, "_build_from_anomaly_pool", lambda **_: FeedbackControlState.empty())
    monkeypatch.setattr(control_builder, "_build_from_reports", lambda **_: FeedbackControlState.empty())

    state = control_builder.build_feedback_control_state(
        db=object(),
        project_id=1,
        user_id=1,
        enable_priority_sample_pool=True,
        memory_fabric=None,
        memory_ctx=None,
    )

    assert calls["priority"] == 1
    assert "RULE-P" in state.must_cover_rules


def test_redundant_case_maps_to_soft_constraints_not_forbidden(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 42,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "sync entry display duplicate",
                    "reason_category": "redundant_case",
                    "expected_priority": "P2",
                    "user_comment": "same semantics as existing case",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert state.forbidden_patterns == []
    assert "sync entry display duplicate" in state.soft_constraints


def test_priority_pool_requirement_retrieval_selects_topk(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 88,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-101 core submit path",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "main path",
                    "pattern_summary": "core submit path",
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-102 ui empty",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "display only",
                    "pattern_summary": "ui empty placeholder",
                },
                {
                    "case_id": "TC-3",
                    "title": "RULE-103 rollback",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "user_comment": "rollback path",
                    "pattern_summary": "rollback after timeout",
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [
            {"sample_index": 2},
            {"sample_index": 0},
        ]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(
        control_builder,
        "retrieve_priority_sample_patterns",
        fake_retrieve_priority_sample_patterns,
    )

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="rollback timeout after submit",
    )

    assert state.source_meta.get("priority_pool_sample_count") == 3
    assert state.source_meta.get("priority_pool_selected_sample_count") == 2
    assert state.source_meta.get("retrieval_hit_count") == 2
    assert state.source_meta.get("retrieval_fallback") == "none"
    assert "RULE-101" in state.must_cover_rules
    assert "RULE-103" in state.must_cover_rules
    assert "RULE-102" not in state.must_cover_rules


def test_normalize_priority_sample_generates_pattern_summary() -> None:
    normalized = normalize_priority_sample(
        {
            "case_id": "TC-100",
            "title": "sync status mismatch",
            "reason_category": "state_transition",
            "user_comment": "state mismatch after retry",
        }
    )
    summary = str(normalized.get("pattern_summary") or "")
    assert summary
    assert "state_transition" in summary
    assert normalized.get("pattern_canonical")
    assert float(normalized.get("pattern_quality_score") or 0) > 0
    assert float(normalized.get("pattern_weight") or 0) > 0


def test_normalize_priority_samples_deduplicates_and_keeps_stronger_weight() -> None:
    samples = normalize_priority_samples(
        [
            {
                "case_id": "TC-1",
                "title": "RULE-100 submit rollback",
                "pattern_summary": "提交后超时回滚校验",
                "expected_priority": "P2",
                "reason_category": "exception_path",
            },
            {
                "case_id": "TC-2",
                "title": "RULE-999 submit rollback",
                "pattern_summary": "提交后超时回滚校验",
                "expected_priority": "P1",
                "reason_category": "exception_path",
            },
        ]
    )
    assert len(samples) == 1
    assert samples[0].get("expected_priority") == "P1"


def test_priority_pool_retrieval_applies_pattern_cluster_cap(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 99,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-1 submit timeout rollback",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "user_comment": "cluster c1",
                    "pattern_summary": "submit timeout rollback path A",
                    "pattern_cluster_key": "c1",
                    "pattern_weight": 1.4,
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-2 submit timeout rollback retry",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "user_comment": "cluster c1",
                    "pattern_summary": "submit timeout rollback path B",
                    "pattern_cluster_key": "c1",
                    "pattern_weight": 1.3,
                },
                {
                    "case_id": "TC-3",
                    "title": "RULE-3 submit timeout rollback duplicate",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "user_comment": "cluster c1",
                    "pattern_summary": "submit timeout rollback path C",
                    "pattern_cluster_key": "c1",
                    "pattern_weight": 1.2,
                },
                {
                    "case_id": "TC-4",
                    "title": "RULE-4 auth denied error path",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "user_comment": "cluster c2",
                    "pattern_summary": "auth denied error path",
                    "pattern_cluster_key": "c2",
                    "pattern_weight": 1.1,
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [
            {"sample_index": 0, "pattern_cluster_key": "c1"},
            {"sample_index": 1, "pattern_cluster_key": "c1"},
            {"sample_index": 2, "pattern_cluster_key": "c1"},
            {"sample_index": 3, "pattern_cluster_key": "c2"},
        ]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(
        control_builder,
        "retrieve_priority_sample_patterns",
        fake_retrieve_priority_sample_patterns,
    )

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="submit timeout rollback and auth denied",
    )

    assert state.source_meta.get("priority_pool_selected_sample_count") == 3
    assert state.source_meta.get("retrieval_diversity_skipped_count") == 1
    assert "RULE-1" in state.must_cover_rules
    assert "RULE-2" in state.must_cover_rules
    assert "RULE-4" in state.must_cover_rules
    assert "RULE-3" not in state.must_cover_rules


def test_normalize_priority_sample_respects_weight_adjustment() -> None:
    base = normalize_priority_sample(
        {
            "case_id": "TC-10",
            "title": "RULE-10 rollback on timeout",
            "reason_category": "exception_path",
            "expected_priority": "P1",
            "pattern_summary": "rollback after timeout",
        }
    )
    adjusted = normalize_priority_sample(
        {
            "case_id": "TC-10",
            "title": "RULE-10 rollback on timeout",
            "reason_category": "exception_path",
            "expected_priority": "P1",
            "pattern_summary": "rollback after timeout",
            "pattern_weight_adjustment": 0.5,
        }
    )
    assert float(adjusted.get("pattern_weight") or 0.0) < float(base.get("pattern_weight") or 0.0)
    assert adjusted.get("pattern_weight_adjustment") == 0.5


def test_priority_pool_selection_skips_disabled_patterns(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 77,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-1 rollback path",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "pattern_summary": "rollback path disabled",
                    "pattern_weight": 1.6,
                    "governance_status": "disabled",
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-2 retry path",
                    "reason_category": "exception_path",
                    "expected_priority": "P1",
                    "pattern_summary": "retry path active",
                    "pattern_weight": 1.1,
                    "governance_status": "active",
                },
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="",
    )

    assert state.source_meta.get("retrieval_disabled_sample_count") == 1
    assert "RULE-2" in state.must_cover_rules
    assert "RULE-1" not in state.must_cover_rules
