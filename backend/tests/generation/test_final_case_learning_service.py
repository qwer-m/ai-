import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.services.final_case_learning_service import (
    _filter_quality_evaluation_sample_for_apply,
    _workflow_contract_candidates_from_derived,
    build_learning_candidates_from_evaluation_result,
    build_learning_samples_from_final_cases,
    parse_test_cases_spreadsheet_bytes,
    parse_test_cases_payload,
)
from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.control.workflow_blueprint_repository import (
    is_trusted_workflow_contract,
    normalize_workflow_contract,
)
from routers.automation.test_generation_history_routes import FinalCaseLearningRequest


def _explicit_workflow_case(
    *,
    case_id: str,
    sequence: int,
    stage_kind: str,
    action: str,
    state_in: str,
    state_out: str,
    description: str = "",
    test_module: str = "",
    expected_result: str = "",
    actor: str = "business_user",
    workflow_id: str = "explicit_workflow",
) -> dict:
    return {
        "id": case_id,
        "description": description,
        "test_module": test_module,
        "steps": [action],
        "expected_result": expected_result,
        "priority": "P0",
        "execution_group": "main_smoke",
        "execution_sequence": sequence,
        "workflow_transition": {
            "workflow_id": workflow_id,
            "stage_kind": stage_kind,
            "actor": actor,
            "action": action,
            "source_state": state_in,
            "target_state": state_out,
            "path_type": "positive",
            "main_path_step": True,
            "can_advance_main_flow": True,
        },
    }


def test_final_case_text_does_not_infer_manual_business_extension() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证二轮复习模块在首页展示",
            "test_module": "高中首页",
            "steps": ["打开首页", "查看二轮复习模块"],
            "expected_result": "首页展示二轮复习模块，位置在查漏补缺与真题套卷之间",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证未开卡用户在小程序和书房端均不可进入二轮复习课程",
            "test_module": "跨端权限",
            "steps": ["使用未开卡账号分别进入小程序和书房端", "点击二轮复习课程"],
            "expected_result": "两个端均拦截进入并展示开卡/权限提示，后台无学习进度写入",
            "priority": "P1",
        }
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="高中首页新增二轮复习模块，位于查漏补缺与真题套卷之间",
        generation_id=123,
    )

    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["diagnostics"]["manual_business_extension_count"] == 0
    assert result["diagnostics"]["manual_business_extension_candidate_count"] == 0
    assert result["diagnostics"]["negative_sample_count"] == 0
    sample = result["positive_samples"][0]
    assert sample["signal_type"] == "positive"
    assert sample["pattern_usage"] == "prefer"
    assert sample["manual_business_extension"] is False
    assert sample["pattern_category"] == "human_final_case"


def test_ai_only_is_not_negative_without_clear_quality_failure() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证首页模块顺序",
            "test_module": "高中首页",
            "steps": ["打开首页", "记录模块顺序"],
            "expected_result": "模块顺序为标准化自习、查漏补缺、二轮复习、真题套卷、名师精品课",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证TA端课程管理中二轮复习课程状态同步",
            "test_module": "TA端课程管理",
            "steps": ["学生完成课程节点", "TA端查看课程状态"],
            "expected_result": "TA端展示的课程完成状态与学生端一致",
            "priority": "P1",
        }
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="首页新增二轮复习模块",
    )

    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["diagnostics"]["negative_sample_count"] == 0


def test_ai_only_with_non_assertable_expected_result_becomes_negative() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证按钮正常展示",
            "test_module": "页面展示",
            "steps": ["打开页面", "查看按钮"],
            "expected_result": "正常展示",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证退费后课程权限和订单状态同步回滚",
            "test_module": "交易与权限",
            "steps": ["购买课程", "发起退费", "查看课程权限和订单状态"],
            "expected_result": "退费成功后课程权限收回，订单状态为已退费，学习入口不可进入",
            "priority": "P0",
        }
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="首页新增二轮复习模块",
    )

    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["diagnostics"]["negative_sample_count"] == 1
    negative = result["negative_samples"][0]
    assert negative["signal_type"] == "negative"
    assert negative["pattern_usage"] == "avoid"
    assert negative["pattern_category"] == "non_assertable_expected_result"
    assert negative["user_comment"] == ""


