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
from modules.testing.test_generation_components.postprocess.execution_plan_semantic_alignment import (
    analyze_main_smoke_semantic_alignment,
)
from modules.testing.test_generation_components.postprocess.persistence_gate import (
    evaluate_persistence_gate,
    summarize_persistence_case_quality_gate,
)
from modules.testing.test_generation_components.postprocess.case_contract import (
    project_persistable_cases,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    retain_structured_case_candidates,
    merge_cases_for_append,
    prepare_append_existing_cases,
    strip_case_meta_fields,
)
from modules.testing.test_generation_components.postprocess.json_processing import (
    count_unique_test_cases,
    deduplicate_test_cases,
    normalize_json_structure,
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
            "main_chain_stage": stage_kind,
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
        EXECUTION_PLAN_REJECT_CANDIDATE_DERIVED_BLUEPRINT=True,
    )


def _trusted_blueprint() -> dict:
    return {
        "id": "schedule_flow",
        "workflow_id": "schedule_flow",
        "source_type": "human_reviewed",
        "repository_source": "workflow_blueprint_repository",
        "trusted": True,
        "primary": True,
        "initial_state": "ready",
        "terminal_states": ["consumed"],
        "required_stage_ids": [
            "entry",
            "configure",
            "preview",
            "commit",
            "downstream_visibility",
            "consume",
        ],
        "steps": [
            {"id": "entry", "label": "entry", "action": "open", "state_in": "ready", "state_out": "started", "required": True},
            {"id": "configure", "label": "configure", "action": "configure", "state_in": "started", "state_out": "configured", "required": True},
            {"id": "preview", "label": "preview", "action": "preview", "state_in": "configured", "state_out": "preview_ready", "required": True},
            {"id": "commit", "label": "commit", "action": "save", "state_in": "preview_ready", "state_out": "committed", "required": True},
            {"id": "downstream_visibility", "label": "visible", "action": "sync", "state_in": "committed", "state_out": "visible", "required": True},
            {"id": "consume", "label": "consume", "action": "enter", "state_in": "visible", "state_out": "consumed", "required": True, "terminal": True},
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
            "main_chain_stage": (
                "entry",
                "configure",
                "preview",
                "commit",
                "downstream_visibility",
                "consume",
            )[index - 1],
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
    assert result["metrics"]["workflow_closure_satisfied"] is True


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
    assert result["metrics"]["workflow_closure_satisfied"] is True


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


def test_scoring_business_terms_do_not_satisfy_protocol_stage_actions() -> None:
    cases = [
        {
            "id": "TC-SCORE-COMMIT",
            "description": "系统自动打分并给出评分",
            "steps": ["触发打分"],
            "expected_result": "生成综合评分",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "commit",
        },
        {
            "id": "TC-SCORE-DOWNSTREAM",
            "description": "评分结果为85分",
            "steps": ["读取打分结果"],
            "expected_result": "综合评分为85分",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "downstream_visibility",
        },
    ]

    reasons_by_case: dict[str, set[str]] = {}
    for warning in analyze_main_smoke_semantic_alignment(cases)["warnings"]:
        reasons_by_case.setdefault(warning["case_id"], set()).add(warning["reason"])

    assert "stage_text_lacks_commit_action" in reasons_by_case["TC-SCORE-COMMIT"]
    assert "stage_text_lacks_downstream_propagation" in reasons_by_case["TC-SCORE-DOWNSTREAM"]


def test_automated_actor_edit_and_commit_use_declared_action_anchors() -> None:
    cases = [
        {
            "id": "TC-SERVICE-EDIT",
            "description": "处理服务播放状态计算完成动画",
            "steps": ["触发处理服务播放状态计算完成动画"],
            "expected_result": "状态计算完成动画已播放",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "edit",
            "main_chain_stage_label": "状态计算完成动画展示",
            "action": "show_state_calculation_animation",
            "role": "processing_service",
        },
        {
            "id": "TC-SYSTEM-COMMIT",
            "description": "评分系统计算综合评分",
            "steps": ["等待评分系统计算综合评分"],
            "expected_result": "综合评分已产生",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "commit",
            "main_chain_stage_label": "评分系统计算综合评分",
            "action": "calculate_overall_score",
            "role": "system_actor",
        },
        {
            "id": "TC-AGENT-COMMIT",
            "description": "评语智能体整合分析结果",
            "steps": ["启动评语智能体整合分析结果"],
            "expected_result": "分析结果已整合",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "commit",
            "main_chain_stage_label": "评语智能体整合分析结果",
            "action": "aggregate_analysis_result",
            "role": "review_agent",
        },
    ]

    analysis = analyze_main_smoke_semantic_alignment(cases)
    reasons = {
        warning["reason"]
        for warning in analysis["warnings"]
    } | {
        conflict["reason"]
        for conflict in analysis["conflicts"]
    }

    assert "stage_text_lacks_edit_action" not in reasons
    assert "stage_text_lacks_commit_action" not in reasons
    assert "case_goal_spans_commit_stage" not in reasons
    assert "automated_stage_action_anchor_missing" not in reasons
    assert "automated_stage_action_anchor_not_supported" not in reasons


def test_automated_actor_stage_requires_supported_action_or_label_anchor() -> None:
    cases = [
        {
            "id": "TC-SYSTEM-MISSING",
            "description": "系统完成一次自动处理",
            "steps": ["等待自动处理"],
            "expected_result": "自动处理完成",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "commit",
            "role": "system_actor",
        },
        {
            "id": "TC-AGENT-UNSUPPORTED",
            "description": "评语智能体生成评语",
            "steps": ["触发评语生成"],
            "expected_result": "评语已生成",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "edit",
            "main_chain_stage_label": "同步课程统计报告",
            "action": "sync_course_report",
            "role": "review_agent",
        },
    ]

    reasons_by_case = {
        conflict["case_id"]: conflict["reason"]
        for conflict in analyze_main_smoke_semantic_alignment(cases)["conflicts"]
    }

    assert reasons_by_case["TC-SYSTEM-MISSING"] == (
        "automated_stage_action_anchor_missing"
    )
    assert reasons_by_case["TC-AGENT-UNSUPPORTED"] == (
        "automated_stage_action_anchor_not_supported"
    )


def test_learning_business_term_does_not_satisfy_consume_protocol_action() -> None:
    cases = [
        {
            "id": "TC-LEARNING-CONSUME",
            "description": "开始学习课程",
            "steps": ["学习课程内容"],
            "expected_result": "课程处于可学习状态",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "consume",
        }
    ]

    reasons = {
        warning["reason"]
        for warning in analyze_main_smoke_semantic_alignment(cases)["warnings"]
    }

    assert "stage_text_lacks_consume_action" in reasons


def test_validator_separates_materialized_conflicts_from_local_semantic_warnings() -> None:
    cases = [
        {
            "id": "TC-UI",
            "test_module": "Feedback",
            "description": "Post button copy and visual style remain unchanged",
            "preconditions": ["User entered the feedback page"],
            "steps": ["Open the page", "Observe the button copy"],
            "test_input": "Original design",
            "expected_result": "The button copy and visual style match the original design",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "consume",
            "workflow_contract_materialized_case": True,
        },
        {
            "id": "TC-FULL",
            "test_module": "Content",
            "description": "Open the editor, edit content, publish it, then verify the notification",
            "preconditions": ["User is signed in"],
            "steps": ["Open", "Edit", "Publish", "View notification"],
            "test_input": "Valid content",
            "expected_result": "The content is published and the notification is visible",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "entry",
            "workflow_contract_materialized_case": True,
        },
        {
            "id": "TC-COMMIT-REPLAY",
            "test_module": "Content",
            "description": "Publish content",
            "preconditions": ["Content preview is ready"],
            "steps": ["Open the editor", "Edit content", "Publish content"],
            "test_input": "Valid content",
            "expected_result": "The content is published",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "commit",
            "workflow_contract_materialized_case": True,
        },
    ]

    analysis = analyze_main_smoke_semantic_alignment(cases)
    reasons_by_case: dict[str, set[str]] = {}
    for warning in analysis["warnings"]:
        reasons_by_case.setdefault(warning["case_id"], set()).add(warning["reason"])

    assert "display_only_case_used_in_main_chain" in reasons_by_case["TC-UI"]
    assert "case_goal_spans_commit_stage" in reasons_by_case["TC-FULL"]
    assert "commit_case_replays_edit_stage" in reasons_by_case["TC-COMMIT-REPLAY"]
    assert {
        item["case_id"] for item in analysis["conflicts"]
        if item["reason"] == "generated_bridge_case_in_final_main_smoke"
    } == {"TC-UI", "TC-FULL", "TC-COMMIT-REPLAY"}


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

    conflicts = analyze_main_smoke_semantic_alignment(cases)["warnings"]
    reasons_by_case: dict[str, set[str]] = {}
    for conflict in conflicts:
        reasons_by_case.setdefault(conflict["case_id"], set()).add(conflict["reason"])

    assert {"TC-003", "TC-004", "TC-005", "TC-006"}.issubset(reasons_by_case)
    assert "stage_text_lacks_edit_action" in reasons_by_case["TC-003"]
    assert all(
        "stage_action_not_supported_by_case_text" in reasons_by_case[case_id]
        for case_id in {"TC-003", "TC-004", "TC-005", "TC-006"}
    )


def test_validator_reports_disconnected_state_and_non_advancing_main_case() -> None:
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
    assert "blocking_case_in_main_smoke" not in reasons
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
        for item in analyze_main_smoke_semantic_alignment(cases)["warnings"]
    }

    assert "stage_module_not_aligned_with_blueprint" in reasons


def test_validator_rejects_explicit_bridge_and_keeps_body_mismatch_diagnostic() -> None:
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
    warnings = {item["reason"] for item in result["semantic_warnings"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "stage_text_lacks_configure_action" in warnings
    assert "passive_list_status_case_used_as_configure" in warnings
    assert "generated_bridge_case_in_final_main_smoke" in reasons


def test_validator_warns_when_workflow_action_is_weakly_supported() -> None:
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

    reasons = {item["reason"] for item in result["semantic_warnings"]}
    assert result["passed"] is True
    assert "main_smoke_semantic_conflict" not in result["failure_reasons"]
    assert "stage_action_not_supported_by_case_text" in reasons


def test_validator_rejects_report_history_case_as_completion_sync_without_role_guessing() -> None:
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

    reasons = {item["reason"] for item in result["semantic_warnings"]}
    assert result["passed"] is False
    assert "workflow_terminal_state_not_reachable" in result["failure_reasons"]
    assert "student_role_with_management_surface_text" not in reasons
    assert "report_history_case_not_completion_sync" in reasons


def test_validator_keeps_conditional_visibility_and_resume_as_warnings() -> None:
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

    reasons = {item["reason"] for item in result["semantic_warnings"]}
    assert result["passed"] is True
    assert "main_smoke_semantic_conflict" not in result["failure_reasons"]
    assert "conditional_visibility_case_in_main_smoke" in reasons
    assert "resume_state_case_in_main_smoke" in reasons


def test_validator_blocks_reset_or_abort_case_in_main_smoke() -> None:
    cases = _main_chain_cases()
    cases[2]["description"] = "Exit the workflow and reset all entered values"
    cases[2]["steps"] = ["Exit the workflow", "Clear all configured values"]
    cases[2]["expected_result"] = "The workflow returns to the blank initial state"

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
    )

    reasons = {item["reason"] for item in result["semantic_conflicts"]}
    assert result["passed"] is False
    assert "main_smoke_semantic_conflict" in result["failure_reasons"]
    assert "reset_or_abort_case_in_main_smoke" in reasons
    assert all(
        item.get("diagnostic_only") is not True
        for item in result["semantic_conflicts"]
    )


def test_validator_rejects_candidate_derived_blueprint_as_strong_proof() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "current_generation_cases"},
    )

    assert result["passed"] is False
    assert "workflow_contract_missing" in result["failure_reasons"]
    assert "untrusted_candidate_derived_blueprint" in result["failure_reasons"]


