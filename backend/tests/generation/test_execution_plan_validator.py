from __future__ import annotations

import json
from types import SimpleNamespace

import modules.testing.test_generation_components.legacy.stream.persist as stream_persist_mod
from fastapi import HTTPException
from modules.testing.test_generation_components.legacy.stream.persist import (
    LegacyGenerationStreamPersistMixin,
)
from modules.testing.test_generation_components.postprocess.execution_plan_validator import (
    ExecutionPlanValidationPolicy,
    materialize_final_case_state_fields,
    validate_execution_group_order,
    validate_execution_plan,
    validate_main_smoke_semantic_alignment,
)
from modules.testing.test_generation_components.postprocess.persistence_gate import (
    evaluate_persistence_gate,
    summarize_persistence_case_quality_gate,
)
from modules.testing.test_generation_components.postprocess.case_contract import (
    project_persistable_cases,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    filter_invalid_final_cases,
    merge_cases_for_append,
    strip_case_meta_fields,
)
from routers.automation import test_generation_generate_routes_impl as generate_routes
from routers.automation import test_generation_generate_routes_json as generate_json_routes
from schemas.automation.test_generation import TestGenRequest as _TestGenRequest


def _main_chain_cases() -> list[dict]:
    stages = [
        ("entry", "ready", "started", "open workflow entry"),
        ("configure", "started", "configured", "select courses and configure schedule time"),
        ("preview", "configured", "preview_ready", "preview schedule plan before save"),
        ("commit", "preview_ready", "committed", "save plan and confirm creation"),
        ("downstream_visibility", "committed", "visible", "saved plan is synced and visible on student home"),
        ("consume", "visible", "consumed", "student clicks visible course and enters learning"),
    ]
    return [
        {
            "id": f"TC-{index:03d}",
            "description": description,
            "priority": "P0",
            "execution_group": "main_smoke",
            "main_chain_step": index,
            "role": "student",
            "session_key": "student_session",
            "expected_result": f"state reaches {target_state}",
            "workflow_transition": {
                "workflow_id": "schedule_flow",
                "source_state": source_state,
                "action": stage_kind,
                "target_state": target_state,
                "path_type": "positive",
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "state_transition_confidence": 0.9,
                "stage_kind": stage_kind,
            },
        }
        for index, (stage_kind, source_state, target_state, description) in enumerate(stages, start=1)
    ]


def _settings(mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        EXECUTION_PLAN_GATE_MODE=mode,
        EXECUTION_PLAN_MIN_MAIN_SMOKE_COUNT=6,
        EXECUTION_PLAN_MIN_P0_COUNT=6,
        EXECUTION_PLAN_MIN_STATE_FIELD_COVERAGE=0.8,
        EXECUTION_PLAN_MAX_WORKFLOW_ID_MISSING_RATE=0.2,
        EXECUTION_PLAN_REJECT_CANDIDATE_DERIVED_BLUEPRINT=True,
    )


def _trusted_blueprint() -> dict:
    return {
        "id": "schedule_flow",
        "workflow_id": "schedule_flow",
        "source_type": "human_reviewed",
        "repository_source": "workflow_blueprint_repository",
        "trusted": True,
        "steps": [
            {"id": "start", "label": "start", "action": "start", "state_in": "ready", "state_out": "started"},
            {"id": "commit", "label": "commit", "action": "commit", "state_in": "started", "state_out": "committed"},
        ],
    }


def test_validator_accepts_connected_main_smoke_and_materializes_final_fields() -> None:
    cases = _main_chain_cases()

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )

    assert result["passed"] is True
    assert result["failure_reasons"] == []
    assert result["metrics"]["state_conflict_count"] == 0
    assert result["metrics"]["linear_executable"] is True
    assert result["cases"][0]["workflow_id"] == "schedule_flow"
    assert result["cases"][0]["source_state"] == "ready"
    assert result["cases"][0]["target_state"] == "started"


def test_validate_execution_group_order_rejects_main_smoke_after_side_suite() -> None:
    cases = [
        {"id": "TC-001", "execution_group": "main_smoke"},
        {"id": "TC-002", "execution_group": "permission"},
        {"id": "TC-003", "execution_group": "main_smoke"},
    ]

    conflicts = validate_execution_group_order(cases)

    assert conflicts == [
        {
            "case_id": "TC-003",
            "index": 3,
            "execution_group": "main_smoke",
            "reason": "main_smoke_after_independent_suite",
        }
    ]


