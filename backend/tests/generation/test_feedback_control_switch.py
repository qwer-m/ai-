import sys
import importlib
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.control.feedback_control_state import (
    FeedbackControlState,
)
from modules.testing.priority_sample_pool_store import (
    normalize_raw_priority_samples,
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


def test_sample_value_supports_aliases_attrs_and_default_values() -> None:
    sample = {"caseId": "TC-001"}
    row = type("SampleRow", (), {"dataset_id": 7})()

    assert control_builder._sample_value(sample, "case_id", "caseId") == "TC-001"
    assert control_builder._sample_value(row, "dataset_id") == 7
    assert control_builder._sample_value(sample, "answer_points", []) == []
    assert control_builder._sample_value(sample, "query", "") == ""


def test_priority_pool_retrieval_text_uses_shared_case_aliases() -> None:
    sample = {
        "caseId": "TC-ALIAS",
        "description": "Create learning plan",
        "testModule": "Learning plan",
        "testSteps": ["Open plan page", {"action": "Save plan"}],
        "expectedResult": {"status": "saved", "message": ["success visible"]},
    }

    text = control_builder._sample_text_for_retrieval(sample)

    assert "Create learning plan" in text
    assert "Learning plan" in text
    assert "Open plan page Save plan" in text
    assert "saved success visible" in text


def test_priority_pool_case_identity_uses_case_id_aliases_without_plain_id() -> None:
    assert control_builder._sample_case_id({"id": "row-1", "test_case_id": "TC-002"}) == "TC-002"
    assert control_builder._sample_case_id({"id": "row-1", "用例编号": "TC-003"}) == "TC-003"
    assert control_builder._sample_case_id({"id": "row-1"}) == ""
    assert control_builder._priority_pool_sample_identity({"id": "row-1", "sourceCaseId": "SRC-1"}) == "SRC-1"


def test_workflow_blueprint_sample_builds_steps_from_shared_step_aliases() -> None:
    blueprint = control_builder._workflow_blueprint_from_sample(
        {
            "pattern_grain": "workflow_blueprint",
            "id": "row-1",
            "test_case_id": "TC-ALIAS",
            "pattern_summary": "Create plan flow",
            "testSteps": ["Open plan page", "Save plan"],
        }
    )

    assert blueprint is not None
    assert blueprint["id"] == "TC-ALIAS"
    assert [step["label"] for step in blueprint["steps"]] == ["Open plan page", "Save plan"]


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


def test_redundant_case_maps_registered_family_to_scenario_cap(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 43,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "答非所问准确性0分重复用例",
                    "reason_category": "redundant_case",
                    "expected_priority": "P2",
                    "user_comment": "和已有答非所问准确性0分用例重复",
                    "pattern_summary": "答非所问时准确性得0分",
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
    assert state.source_meta["priority_pool_redundant_scenario_caps"] == {
        "ai_answer_irrelevant_score_zero": 1
    }


def test_positive_pattern_maps_to_preferred_patterns(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 43,
            "samples": [
                {
                    "case_id": "TC-9",
                    "title": "stable settlement ordering",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "golden path with deterministic assertion",
                    "pattern_summary": "deterministic settlement assertion chain",
                    "signal_type": "positive",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert "deterministic settlement assertion chain" in state.preferred_patterns
    assert state.source_meta.get("positive_selected_count") == 1
    assert state.source_meta.get("negative_selected_count") == 0


def test_positive_case_maps_registered_family_to_must_have_scenario(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 44,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "答非所问准确性0分优质用例",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "保留这类答非所问准确性0分的评分规则覆盖",
                    "pattern_summary": "答非所问时准确性得0分",
                    "signal_type": "positive",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert "ai_answer_irrelevant_score_zero" in state.must_have_scenarios
    assert state.source_meta["priority_pool_positive_scenario_families"] == {
        "ai_answer_irrelevant_score_zero": 1
    }


def test_sample_kind_positive_maps_to_preferred_patterns(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 44,
            "samples": [
                {
                    "case_id": "TC-10",
                    "title": "stable lesson flow",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "stable regression-safe flow",
                    "pattern_summary": "stable lesson flow assertions",
                    "sampleKind": "positive",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert "stable lesson flow assertions" in state.preferred_patterns
    assert state.source_meta.get("positive_selected_count") == 1
    assert state.source_meta.get("negative_selected_count") == 0


def test_explicit_negative_signal_enters_forbidden_patterns(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 45,
            "samples": [
                {
                    "case_id": "TC-N1",
                    "title": "avoid duplicate submit ui-only check",
                    "pattern_summary": "avoid duplicate submit ui-only validation",
                    "signal_type": "negative",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert "avoid duplicate submit ui-only validation" in state.forbidden_patterns
    assert state.source_meta.get("positive_selected_count") == 0
    assert state.source_meta.get("negative_selected_count") == 1


def test_ui_low_value_negative_adds_stronger_forbidden_guardrails(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 46,
            "samples": [
                {
                    "case_id": "TC-N2",
                    "title": "ui copy and layout only check",
                    "reason_category": "display_issue",
                    "pattern_summary": "ui copy and layout only check",
                    "signal_type": "negative",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert "ui copy and layout only check" in state.forbidden_patterns
    assert "avoid static ui-only checks without workflow/state transition assertions" in state.forbidden_patterns
    assert state.source_meta.get("ui_low_value_negative_count") == 1


def test_priority_pool_extracts_reuse_risks(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 47,
            "samples": [
                {
                    "case_id": "TC-R1",
                    "title": "复用页面完成后回首页而不是回原列表",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "检查旧跳转是否残留",
                    "signal_type": "positive",
                    "pattern_summary": "reused flow should return home without legacy redirect",
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
    )

    assert any("wrong_return_target_risk" in item for item in state.reuse_risks)
    assert any("legacy_behavior_risk" in item for item in state.reuse_risks)


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


def test_priority_pool_retrieval_uses_sample_id_when_index_order_is_stale(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 89,
            "samples": [
                {
                    "sample_id": "sample-a",
                    "case_id": "TC-A",
                    "title": "RULE-A stale first sample",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "signal_type": "positive",
                    "pattern_summary": "stale first sample",
                    "pattern_canonical": "stale first sample",
                },
                {
                    "sample_id": "sample-b",
                    "case_id": "TC-B",
                    "title": "RULE-B selected by sample id",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "signal_type": "positive",
                    "pattern_summary": "selected by sample id",
                    "pattern_canonical": "selected by sample id",
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [
            {
                "sample_index": 0,
                "sample_id": "sample-b",
                "pattern_canonical": "selected by sample id",
            }
        ]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="core flow",
    )

    assert "RULE-B" in state.must_cover_rules
    assert "RULE-A" not in state.must_cover_rules
    assert state.source_meta.get("retrieval_sample_id_hit_count") == 1


def test_priority_pool_directly_includes_workflow_blueprints_outside_pattern_topk(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 90,
            "samples": [
                {
                    "sample_id": "negative-ui",
                    "case_id": "TC-N",
                    "title": "avoid duplicate display-only card checks",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "signal_type": "negative",
                    "pattern_usage": "avoid",
                    "pattern_summary": "avoid display-only card checks",
                },
                {
                    "sample_id": "workflow-main",
                    "case_id": "WF-1",
                    "title": "Workflow blueprint: save then verify",
                    "reason_category": "main_smoke_flow",
                    "pattern_category": "main_smoke_flow",
                    "expected_priority": "P0",
                    "signal_type": "positive",
                    "pattern_usage": "prefer",
                    "pattern_grain": "workflow_blueprint",
                    "pattern_summary": "workflow_blueprint | main_smoke_flow | save then verify",
                    "workflow_blueprint": {
                        "id": "wf-save-verify",
                        "name": "save then verify",
                        "steps": [
                            {"id": "save", "label": "Save plan", "stage_kind": "commit"},
                            {
                                "id": "visible",
                                "label": "Student side plan visible",
                                "stage_kind": "downstream_visibility",
                            },
                        ],
                    },
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [{"sample_index": 0, "sample_id": "negative-ui"}]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="display card checks",
    )

    assert len(state.workflow_blueprints) == 1
    assert state.workflow_blueprints[0]["id"] == "wf-save-verify"
    assert state.source_meta.get("workflow_blueprint_direct_selected_count") == 1
    assert state.source_meta.get("workflow_blueprint_count") == 1


def test_priority_pool_requirement_domain_filter_blocks_unrelated_pool_samples(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 470,
            "samples": [
                {
                    "case_id": "TC-SCHEDULE",
                    "title": "RULE-SCHEDULE course scheduling save path",
                    "reason_category": "core_flow",
                    "expected_priority": "P0",
                    "signal_type": "positive",
                    "pattern_summary": "排课新增计划保存后跳转课程管理",
                },
                {
                    "case_id": "TC-AI",
                    "title": "RULE-AI wrong-question teaching retry path",
                    "reason_category": "core_flow",
                    "expected_priority": "P0",
                    "signal_type": "positive",
                    "pattern_summary": "讲错题提交超时后可重试并保留对话",
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [{"sample_index": 0}]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="讲错题接入AI：覆盖追问、评分和对话恢复",
    )

    assert state.source_meta.get("retrieval_domain_filter_applied") is True
    assert state.source_meta.get("retrieval_domain_matched_sample_count") == 1
    assert "RULE-AI" in state.must_cover_rules
    assert "RULE-SCHEDULE" not in state.must_cover_rules


def test_priority_pool_requirement_domain_no_match_disables_pool_signal(monkeypatch) -> None:
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", lambda **_: [])
    selected, meta = control_builder._select_priority_pool_samples_by_requirement(
        samples=[
            {
                "case_id": "TC-SCHEDULE",
                "title": "RULE-SCHEDULE course scheduling save path",
                "reason_category": "core_flow",
                "expected_priority": "P0",
                "signal_type": "positive",
                "pattern_summary": "近期课程排课保存后同步本周任务",
            }
        ],
        project_id=1,
        user_id=1,
        requirement_text="讲错题接入AI：覆盖追问、评分和对话恢复",
    )

    assert selected == []
    assert meta.get("retrieval_fallback") == "domain_no_match"
    assert meta.get("retrieval_domain_no_match") is True


def test_priority_pool_requirement_domain_filter_keeps_matching_schedule_samples(monkeypatch) -> None:
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", lambda **_: [])
    selected, meta = control_builder._select_priority_pool_samples_by_requirement(
        samples=[
            {
                "case_id": "TC-SCHEDULE",
                "title": "RULE-SCHEDULE course scheduling save path",
                "reason_category": "core_flow",
                "expected_priority": "P0",
                "signal_type": "positive",
                "pattern_summary": "近期课程排课保存后同步本周任务",
            }
        ],
        project_id=1,
        user_id=1,
        requirement_text="近期课程+排课回归",
    )

    assert len(selected) == 1
    assert meta.get("retrieval_domain_filter_applied") is True
    assert meta.get("retrieval_domain_matched_sample_count") == 1


def test_priority_pool_ambiguous_registered_domain_blocks_historical_profile(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 496,
            "samples": [
                {
                    "case_id": "TC-COURSE",
                    "title": "RULE-201 course member access",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "signal_type": "positive",
                    "pattern_usage": "prefer",
                    "pattern_summary": "course member permission unlock",
                    "user_comment": "keep this profile",
                },
                {
                    "case_id": "TC-WF",
                    "title": "workflow blueprint course access",
                    "reason_category": "main_smoke_flow",
                    "pattern_category": "main_smoke_flow",
                    "expected_priority": "P0",
                    "signal_type": "positive",
                    "pattern_usage": "prefer",
                    "pattern_grain": "workflow_blueprint",
                    "pattern_summary": "workflow blueprint main smoke flow course member access",
                    "workflow_blueprint": {
                        "id": "course-access",
                        "steps": [
                            {"id": "entry", "label": "Open course"},
                            {"id": "unlock", "label": "Unlock member course"},
                        ],
                    },
                },
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(
        control_builder,
        "retrieve_priority_sample_patterns",
        lambda **_: [{"sample_index": 0, "sample_id": ""}],
    )

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="forum optimization with essay writing and course member permission signals",
    )

    assert state.must_cover_rules == []
    assert state.quality_fix_hints == []
    assert state.workflow_blueprints == []
    assert state.source_meta.get("retrieval_fallback") == "domain_unstable"
    assert state.source_meta.get("retrieval_domain_gate_blocked") is True
    assert state.source_meta.get("retrieval_domain_gate_status") == "ambiguous_registered_domain"
    assert "manual_quality_profile" not in state.source_meta


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
    assert normalized.get("pattern_scope") == "project"
    assert normalized.get("pattern_grain") == "anti_pattern"
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


def test_normalize_priority_sample_biases_ui_weight_by_signal() -> None:
    negative = normalize_priority_sample(
        {
            "case_id": "TC-20",
            "title": "UI copy layout verification",
            "reason_category": "display_issue",
            "pattern_summary": "ui copy layout check only",
            "signal_type": "negative",
        }
    )
    positive = normalize_priority_sample(
        {
            "case_id": "TC-21",
            "title": "UI copy layout verification",
            "reason_category": "display_issue",
            "pattern_summary": "ui copy layout check only",
            "signal_type": "positive",
        }
    )
    assert float(negative.get("pattern_weight") or 0.0) > float(positive.get("pattern_weight") or 0.0)


def test_priority_sample_weight_prefers_assertable_learning_exception_over_display() -> None:
    learning = normalize_priority_sample(
        {
            "case_id": "TC-001",
            "title": "学员端讲错题网络中断后恢复继续当前追问轮次",
            "test_module": "学员端AI讲错题",
            "expected_priority": "P0",
            "reason_category": "exception_path",
            "expected_result_quality": "contains_concrete_assertion",
            "expected_result": "刷新后显示第二轮追问，回答后进入第三轮，不重复提问且保留输入",
            "pattern_summary": "学员端讲错题网络中断恢复后继续当前追问轮次，不重复提问",
            "signal_type": "positive",
        }
    )
    display = normalize_priority_sample(
        {
            "case_id": "TC-002",
            "title": "历史课程列表按钮展示和文案检查",
            "test_module": "历史课程",
            "expected_priority": "P2",
            "reason_category": "display_issue",
            "expected_result": "页面展示报告按钮和讲错题按钮",
            "pattern_summary": "历史课程页面通用按钮展示和文案检查",
            "signal_type": "positive",
        }
    )

    assert float(learning.get("pattern_weight") or 0.0) > float(display.get("pattern_weight") or 0.0)
    assert float(learning.get("pattern_quality_score") or 0.0) > float(display.get("pattern_quality_score") or 0.0)


def test_normalize_raw_priority_samples_merges_same_intent_and_keeps_best_case() -> None:
    samples = normalize_raw_priority_samples(
        [
            {
                "sample_id": "low",
                "case_id": "TC-101",
                "title": "学员回答49字时完整性扣分",
                "test_module": "评分规则-边界",
                "expected_priority": "P2",
                "reason_category": "boundary_condition",
                "expected_result": "完整性扣分",
                "pattern_summary": "49字回答扣分",
                "signal_type": "positive",
            },
            {
                "sample_id": "high",
                "case_id": "TC-102",
                "title": "学员回答接近50字（49字）时完整性扣50%验证",
                "test_module": "评分规则-边界",
                "expected_priority": "P0",
                "reason_category": "boundary_condition",
                "expected_result_quality": "contains_concrete_assertion",
                "expected_result": "完整性原始分20分扣50%后为10分，清晰度也扣50%，准确性正常评分",
                "pattern_summary": "49字回答触发完整性和清晰度扣50%，保留准确性正常评分",
                "signal_type": "positive",
            },
        ]
    )

    assert len(samples) == 1
    assert samples[0].get("sample_id") == "high"
    assert samples[0].get("expected_priority") == "P0"


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


def test_priority_pool_selection_skips_low_confidence_patterns(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 78,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-1 weak learned pattern",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "pattern_summary": "weak learned pattern",
                    "signal_type": "positive",
                    "pattern_confidence": 0.3,
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-2 strong learned pattern",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "pattern_summary": "strong learned pattern",
                    "signal_type": "positive",
                    "pattern_confidence": 0.82,
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

    assert state.source_meta.get("retrieval_low_confidence_sample_count") == 1
    assert "RULE-2" in state.must_cover_rules
    assert "RULE-1" not in state.must_cover_rules


def test_priority_pool_query_selection_skips_low_confidence_patterns(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 79,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-1 weak vector hit",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "pattern_summary": "weak learned pattern",
                    "signal_type": "positive",
                    "pattern_confidence": 0.3,
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-2 strong vector hit",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "pattern_summary": "strong learned pattern",
                    "signal_type": "positive",
                    "pattern_confidence": 0.82,
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [{"sample_index": 0}, {"sample_index": 1}]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="core flow",
    )

    assert state.source_meta.get("retrieval_low_confidence_sample_count") == 1
    assert "RULE-2" in state.must_cover_rules
    assert "RULE-1" not in state.must_cover_rules


def test_priority_pool_source_meta_tracks_pattern_scope_and_grain(monkeypatch) -> None:
    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 80,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-1 reusable project pattern",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "pattern_summary": "state consistency pattern",
                    "signal_type": "positive",
                    "pattern_scope": "project",
                    "pattern_grain": "pattern",
                    "pattern_confidence": 0.82,
                }
            ],
        }

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="",
    )

    assert state.source_meta.get("pattern_scope_distribution") == {"project": 1}
    assert state.source_meta.get("pattern_grain_distribution") == {"pattern": 1}
    assert "state consistency pattern" in state.preferred_patterns


def test_priority_pool_manual_profile_uses_full_verified_pool_not_selected_topk(monkeypatch) -> None:
    samples = [
        {
            "case_id": f"TC-{index}",
            "title": f"manual flow case {index}",
            "source_case_module": module,
            "source_case_expected_result": f"{module} assertion",
            "reason_category": "core_flow",
            "expected_priority": priority,
            "pattern_summary": f"{module} reusable flow pattern",
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "pattern_cluster_key": f"p{index}",
            "learning_status": "user_confirmed",
            "manual_confirmed": True,
        }
        for index, module, priority in [
            (1, "入口", "P0"),
            (2, "本周课程模块", "P0"),
            (3, "排课-学习计划-第1步", "P1"),
            (4, "排课-学习计划-第2步", "P1"),
        ]
    ]
    samples.append(
        {
            "case_id": "TC-CANDIDATE",
            "title": "unconfirmed display candidate",
            "source_case_module": "未确认展示模块",
            "reason_category": "display_issue",
            "expected_priority": "P2",
            "pattern_summary": "display-only candidate",
            "signal_type": "negative",
            "pattern_usage": "avoid",
            "learning_status": "system_candidate",
        }
    )

    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {"generation_id": 122, "samples": samples}

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [{"sample_index": 0}, {"sample_index": 1}]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="manual flow",
    )

    profile = state.source_meta.get("manual_quality_profile")
    assert isinstance(profile, dict)
    assert state.source_meta.get("priority_pool_selected_sample_count") == 2
    assert profile["trusted_sample_count"] == 4
    assert profile["priority_distribution"] == {"P0": 2, "P1": 2}
    assert profile["high_priority_ratio"] == 1.0
    assert "未确认展示模块" not in profile["module_distribution_top"]


def test_priority_pool_selection_enforces_positive_min_quota_when_available(monkeypatch) -> None:
    monkeypatch.setattr(control_builder, "_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K", 5)
    monkeypatch.setattr(control_builder, "_PRIORITY_POOL_MIN_POSITIVE_TOP_K", 2)
    monkeypatch.setattr(control_builder, "_PRIORITY_POOL_MAX_NEGATIVE_TOP_K", 3)

    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 120,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-201 ui copy check",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 1",
                    "signal_type": "negative",
                    "pattern_summary": "display copy validation",
                    "pattern_cluster_key": "n1",
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-202 ui color check",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 2",
                    "signal_type": "negative",
                    "pattern_summary": "display color validation",
                    "pattern_cluster_key": "n2",
                },
                {
                    "case_id": "TC-3",
                    "title": "RULE-203 non-critical ui",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 3",
                    "signal_type": "negative",
                    "pattern_summary": "placeholder validation",
                    "pattern_cluster_key": "n3",
                },
                {
                    "case_id": "TC-4",
                    "title": "RULE-204 core flow closure",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "positive sample 1",
                    "signal_type": "positive",
                    "pattern_summary": "core flow closure assertions",
                    "pattern_cluster_key": "p1",
                },
                {
                    "case_id": "TC-5",
                    "title": "RULE-205 state transition flow",
                    "reason_category": "state_transition",
                    "expected_priority": "P1",
                    "user_comment": "positive sample 2",
                    "signal_type": "positive",
                    "pattern_summary": "state transition assertions",
                    "pattern_cluster_key": "p2",
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [
            {"sample_index": 0},
            {"sample_index": 1},
            {"sample_index": 2},
            {"sample_index": 3},
            {"sample_index": 4},
        ]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="core flow and state transition",
    )

    assert state.source_meta.get("priority_pool_selected_sample_count") == 5
    assert state.source_meta.get("positive_selected_count") == 2
    assert state.source_meta.get("negative_selected_count") == 3
    assert state.source_meta.get("retrieval_signal_quota_applied") is True
    assert state.source_meta.get("retrieval_signal_quota_relaxed") is False


def test_priority_pool_selection_relaxes_negative_cap_when_positive_not_enough(monkeypatch) -> None:
    monkeypatch.setattr(control_builder, "_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K", 5)
    monkeypatch.setattr(control_builder, "_PRIORITY_POOL_MIN_POSITIVE_TOP_K", 2)
    monkeypatch.setattr(control_builder, "_PRIORITY_POOL_MAX_NEGATIVE_TOP_K", 3)

    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 121,
            "samples": [
                {
                    "case_id": "TC-1",
                    "title": "RULE-211 ui copy check",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 1",
                    "signal_type": "negative",
                    "pattern_summary": "display copy validation",
                    "pattern_cluster_key": "n1",
                },
                {
                    "case_id": "TC-2",
                    "title": "RULE-212 ui color check",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 2",
                    "signal_type": "negative",
                    "pattern_summary": "display color validation",
                    "pattern_cluster_key": "n2",
                },
                {
                    "case_id": "TC-3",
                    "title": "RULE-213 non-critical ui",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 3",
                    "signal_type": "negative",
                    "pattern_summary": "placeholder validation",
                    "pattern_cluster_key": "n3",
                },
                {
                    "case_id": "TC-4",
                    "title": "RULE-214 fallback ui",
                    "reason_category": "display_issue",
                    "expected_priority": "P2",
                    "user_comment": "negative sample 4",
                    "signal_type": "negative",
                    "pattern_summary": "fallback rendering",
                    "pattern_cluster_key": "n4",
                },
                {
                    "case_id": "TC-5",
                    "title": "RULE-215 core flow closure",
                    "reason_category": "core_flow",
                    "expected_priority": "P1",
                    "user_comment": "positive sample 1",
                    "signal_type": "positive",
                    "pattern_summary": "core flow closure assertions",
                    "pattern_cluster_key": "p1",
                },
            ],
        }

    def fake_retrieve_priority_sample_patterns(**_: object) -> list[dict[str, object]]:
        return [
            {"sample_index": 0},
            {"sample_index": 1},
            {"sample_index": 2},
            {"sample_index": 3},
            {"sample_index": 4},
        ]

    monkeypatch.setattr(control_builder, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(control_builder, "retrieve_priority_sample_patterns", fake_retrieve_priority_sample_patterns)

    state = control_builder._build_from_priority_sample_pool(
        db=object(),
        project_id=1,
        user_id=1,
        requirement_text="core flow",
    )

    assert state.source_meta.get("priority_pool_selected_sample_count") == 5
    assert state.source_meta.get("positive_selected_count") == 1
    assert state.source_meta.get("negative_selected_count") == 4
    assert state.source_meta.get("retrieval_signal_quota_applied") is True
    assert state.source_meta.get("retrieval_signal_quota_relaxed") is True
