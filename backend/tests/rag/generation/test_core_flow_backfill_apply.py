from __future__ import annotations

import json
from typing import Any

from core.settings.config import settings

import modules.testing.test_generation_components.legacy.json_generation as json_generation_mod
from modules.testing.test_generation_components.legacy_generation_impl import TestGenerationModule


class _DeterministicBackfillApplyClient:
    max_tokens = 2048

    def select_model(self, full_input: str, task_type: str = "generation") -> str:  # noqa: ARG002
        return "deterministic-backfill-model"

    def compress_context(self, context: str, *args, **kwargs) -> str:  # noqa: ARG002
        return context

    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:  # noqa: ARG002
        return "[]"


class _FeedbackState:
    def to_dict(self) -> dict[str, Any]:
        return {}


class _InMemoryActiveSession:
    def __init__(self) -> None:
        self.entries: list[Any] = []
        self.logs: list[Any] = []
        self.generations: list[Any] = []
        self._next_id = 443

    def add(self, obj: Any) -> None:
        self.entries.append(obj)
        if hasattr(obj, "message"):
            self.logs.append(obj)
        elif hasattr(obj, "generated_result"):
            self.generations.append(obj)

    def commit(self) -> None:
        return None

    def refresh(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1

    def rollback(self) -> None:
        return None


class _SignalSet:
    violates_confirmed_fact = False
    missing_core_flow = False
    missing_reuse_risk = False
    contains_pending_logic = False
    confirmed_fact_hits: list[str] = []
    confirmed_fact_violations: list[str] = []
    reuse_risk_hits: list[str] = []
    pending_hits: list[str] = []


class _JudgedItem:
    def __init__(self, case: dict[str, Any]) -> None:
        self.case_id = str(case.get("id") or case.get("case_id") or "")
        self.status = "PASS"
        self.reject_reason = ""
        self.pending_reason = ""
        self.signals = _SignalSet()
        self.before_case = dict(case)
        self.after_case = dict(case)


class _RepairedPayload:
    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.pass_count = len(cases)
        self.repairable_count = 0
        self.reject_count = 0
        self.pending_count = 0
        self.repaired_case_count = 0
        self.appended_case_count = 0
        self.core_flow_covered = True
        self.reuse_risk_covered = True
        self.cases = [_JudgedItem(case) for case in cases]
        self.pass_cases = [dict(case) for case in cases]


def _primary_cases_for_backfill_apply() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(1, 9):
        rows.append(
            {
                "id": f"TC-{idx:03d}",
                "case_id": f"TC-{idx:03d}",
                "description": f"primary-case-{idx}",
                "test_module": "primary-module",
                "preconditions": ["user logged in"],
                "steps": ["1. open page", "2. verify state"],
                "test_input": "default input",
                "expected_result": "page text equals primary page",
                "priority": "P1",
                "priority_final": "P1",
            }
        )
    return rows


def _backfill_cases_for_backfill_apply() -> list[dict[str, Any]]:
    flow_keys = [
        "paid_gate",
        "textbook_grade_isolation",
        "weekday_review_to_workbook",
        "workbook_statistics",
        "wrong_only_filter",
        "mastery_calculation_level",
        "weekend_classification",
        "no_weekday_data_fallback",
        "all_correct_history_review",
        "weekend_completion_sync",
        "supervisor_report_generation",
        "unauthorized_data_isolation",
    ]
    rows: list[dict[str, Any]] = []
    for idx, flow_key in enumerate(flow_keys, start=1):
        rows.append(
            {
                "id": f"BF-{idx:03d}",
                "case_id": f"BF-{idx:03d}",
                "description": f"backfill-{flow_key}",
                "test_module": flow_key,
                "preconditions": ["user logged in"],
                "steps": ["1. execute flow", "2. verify result"],
                "test_input": flow_key,
                "expected_result": f"field status={flow_key}",
                "priority": "P1",
                "priority_final": "P1",
                "source_flow_key": flow_key,
                "matched_core_flows": [flow_key],
                "backfill_generated": True,
            }
        )
    return rows


def _extract_gen_diag(db: _InMemoryActiveSession, kind: str) -> dict[str, Any]:
    for log_entry in reversed(db.logs):
        message = str(getattr(log_entry, "message", "") or "")
        if not message.startswith("GEN_DIAG:"):
            continue
        payload = json.loads(message[len("GEN_DIAG:") :])
        if str(payload.get("kind") or "") == kind:
            return payload
    return {}


def _configure_backfill_apply_env(
    monkeypatch,
    *,
    enabled: bool,
    apply_to_final: bool,
    merged_preview_cases: list[dict[str, Any]],
    coverage_after_ratio: float = 1.0,
) -> dict[str, int]:
    import modules.test_generation_components.coverage.core_flow_backfill as backfill_plan_mod
    import modules.test_generation_components.coverage.core_flow_backfill_generation as backfill_generation_mod
    import modules.test_generation_components.coverage.core_flow_coverage_contract as coverage_contract_mod

    call_counter = {"generate_backfill": 0}
    primary_cases = _primary_cases_for_backfill_apply()
    accepted_backfill_cases = _backfill_cases_for_backfill_apply()

    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_ENABLED", enabled, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_APPLY_TO_FINAL", apply_to_final, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 12, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MIN_FINAL_CASES", 12, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MAX_FINAL_CASES", 18, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MIN_COVERAGE_RATIO", 0.8, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    monkeypatch.setattr(json_generation_mod, "get_client_for_user", lambda user_id, db: _DeterministicBackfillApplyClient())
    monkeypatch.setattr(TestGenerationModule, "_is_active_db_session", lambda self, db: True)
    monkeypatch.setattr(
        TestGenerationModule,
        "_run_snapshot_readiness_gate",
        lambda self, **kwargs: {"proceed": True, "gate_debug": {}},
    )
    monkeypatch.setattr(
        TestGenerationModule,
        "_resolve_kb_context_with_hybrid",
        lambda self, **kwargs: {
            "kb_context": "",
            "context_source": "none",
            "fusion_debug": {},
            "rag_result": {},
        },
    )
    monkeypatch.setattr(
        TestGenerationModule,
        "analyze_requirement_context",
        lambda self, requirement, kb_context, client, db: {
            "system_type": "Web",
            "complexity": "Medium",
            "suggested_ratios": {"functional": 0.6, "regression": 0.2, "non_functional": 0.2},
            "focus_areas": ["core flow"],
            "device_scenarios": ["web"],
            "impact_scope": "single_module",
        },
    )
    monkeypatch.setattr(json_generation_mod, "build_feedback_control_state", lambda **kwargs: _FeedbackState())
    monkeypatch.setattr(
        json_generation_mod,
        "run_multi_pass_generation",
        lambda **kwargs: {
            "final_cases": [dict(item) for item in primary_cases],
            "stage_logs": [
                {"kind": "generation_stage", "stage": "primary", "case_count": len(primary_cases)},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(primary_cases)},
            ],
            "coverage": {"kind": "coverage_check", "covered_rules": ["RULE-001"], "missing_rules": []},
            "raw": {},
        },
    )
    monkeypatch.setattr(json_generation_mod, "judge_cases", lambda cases, **kwargs: cases)
    monkeypatch.setattr(json_generation_mod, "repair_cases", lambda judged, **kwargs: _RepairedPayload(judged))
    monkeypatch.setattr(json_generation_mod, "training_gate", lambda repaired: (repaired.pass_cases, [], [], []))
    monkeypatch.setattr(json_generation_mod, "deduplicate_test_cases", lambda cases: cases)
    monkeypatch.setattr(json_generation_mod, "reorder_cases_by_closed_loop", lambda cases, **kwargs: cases)
    monkeypatch.setattr(
        json_generation_mod,
        "analyze_coverage",
        lambda requirement, cases: {"covered_rules": ["RULE-001"], "missing_rules": []},
    )
    monkeypatch.setattr(
        backfill_plan_mod,
        "plan_core_flow_backfill",
        lambda **kwargs: {
            "backfill_plan": [{"flow_key": "paid_gate", "flow_name": "付费拦截"}],
            "missing_core_flow_count": 12,
        },
    )

    def _generate_core_flow_backfill_candidates(**kwargs) -> dict[str, Any]:
        call_counter["generate_backfill"] += 1
        return {
            "generated_backfill_candidate_cases": [dict(item) for item in accepted_backfill_cases],
            "accepted_backfill_cases": [dict(item) for item in accepted_backfill_cases],
            "rejected_backfill_cases": [],
            "merged_preview_cases": [dict(item) for item in merged_preview_cases],
            "accepted_for_preview_count": 12,
            "primary_retained_count": 6,
            "primary_trimmed_count": 2,
            "backfill_retained_count": 12,
            "backfill_trimmed_count": 0,
        }

    def _audit_core_flow_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
        has_backfill = any(str((case or {}).get("id") or (case or {}).get("case_id") or "").startswith("BF-") for case in (cases or []))
        coverage_ratio = float(coverage_after_ratio if has_backfill else 0.0)
        covered_count = 12 if has_backfill and coverage_ratio >= 0.8 else 0
        missing = [] if covered_count == 12 else ["supervisor_report_generation", "unauthorized_data_isolation"]
        return {
            "core_flow_covered_count": int(covered_count),
            "core_flow_required_count": 12,
            "core_flow_coverage_ratio": coverage_ratio,
            "core_flow_coverage_passed": covered_count == 12,
            "missing_core_flows": list(missing),
            "false_positive_guard_notes": [],
            "coverage_detail": {},
        }

    monkeypatch.setattr(backfill_generation_mod, "generate_core_flow_backfill_candidates", _generate_core_flow_backfill_candidates)
    monkeypatch.setattr(coverage_contract_mod, "audit_core_flow_coverage", _audit_core_flow_coverage)
    return call_counter