def test_final_case_learning_samples_attach_quality_ledger_scope_and_confidence() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证按钮正常展示",
            "test_module": "页面展示",
            "steps": ["打开页面", "查看按钮"],
            "expected_result": "正常展示",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证跨端学习状态同步",
            "test_module": "跨端状态",
            "steps": ["学生端完成课程", "管理端查看状态"],
            "expected_result": "管理端展示的完成状态与学生端一致",
            "priority": "P1",
        }
    ]
    quality_ledger = {
        "generation_id": 460,
        "quality_assessment": "high",
        "final_count": 83,
        "coverage": {
            "coverage_rate": 0.98,
            "missing_rules_count": 1,
            "non_blocking_rules_count": 7,
        },
        "review": {"candidate_total": 100, "retained_total": 83},
        "judge": {"rejected_out_count": 0, "pending_out_count": 0},
        "context": {"snapshot_used": True, "fusion_mode": "snapshot+rag"},
    }

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="学习状态需要跨端一致",
        generation_id=460,
        quality_ledger=quality_ledger,
    )

    assert result["diagnostics"]["quality_ledger_attached"] is True
    positive = result["positive_samples"][0]
    negative = result["negative_samples"][0]
    assert positive["pattern_scope"] == "project"
    assert positive["quality_ledger"]["generation_id"] == 460
    assert positive["quality_ledger"]["coverage_rate"] == 0.98
    assert positive["pattern_confidence"] > negative["pattern_confidence"]


def test_final_case_learning_plain_text_keeps_structured_neutral_category() -> None:
    final_title = "Verify switching back to course A keeps progress after operating course B"
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-001",
                "description": final_title,
                "test_module": "course progress consistency",
                "steps": ["complete course A", "switch to course B", "switch back to course A"],
                "expected_result": "course A progress remains unchanged after switching back",
                "priority": "P1",
            }
        ],
        requirement_text="course learning progress must be retained",
        generation_id=461,
    )

    positive = result["positive_samples"][0]
    assert positive["pattern_grain"] == "pattern"
    assert positive["source_case_title"] == final_title
    assert positive["pattern_scope"] == "project"
    assert positive["pattern_summary"] != final_title
    assert positive["pattern_category"] == "human_final_case"
    assert "structured_source:human_final_case" in positive["pattern_summary"]
    assert "state_transition" not in positive["pattern_summary"]


def test_final_case_learning_positive_sample_does_not_prefill_hardcoded_comment() -> None:
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-001",
                "description": "Verify learning progress dashboard renders latest rank",
                "test_module": "learning dashboard",
                "steps": ["open dashboard", "view rank card"],
                "expected_result": "rank card renders the latest ranking data",
                "priority": "P0",
            }
        ],
        requirement_text="learning dashboard should render progress and rank data",
        generation_id=464,
    )

    positive = result["positive_samples"][0]
    assert positive["user_comment"] == ""
    assert "Linked human-final case" not in str(positive)


def test_final_case_learning_progress_display_does_not_default_to_state_flow() -> None:
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-002",
                "description": "Verify learning progress dashboard renders latest rank",
                "test_module": "learning dashboard",
                "steps": ["open dashboard", "view progress card"],
                "expected_result": "progress card and rank list are visible",
                "priority": "P0",
            }
        ],
        requirement_text="learning dashboard should render progress and rank data",
        generation_id=464,
    )

    positive = result["positive_samples"][0]
    assert positive["pattern_category"] == "human_final_case"
    assert "state_transition" not in positive["pattern_summary"]


def test_final_case_learning_aggregates_final_cases_into_patterns() -> None:
    final_cases = [
        {
            "id": f"TC-H-{idx:03d}",
            "description": f"Verify course progress state remains consistent after switching course #{idx}",
            "test_module": "course progress consistency",
            "steps": ["complete course A", "switch to course B", "switch back to course A"],
            "expected_result": "course A progress remains unchanged after switching back",
            "priority": "P1",
        }
        for idx in range(1, 8)
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=final_cases,
        requirement_text="course learning progress must be retained",
        generation_id=462,
    )

    diagnostics = result["diagnostics"]
    assert diagnostics["final_case_count"] == 7
    assert diagnostics["positive_candidate_count"] == 7
    assert diagnostics["positive_sample_count"] == 2
    assert diagnostics["workflow_blueprint_sample_count"] == 0
    assert diagnostics["positive_aggregation_policy"].startswith("pattern_key")
    assert all(item["pattern_grain"] == "pattern" for item in result["positive_samples"])


