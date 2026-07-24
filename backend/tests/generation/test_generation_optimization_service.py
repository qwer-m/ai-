from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db.models import LogEntry, Project, TestGeneration as GenerationModel, User
from modules.test_generation_components.services import generation_optimization_service as gos
from modules.test_generation_components.services.generation_optimization_service import (
    GenerationOptimizationService,
    apply_optimization_patch,
    parse_optimization_patch,
)


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_sqlite(_type, _compiler, **_kw):
    return "TEXT"


def _make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(bind=engine)
    Project.__table__.create(bind=engine)
    GenerationModel.__table__.create(bind=engine)
    LogEntry.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    db.add(User(id=1, username="u1", email="u1@example.com", hashed_password="pw"))
    db.add(Project(id=10, user_id=1, name="p1"))
    db.commit()
    return db


def _case(case_id: str, description: str) -> dict:
    case = {
        "id": case_id,
        "description": description,
        "test_module": "论坛",
        "preconditions": ["用户已登录"],
        "steps": ["进入论坛", "执行操作", "查看结果"],
        "test_input": description,
        "expected_result": f"系统成功处理 {description}，页面展示对应结果",
        "priority": "P0",
        "priority_final": "P0",
    }
    case["_semantic"] = {
        "module_candidates": [
            {
                "module_key": "forum",
                "module_name": case["test_module"],
                "role": "primary",
                "confidence": 0.95,
                "evidence": [description],
            }
        ],
        "interaction_ids": [],
        "workflow_stage_candidates": [],
        "precondition_states": [],
        "produced_states": [],
    }
    return case


def _requirement_contract() -> dict:
    module_name = _case("TC-CONTRACT", "contract evidence")["test_module"]
    return {
        "semantic_contract_version": "requirement-semantic-v1",
        "status": "applied",
        "workflow_absence_declared": True,
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "forum",
                    "module_name": module_name,
                    "scope_status": "in_scope",
                }
            ],
            "module_interactions": [],
        },
        "workflow_blueprints": [],
    }


def _repair_contract() -> dict:
    return {
        "id": "declared-flow",
        "workflow_id": "declared-flow",
        "repository_source": "current_requirement_blueprint",
        "source_type": "current_requirement_extracted",
        "initial_state": "ready",
        "required_stage_ids": ["open", "submit", "visible"],
        "terminal_states": ["visible"],
        "steps": [
            {
                "id": "open",
                "action": "open workflow",
                "state_in": "ready",
                "state_out": "opened",
                "required": True,
                "critical": False,
                "blocking": False,
            },
            {
                "id": "submit",
                "action": "submit workflow",
                "state_in": "opened",
                "state_out": "submitted",
                "required": True,
                "critical": True,
                "blocking": True,
            },
            {
                "id": "visible",
                "action": "view submitted result",
                "state_in": "submitted",
                "state_out": "visible",
                "required": True,
                "terminal": True,
                "critical": False,
                "blocking": False,
            },
        ],
    }


def _execution_repair_validation() -> dict:
    contract = _repair_contract()
    return {
        "passed": False,
        "failure_reasons": ["required_stage_coverage_missing"],
        "metrics": {
            "workflow_blueprint_source": "current_requirement_blueprint",
            "workflow_closure": {
                "contract_present": True,
                "required_stage_ids": list(contract["required_stage_ids"]),
                "initial_state": contract["initial_state"],
                "terminal_states": list(contract["terminal_states"]),
                "declared_workflow_contract": contract,
            },
        },
    }


def test_parse_optimization_patch_rejects_full_rewrite_payload() -> None:
    status, payload = parse_optimization_patch(json.dumps({"cases": [_case("TC-001", "全量重写")]}))

    assert status == "error"
    assert "full_rewrite_keys_forbidden:cases" in payload["schema_errors"]


def test_parse_optimization_patch_accepts_string_fix_notes() -> None:
    status, payload = parse_optimization_patch(
        json.dumps(
            {
                "add_cases": [],
                "replace_cases": [],
                "drop_case_ids": [],
                "fix_notes": "no change needed",
            }
        )
    )

    assert status == "ok"
    assert payload["fix_notes"] == ["no change needed"]