def test_validate_execution_group_order_rejects_execution_sequence_mismatch() -> None:
    cases = [
        {"id": "TC-001", "execution_group": "main_smoke", "execution_sequence": 1},
        {"id": "TC-002", "execution_group": "permission", "execution_sequence": 4},
    ]

    conflicts = validate_execution_group_order(cases)

    assert conflicts == [
        {
            "case_id": "TC-002",
            "index": 2,
            "execution_sequence": 4,
            "execution_group": "permission",
            "reason": "execution_sequence_mismatch",
        }
    ]


def test_validator_rejects_final_json_array_execution_group_order_conflict() -> None:
    cases = [
        *_main_chain_cases(),
        {
            "id": "TC-display",
            "description": "display saved plan detail",
            "priority": "P2",
            "execution_group": "display",
        },
        {
            "id": "TC-permission",
            "description": "permission check for saving plan",
            "priority": "P2",
            "execution_group": "permission",
        },
    ]

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )

    assert result["passed"] is False
    assert "execution_group_order_conflict" in result["failure_reasons"]
    assert result["metrics"]["execution_group_order_conflict_count"] == 1
    assert result["execution_group_order_conflicts"][0]["reason"] == "side_suite_rank_decreased"
    assert result["execution_group_order_conflicts"][0]["previous_execution_group"] == "display"


def test_validator_treats_save_and_display_case_as_commit_not_downstream() -> None:
    actions = [
        ("open workflow", "ready", "started"),
        ("configure schedule", "started", "configured"),
        ("review preview", "configured", "preview_ready"),
        ("save plan and display confirmation", "preview_ready", "committed"),
        ("display saved plan on student home", "committed", "visible"),
        ("learn course from plan and enter learning", "visible", "consumed"),
    ]
    cases = [
        {
            "id": f"TC-{index:03d}",
            "description": action,
            "priority": "P0",
            "workflow_id": "schedule_flow",
            "source_state": source_state,
            "action": action,
            "target_state": target_state,
            "path_type": "positive",
            "blocking": False,
            "destructive": False,
            "can_advance_main_flow": True,
            "execution_group": "main_smoke",
            "main_chain_step": index,
            "role": "student",
            "session_key": "student_session",
            "expected_result": "saved plan is visible" if index == 5 else f"state reaches {target_state}",
        }
        for index, (action, source_state, target_state) in enumerate(actions, start=1)
    ]

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )

    assert result["passed"] is True
    assert result["metrics"]["main_chain_stage_kinds"][3] == "commit"
    assert result["metrics"]["main_chain_stage_kinds"][4] == "downstream_visibility"
    assert result["metrics"]["commit_downstream_completion_closed"] is True


def test_gate_validation_uses_internal_stage_kind_before_public_projection() -> None:
    cases = _main_chain_cases()
    cases[3]["workflow_transition"]["action"] = "show saved plan confirmation"
    cases[3]["workflow_transition"]["stage_kind"] = "commit"

    gate_cases = materialize_final_case_state_fields(cases)
    result = validate_execution_plan(
        gate_cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        generation_mode="stream",
    )
    projected = project_persistable_cases(cases)

    assert gate_cases[3]["workflow_id"] == "schedule_flow"
    assert "workflow_id" not in projected[3]
    assert "main_chain_stage_kind" not in projected[3]
    assert result["passed"] is True
    assert result["metrics"]["commit_downstream_completion_closed"] is True


def test_validator_accepts_view_as_consume_action() -> None:
    conflicts = validate_main_smoke_semantic_alignment(
        [
            {
                "id": "TC-001",
                "description": "Student views AI question prompt before answering",
                "test_module": "AI question prompt",
                "steps": ["View current AI question prompt"],
                "expected_result": "question prompt is ready for student answer",
                "execution_group": "main_smoke",
                "main_chain_stage_kind": "consume",
            }
        ]
    )

    assert conflicts == []


def test_validator_accepts_chinese_preview_and_downstream_terms() -> None:
    conflicts = validate_main_smoke_semantic_alignment(
        [
            {
                "id": "TC-001",
                "description": "帖子详情页-预览并查看图片展示",
                "test_module": "论坛详情",
                "steps": ["进入帖子详情页", "查看预览区域和图片展示"],
                "expected_result": "帖子详情内容可查看，图片展示正确",
                "execution_group": "main_smoke",
                "main_chain_stage_kind": "preview",
                "action": "preview_post_detail",
            },
            {
                "id": "TC-002",
                "description": "发帖提交后消息小红点显示且帖子列表可见",
                "test_module": "论坛消息",
                "steps": ["提交帖子后进入消息页", "查看消息小红点和帖子列表"],
                "expected_result": "消息通知显示，小红点出现，帖子在列表可见",
                "execution_group": "main_smoke",
                "main_chain_stage_kind": "downstream_visibility",
                "action": "display_message_notification",
            },
        ]
    )

    assert conflicts == []