def test_json_persistence_projects_final_case_contract(monkeypatch) -> None:
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=[],
    )

    source_cases = _primary_cases_for_backfill_apply()
    source_cases[0].update(
        {
            "workflow_transition": {
                "workflow_id": "schedule-main",
                "source_state": "course_selected",
                "action": "configure_time",
                "target_state": "time_configured",
                "path_type": "main",
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
            },
            "execution_group": "schedule-main",
            "execution_sequence": 1,
            "depends_on": ["TC-000"],
            "role": "teacher",
            "session_key": "teacher_session",
            "fixture_key": "schedule_seed",
            "group_setup": "seed_schedule_dataset()",
            "group_teardown": "cleanup_schedule_dataset()",
            "model_priority_current": "P1",
            "priority_decision_state": "decided",
            "priority_debug": {"reason": "debug-only"},
        }
    )

    monkeypatch.setattr(
        json_generation_mod,
        "run_multi_pass_generation",
        lambda **kwargs: {
            "final_cases": [dict(item) for item in source_cases],
            "stage_logs": [
                {"kind": "generation_stage", "stage": "primary", "case_count": len(source_cases)},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(source_cases)},
            ],
            "coverage": {"kind": "coverage_check", "covered_rules": ["RULE-001"], "missing_rules": []},
            "raw": {},
        },
    )

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="json path should persist only formal case fields",
        project_id=16,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    assert db.generations
    stored = json.loads(str(db.generations[-1].generated_result or "[]"))
    assert stored[0]["priority"] == "P1"
    assert stored[0]["priority_final"] == "P1"
    assert stored[0]["workflow_id"] == "schedule-main"
    assert stored[0]["source_state"] == "course_selected"
    assert stored[0]["target_state"] == "time_configured"
    assert stored[0]["execution_group"] == "schedule-main"
    assert stored[0]["execution_sequence"] == 1
    assert stored[0]["depends_on"] == ["TC-000"]
    assert stored[0]["role"] == "teacher"
    assert stored[0]["session_key"] == "teacher_session"
    assert stored[0]["fixture_key"] == "schedule_seed"
    assert stored[0]["group_setup"] == "seed_schedule_dataset()"
    assert stored[0]["group_teardown"] == "cleanup_schedule_dataset()"
    assert "model_priority_current" not in stored[0]
    assert "priority_decision_state" not in stored[0]
    assert "priority_debug" not in stored[0]
    assert "workflow_transition" not in stored[0]