def test_final_case_learning_plain_text_does_not_emit_workflow_blueprint() -> None:
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-001",
                "description": "Open checkout and submit payment",
                "test_module": "checkout",
                "steps": ["open checkout", "submit payment"],
                "expected_result": "payment is accepted and order is created",
                "priority": "P0",
            },
            {
                "id": "TC-H-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["open order detail"],
                "expected_result": "order status is paid",
                "priority": "P0",
            },
        ],
        requirement_text="checkout payment flow",
        generation_id=500,
    )

    assert result["diagnostics"]["workflow_blueprint_sample_count"] == 0
    assert all(item["pattern_grain"] == "pattern" for item in result["positive_samples"])
    assert all("workflow_blueprint" not in item for item in result["positive_samples"])


def test_final_case_learning_blueprint_uses_only_explicit_advancing_contract_steps() -> None:
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-001",
                "description": "权限：非督导角色访问排课页面提示无权限",
                "test_module": "排课",
                "steps": ["使用学员账号访问排课页"],
                "expected_result": "页面提示无权限",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-H-002",
                    sequence=1,
                    stage_kind="configure",
                    action="select_courses",
                    state_in="schedule_draft_created",
                    state_out="schedule_courses_selected",
                    actor="supervisor",
                    workflow_id="schedule_plan_flow",
                ),
                "id": "TC-H-002",
                "description": "排课新增计划第一步选择课程",
                "test_module": "排课-新增计划",
                "steps": ["督导进入新增计划", "选择课程", "点击下一步"],
                "expected_result": "课程加入已选列表并进入时间设置步骤",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-H-003",
                    sequence=2,
                    stage_kind="edit",
                    action="configure_schedule_time",
                    state_in="schedule_courses_selected",
                    state_out="schedule_time_configured",
                    actor="supervisor",
                    workflow_id="schedule_plan_flow",
                ),
                "id": "TC-H-003",
                "description": "排课新增计划第二步设置上课日期和时间",
                "test_module": "排课-新增计划",
                "steps": ["设置上课日期和时间", "点击下一步"],
                "expected_result": "时间配置保存到计划草稿并进入预览步骤",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-H-004",
                    sequence=3,
                    stage_kind="commit",
                    action="save_schedule_plan",
                    state_in="schedule_time_configured",
                    state_out="schedule_plan_saved",
                    actor="supervisor",
                    workflow_id="schedule_plan_flow",
                ),
                "id": "TC-H-004",
                "description": "排课新增计划第三步预览并保存",
                "test_module": "排课-新增计划",
                "steps": ["查看预览", "点击保存"],
                "expected_result": "计划保存成功",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-H-005",
                    sequence=4,
                    stage_kind="downstream_visibility",
                    action="view_student_weekly_task",
                    state_in="schedule_plan_saved",
                    state_out="student_home_weekly_task_visible",
                    actor="student",
                    workflow_id="schedule_plan_flow",
                ),
                "id": "TC-H-005",
                "description": "学生端首页本周任务展示新增课程",
                "test_module": "首页本周任务",
                "steps": ["打开学生端首页"],
                "expected_result": "本周任务展示新增课程",
                "priority": "P0",
            },
            {
                "id": "TC-H-006",
                "description": "埋点：首页任务卡片点击上报",
                "test_module": "埋点",
                "steps": ["点击任务卡片"],
                "expected_result": "点击事件成功上报",
                "priority": "P1",
            },
        ],
        requirement_text="督导新增排课计划，保存后学生首页展示本周任务",
        generation_id=501,
    )

    blueprint = result["positive_samples"][0]["workflow_blueprint"]
    steps = blueprint["steps"]
    assert blueprint["state_machine_version"] == "workflow-blueprint-v2"
    assert [step["source_case_id"] for step in steps] == [
        "TC-H-002",
        "TC-H-003",
        "TC-H-004",
        "TC-H-005",
    ]
    assert [step["state_out"] for step in steps] == [
        "schedule_courses_selected",
        "schedule_time_configured",
        "schedule_plan_saved",
        "student_home_weekly_task_visible",
    ]
    assert [step["state_in"] for step in steps[1:]] == [step["state_out"] for step in steps[:-1]]
    assert all(step["workflow_id"] == "schedule_plan_flow" for step in steps)