def test_validator_rejects_generation_490_weak_token_main_chain_mismatches() -> None:
    cases = [
        {
            "id": "TC-003",
            "description": "审核后台支持按用户发帖/回帖内容模糊搜索",
            "test_module": "审核后台",
            "steps": [
                "1. 在搜索框输入帖子内容关键词",
                "2. 点击搜索",
                "3. 验证搜索结果包含匹配内容",
                "4. 输入回帖内容关键词搜索",
                "5. 验证搜索结果匹配",
            ],
            "expected_result": "搜索结果返回包含关键词的帖子或回帖内容，支持模糊匹配；无全文检索功能",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "edit",
            "main_chain_stage_label": "编辑发帖内容",
            "action": "输入帖子文案与图片",
        },
        {
            "id": "TC-004",
            "description": "置顶帖展示官方图标、帖子标题与发帖时间",
            "test_module": "论坛首页-内容列表",
            "steps": [
                "1. 进入论坛首页官方区",
                "2. 查看置顶帖的展示内容",
                "3. 确认是否包含官方图标、帖子标题和发帖时间",
                "4. 确认置顶帖固定在列表顶部位置",
            ],
            "expected_result": "置顶帖在列表顶部展示，包含官方图标标识、帖子标题文本和发帖时间；置顶帖位置固定，不会被普通帖子覆盖",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "preview",
            "main_chain_stage_label": "预览发帖内容",
            "action": "确认帖子内容与图片",
        },
        {
            "id": "TC-005",
            "description": "帖子详情页回复帖子完整闭环",
            "test_module": "帖子详情页-回复流程",
            "steps": [
                "1. 点击页面右下角悬浮回帖按钮",
                "2. 在回复弹窗输入回复内容",
                "3. 点击发送按钮",
                "4. 检查回复是否出现在回复列表中",
                "5. 检查回复楼层号和时间显示",
            ],
            "expected_result": "1.点击回帖按钮后弹出回复弹窗，提示词为'我说两句'；2.输入内容后点击发送成功提交；3.回复出现在回复列表中，内容与输入一致；4.回复显示楼层号（如第N楼）；5.回复时间显示为'刚刚'",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "commit",
            "main_chain_stage_label": "提交发布帖子",
            "action": "点击发布按钮提交帖子",
        },
        {
            "id": "TC-006",
            "description": "审核通过消息点击跳转至帖子详情页",
            "test_module": "功能-审核通过消息跳转",
            "steps": ["1. 进入消息分区", "2. 切换到系统消息TAB", "3. 找到审核通过的消息", "4. 点击该消息"],
            "expected_result": "点击后跳转至对应帖子的详情页，帖子内容完整展示",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "downstream_visibility",
            "main_chain_stage_label": "查看帖子详情",
            "action": "点击帖子进入详情页查看",
        },
    ]

    conflicts = validate_main_smoke_semantic_alignment(cases)
    reasons_by_case: dict[str, set[str]] = {}
    for conflict in conflicts:
        reasons_by_case.setdefault(conflict["case_id"], set()).add(conflict["reason"])

    assert {"TC-003", "TC-004", "TC-005", "TC-006"}.issubset(reasons_by_case)
    assert "stage_text_lacks_edit_action" in reasons_by_case["TC-003"]
    assert all(
        "stage_action_not_supported_by_case_text" in reasons_by_case[case_id]
        for case_id in {"TC-003", "TC-004", "TC-005", "TC-006"}
    )


def test_validator_reports_disconnected_state_and_blocking_main_case() -> None:
    cases = _main_chain_cases()
    cases[2]["workflow_transition"]["source_state"] = "unexpected_state"
    cases[3]["workflow_transition"]["blocking"] = True
    cases[3]["workflow_transition"]["can_advance_main_flow"] = False

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = [item["reason"] for item in result["state_conflicts"]]
    assert result["passed"] is False
    assert "state_chain_conflict" in result["failure_reasons"]
    assert "state_not_connected" in reasons
    assert "blocking_case_in_main_smoke" in reasons
    assert "non_advancing_case_in_main_smoke" in reasons


def test_semantic_alignment_uses_alias_case_text_fields() -> None:
    cases = [
        {
            "caseId": "TC-ALIAS",
            "module": "Schedule Commit",
            "title": "save plan and confirm creation",
            "testSteps": ["save plan"],
            "testInput": "configured plan",
            "expectedResult": "state reaches committed",
            "execution_group": "main_smoke",
            "main_chain_step": 1,
            "role": "student",
            "session_key": "student_session",
            "workflow_transition": {
                "workflow_id": "schedule_flow",
                "source_state": "preview_ready",
                "action": "save plan",
                "target_state": "committed",
                "path_type": "positive",
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "state_transition_confidence": 0.9,
                "stage_kind": "commit",
            },
        }
    ]

    assert validate_main_smoke_semantic_alignment(cases) == []