def test_parse_optimization_patch_rejects_added_case_without_semantic_contract() -> None:
    added_case = _case("TC-001", "comment post")
    added_case.pop("_semantic")

    status, payload = parse_optimization_patch(
        json.dumps(
            {
                "add_cases": [added_case],
                "replace_cases": [],
                "drop_case_ids": [],
                "fix_notes": [],
            },
            ensure_ascii=False,
        ),
        requirement_contract=_requirement_contract(),
    )

    assert status == "error"
    assert any(
        item.startswith("add_cases[1]_semantic_contract_invalid:semantic_object_missing")
        for item in payload["schema_errors"]
    )


def test_parse_optimization_patch_rejects_invented_module_reference() -> None:
    added_case = _case("TC-001", "comment post")
    added_case["_semantic"]["module_candidates"][0]["module_key"] = "invented"
    added_case["_semantic"]["module_candidates"][0]["module_name"] = "Invented Module"

    status, payload = parse_optimization_patch(
        json.dumps(
            {
                "add_cases": [added_case],
                "replace_cases": [],
                "drop_case_ids": [],
                "fix_notes": [],
            },
            ensure_ascii=False,
        ),
        requirement_contract=_requirement_contract(),
    )

    assert status == "error"
    assert any(
        "module_candidates:no_verified_candidate" in item
        for item in payload["schema_errors"]
    )


def test_apply_optimization_patch_limits_drop_ratio() -> None:
    original = [_case(f"TC-{idx:03d}", f"场景 {idx}") for idx in range(1, 6)]

    status, _cases, summary = apply_optimization_patch(
        original,
        {"drop_case_ids": ["TC-001", "TC-002"]},
    )

    assert status == "drop_ratio_exceeded"
    assert summary["allowed_drop_count"] == 1


def test_apply_optimization_patch_keeps_presentation_order_separate_from_execution_order() -> None:
    original = [
        {
            **_case("TC-001", "display detail"),
            "execution_group": "display",
            "execution_sequence": 3,
        },
        {
            **_case("TC-002", "publish post"),
            "execution_group": "main_smoke",
            "execution_sequence": 1,
        },
        {
            **_case("TC-003", "permission denied"),
            "execution_group": "permission",
            "execution_sequence": 2,
        },
    ]

    status, cases, summary = apply_optimization_patch(
        original,
        {"add_cases": [], "replace_cases": [], "drop_case_ids": [], "fix_notes": []},
    )

    assert status == "ok"
    assert summary["result_count"] == 3
    assert [item["execution_group"] for item in cases] == ["main_smoke", "permission", "display"]
    assert [item["execution_sequence"] for item in cases] == [1, 2, 3]
    assert [item["presentation_order"] for item in cases] == [2, 3, 1]