def test_final_case_learning_blueprint_scans_past_early_downstream_display_cases() -> None:
    early_display_cases = [
        {
            "id": f"TC-D-{index:03d}",
            "description": f"Student dashboard weekly card visible {index}",
            "test_module": "student home",
            "steps": ["open student home"],
            "expected_result": "weekly task card is visible",
            "priority": "P0",
        }
        for index in range(1, 11)
    ]
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            *early_display_cases,
            {
                **_explicit_workflow_case(
                    case_id="TC-CFG-001",
                    sequence=1,
                    stage_kind="configure",
                    action="select_course",
                    state_in="lesson_plan_draft_created",
                    state_out="lesson_plan_course_selected",
                    actor="supervisor",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-CFG-001",
                "description": "Create lesson plan and select course",
                "test_module": "lesson plan",
                "steps": ["open create plan", "select course", "click next"],
                "expected_result": "course is selected and time setup is available",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-PRE-001",
                    sequence=2,
                    stage_kind="preview",
                    action="preview_plan",
                    state_in="lesson_plan_course_selected",
                    state_out="lesson_plan_previewed",
                    actor="supervisor",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-PRE-001",
                "description": "Preview lesson plan summary",
                "test_module": "lesson plan",
                "steps": ["review preview"],
                "expected_result": "selected course and time are shown before saving",
                "priority": "P1",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-SAVE-001",
                    sequence=3,
                    stage_kind="commit",
                    action="save_plan",
                    state_in="lesson_plan_previewed",
                    state_out="lesson_plan_saved",
                    actor="supervisor",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-SAVE-001",
                "description": "Save lesson plan",
                "test_module": "lesson plan",
                "steps": ["click save"],
                "expected_result": "plan saved successfully",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-VIS-001",
                    sequence=4,
                    stage_kind="downstream_visibility",
                    action="view_student_plan",
                    state_in="lesson_plan_saved",
                    state_out="student_plan_visible",
                    actor="student",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-VIS-001",
                "description": "Student side lesson plan visible after save",
                "test_module": "student side",
                "steps": ["open student side learning plan"],
                "expected_result": "saved plan is visible and consistent with supervisor side",
                "priority": "P0",
            },
        ],
        requirement_text="create a lesson plan, save it, then verify student side visibility",
        generation_id=601,
    )

    blueprint = result["positive_samples"][0]["workflow_blueprint"]
    stage_kinds = [step["stage_kind"] for step in blueprint["steps"]]

    assert "configure" in stage_kinds
    assert "preview" in stage_kinds
    assert "commit" in stage_kinds
    assert "downstream_visibility" in stage_kinds
    assert stage_kinds.index("commit") < stage_kinds.index("downstream_visibility")
    assert any(step["source_case_id"] == "TC-VIS-001" for step in blueprint["steps"])


def test_feedback_control_state_roundtrips_workflow_blueprints() -> None:
    state = FeedbackControlState.from_dict(
        {
            "workflow_blueprints": [
                {
                    "id": "checkout_flow",
                    "name": "checkout flow",
                    "steps": [
                        {"id": "submit", "label": "Submit order"},
                        {"id": "verify", "label": "Verify paid status"},
                    ],
                }
            ]
        }
    )
    merged = state.merge(
        FeedbackControlState(
            workflow_blueprints=[
                {
                    "id": "checkout_flow",
                    "steps": [
                        {"id": "submit", "label": "Submit order"},
                        {"id": "verify", "label": "Verify paid status"},
                    ],
                }
            ]
        )
    )

    assert merged.has_signals() is True
    assert len(merged.workflow_blueprints) == 1
    payload = merged.to_dict()
    assert payload["workflow_blueprints"][0]["steps"][1]["label"] == "Verify paid status"