def test_semantic_alignment_does_not_treat_stage_action_as_module_anchor() -> None:
    cases = [
        {
            "id": "TC-004",
            "test_module": "论坛浏览发帖主流程",
            "description": "预览帖子内容",
            "steps": ["预览待提交帖子内容"],
            "expected_result": "标题、正文和图片显示正确",
            "execution_group": "main_smoke",
            "main_chain_step": 4,
            "main_chain_stage_label": "预览帖子内容",
            "role": "student",
            "session_key": "student_session",
            "workflow_transition": {
                "workflow_id": "forum_post_flow",
                "source_state": "editing_post",
                "action": "预览帖子内容",
                "target_state": "post_ready",
                "path_type": "positive",
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "state_transition_confidence": 0.9,
                "stage_kind": "preview",
            },
        }
    ]

    assert validate_main_smoke_semantic_alignment(cases) == []

    cases[0]["main_chain_stage_module"] = "评论区"
    reasons = {
        item["reason"]
        for item in validate_main_smoke_semantic_alignment(cases)
    }

    assert "stage_module_not_aligned_with_blueprint" in reasons


def test_validator_rejects_stage_labels_that_do_not_match_case_text() -> None:
    cases = _main_chain_cases()
    cases[1]["description"] = "existing plan list is sorted by course time and status labels"
    cases[1]["expected_result"] = "list rows are sorted and marked completed or in progress"
    cases[2]["description"] = "exit schedule creation and verify selected courses are not retained"
    cases[2]["expected_result"] = "selection is cleared and the page returns to blank initial state"
    cases[3]["description"] = "preview schedule plan"
    cases[3]["steps"] = ["preview_schedule_plan"]
    cases[3]["expected_result"] = "schedule_preview_ready"
    cases[3]["generated_bridge_case"] = True

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "reset_or_abort_case_in_main_smoke" in reasons
    assert "stage_text_lacks_configure_action" in reasons
    assert "passive_list_status_case_used_as_configure" in reasons
    assert "generated_bridge_case_in_final_main_smoke" in reasons
    assert "internal_placeholder_text_in_final_main_smoke" in reasons


def test_validator_rejects_workflow_action_not_supported_by_case_text() -> None:
    cases = _main_chain_cases()
    cases[3]["description"] = "Course list switches grade version and keeps the previous records"
    cases[3]["test_module"] = "Course list"
    cases[3]["steps"] = [
        "Open the switch grade version dialog",
        "Confirm switching to another grade version",
    ]
    cases[3]["expected_result"] = "The old grade version records are retained and navigation follows the target grade status"
    cases[3]["workflow_transition"]["stage_kind"] = "commit"
    cases[3]["workflow_transition"]["action"] = "Finish all assessment questions and submit the assessment"
    cases[3]["main_chain_stage_label"] = "Submit assessment"

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "stage_action_not_supported_by_case_text" in reasons


def test_validator_rejects_management_report_case_as_student_completion_sync() -> None:
    cases = _main_chain_cases()
    terminal = cases[-1]
    terminal["description"] = "student info table shows report and history buttons after class"
    terminal["test_module"] = "student info table learning progress"
    terminal["expected_result"] = "report and history buttons open the report page and history page"
    terminal["role"] = "student"
    terminal["workflow_transition"]["stage_kind"] = "completion_sync"
    terminal["workflow_transition"]["action"] = "update_learning_progress"
    terminal["workflow_transition"]["target_state"] = "progress_updated"

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "student_role_with_management_surface_text" in reasons
    assert "report_history_case_not_completion_sync" in reasons


def test_validator_rejects_conditional_visibility_and_resume_state_in_main_smoke() -> None:
    cases = _main_chain_cases()
    cases[4]["description"] = "Only when quiz accuracy is greater than 50%, the review button is visible"
    cases[4]["expected_result"] = "the review button is visible only for the threshold condition"
    cases[5]["description"] = "Re-enter unfinished flow and verify retained dialog history"
    cases[5]["expected_result"] = "retained dialog history is displayed after reentry"

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "conditional_visibility_case_in_main_smoke" in reasons
    assert "resume_state_case_in_main_smoke" in reasons