def test_validator_rejects_candidate_blueprint_when_no_contract_is_available() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "current_generation_cases"},
    )

    assert result["passed"] is False
    assert result["metrics"]["trusted_workflow_contract_count"] == 0
    assert "workflow_contract_missing" in result["failure_reasons"]
    assert "untrusted_candidate_derived_blueprint" in result["failure_reasons"]


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


def test_validator_accepts_declared_independent_suite_without_workflow() -> None:
    cases = [
        {
            "id": "TC-independent",
            "description": "Validate forum filter independently",
            "priority": "P1",
            "execution_group": "independent_functional",
        }
    ]

    result = validate_execution_plan(
        cases,
        workflow_blueprints=[],
        execution_plan={
            "workflow_blueprint_source": "none",
            "workflow_absence_declared": True,
        },
    )

    assert result["passed"] is True
    assert result["failure_reasons"] == []
    assert result["metrics"]["workflow_absence_declared"] is True
    assert result["metrics"]["independent_suite_executable"] is True
    assert result["metrics"]["linear_executable"] is False


def test_persistence_gate_accepts_declared_independent_suite_without_main_chain() -> None:
    result = evaluate_persistence_gate(
        [
            {
                "id": "TC-independent",
                "description": "Validate forum filter independently",
                "priority": "P1",
                "execution_group": "independent_functional",
            }
        ],
        workflow_blueprints=[],
        execution_plan={
            "workflow_blueprint_source": "none",
            "workflow_absence_declared": True,
        },
        settings=_settings("enforce"),
    )

    assert result["passed"] is True
    validation = dict(result.get("execution_plan_validation") or {})
    assert validation.get("passed") is True
    assert dict(validation.get("metrics") or {}).get("independent_suite_executable") is True