def test_final_case_workflow_blueprint_builds_trusted_contract_candidate() -> None:
    derived = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                **_explicit_workflow_case(
                    case_id="TC-CFG-001",
                    sequence=1,
                    stage_kind="configure",
                    action="select_course",
                    state_in="lesson_plan_draft_created",
                    state_out="lesson_plan_course_selected",
                    actor="supervisor",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-CFG-001",
                "description": "Create lesson plan and select course",
                "test_module": "lesson plan",
                "steps": ["open create plan", "select course", "click next"],
                "expected_result": "course is selected and time setup is available",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-PRE-001",
                    sequence=2,
                    stage_kind="preview",
                    action="preview_plan",
                    state_in="lesson_plan_course_selected",
                    state_out="lesson_plan_previewed",
                    actor="supervisor",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-PRE-001",
                "description": "Preview lesson plan summary",
                "test_module": "lesson plan",
                "steps": ["review preview"],
                "expected_result": "selected course and time are shown before saving",
                "priority": "P1",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-SAVE-001",
                    sequence=3,
                    stage_kind="commit",
                    action="save_plan",
                    state_in="lesson_plan_previewed",
                    state_out="lesson_plan_saved",
                    actor="supervisor",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-SAVE-001",
                "description": "Save lesson plan",
                "test_module": "lesson plan",
                "steps": ["click save"],
                "expected_result": "plan saved successfully",
                "priority": "P0",
            },
            {
                **_explicit_workflow_case(
                    case_id="TC-VIS-001",
                    sequence=4,
                    stage_kind="downstream_visibility",
                    action="view_student_plan",
                    state_in="lesson_plan_saved",
                    state_out="student_plan_visible",
                    actor="student",
                    workflow_id="lesson_plan_flow",
                ),
                "id": "TC-VIS-001",
                "description": "Student side lesson plan visible after save",
                "test_module": "student side",
                "steps": ["open student side learning plan"],
                "expected_result": "saved plan is visible and consistent with supervisor side",
                "priority": "P0",
            },
        ],
        requirement_text="create a lesson plan, save it, then verify student side visibility",
        generation_id=602,
    )

    contracts = _workflow_contract_candidates_from_derived(derived)
    normalized = normalize_workflow_contract({**contracts[0], "project_id": 1})

    assert len(contracts) == 1
    assert contracts[0]["source_type"] == "manual_final_case_derived"
    assert normalized is not None
    assert is_trusted_workflow_contract(normalized) is True


def test_parse_csv_final_cases_with_chinese_headers() -> None:
    csv_text = (
        "编号,用例标题,模块,测试步骤,预期结果,优先级\n"
        "TC-001,验证OPS端课程上下架影响书房端入口,跨端发布,"
        "OPS下架课程后书房端刷新,书房端不再展示该课程入口,P1\n"
    )

    cases = parse_test_cases_payload(csv_text)

    assert len(cases) == 1
    assert cases[0]["id"] == "TC-001"
    assert cases[0]["description"] == "验证OPS端课程上下架影响书房端入口"
    assert cases[0]["priority"] == "P1"


def test_parse_test_cases_payload_uses_shared_case_alias_registry() -> None:
    cases = parse_test_cases_payload(
        [
            {
                "编号": "TC-ALIAS",
                "测试点": "course publish rollback",
                "测试模块": "course publishing",
                "前提条件": "course exists",
                "执行步骤": ["publish course", "rollback publish"],
                "测试数据": "course id",
                "期望结果": "course is hidden after rollback",
                "用例级别": "P0",
                "owner": "qa",
            }
        ]
    )

    assert cases == [
        {
            "id": "TC-ALIAS",
            "description": "course publish rollback",
            "test_module": "course publishing",
            "preconditions": "course exists",
            "steps": ["publish course", "rollback publish"],
            "test_input": "course id",
            "expected_result": "course is hidden after rollback",
            "priority": "P0",
            "owner": "qa",
        }
    ]


def test_final_case_learning_request_defaults_to_dry_run() -> None:
    req = FinalCaseLearningRequest()

    assert req.dry_run is True


def test_evaluation_defect_analysis_builds_confirmable_candidates() -> None:
    report = {
        "metrics": {
            "precision": 0.84,
            "recall": 0.67,
            "f1_score": 0.75,
            "semantic_similarity": 0.85,
        },
        "defect_analysis": {
            "missing_points": ["Missing cross-client state consistency after course switching"],
            "hallucinations": ["Generated unrelated static UI color check"],
            "modifications": ["Expected result should assert persisted progress, not generic success"],
        },
        "summary": "quality report",
    }

    result = build_learning_candidates_from_evaluation_result(report)

    assert result["diagnostics"]["candidate_count"] == 3
    assert result["diagnostics"]["selected_by_default_count"] == 2
    candidates = result["candidates"]
    missing = next(item for item in candidates if item["source_field"] == "missing_points")
    hallucination = next(item for item in candidates if item["source_field"] == "hallucinations")
    modification = next(item for item in candidates if item["source_field"] == "modifications")
    assert missing["candidate_type"] == "positive_pattern"
    assert missing["selected_by_default"] is True
    assert missing["sample"]["signal_type"] == "positive"
    assert hallucination["candidate_type"] == "negative_pattern"
    assert hallucination["selected_by_default"] is False
    assert hallucination["sample"]["pattern_usage"] == "avoid"
    assert modification["candidate_type"] == "quality_fix_hint"
    assert modification["sample"]["pattern_category"] == "quality_fix_hint"