def test_validator_rejects_candidate_derived_blueprint_as_strong_proof() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "current_generation_cases"},
        policy=ExecutionPlanValidationPolicy(allow_candidate_blueprint_without_contract=False),
    )

    assert result["passed"] is False
    assert "workflow_contract_missing" in result["failure_reasons"]
    assert "untrusted_candidate_derived_blueprint" in result["failure_reasons"]


def test_validator_allows_candidate_blueprint_when_no_contract_is_available() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "current_generation_cases"},
    )

    assert result["passed"] is True
    assert result["metrics"]["trusted_workflow_contract_count"] == 0
    assert result["metrics"]["candidate_blueprint_without_contract_allowed"] is True


def test_validator_rejects_priority_pool_blueprint_without_repository_trust() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[
            {
                "id": "schedule_flow",
                "source_type": "linked_final_case_workflow_blueprint",
                "source": "priority_sample_pool",
                "steps": [
                    {"id": "start", "label": "start", "state_in": "ready", "state_out": "started"},
                    {"id": "commit", "label": "commit", "state_in": "started", "state_out": "committed"},
                ],
            }
        ],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    assert result["passed"] is False
    assert "workflow_contract_missing" in result["failure_reasons"]
    assert result["metrics"]["workflow_blueprint_count"] == 1
    assert result["metrics"]["trusted_workflow_contract_count"] == 0


def test_persistence_gate_shadows_then_enforces_execution_failure() -> None:
    broken_cases = materialize_final_case_state_fields(_main_chain_cases()[:2])

    shadow = evaluate_persistence_gate(
        broken_cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        settings=_settings("shadow"),
    )
    enforced = evaluate_persistence_gate(
        broken_cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        settings=_settings("enforce"),
    )

    assert shadow["passed"] is True
    assert shadow["execution_plan_would_block"] is True
    assert enforced["passed"] is False
    assert enforced["failure_code"] == "execution_plan_failed"


def test_persistence_gate_always_blocks_empty_formal_result() -> None:
    result = evaluate_persistence_gate([], settings=_settings("shadow"))

    assert result["passed"] is False
    assert result["failure_code"] == "EMPTY_GENERATED_RESULT"


def test_persistence_case_quality_gate_fails_batch_quality_metrics() -> None:
    quality = summarize_persistence_case_quality_gate(
        {"passed": True, "failed_checks": []},
        generation_summary={"final_count": 89, "min_acceptable_final": 104},
        review_decision_summary={
            "final_scenario_duplicate_case_count": 41,
            "final_flow_misordered_count": 0,
        },
        judge_summary={"rejected_out_count": 44},
    )

    assert quality["passed"] is False
    assert {
        "judge_rejected_above_threshold",
        "final_scenario_duplicates_above_threshold",
    }.issubset(set(quality["failed_checks"]))
    assert quality["metrics"]["final_count"] == 89
    assert quality["metrics"]["judge_rejected_count"] == 44
    assert quality["metrics"]["quantity_shortfall_warning"] is True


def test_persistence_gate_treats_candidate_insufficient_underfill_as_advisory() -> None:
    quality = summarize_persistence_case_quality_gate(
        {"passed": True, "failed_checks": []},
        generation_summary={
            "final_count": 60,
            "min_acceptable_final": 85,
            "underfilled": True,
            "underfill_reason": "valid_candidate_insufficient",
            "underfill_root_cause": "candidate_insufficient",
            "quality_assessment": "medium",
        },
        review_decision_summary={
            "final_scenario_duplicate_case_count": 0,
            "final_flow_misordered_count": 0,
        },
        judge_summary={"rejected_out_count": 4},
    )

    assert quality["passed"] is True
    assert "final_count_below_min_acceptable" not in set(quality["failed_checks"])
    assert quality["metrics"]["quantity_shortfall_advisory"] is True

    gate = evaluate_persistence_gate(
        _main_chain_cases(),
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        quality_gate=quality,
        settings=_settings("enforce"),
    )

    assert gate["passed"] is True
    assert gate["failure_code"] == ""


def test_persistence_gate_treats_count_shortfall_as_soft_warning() -> None:
    quality = {
        "passed": False,
        "failed_checks": ["final_count_below_min_acceptable"],
        "metrics": {"final_count": 74, "min_acceptable_final": 85},
    }

    gate = evaluate_persistence_gate(
        _main_chain_cases(),
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        quality_gate=quality,
        settings=_settings("enforce"),
    )

    assert gate["passed"] is True
    assert gate["failure_code"] == ""
    assert gate["quality_would_block"] is False
    assert gate["quality_would_warn"] is True
    assert gate["quality_soft_failures"] == ["final_count_below_min_acceptable"]