def test_validator_does_not_treat_model_failure_as_declared_independent_suite() -> None:
    result = validate_execution_plan(
        [
            {
                "id": "TC-independent",
                "description": "Validate forum filter independently",
                "priority": "P1",
                "execution_group": "independent_functional",
            }
        ],
        workflow_blueprints=[],
        execution_plan={"workflow_blueprint_source": "none"},
    )

    assert result["passed"] is False
    assert result["metrics"]["workflow_absence_declared"] is False
    assert result["metrics"]["independent_suite_executable"] is False
    assert "workflow_contract_missing" in result["failure_reasons"]


def test_validator_rejects_workflow_absence_that_conflicts_with_blueprint() -> None:
    result = validate_execution_plan(
        [
            {
                "id": "TC-independent",
                "description": "Validate forum filter independently",
                "priority": "P1",
                "execution_group": "independent_functional",
            }
        ],
        workflow_blueprints=[{"id": "invalid-flow", "steps": []}],
        execution_plan={
            "workflow_blueprint_source": "none",
            "workflow_absence_declared": True,
        },
    )

    assert result["passed"] is False
    assert "workflow_absence_conflicts_with_blueprint" in result["failure_reasons"]
    assert result["metrics"]["independent_suite_executable"] is False


def test_validator_rejects_declared_independent_suite_when_main_smoke_exists() -> None:
    result = validate_execution_plan(
        _main_chain_cases(),
        workflow_blueprints=[],
        execution_plan={
            "workflow_blueprint_source": "none",
            "workflow_absence_declared": True,
        },
    )

    assert result["passed"] is False
    assert "workflow_absence_conflicts_with_main_smoke" in result["failure_reasons"]
    assert result["metrics"]["independent_suite_executable"] is False