def test_evaluation_defect_analysis_accepts_markdown_json() -> None:
    raw = """
```json
{
  "metrics": {"recall": 0.5},
  "defect_analysis": {"missing_points": ["Need payment rollback coverage"]}
}
```
"""

    result = build_learning_candidates_from_evaluation_result(raw)

    assert result["diagnostics"]["candidate_count"] == 1
    assert result["candidates"][0]["sample"]["learning_signal_source"] == "defect_analysis.missing_points"


def test_parse_test_cases_payload_accepts_excel_html_table_export() -> None:
    raw = """
<h5>Sheet: 功能测试</h5>
<table>
  <tr><td>相关文档</td><td></td><td>近期课程+排课</td><td></td></tr>
  <tr><td>用例标题</td><td>测试模块</td><td>执行步骤</td><td>预期结果</td><td>用例级别</td></tr>
  <tr><td>排课展示入口</td><td>入口</td><td>书房app-首页</td><td>顶部展示本周进度、本周课程、全部学习计划</td><td>P0</td></tr>
  <tr><td>课程卡片展示</td><td>本周课程模块</td><td>打开首页查看卡片</td><td>展示课程名称、讲次和学习状态</td><td>P1</td></tr>
</table>
"""

    cases = parse_test_cases_payload(raw)

    assert len(cases) == 2
    assert cases[0]["description"] == "排课展示入口"
    assert cases[0]["test_module"] == "入口"
    assert cases[0]["priority"] == "P0"
    assert cases[1]["expected_result"] == "展示课程名称、讲次和学习状态"


def test_parse_test_cases_spreadsheet_bytes_reads_uploaded_xlsx_rows() -> None:
    from io import BytesIO

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "功能测试"
    sheet.append(["相关文档", "", "近期课程+排课"])
    sheet.append(["用例标题", "测试模块", "执行步骤", "预期结果", "用例级别"])
    sheet.append(["排课展示入口", "入口", "书房app-首页", "顶部展示本周进度", "P0"])
    sheet.append(["课程卡片展示", "本周课程模块", "打开首页", "展示课程名称", "P1"])
    buffer = BytesIO()
    workbook.save(buffer)

    cases = parse_test_cases_spreadsheet_bytes("近期课程+排课.xlsx", buffer.getvalue())

    assert len(cases) == 2
    assert cases[0]["description"] == "排课展示入口"
    assert cases[0]["test_module"] == "入口"
    assert cases[0]["priority"] == "P0"