def test_final_case_strip_preserves_formal_priority_and_execution_fields() -> None:
    cases = strip_case_meta_fields(
        [
            {
                "id": "TC-001",
                "priority": "P1",
                "priority_final": "P0",
                "priority_decision_source": "execution_plan_final_priority",
                "workflow_id": "schedule_flow",
                "source_state": "draft",
                "target_state": "saved",
                "execution_group": "main_smoke",
                "role": "student",
                "session_key": "student_session",
            }
        ]
    )

    assert cases[0]["priority"] == "P0"
    assert cases[0]["priority_final"] == "P0"
    assert cases[0]["workflow_id"] == "schedule_flow"
    assert cases[0]["source_state"] == "draft"
    assert cases[0]["target_state"] == "saved"
    assert cases[0]["execution_group"] == "main_smoke"
    assert cases[0]["role"] == "student"
    assert cases[0]["session_key"] == "student_session"
    assert "priority_decision_source" not in cases[0]


def test_final_filter_blocks_reasoning_leakage_in_test_input() -> None:
    result = filter_invalid_final_cases(
        [
            {
                "id": "TC-001",
                "description": "valid case",
                "steps": ["open page"],
                "test_input": "需考虑午休？需求未明确，此处假设连续排",
                "expected_result": "shows saved plan",
                "priority": "P1",
            }
        ]
    )

    assert result == []


def test_final_filter_blocks_reasoning_leakage_in_alias_fields() -> None:
    result = filter_invalid_final_cases(
        [
            {
                "caseId": "TC-ALIAS",
                "title": "valid case",
                "testSteps": ["open page"],
                "testInput": "need product confirm before assuming here",
                "expectedResult": "shows saved plan",
                "Priority": "P1",
            }
        ]
    )

    assert result == []


def test_append_merge_semantically_deduplicates_new_cases() -> None:
    existing = [
        {
            "id": "TC-001",
            "test_module": "学习计划",
            "description": "查看全部学习计划跳转",
            "steps": ["点击查看全部学习计划"],
            "test_input": "存在学习计划",
            "expected_result": "进入学习计划列表页",
            "priority": "P1",
            "priority_final": "P1",
        }
    ]
    new_cases = [
        {
            "id": "TC-002",
            "test_module": "学习计划",
            "description": "点击查看全部学习计划后跳转列表",
            "steps": ["点击查看全部学习计划"],
            "test_input": "存在学习计划",
            "expected_result": "进入学习计划列表页",
            "priority": "P1",
            "priority_final": "P1",
        },
        {
            "id": "TC-003",
            "test_module": "学习进度",
            "description": "学习完成后更新进度",
            "steps": ["完成一节课"],
            "test_input": "课程可学习",
            "expected_result": "学习进度增加",
            "priority": "P0",
            "priority_final": "P0",
        },
    ]

    def _dedupe(cases):  # noqa: ANN001, ANN202
        return [dict(item) for item in cases]

    def _reorder(cases, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return [dict(item) for item in cases]

    merged = merge_cases_for_append(
        existing,
        new_cases,
        deduplicate_test_cases_fn=_dedupe,
        reorder_cases_by_closed_loop_fn=_reorder,
    )

    assert [item["id"] for item in merged] == ["TC-001", "TC-003"]


def test_append_merge_semantically_deduplicates_alias_cases() -> None:
    existing = [
        {
            "id": "TC-001",
            "test_module": "learning-plan",
            "description": "view all learning plans",
            "steps": ["click view all learning plans"],
            "test_input": "existing plans",
            "expected_result": "opens learning plan list",
            "priority": "P1",
            "priority_final": "P1",
        }
    ]
    new_cases = [
        {
            "caseId": "TC-002",
            "testModule": "learning-plan",
            "title": "click view all learning plans and open list",
            "testSteps": ["click view all learning plans"],
            "testInput": "existing plans",
            "expectedResult": "opens learning plan list",
            "Priority": "P1",
            "priorityFinal": "P1",
        },
        {
            "caseId": "TC-003",
            "testModule": "learning-progress",
            "title": "complete lesson updates progress",
            "testSteps": ["complete one lesson"],
            "testInput": "available lesson",
            "expectedResult": "progress increases",
            "Priority": "P0",
            "priorityFinal": "P0",
        },
    ]

    def _dedupe(cases):  # noqa: ANN001, ANN202
        return [dict(item) for item in cases]

    def _reorder(cases, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return [dict(item) for item in cases]

    merged = merge_cases_for_append(
        existing,
        new_cases,
        deduplicate_test_cases_fn=_dedupe,
        reorder_cases_by_closed_loop_fn=_reorder,
    )

    assert [item.get("id") or item.get("caseId") for item in merged] == ["TC-001", "TC-003"]


class _FakeDb:
    def __init__(self) -> None:
        self.entries: list[object] = []
        self._next_generation_id = 9001

    def add(self, item: object) -> None:
        if hasattr(item, "generated_result") and not getattr(item, "id", None):
            setattr(item, "id", self._next_generation_id)
            self._next_generation_id += 1
        self.entries.append(item)

    def commit(self) -> None:
        return None


def test_stream_persistence_enforce_mode_blocks_formal_generation_insert(monkeypatch) -> None:
    cases = _main_chain_cases()[:2]

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"}
            },
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "schedule workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 6,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "schedule workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "multi_pass",
        "multi_pass": True,
        "request_id": "req-stream-gate",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert any("execution_plan_failed" in item for item in output)
    assert any("persistence_gate" in str(getattr(item, "message", "")) for item in db.entries)
    assert not [item for item in db.entries if hasattr(item, "generated_result")]


def test_stream_persistence_blocks_case_quality_even_when_execution_plan_passes(monkeypatch) -> None:
    cases = _main_chain_cases()

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "stage_counts": {"primary": 120, "review": 89},
            "coverage": {"coverage_rate": 0.8, "total_rules": 10, "missing_rules": [], "missing_types": {}},
            "convergence_debug": {
                "final_count": 89,
                "candidate_count_before_review": 120,
                "review_selected_count": 89,
            },
            "generation_summary": {"final_count": 89, "min_acceptable_final": 104},
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"},
                "candidate_total": 120,
                "retained_total": 89,
                "final_scenario_duplicate_case_count": 41,
                "final_flow_misordered_count": 0,
            },
            "judge_summary": {"rejected_out_count": 44},
            "judge_decision_table": [
                {
                    "case_id": "TC-088",
                    "status": "REJECT",
                    "reject_reason": "semantic_duplicate:TC-087",
                    "signals": {"is_semantic_duplicate": True},
                }
            ],
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "schedule workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 149,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "schedule workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "multi_pass",
        "multi_pass": True,
        "request_id": "req-stream-case-quality-gate",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert any("LOW_QUALITY_GENERATED_CASES" in item for item in output)
    gate_entries = [str(getattr(item, "message", "")) for item in db.entries]
    assert any("quantity_shortfall_warning" in item for item in gate_entries)
    assert any("judge_rejected_above_threshold" in item for item in gate_entries)
    assert any('"kind": "generation_summary"' in item for item in gate_entries)
    assert any('"kind": "judge_summary"' in item for item in gate_entries)
    assert any('"kind": "judge_decision_table"' in item for item in gate_entries)
    assert any('"kind": "generation_quality_ledger"' in item for item in gate_entries)
    assert any('"dominant_reason": "semantic_duplicate"' in item for item in gate_entries)
    assert not [item for item in db.entries if hasattr(item, "generated_result")]