def test_json_persistence_recalculates_priority_final_when_upstream_stripped(monkeypatch) -> None:
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=[],
    )

    source_cases = _primary_cases_for_backfill_apply()
    for item in source_cases:
        item.pop("priority_final", None)

    monkeypatch.setattr(
        json_generation_mod,
        "run_multi_pass_generation",
        lambda **kwargs: {
            "final_cases": [dict(item) for item in source_cases],
            "stage_logs": [
                {"kind": "generation_stage", "stage": "primary", "case_count": len(source_cases)},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(source_cases)},
            ],
            "coverage": {"kind": "coverage_check", "covered_rules": ["RULE-001"], "missing_rules": []},
            "raw": {},
        },
    )

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="json path should finalize priority when priority_final is stripped",
        project_id=17,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    stored = json.loads(str(db.generations[-1].generated_result or "[]"))
    assert stored
    assert all(str(item.get("priority_final") or "").strip().upper() == "P1" for item in stored)
    assert all(str(item.get("priority") or "").strip().upper() == "P1" for item in stored)


def test_backfill_apply_default_disabled_keeps_primary_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    call_counter = _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=merged_preview_cases,
    )

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="core flow backfill disabled by default",
        project_id=11,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    assert len(result) == 8
    assert call_counter["generate_backfill"] == 0

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_enabled"] is False
    assert apply_summary["backfill_apply_to_final"] is False
    assert apply_summary["backfill_applied"] is False
    assert apply_summary["apply_skip_reason"] == "backfill_feature_disabled"
    assert int(apply_summary["final_case_count"]) == 8

    generation_summary = _extract_gen_diag(db, "generation_summary")
    assert generation_summary["core_flow_backfill_enabled"] is False
    assert generation_summary["core_flow_backfill_applied"] is False
    assert int(generation_summary["primary_case_count_before_backfill"]) == 8
    assert int(generation_summary["final_case_count_after_backfill"]) == 8