def test_optimize_generation_persists_new_row_and_keeps_original(monkeypatch) -> None:
    db = _make_session()
    source_cases = [_case("TC-001", "发布帖子"), _case("TC-002", "查看帖子")]
    source = GenerationModel(
        id=21,
        user_id=1,
        project_id=10,
        requirement_text="论坛支持发帖、查看和评论。",
        generated_result=json.dumps(source_cases, ensure_ascii=False),
    )
    db.add(source)
    db.add(
        LogEntry(
            user_id=1,
            project_id=10,
            log_type="system",
            message="GEN_DIAG:" + json.dumps(
                {
                    "kind": "generation_quality_ledger",
                    "generation_id": 21,
                    "quality_score": 45,
                    "quality_score_grade": "critical",
                    "quality_remediation": {
                        "actions": [
                            {
                                "action_id": "cover_missing_rules",
                                "priority": "P0",
                                "reason": "missing_comment_case",
                            }
                        ]
                    },
                        "case_quality_gate": {"passed": False, "blocked": False},
                        "control": {
                            "requirement_semantic_contract": _requirement_contract(),
                        },
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()

    class FakeClient:
        def generate_response(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "add_cases": [_case("TC-003", "评论帖子")],
                    "replace_cases": [],
                    "drop_case_ids": [],
                    "fix_notes": ["补齐评论覆盖"],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(gos.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    status, payload = GenerationOptimizationService(db).optimize_generation(
        generation_id=21,
        user_id=1,
        apply=True,
        max_new_cases=5,
    )

    assert status == "ok", payload
    assert payload["source_generation_id"] == 21
    assert payload["generation_id"] != 21
    assert len(payload["cases"]) == 3
    assert db.query(GenerationModel).filter(GenerationModel.id == 21).first().generated_result == json.dumps(
        source_cases,
        ensure_ascii=False,
    )
    new_entry = db.query(GenerationModel).filter(GenerationModel.id == payload["generation_id"]).first()
    assert new_entry is not None
    assert len(json.loads(new_entry.generated_result)) == 3
    assert db.query(LogEntry).filter(LogEntry.message.like("%generation_optimization%")).count() >= 1


def test_optimize_preview_generation_persists_without_source_generation_id(monkeypatch) -> None:
    db = _make_session()
    preview_cases = [_case("TC-001", "发布帖子"), _case("TC-002", "查看帖子")]

    class FakeClient:
        def generate_response(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "add_cases": [_case("TC-003", "评论帖子")],
                    "replace_cases": [],
                    "drop_case_ids": [],
                    "fix_notes": ["补齐未落库预览结果的评论覆盖"],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(gos.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=10,
        user_id=1,
        requirement_text="论坛支持发帖、查看和评论。",
        cases=preview_cases,
        diagnostics={
            "requirementSemanticContract": _requirement_contract(),
            "persistenceGate": {
                "kind": "persistence_gate",
                "passed": False,
                "failure_code": "execution_plan_failed",
                "execution_plan_validation": {
                    "passed": False,
                    "failure_reasons": ["workflow_contract_missing"],
                },
            }
        },
        apply=True,
        max_new_cases=5,
    )

    assert status == "ok"
    assert payload["source_generation_id"] is None
    assert payload["generation_id"]
    assert len(payload["cases"]) == 3
    new_entry = db.query(GenerationModel).filter(GenerationModel.id == payload["generation_id"]).first()
    assert new_entry is not None
    assert len(json.loads(new_entry.generated_result)) == 3


def test_optimization_prompt_keeps_complete_case_catalog() -> None:
    cases = [_case(f"TC-{idx:03d}", f"场景 {idx}") for idx in range(1, 90)]
    ledger = {
        "judgeDecisionTableRows": [
            {
                "case_id": "TC-055",
                "judge_status": "REJECT",
                "reject_reason": "semantic_duplicate:TC-012",
                "duplicate_of_case_id": "TC-012",
            }
        ],
        "case_quality_gate": {
            "passed": False,
            "metrics": {"min_acceptable_final": 80, "final_count": 71},
        },
    }

    focused = gos._focused_case_briefs(cases, ledger)
    focused_ids = {item["id"] for item in focused}

    assert len(focused) == len(cases)
    assert focused[0]["id"] == "TC-001"
    assert focused[-1]["id"] == "TC-089"
    assert "TC-055" in focused_ids
    assert "TC-012" in focused_ids


def test_optimization_prompt_keeps_all_execution_repair_cases(monkeypatch) -> None:
    cases = [_case(f"TC-{idx:03d}", f"scene {idx}") for idx in range(1, 40)]
    for idx, case in enumerate(cases):
        if idx % 3 == 0:
            case["priority"] = "P0"
            case["priority_final"] = "P0"
    ledger = {
        "persistence_gate": {
            "passed": False,
            "blocked": True,
            "failure_code": "execution_plan_failed",
            "execution_plan_validation": _execution_repair_validation(),
        }
    }

    monkeypatch.setattr(gos.settings, "GENERATION_OPTIMIZATION_EXECUTION_CASE_COUNT", 8, raising=False)

    focused = gos._focused_case_briefs(cases, ledger)
    prompt = gos._build_prompt(
        requirement="forum requirement",
        cases=cases,
        ledger=ledger,
        max_new_cases=12,
        case_briefs=focused,
    )

    assert len(focused) == 39
    assert all("steps" in item for item in focused)
    assert all("expected_result" in item for item in focused)
    assert "Primary repair focus: execution_plan_failed" in prompt
    assert '"required_stage_ids": ["open", "submit", "visible"]' in prompt
    assert "Use P0 only when the declared stage is critical or blocking" in prompt
    assert "Do not include description, test_module, preconditions, steps, test_input, or expected_result" in prompt
    assert "Every add_cases item MUST also contain `_semantic`" in prompt


def test_execution_repair_prefers_declared_stage_ids_over_case_wording(monkeypatch) -> None:
    generic_cases = [_case(f"TC-{idx:03d}", f"generic case {idx}") for idx in range(1, 7)]
    declared_cases = []
    for offset, stage_id in enumerate(("open", "submit", "visible"), start=7):
        case = _case(f"TC-{offset:03d}", f"neutral wording {offset}")
        case["main_chain_stage"] = stage_id
        case["workflow_id"] = "declared-flow"
        declared_cases.append(case)
    ledger = {
        "persistence_gate": {
            "passed": False,
            "blocked": True,
            "failure_code": "execution_plan_failed",
            "execution_plan_validation": _execution_repair_validation(),
        }
    }
    monkeypatch.setattr(gos.settings, "GENERATION_OPTIMIZATION_EXECUTION_CASE_COUNT", 8, raising=False)

    focused = gos._focused_case_briefs([*generic_cases, *declared_cases], ledger)

    declared = [item for item in focused if item["main_chain_stage"]]
    assert [item["id"] for item in declared] == ["TC-007", "TC-008", "TC-009"]
    assert [item["main_chain_stage"] for item in declared] == ["open", "submit", "visible"]


def test_apply_optimization_patch_reorders_execution_groups_and_sequences() -> None:
    original = [
        {**_case("TC-001", "display suite"), "execution_group": "display", "execution_sequence": 50},
        {**_case("TC-002", "main smoke second"), "execution_group": "main_smoke", "execution_sequence": 2},
        {**_case("TC-003", "main smoke first"), "execution_group": "main_smoke", "execution_sequence": 1},
    ]

    status, cases, _summary = apply_optimization_patch(
        original,
        {"add_cases": [], "replace_cases": [], "drop_case_ids": [], "fix_notes": []},
    )

    assert status == "ok"
    assert [case["execution_group"] for case in cases] == ["main_smoke", "main_smoke", "display"]
    assert [case["execution_sequence"] for case in cases] == [1, 2, 3]


def test_apply_optimization_patch_demotes_stale_main_smoke_during_execution_repair() -> None:
    original = [
        {
            **_case(f"TC-{idx:03d}", f"main smoke {idx}"),
            "execution_group": "main_smoke",
            "execution_sequence": idx,
            "workflow_id": "old_flow",
            "source_state": f"s{idx - 1}",
            "target_state": f"s{idx}",
            "path_type": "positive",
            "blocking": False,
            "destructive": False,
            "can_advance_main_flow": True,
            "main_chain_stage_kind": "entry",
        }
        for idx in range(1, 5)
    ]
    replacements = [
        {
            "case_id": f"TC-{idx:03d}",
            "case": {
                "execution_group": "main_smoke",
                "execution_sequence": idx,
                "workflow_id": "declared-flow",
                "source_state": source_state,
                "target_state": target_state,
                "path_type": "positive",
                "blocking": stage_id == "submit",
                "destructive": False,
                "can_advance_main_flow": True,
                "main_chain_stage": stage_id,
                "main_chain_step": idx,
            },
        }
        for idx, (stage_id, source_state, target_state) in enumerate(
            (
                ("open", "ready", "opened"),
                ("submit", "opened", "submitted"),
                ("visible", "submitted", "visible"),
            ),
            start=1,
        )
    ]

    status, cases, summary = apply_optimization_patch(
        original,
        {"add_cases": [], "replace_cases": replacements, "drop_case_ids": [], "fix_notes": []},
        execution_repair=True,
        execution_repair_contract=_repair_contract(),
    )

    assert status == "ok"
    assert summary["demoted_stale_main_smoke_count"] == 1
    assert summary["active_required_stage_ids"] == ["open", "submit", "visible"]
    main_cases = [case for case in cases if case.get("execution_group") == "main_smoke"]
    assert len(main_cases) == 3
    assert [case["main_chain_stage"] for case in main_cases] == ["open", "submit", "visible"]
    assert [case["source_state"] for case in main_cases] == ["ready", "opened", "submitted"]
    demoted = [case for case in cases if case.get("description") == "main smoke 4"][0]
    assert demoted["execution_group"] == "independent_functional"
    assert "source_state" not in demoted


def test_apply_optimization_patch_does_not_demote_main_chain_without_declared_contract() -> None:
    original = [
        {**_case(f"TC-{idx:03d}", f"main smoke {idx}"), "execution_group": "main_smoke"}
        for idx in range(1, 5)
    ]

    status, cases, summary = apply_optimization_patch(
        original,
        {"add_cases": [], "replace_cases": [], "drop_case_ids": [], "fix_notes": []},
        execution_repair=True,
    )

    assert status == "ok", payload
    assert "demoted_stale_main_smoke_count" not in summary
    assert len([case for case in cases if case.get("execution_group") == "main_smoke"]) == 4


def test_optimize_preview_generation_reports_model_timeout(monkeypatch) -> None:
    db = _make_session()
    preview_cases = [_case("TC-001", "发布帖子"), _case("TC-002", "查看帖子")]

    class TimeoutClient:
        def generate_response(self, *_args, **_kwargs):
            return "Exception occurred: The read operation timed out"

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: TimeoutClient())

    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=10,
        user_id=1,
        requirement_text="论坛支持发帖、查看和评论。",
        cases=preview_cases,
        diagnostics={"caseQualityGate": {"passed": False, "metrics": {"min_acceptable_final": 2}}},
        apply=True,
        max_new_cases=5,
    )

    assert status == "model_timeout"
    assert payload["message"] == "optimization_model_timeout"
    assert payload["prompt_case_count"] > 0


def test_optimize_preview_recovers_after_response_timeout(monkeypatch) -> None:
    db = _make_session()
    preview_cases = [_case("TC-001", "publish post"), _case("TC-002", "view post")]
    calls: list[dict] = []

    class Provider:
        pass

    class FlakyResponseClient:
        last_response_metadata = {}
        provider = Provider()

        def generate_response(self, _requirement, prompt, **kwargs):
            calls.append(
                {
                    "mode": "response",
                    "prompt": prompt,
                    "max_tokens": kwargs.get("max_tokens"),
                    "timeout": getattr(self.provider, "request_timeout_seconds", None),
                }
            )
            if len(calls) == 1:
                return "Exception occurred: The read operation timed out"
            return json.dumps(
                {
                    "add_cases": [_case("TC-003", "comment post")],
                    "replace_cases": [],
                    "drop_case_ids": [],
                    "fix_notes": ["stream retry recovered"],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: FlakyResponseClient())
    monkeypatch.setattr(gos.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=10,
        user_id=1,
        requirement_text="forum publish, view, and comment",
        cases=preview_cases,
        diagnostics={
            "requirementSemanticContract": _requirement_contract(),
            "persistenceGate": {
                "passed": False,
                "failure_code": "execution_plan_failed",
                "execution_plan_validation": {
                    "passed": False,
                    "failure_reasons": ["workflow_contract_missing"],
                },
            }
        },
        apply=True,
        max_new_cases=5,
    )

    assert status == "ok"
    assert len(calls) >= 2
    assert calls[0]["max_tokens"] == gos.DEFAULT_OPTIMIZATION_MAX_TOKENS
    assert "[Declared workflow repair contract]" not in calls[0]["prompt"]
    assert calls[0]["timeout"] == gos.DEFAULT_OPTIMIZATION_HTTP_TIMEOUT_SECONDS
    assert payload["optimization_summary"]["batch_count"] == 1
    assert len(payload["cases"]) == 3


def test_optimize_preview_recovers_after_reasoning_only_empty_response(monkeypatch) -> None:
    db = _make_session()
    preview_cases = [_case("TC-001", "publish post"), _case("TC-002", "view post")]
    calls: list[dict] = []

    class Provider:
        pass

    class ReasoningOnlyClient:
        last_response_metadata = {}
        provider = Provider()

        def generate_response(self, _requirement, prompt, **kwargs):
            calls.append(
                {
                    "prompt": prompt,
                    "max_tokens": kwargs.get("max_tokens"),
                    "disable_response_format": getattr(self.provider, "disable_json_response_format", False),
                    "disable_reasoning_effort": getattr(self.provider, "disable_json_reasoning_effort", False),
                }
            )
            if len(calls) == 1:
                self.last_response_metadata = {
                    "http_status": 200,
                    "finish_reason": "length",
                    "content_len": 0,
                    "reasoning_len": 4187,
                }
                return "Error: Empty response from model glm-5.1"
            self.last_response_metadata = {
                "http_status": 200,
                "finish_reason": "stop",
                "content_len": 120,
                "reasoning_len": 0,
            }
            return json.dumps(
                {
                    "add_cases": [_case("TC-003", "comment post")],
                    "replace_cases": [],
                    "drop_case_ids": [],
                    "fix_notes": ["json compat retry recovered"],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: ReasoningOnlyClient())
    monkeypatch.setattr(gos.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=10,
        user_id=1,
        requirement_text="forum publish, view, and comment",
        cases=preview_cases,
        diagnostics={
            "requirementSemanticContract": _requirement_contract(),
            "persistenceGate": {
                "passed": False,
                "failure_code": "execution_plan_failed",
                "execution_plan_validation": {
                    "passed": False,
                    "failure_reasons": ["workflow_contract_missing"],
                },
            }
        },
        apply=True,
        max_new_cases=5,
    )

    assert status == "ok"
    assert len(calls) == 2
    assert calls[0]["disable_response_format"] is True
    assert calls[0]["disable_reasoning_effort"] is True
    assert calls[1]["disable_response_format"] is True
    assert calls[1]["disable_reasoning_effort"] is True
    assert calls[1]["max_tokens"] == gos.DEFAULT_OPTIMIZATION_MAX_TOKENS
    assert "[Declared workflow repair contract]" not in calls[0]["prompt"]
    first_batch = payload["optimization_summary"]["prompt_batches"][0]
    assert first_batch["call_mode"] == "response_json_compat_retry"
    assert first_batch["retry_reason"] == "reasoning_only_empty_response"
    assert len(payload["cases"]) == 3


def test_optimize_preview_generation_uses_one_complete_global_prompt(monkeypatch) -> None:
    db = _make_session()
    preview_cases = [_case(f"TC-{idx:03d}", f"scene {idx}") for idx in range(1, 31)]
    prompts: list[str] = []

    class RecordingClient:
        def generate_response(self, _requirement, prompt, **_kwargs):
            prompts.append(prompt)
            return json.dumps(
                {
                    "add_cases": [],
                    "replace_cases": [],
                    "drop_case_ids": [],
                    "fix_notes": ["batch noop"],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: RecordingClient())
    monkeypatch.setattr(gos.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)
    monkeypatch.setattr(gos.settings, "GENERATION_OPTIMIZATION_BATCH_CASE_COUNT", 5, raising=False)

    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=10,
        user_id=1,
        requirement_text="forum multi-scenario quality repair",
        cases=preview_cases,
        diagnostics={
            "requirementSemanticContract": _requirement_contract(),
            "caseQualityGate": {
                "passed": False,
                "failure_reasons": ["quality_score_critical"],
                "metrics": {"final_count": 30, "min_acceptable_final": 20},
            }
        },
        apply=True,
        max_new_cases=10,
    )

    assert status == "ok"
    assert len(prompts) == 1
    assert "global optimization pass 1/1" in prompts[0]
    assert '"id": "TC-001"' in prompts[0]
    assert '"id": "TC-030"' in prompts[0]
    assert payload["optimization_summary"]["batch_count"] == 1
    assert payload["optimization_summary"]["global_candidate_count"] == 30


def test_optimize_preview_uses_original_min_acceptable_floor(monkeypatch) -> None:
    db = _make_session()
    preview_cases = [_case(f"TC-{idx:03d}", f"场景 {idx}") for idx in range(1, 89)]

    class NoopPatchClient:
        def generate_response(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "add_cases": [],
                    "replace_cases": [],
                    "drop_case_ids": [],
                    "fix_notes": ["保持当前可用用例，沿用原始最低可接受数量"],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(gos, "get_optimization_ai_client", lambda **_kwargs: NoopPatchClient())
    monkeypatch.setattr(gos.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=10,
        user_id=1,
        requirement_text="论坛支持多路径回归。",
        cases=preview_cases,
        diagnostics={
            "requirementSemanticContract": _requirement_contract(),
            "caseQualityGate": {
                "passed": False,
                "failure_reasons": ["final_count_below_min_acceptable"],
                "metrics": {"final_count": 71, "min_acceptable_final": 80},
            }
        },
        apply=True,
        max_new_cases=5,
    )

    assert status == "ok"
    assert payload["case_quality_gate"]["metrics"]["min_acceptable_final"] == 80
    assert len(payload["cases"]) == 88