def test_stream_persistence_allows_candidate_insufficient_underfill(monkeypatch) -> None:
    main_cases = []
    for case in _main_chain_cases():
        item = dict(case)
        item["preconditions"] = ["workflow data exists and user is logged in"]
        item["steps"] = [str(item.get("description") or "run workflow step")]
        item["test_input"] = "valid workflow input"
        item["test_module"] = "main workflow"
        item["priority_final"] = item.get("priority")
        main_cases.append(item)
    extra_cases = [
        {
            "id": f"TC-{index:03d}",
            "description": f"independent functional coverage {index}",
            "test_module": "independent module",
            "preconditions": ["user is logged in"],
            "steps": ["open feature", "run action"],
            "test_input": f"input {index}",
            "expected_result": f"result {index} is displayed with concrete state",
            "priority": "P1",
            "priority_final": "P1",
            "execution_group": "independent_functional",
        }
        for index in range(7, 61)
    ]
    cases = main_cases + extra_cases

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "stage_counts": {"primary": 44, "review": 60},
            "coverage": {"coverage_rate": 0.8, "total_rules": 10, "missing_rules": [], "missing_types": {}},
            "convergence_debug": {
                "final_count": 60,
                "candidate_count_before_review": 60,
                "review_selected_count": 60,
                "judge_rejected_or_pending_count": 4,
            },
            "generation_summary": {
                "final_count": 60,
                "min_acceptable_final": 85,
                "quality_assessment": "medium",
                "underfilled": True,
                "underfill_reason": "valid_candidate_insufficient",
                "underfill_root_cause": "candidate_insufficient",
            },
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"},
                "candidate_total": 60,
                "retained_total": 60,
                "final_scenario_duplicate_case_count": 0,
                "final_flow_misordered_count": 0,
            },
            "judge_summary": {"total": 61, "rejected_out_count": 4, "pending_out_count": 0},
            "judge_decision_table": [
                {
                    "case_id": "TC-039",
                    "status": "REJECT",
                    "reject_reason": "semantic_duplicate:TC-038",
                    "signals": {"is_semantic_duplicate": True},
                }
            ],
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "compact full regression workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 100,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "compact full regression workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "full_functional_regression",
        "multi_pass": True,
        "request_id": "req-stream-candidate-insufficient-underfill",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert not any("LOW_QUALITY_GENERATED_CASES" in item for item in output)
    gate_entries = [str(getattr(item, "message", "")) for item in db.entries]
    assert any('"quantity_shortfall_advisory": true' in item for item in gate_entries)
    execution_suite_entries = [
        json.loads(item.removeprefix("GEN_DIAG:"))
        for item in output
        if isinstance(item, str) and '"kind": "generation_execution_suite"' in item
    ]
    assert execution_suite_entries
    assert execution_suite_entries[0]["generation_id"] == 9001
    assert execution_suite_entries[0]["source"] == "persistence_gate_pre_projection"
    assert execution_suite_entries[0]["execution_suite"]["execution_readiness"] == "partial"
    assert any(
        suite["execution_group"] == "main_smoke"
        for suite in execution_suite_entries[0]["execution_suite"]["suites"]
    )
    persisted = [item for item in db.entries if hasattr(item, "generated_result")]
    assert persisted
    persisted_cases = json.loads(persisted[0].generated_result)
    assert "execution_group" not in persisted_cases[0]
    assert "workflow_id" not in persisted_cases[0]