def test_validator_rejects_empty_declared_independent_suite() -> None:
    result = validate_execution_plan(
        [],
        workflow_blueprints=[],
        execution_plan={
            "workflow_blueprint_source": "none",
            "workflow_absence_declared": True,
        },
    )

    assert result["passed"] is False
    assert "workflow_absence_independent_suite_empty" in result["failure_reasons"]
    assert result["metrics"]["independent_suite_executable"] is False


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


def test_explicit_expected_count_floor_blocks_underfilled_formal_result() -> None:
    quality = summarize_persistence_case_quality_gate(
        {"passed": True, "failed_checks": []},
        generation_summary={
            "final_count": 21,
            "min_acceptable_final": 18,
            "underfilled": True,
            "underfill_reason": "valid_candidate_insufficient",
            "underfill_root_cause": "candidate_insufficient",
        },
        review_decision_summary={},
        judge_summary={"rejected_out_count": 0},
        expected_count=25,
        enforce_expected_count_floor=True,
    )

    assert quality["passed"] is False
    assert "final_count_below_explicit_expected_count" in quality["failed_checks"]
    assert quality["metrics"]["explicit_expected_count"] == 25
    assert quality["metrics"]["explicit_expected_count_shortfall"] == 4

    gate = evaluate_persistence_gate(
        _main_chain_cases(),
        workflow_blueprints=[_trusted_blueprint()],
        execution_plan={"workflow_blueprint_source": "feedback_control_state"},
        quality_gate=quality,
        settings=_settings("enforce"),
    )

    assert gate["passed"] is False
    assert gate["failure_code"] == "LOW_QUALITY_GENERATED_CASES"


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