def test_backfill_apply_to_final_replaces_final_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    call_counter = _configure_backfill_apply_env(
        monkeypatch,
        enabled=True,
        apply_to_final=True,
        merged_preview_cases=merged_preview_cases,
        coverage_after_ratio=1.0,
    )

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="apply merged backfill preview to final result",
        project_id=12,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    assert len(result) == 18
    assert call_counter["generate_backfill"] == 1
    assert sum(1 for item in result if str(item.get("id") or "").startswith("BF-")) == 12

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_enabled"] is True
    assert apply_summary["backfill_apply_to_final"] is True
    assert apply_summary["backfill_applied"] is True
    assert apply_summary["final_quality_gate_passed"] is True
    assert apply_summary["still_missing_core_flows"] == []
    assert int(apply_summary["primary_retained_count"]) == 6
    assert int(apply_summary["primary_trimmed_count"]) == 2
    assert int(apply_summary["backfill_retained_count"]) == 12
    assert int(apply_summary["backfill_trimmed_count"]) == 0

    generation_summary = _extract_gen_diag(db, "generation_summary")
    assert generation_summary["core_flow_backfill_enabled"] is True
    assert generation_summary["core_flow_backfill_applied"] is True
    assert int(generation_summary["final_count"]) == 18
    assert int(generation_summary["final_case_count_after_backfill"]) == 18
    assert float(generation_summary["core_flow_coverage_after"]) >= 0.8


def test_backfill_apply_to_final_blocks_non_assertable_merged_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    merged_preview_cases[0] = {
        **merged_preview_cases[0],
        "priority_final": None,
        "expected_result": "对应内容一致",
        "expected_result_quality": "non_assertable",
    }
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=True,
        apply_to_final=True,
        merged_preview_cases=merged_preview_cases,
        coverage_after_ratio=1.0,
    )

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="merged result quality gate should block apply",
        project_id=13,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, dict)
    assert result["error_code"] == "LOW_QUALITY_GENERATED_CASES"
    assert result["final_status"] == "quality_gate_failed"
    assert int(result["invalid_priority_final_count"]) >= 1
    assert int(result["non_assertable_expected_result_count"]) >= 1
    assert result["apply_skip_reason"] == "merged_result_quality_gate_failed"

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_applied"] is False
    assert apply_summary["final_quality_gate_passed"] is False
    assert apply_summary["apply_skip_reason"] == "merged_result_quality_gate_failed"


def test_backfill_apply_to_final_blocks_low_coverage_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=True,
        apply_to_final=True,
        merged_preview_cases=merged_preview_cases,
        coverage_after_ratio=0.5,
    )

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="merged result coverage threshold should block apply",
        project_id=14,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, dict)
    assert result["error_code"] == "LOW_QUALITY_GENERATED_CASES"
    assert result["final_status"] == "quality_gate_failed"
    assert result["apply_skip_reason"] == "merged_result_coverage_below_threshold"
    assert float(result["core_flow_coverage_ratio"]) < 0.8

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_applied"] is False
    assert apply_summary["final_quality_gate_passed"] is False
    assert apply_summary["apply_skip_reason"] == "merged_result_coverage_below_threshold"


def test_json_persistence_enforce_mode_blocks_without_workflow_contract(monkeypatch) -> None:
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=[],
    )
    monkeypatch.setattr(settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    monkeypatch.setattr(settings, "EXECUTION_PLAN_ALLOW_CANDIDATE_BLUEPRINT_WITHOUT_CONTRACT", False, raising=False)

    module = TestGenerationModule()
    db = _InMemoryActiveSession()
    result = module.generate_test_cases_json(
        requirement="formal persistence requires an executable workflow contract",
        project_id=15,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, dict)
    assert result["error_code"] == "execution_plan_failed"
    assert "workflow_contract_missing" in list(result.get("failure_reasons") or [])
    assert result.get("semantic_conflicts") == []
    assert result.get("execution_group_order_conflicts") == []
    assert db.generations == []
    persistence_gate = _extract_gen_diag(db, "persistence_gate")
    assert persistence_gate["blocked"] is True
    assert persistence_gate["failure_code"] == "execution_plan_failed"