def test_stream_persistence_does_not_mask_explicit_invalid_priority_final(monkeypatch) -> None:
    cases = _main_chain_cases()
    cases[0]["priority"] = "P1"
    cases[0]["priority_final"] = None

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": cases,
            "review_decision_summary": {
                "execution_plan": {"workflow_blueprint_source": "feedback_control_state"},
            },
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod.settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    db = _FakeDb()
    state = {
        "client": object(),
        "requirement": "schedule workflow",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 149,
        "overwrite": False,
        "append": False,
        "user_id": 9,
        "original_requirement": "schedule workflow",
        "feedback_control_state": {
            "workflow_blueprints": [_trusted_blueprint()]
        },
        "generation_mode": "multi_pass",
        "multi_pass": True,
        "request_id": "req-stream-explicit-invalid-priority",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert any("LOW_QUALITY_GENERATED_CASES" in item for item in output)
    error_lines = [item for item in output if "Error:" in item]
    assert any("priority_final_null_count=1" in item for item in error_lines)
    assert not any("commit_downstream_completion_missing" in item for item in error_lines)
    gate_entries = [str(getattr(item, "message", "")) for item in db.entries]
    assert any("priority_final_null_count=1" in item for item in gate_entries)
    assert not [item for item in db.entries if hasattr(item, "generated_result")]


def test_json_api_returns_execution_plan_failed_as_502(monkeypatch) -> None:
    monkeypatch.setattr(generate_json_routes, "get_owned_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_json_routes.context_orchestrator,
        "assemble_context",
        lambda *args, **kwargs: {"diagnostics": {}},
    )
    monkeypatch.setattr(generate_json_routes, "log_workflow_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_json_routes, "log_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_json_routes.test_generator,
        "generate_test_cases_json",
        lambda *args, **kwargs: {
            "error_code": "execution_plan_failed",
            "error_message": "execution plan gate failed",
            "final_status": "execution_plan_failed",
            "persistence_gate_failed": True,
            "failure_reasons": ["state_chain_conflict"],
            "metrics": {"state_conflict_count": 1},
            "state_conflicts": [{"reason": "state_not_connected"}],
        },
    )

    request = _TestGenRequest(requirement="req", project_id=1, expected_count=20)
    current_user = type("User", (), {"id": 1})()

    try:
        generate_json_routes.generate_tests(request=request, db=object(), current_user=current_user)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert int(exc.status_code) == 502
        detail = dict(exc.detail or {})
        assert detail["error_code"] == "execution_plan_failed"
        assert detail["failure_reasons"] == ["state_chain_conflict"]
        assert detail["metrics"]["state_conflict_count"] == 1


def test_generate_tests_async_route_is_not_estimate_count_duplicate() -> None:
    route_paths = [getattr(route, "path", "") for route in generate_routes.router.routes]

    assert "/estimate-test-count" in route_paths
    assert "/generate-tests/async" in route_paths
    assert route_paths.count("/estimate-test-count") == 1