def test_candidate_intake_does_not_delete_body_text_before_review() -> None:
    result = retain_structured_case_candidates(
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

    assert [item["id"] for item in result] == ["TC-001"]


def test_candidate_intake_keeps_alias_fields_for_later_contract_normalization() -> None:
    result = retain_structured_case_candidates(
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

    assert [item["caseId"] for item in result] == ["TC-ALIAS"]


def _append_case(
    case_id: str,
    *,
    description: str,
    module: str = "account",
    priority: str = "P1",
) -> dict:
    return {
        "id": case_id,
        "test_module": module,
        "description": description,
        "preconditions": ["user is signed in"],
        "steps": ["perform the declared action"],
        "test_input": "valid business input",
        "expected_result": "the declared result is visible",
        "priority": priority,
        "priority_final": priority,
    }


def test_append_merge_preserves_baseline_order_ids_and_content() -> None:
    existing = [
        {**_append_case("TC-010", description="load account overview"), "legacy_marker": "keep"},
        _append_case("TC-003", description="update account nickname"),
    ]
    baseline_snapshot = json.loads(json.dumps(existing))
    new_cases = [
        _append_case("TC-011", description="load account overview"),
        _append_case("TC-020", description="load account overview with expired session"),
        _append_case("TC-010", description="export account audit record"),
    ]

    merged = merge_cases_for_append(
        existing,
        new_cases,
        deduplicate_test_cases_fn=deduplicate_test_cases,
    )

    assert existing == baseline_snapshot
    assert merged[:2] == baseline_snapshot
    assert [item["id"] for item in merged] == ["TC-010", "TC-003", "TC-020", "TC-011"]
    assert [item["description"] for item in merged[2:]] == [
        "load account overview with expired session",
        "export account audit record",
    ]


def test_append_merge_only_removes_exact_public_duplicates_from_new_cases() -> None:
    existing = [
        _append_case("TC-001", description="view account details"),
    ]
    new_cases = [
        {
            "caseId": "TC-002",
            "testModule": "account",
            "title": "view account details",
            "preconditions": ["user is signed in"],
            "testSteps": ["perform the declared action"],
            "testInput": "valid business input",
            "expectedResult": "the declared result is visible",
            "Priority": "P1",
            "priorityFinal": "P1",
        },
        _append_case("TC-003", description="view account details after refresh"),
        _append_case("TC-004", description="view account details after refresh"),
    ]

    merged = merge_cases_for_append(
        existing,
        new_cases,
        deduplicate_test_cases_fn=deduplicate_test_cases,
    )

    assert [item.get("id") or item.get("caseId") for item in merged] == ["TC-001", "TC-003"]


def test_prepare_append_history_does_not_normalize_or_deduplicate_baseline() -> None:
    first = _append_case("TC-020", description="existing exact behavior")
    second = _append_case("TC-007", description="existing exact behavior")
    source = json.dumps([first, second], ensure_ascii=False)

    existing, unique_count, start_id = prepare_append_existing_cases(
        source,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        count_unique_test_cases_fn=count_unique_test_cases,
    )

    assert existing == [first, second]
    assert unique_count == 1
    assert start_id == 21


def test_append_validation_never_removes_invalid_historical_case() -> None:
    historical = {"id": "TC-001", "description": "legacy incomplete row"}
    invalid_new = {"id": "TC-002", "description": "new incomplete row"}
    valid_new = _append_case("TC-003", description="new valid row")

    merged = merge_cases_for_append(
        [historical],
        [invalid_new, valid_new],
        deduplicate_test_cases_fn=deduplicate_test_cases,
    )

    assert merged == [historical, valid_new]


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


def test_stream_append_persistence_validates_only_new_cases_and_keeps_baseline(monkeypatch) -> None:
    baseline = [
        {**_append_case("TC-010", description="historical first"), "legacy_marker": "keep"},
        _append_case("TC-003", description="historical second"),
    ]
    baseline_snapshot = json.loads(json.dumps(baseline))
    new_cases = [
        {
            **_append_case("TC-010", description="new behavior with colliding id"),
            "execution_group": "main_smoke",
            "execution_sequence": 1,
            "main_chain_step": 1,
            "workflow_id": "append_flow",
            "source_state": "ready",
            "target_state": "completed",
            "action": "run appended behavior",
            "role": "business_user",
            "session_key": "business_user_session",
        },
    ]
    existing_entry = SimpleNamespace(
        id=77,
        generated_result=json.dumps(baseline, ensure_ascii=False),
    )
    gate_inputs: list[list[dict]] = []

    def _fake_stream_postprocess_cases(**kwargs):  # noqa: ANN003, ARG001
        if False:
            yield None
        return {
            "cases": new_cases,
            "generation_summary": {"final_count": 1, "min_acceptable_final": 1},
            "review_decision_summary": {"execution_plan": {}},
        }

    def _fake_persistence_gate(cases, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        captured = [dict(item) for item in cases]
        gate_inputs.append(captured)
        return {
            "passed": True,
            "failure_code": "",
            "cases": captured,
            "execution_plan_validation": {"passed": True, "failure_reasons": [], "metrics": {}},
            "quality_gate": {"passed": True, "failed_checks": [], "metrics": {}},
        }

    monkeypatch.setattr(stream_persist_mod, "stream_postprocess_cases", _fake_stream_postprocess_cases)
    monkeypatch.setattr(stream_persist_mod, "evaluate_persistence_gate", _fake_persistence_gate)
    db = _FakeDb()
    state = {
        "client": SimpleNamespace(
            select_model=lambda *args, **kwargs: "test-model",
            max_tokens=1024,
        ),
        "requirement": "append one verified behavior",
        "project_id": 7,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 1,
        "overwrite": False,
        "append": True,
        "user_id": 9,
        "original_requirement": "append one verified behavior",
        "existing_cases": baseline,
        "existing_entry": existing_entry,
        "existing_unique_count": 2,
        "feedback_control_state": {"workflow_blueprints": []},
        "generation_mode": "single_pass",
        "multi_pass": False,
        "request_id": "req-stream-append-baseline-protection",
    }

    output = list(LegacyGenerationStreamPersistMixin()._stream_persist_phase(state=state))

    assert gate_inputs == [[{**new_cases[0], "id": "TC-011"}]]
    persisted_cases = json.loads(existing_entry.generated_result)
    assert baseline == baseline_snapshot
    assert persisted_cases[:2] == [
        project_persistable_cases([item])[0]
        for item in baseline_snapshot
    ]
    assert [item["id"] for item in persisted_cases] == ["TC-010", "TC-003", "TC-011"]
    assert all("execution_group" not in item for item in persisted_cases)
    execution_suite_logs = [
        json.loads(item.removeprefix("GEN_DIAG:"))
        for item in output
        if isinstance(item, str) and '"kind": "generation_execution_suite"' in item
    ]
    assert execution_suite_logs
    main_suite = next(
        suite
        for suite in execution_suite_logs[0]["execution_suite"]["suites"]
        if suite["execution_group"] == "main_smoke"
    )
    assert [item["case_id"] for item in main_suite["cases"]] == ["TC-011"]


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