def test_evaluation_defect_analysis_trusts_structured_missing_point_field() -> None:
    report = {
        "metrics": {"precision": 0.49, "recall": 0.67},
        "defect_analysis": {
            "missing_points": [
                "生成用例包含大量作文批改模块用例（去批改、投稿前准备、提交投稿、审核通过、作文圈列表、点赞），修改用例未涉及"
            ],
        },
    }

    result = build_learning_candidates_from_evaluation_result(report)

    assert result["diagnostics"]["candidate_count"] == 1
    assert result["diagnostics"]["selected_by_default_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["source_field"] == "missing_points"
    assert candidate["candidate_type"] == "positive_pattern"
    assert candidate["selected_by_default"] is True
    assert candidate["sample"]["signal_type"] == "positive"
    assert candidate["sample"]["pattern_usage"] == "prefer"
    assert candidate["sample"]["reason_category"] == "recall_gap"


def test_evaluation_defect_analysis_filters_low_context_learning_candidates() -> None:
    report = {
        "analysis_mode": "llm_chunked",
        "metrics": {
            "precision": 0.1309,
            "recall": 0.1667,
            "f1_score": 0.1439,
            "semantic_similarity": 0.3798,
        },
        "defect_analysis": {
            "missing_points": [
                "AI未包含录音时长约束",
                "CASE-14 ai提问题目判断",
                "缺失讲错题录音按钮功能验证",
            ],
            "modifications": [
                "TC-039修正CASE-49",
                "AI用例侧重界面操作，人工修改为追问策略",
                "duplicate_redundant 类问题聚合：2 条相似缺陷，代表例：TC-034和TC-039在group6中合并；人工将多个AI用例合并为一个追问用例",
            ],
            "hallucinations": [
                "TC-001提问逻辑",
                "AI生成了无关的左侧导航栏验证",
            ],
        },
    }

    result = build_learning_candidates_from_evaluation_result(report)

    texts = {item["text"] for item in result["candidates"]}
    assert "AI未包含录音时长约束" not in texts
    assert "CASE-14 ai提问题目判断" not in texts
    assert "缺失讲错题录音按钮功能验证" not in texts
    assert "TC-039修正CASE-49" not in texts
    assert "TC-001提问逻辑" not in texts
    assert result["diagnostics"]["quality_gate_rejected_count"] == 6
    assert result["diagnostics"]["selected_by_default_count"] == 0
    assert len(result["candidates"]) == 2
    modification = next(item for item in result["candidates"] if item["source_field"] == "modifications")
    hallucination = next(item for item in result["candidates"] if item["source_field"] == "hallucinations")
    assert modification["candidate_type"] == "quality_fix_hint"
    assert modification["sample"]["signal_type"] == "positive"
    assert hallucination["candidate_type"] == "negative_pattern"
    assert hallucination["sample"]["signal_type"] == "negative"


def test_quality_evaluation_apply_filter_rejects_low_context_sample() -> None:
    low_context_sample = {
        "source": "quality_evaluation_defect",
        "source_type": "quality_evaluation_defect",
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": "quality_fix_hint",
        "learning_signal_source": "defect_analysis.modifications",
        "case_id": "modifications-1",
        "title": "TC-039修正CASE-49",
        "user_comment": "TC-039修正CASE-49",
        "pattern_summary": "prefer | quality_fix_hint | TC-039修正CASE-49",
    }
    reusable_sample = {
        "source": "quality_evaluation_defect",
        "source_type": "quality_evaluation_defect",
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": "quality_fix_hint",
        "learning_signal_source": "defect_analysis.modifications",
        "case_id": "modifications-2",
        "title": "修改版课程卡片内容（科目-讲次-时间-知识点-时长-标识-按钮）与生成版TC-006/029/056/057对应但顺序有差异",
        "user_comment": "修改版课程卡片内容（科目-讲次-时间-知识点-时长-标识-按钮）与生成版TC-006/029/056/057对应但顺序有差异",
        "pattern_summary": "prefer | quality_fix_hint | 修改版课程卡片内容与生成版对应但顺序有差异",
    }

    assert _filter_quality_evaluation_sample_for_apply(low_context_sample) is None
    filtered = _filter_quality_evaluation_sample_for_apply(reusable_sample)

    assert filtered is not None
    assert filtered["quality_gate_status"] == "auto_select"
    assert filtered["quality_gate_policy"] == "evaluation_defect_reusable_pattern_v1"


def test_evaluation_defect_analysis_aggregates_only_identical_evidence_fingerprints() -> None:
    report = {
        "metrics": {"precision": 0.84, "recall": 0.67},
        "defect_analysis": {
            "hallucinations": [
                "Generated case validates an unrelated navigation color not present in final requirements.",
                "Generated case validates an unrelated navigation color not present in final requirements",
                "Generated case includes a redundant footer animation check absent from the final cases.",
                "Generated case includes a redundant footer animation check absent from the final cases",
                "Generated case adds unrelated avatar border behavior absent from the final cases.",
                "Generated case adds unrelated avatar border behavior absent from the final cases",
            ]
        },
    }

    result = build_learning_candidates_from_evaluation_result(report)

    diagnostics = result["diagnostics"]
    assert diagnostics["raw_candidate_count"] == 6
    assert diagnostics["candidate_count"] == 3
    negatives = [item for item in result["candidates"] if item["candidate_type"] == "negative_pattern"]
    assert len(negatives) == 3
    assert all(item["aggregated_count"] == 2 for item in negatives)
    assert all(item["sample"]["aggregated_evidence_count"] == 2 for item in negatives)
