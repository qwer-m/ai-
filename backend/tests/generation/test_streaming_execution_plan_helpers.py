from __future__ import annotations

from modules.testing.test_generation_components.postprocess.streaming_execution_plan_helpers import (
    default_main_chain_exclusion_token_sets,
    derive_workflow_blueprint_from_current_cases,
    derived_workflow_candidate_buckets,
    derived_workflow_selected_for_closure,
    derived_workflow_steps_from_selected,
    empty_execution_plan_summary,
    infer_role,
    infer_workflow_phase,
    infer_workflow_stage_kind,
    is_core_result_output_anchor,
    is_display_only_workflow_text,
    is_internal_state_text,
    is_low_value_main_chain_p0,
    MainChainExclusionRecorder,
    main_chain_closure_status,
    main_chain_exclusion_reason,
    main_chain_stages_from_blueprints,
    main_chain_state_overrides_for_current_generation,
    materialize_workflow_contract_case,
    pattern_match_score,
    public_contract_module_label,
    selected_stage_state_conflicts,
    select_derived_workflow_candidates,
    session_key_for_role,
    stage_match_patterns,
    token_hit,
    workflow_bridge_case,
    workflow_transition_for_case,
    workflow_blueprint_source_label,
)


def test_token_hit_respects_ascii_word_boundaries() -> None:
    assert token_hit("submit success", ("submit",)) is True
    assert token_hit("resubmitted success", ("submit",)) is False
    assert token_hit("提交成功", ("提交",)) is True


def test_empty_execution_plan_summary_matches_streaming_contract() -> None:
    assert empty_execution_plan_summary() == {
        "applied": False,
        "linear_executable": False,
        "main_chain_case_count": 0,
        "independent_case_count": 0,
        "isolation_case_count": 0,
        "role_switch_count": 0,
        "broken_dependency_count": 0,
        "state_conflict_count": 0,
    }
    assert empty_execution_plan_summary() is not empty_execution_plan_summary()


def test_default_main_chain_exclusion_token_sets_matches_streaming_config() -> None:
    token_sets = default_main_chain_exclusion_token_sets()

    assert set(token_sets) == {
        "analytics_tokens",
        "destructive_action_tokens",
        "blocking_negative_tokens",
        "boundary_capacity_tokens",
        "display_only_tokens",
        "downstream_visibility_tokens",
    }
    assert token_sets["analytics_tokens"] == {
        "埋点",
        "上报",
        "曝光",
        "停留时间",
        "pv",
        "uv",
        "tracking",
        "analytics",
        "event",
    }
    assert token_sets["destructive_action_tokens"] == {
        "删除",
        "下架",
        "撤销",
        "作废",
        "取消发布",
        "delete",
        "remove",
        "unpublish",
        "archive",
        "deactivate",
    }
    assert token_sets["blocking_negative_tokens"] == {
        "失败",
        "异常",
        "超时",
        "错误",
        "拒绝",
        "不通过",
        "不可点击",
        "不可操作",
        "置灰",
        "阻止",
        "无法",
        "不能",
        "不允许",
        "不进入",
        "不生成",
        "不保存",
        "failure",
        "failed",
        "timeout",
        "error",
        "invalid",
        "blocked",
        "cannot",
        "not allowed",
        "not saved",
    }
    assert token_sets["boundary_capacity_tokens"] == {
        "边界",
        "上限",
        "下限",
        "最多",
        "最少",
        "容量不足",
        "学不完",
        "课程设置过少",
        "时间冲突",
        "冲突",
        "boundary",
        "limit",
        "capacity",
        "conflict",
        "too few",
        "too many",
    }
    assert token_sets["display_only_tokens"] == {
        "文案",
        "样式",
        "布局",
        "标题",
        "排序",
        "筛选",
        "列表",
        "卡片",
        "弹窗",
        "copy",
        "style",
        "layout",
        "title",
        "sorting",
        "filter",
        "list",
        "card",
        "popup",
    }
    assert token_sets["downstream_visibility_tokens"] == {
        "新增",
        "新计划",
        "同步",
        "生效",
        "最新",
        "进度更新",
        "状态同步",
        "new",
        "created",
        "sync",
        "synced",
        "visible",
        "effective",
        "latest",
        "updated",
    }


def test_default_main_chain_exclusion_token_sets_returns_independent_sets() -> None:
    first = default_main_chain_exclusion_token_sets()
    second = default_main_chain_exclusion_token_sets()

    assert first is not second
    assert first["analytics_tokens"] is not second["analytics_tokens"]
    first["analytics_tokens"].add("mutated")
    first["display_only_tokens"].clear()

    fresh = default_main_chain_exclusion_token_sets()
    assert "mutated" not in second["analytics_tokens"]
    assert "mutated" not in fresh["analytics_tokens"]
    assert "文案" in second["display_only_tokens"]
    assert "文案" in fresh["display_only_tokens"]


def test_main_chain_state_overrides_for_current_generation_links_selected_states() -> None:
    selected_by_stage = [
        ("entry", "Entry", {"id": "TC-001"}),
        ("commit", "Commit", {"id": "TC-002"}),
        ("consume", "Consume", {"id": "TC-003"}),
    ]
    stage_meta = {
        "entry": {"state_in": "", "state_out": "draft"},
        "commit": {"state_in": "ignored", "state_out": "published"},
        "consume": {"state_in": "published", "state_out": "published"},
    }

    overrides = main_chain_state_overrides_for_current_generation(
        selected_by_stage,
        stage_meta_by_key=stage_meta,
        signature_fn=lambda item: str(item.get("id") or ""),
    )

    assert overrides == {
        "TC-001": ("initial", "draft"),
        "TC-002": ("draft", "published"),
        "TC-003": ("published", "derived_selected_state_003"),
    }


def test_workflow_blueprint_source_label_prefers_current_requirement() -> None:
    assert (
        workflow_blueprint_source_label(
            [{"repository_source": "current_requirement_blueprint"}],
            [],
        )
        == "current_requirement_blueprint"
    )
    assert workflow_blueprint_source_label([{"source": "feedback"}], []) == "feedback_control_state"
    assert workflow_blueprint_source_label([], [{"id": "derived"}]) == "current_generation_cases"
    assert workflow_blueprint_source_label([], []) == "none"


def test_main_chain_stages_from_blueprints_materializes_stage_meta() -> None:
    stages, meta_by_key, output_state = main_chain_stages_from_blueprints(
        [
            {
                "id": "bp-1",
                "name": "primary",
                "steps": [
                    {
                        "id": "open",
                        "label": "Open entry",
                        "actor": "student",
                        "state_out": "entry_opened",
                    },
                    {
                        "id": "submit",
                        "label": "Submit form",
                        "role": "teacher",
                        "state_out": "submitted",
                    },
                ],
            }
        ]
    )

    assert [stage[0] for stage in stages] == ["open", "submit"]
    assert stage_match_patterns({"allow_bridge": True, "keywords": ["Save", "Submit"]}) == (
        ("save",),
        ("submit",),
    )
    assert meta_by_key["open"]["blueprint_id"] == "bp-1"
    assert meta_by_key["submit"]["step_index"] == 2
    assert output_state == {"open": "entry_opened", "submit": "submitted"}


def test_pattern_match_score_accepts_compound_keyword_parts() -> None:
    assert pattern_match_score("open entry and submit form", (("submit form",),)) == len("submit form")
    assert pattern_match_score("save and draft the form", (("save draft form",),)) == len("save") + len("draft") + len("form")


def test_workflow_stage_kind_and_phase_infer_action_order() -> None:
    assert infer_workflow_stage_kind("保存并提交后生成评分") == "commit"
    assert infer_workflow_phase("保存并提交后生成评分") == 60
    assert infer_workflow_stage_kind("下游页面展示最新评分结果") == "downstream_visibility"
    assert infer_workflow_phase("下游页面展示最新评分结果") == 70
    assert infer_workflow_stage_kind("点击进入学习页面") == "consume"
    assert infer_workflow_phase("点击进入学习页面") == 10
    assert infer_workflow_stage_kind("预览检查结果") == "preview"
    assert infer_workflow_phase("预览检查结果") == 50


def test_main_chain_closure_status_requires_commit_and_downstream_step() -> None:
    selected = [
        ("open", "Open entry", {"description": "Open workflow entry"}),
        ("submit", "Submit form", {"description": "Submit success"}),
        ("visible", "Downstream visible", {"description": "Downstream page displays latest result"}),
    ]
    meta = {
        "open": {"stage_kind": "entry"},
        "submit": {"stage_kind": "commit"},
        "visible": {"stage_kind": "downstream_visibility"},
    }

    assert main_chain_closure_status(selected, stage_meta_by_key=meta, source="current_generation_cases") == (
        True,
        "",
        ["entry", "commit", "downstream_visibility"],
    )
    missing_downstream = selected[:2]
    assert main_chain_closure_status(
        missing_downstream,
        stage_meta_by_key=meta,
        source="current_generation_cases",
    ) == (
        False,
        "missing_downstream_visibility_or_consume_step",
        ["entry", "commit"],
    )


def test_selected_stage_state_conflicts_reports_disconnected_state_chain() -> None:
    selected = [
        ("draft", "Save draft", {"id": "TC-1"}),
        ("publish", "Publish", {"id": "TC-2"}),
    ]
    conflicts = selected_stage_state_conflicts(
        selected,
        stage_meta_by_key={
            "draft": {"state_out": "draft_saved"},
            "publish": {"state_in": "review_ready", "state_out": "published"},
        },
        case_id_fn=lambda item: str(item.get("id") or ""),
    )

    assert conflicts == [
        {
            "prev_stage_key": "draft",
            "curr_stage_key": "publish",
            "prev_case_id": "TC-1",
            "curr_case_id": "TC-2",
            "prev_target_state": "draft_saved",
            "curr_source_state": "review_ready",
            "reason": "state_not_connected",
        }
    ]


def test_workflow_transition_for_case_materializes_positive_transition() -> None:
    transition = workflow_transition_for_case(
        {"description": "Submit form successfully"},
        step_meta={
            "workflow_id": "wf-1",
            "state_in": "draft_ready",
            "state_out": "submitted",
            "stage_kind": "commit",
            "action": "Submit form",
        },
        workflow_blueprints_present=True,
    )

    assert transition["workflow_id"] == "wf-1"
    assert transition["source_state"] == "draft_ready"
    assert transition["target_state"] == "submitted"
    assert transition["path_type"] == "positive"
    assert transition["can_advance_main_flow"] is True
    assert transition["state_transition_confidence"] == 0.9
    assert transition["stage_kind"] == "commit"


def test_workflow_transition_for_case_marks_destructive_or_blocking_negative() -> None:
    transition = workflow_transition_for_case(
        {"description": "Delete draft and trigger timeout"},
        stage_label="Delete draft",
        destructive_action_tokens=("delete",),
        blocking_negative_tokens=("timeout",),
    )

    assert transition["path_type"] == "negative"
    assert transition["blocking"] is True
    assert transition["destructive"] is True
    assert transition["can_advance_main_flow"] is False
    assert transition["state_transition_confidence"] == 0.35


def test_display_only_workflow_text_ignores_real_actions_and_downstream_visibility() -> None:
    display_tokens = ("文案", "列表", "layout")
    downstream_tokens = ("同步", "visible")

    assert is_display_only_workflow_text(
        "列表文案样式检查",
        display_only_tokens=display_tokens,
        downstream_visibility_tokens=downstream_tokens,
    )
    assert not is_display_only_workflow_text(
        "进入列表并保存设置",
        display_only_tokens=display_tokens,
        downstream_visibility_tokens=downstream_tokens,
    )
    assert not is_display_only_workflow_text(
        "列表同步后最新状态 visible",
        display_only_tokens=display_tokens,
        downstream_visibility_tokens=downstream_tokens,
    )


def test_main_chain_exclusion_recorder_deduplicates_signature_and_reason() -> None:
    records: list[dict[str, str]] = []
    recorder = MainChainExclusionRecorder(
        records,
        signature_fn=lambda item: str(item.get("signature") or ""),
        case_id_fn=lambda item: str(item.get("id") or ""),
        description_fn=lambda item: str(item.get("description") or ""),
    )
    case = {"id": "TC-1", "signature": "sig-1", "description": "保存提交后验证下游展示"}

    recorder(case, "display_only", stage_key="entry")
    recorder(case, "display_only", stage_key="entry")
    recorder(case, "analytics", stage_key="entry")

    assert records == [
        {
            "case_id": "TC-1",
            "description": "保存提交后验证下游展示",
            "stage_key": "entry",
            "reason": "display_only",
            "signature": "sig-1",
        },
        {
            "case_id": "TC-1",
            "description": "保存提交后验证下游展示",
            "stage_key": "entry",
            "reason": "analytics",
            "signature": "sig-1",
        },
    ]


def test_main_chain_exclusion_reason_uses_ordered_guardrails_and_semantic_checker() -> None:
    assert (
        main_chain_exclusion_reason(
            {"description": "查看报表 analytics dashboard"},
            analytics_tokens=("analytics",),
        )
        == "analytics"
    )
    assert (
        main_chain_exclusion_reason(
            {"description": "列表文案样式检查"},
            display_only_tokens=("文案", "列表"),
            downstream_visibility_tokens=("同步",),
        )
        == "display_only"
    )
    assert (
        main_chain_exclusion_reason(
            {"description": "提交成功"},
            step_meta={"stage_kind": "commit", "label": "提交"},
            semantic_alignment_fn=lambda _items: [{"reason": "role_action_conflict"}],
        )
        == "role_action_conflict"
    )


def test_derived_workflow_candidate_buckets_scores_primary_fallback_and_exclusions() -> None:
    cases = [
        {"id": "primary", "priority": "P1", "description": "保存并提交成功，状态更新展示"},
        {"id": "fallback", "priority": "P2", "description": "打开入口后准备完成"},
        {"id": "excluded", "priority": "P1", "description": "analytics dashboard 保存成功"},
    ]
    recorded: list[tuple[str, str]] = []

    primary, fallback = derived_workflow_candidate_buckets(
        cases,
        exclusion_reason_fn=lambda item: "analytics" if item.get("id") == "excluded" else "",
        record_exclusion_fn=lambda item, reason: recorded.append((str(item.get("id") or ""), reason)),
    )

    assert [item["id"] for _score, _phase, _index, item in primary] == ["primary"]
    assert primary[0][1] == 60
    assert [item["id"] for _score, _phase, _index, item in fallback] == ["fallback"]
    assert fallback[0][1] == 10
    assert recorded == [("excluded", "analytics")]


def test_select_derived_workflow_candidates_uses_primary_floor_and_phase_order() -> None:
    primary = [
        (50, 60, 1, {"id": "p2"}),
        (70, 20, 0, {"id": "p1"}),
    ]
    fallback = [
        (80, 10, 2, {"id": "f1"}),
    ]

    selected = select_derived_workflow_candidates(primary, fallback, limit=3)

    assert [item["id"] for _score, _phase, _index, item in selected] == ["f1", "p1", "p2"]
    assert select_derived_workflow_candidates([], fallback) == []
    assert [item["id"] for _score, _phase, _index, item in select_derived_workflow_candidates([], fallback * 2)] == [
        "f1",
        "f1",
    ]


def test_derived_workflow_steps_from_selected_materializes_blueprint_steps() -> None:
    selected = [
        (
            70,
            60,
            0,
            {
                "id": "TC-1",
                "description": "保存并提交作文",
                "test_module": "作文批改",
                "expected_result": "提交成功并进入审核中",
                "steps": ["打开作文编辑页", "点击提交"],
            },
        ),
        (
            65,
            70,
            1,
                {
                    "id": "TC-2",
                    "description": "下游页面展示最新状态",
                    "test_module": "作文圈",
                    "expected_result": "作文圈展示最新状态",
                    "steps": ["进入作文圈"],
                },
        ),
    ]

    steps, terminal_state = derived_workflow_steps_from_selected(
        selected,
        case_id_fn=lambda item: str(item.get("id") or ""),
    )

    assert terminal_state == "derived_state_002"
    assert [step["id"] for step in steps] == ["derived_step_001", "derived_step_002"]
    assert steps[0]["state_in"] == "initial"
    assert steps[0]["state_out"] == "derived_state_001"
    assert steps[1]["state_in"] == "derived_state_001"
    assert steps[0]["stage_kind"] == "commit"
    assert steps[1]["stage_kind"] == "downstream_visibility"
    assert steps[0]["source_case_id"] == "TC-1"
    assert steps[0]["match_keywords"] == [
        "保存并提交作文",
        "作文批改",
        "提交成功并进入审核中",
        "打开作文编辑页",
    ]


def test_derived_workflow_selected_for_closure_maps_steps_to_source_cases() -> None:
    selected = [
        (70, 60, 0, {"id": "TC-1", "description": "保存提交成功"}),
        (65, 70, 1, {"id": "TC-2", "description": "下游展示最新状态"}),
    ]
    steps = [
        {"id": "derived_step_001", "label": "提交", "source_case_id": "TC-1"},
        {"id": "derived_step_002", "label": "展示", "source_case_id": "TC-2"},
    ]

    selected_for_closure = derived_workflow_selected_for_closure(
        steps,
        selected,
        case_id_fn=lambda item: str(item.get("id") or ""),
    )

    assert selected_for_closure == [
        ("derived_step_001", "提交", selected[0][3]),
        ("derived_step_002", "展示", selected[1][3]),
    ]


def test_derive_workflow_blueprint_from_current_cases_returns_debug_steps_and_blueprint() -> None:
    cases = [
        {
            "id": "TC-entry",
            "priority": "P2",
            "test_module": "Workflow entry",
            "description": "Open workflow entry and prepare scoring task",
            "steps": ["Open workflow entry", "Prepare scoring data"],
            "expected_result": "Workflow entry is ready and prepared",
        },
        {
            "id": "TC-submit",
            "priority": "P1",
            "test_module": "Scoring workflow",
            "description": "Submit scoring task successfully",
            "steps": ["Submit scoring task"],
            "expected_result": "Scoring task is saved successfully",
        },
        {
            "id": "TC-visible",
            "priority": "P1",
            "test_module": "Student result",
            "description": "Downstream student view shows visible score result",
            "steps": ["Open student result page"],
            "expected_result": "Score result is visible and reflected downstream",
        },
    ]
    recorded: list[tuple[str, str]] = []

    result = derive_workflow_blueprint_from_current_cases(
        cases,
        exclusion_reason_fn=lambda _item: "",
        record_exclusion_fn=lambda item, reason: recorded.append((str(item.get("id") or ""), reason)),
        case_id_fn=lambda item: str(item.get("id") or ""),
        stage_meta_by_key={},
        closure_status_fn=main_chain_closure_status,
    )

    assert recorded == []
    assert result["incomplete_reason"] == ""
    assert result["debug"] == {
        "candidate_total": 3,
        "action_state_candidate_count": 3,
        "primary_candidate_count": 2,
        "fallback_candidate_count": 1,
        "selected_candidate_count": 3,
        "closure_reason": "",
    }
    blueprint = result["blueprint"]
    assert blueprint is not None
    assert blueprint["source"] == "current_generation_cases"
    assert blueprint["terminal_state"] == "derived_state_003"
    assert result["terminal_state"] == "derived_state_003"
    assert [step["source_case_id"] for step in result["steps"]] == [
        "TC-entry",
        "TC-submit",
        "TC-visible",
    ]
    assert [step["stage_kind"] for step in result["steps"]] == [
        "entry",
        "commit",
        "downstream_visibility",
    ]


def test_derive_workflow_blueprint_from_current_cases_returns_closure_reason_and_steps() -> None:
    cases = [
        {
            "id": "TC-submit",
            "priority": "P1",
            "test_module": "Scoring workflow",
            "description": "Submit scoring task successfully",
            "steps": ["Submit scoring task"],
            "expected_result": "Scoring task is saved successfully",
        },
        {
            "id": "TC-visible",
            "priority": "P1",
            "test_module": "Student result",
            "description": "Downstream student view shows visible score result",
            "steps": ["Open student result page"],
            "expected_result": "Score result is visible and reflected downstream",
        },
    ]

    result = derive_workflow_blueprint_from_current_cases(
        cases,
        exclusion_reason_fn=lambda _item: "",
        record_exclusion_fn=lambda _item, _reason: None,
        case_id_fn=lambda item: str(item.get("id") or ""),
        stage_meta_by_key={},
        closure_status_fn=main_chain_closure_status,
    )

    assert result["blueprint"] is None
    assert result["incomplete_reason"] == "missing_configure_or_entry_step"
    assert result["debug"] == {
        "candidate_total": 2,
        "action_state_candidate_count": 2,
        "primary_candidate_count": 2,
        "fallback_candidate_count": 0,
        "selected_candidate_count": 2,
        "closure_reason": "missing_configure_or_entry_step",
    }
    assert [step["source_case_id"] for step in result["steps"]] == ["TC-submit", "TC-visible"]
    assert result["stage_kinds"] == ["commit", "downstream_visibility"]


def test_role_session_and_public_contract_helpers() -> None:
    assert infer_role({"description": "student opens course", "role": ""}) == "student"
    assert session_key_for_role("student") == "student_session"
    assert is_internal_state_text("draft_saved_state") is True
    assert is_internal_state_text("Student main workflow") is False
    assert public_contract_module_label({"module": "draft_saved_state"}, "student submit") == "学生端主链路"
    assert public_contract_module_label({"module": "Course Center"}, "student submit") == "Course Center"


def test_low_value_main_chain_p0_ignores_pending_only_status() -> None:
    assert is_low_value_main_chain_p0(
        {
            "description": "Review page remains pending after 48 hours",
            "expected_result": "Pending status remains visible",
        }
    )
    assert not is_low_value_main_chain_p0(
        {
            "description": "Submit form",
            "expected_result": "Submit success and correction result is generated",
        }
    )


def test_core_result_output_anchor_rejects_detail_only_display() -> None:
    assert is_core_result_output_anchor(
        {
            "description": "Open correction result",
            "expected_result": "Four modules and result details are visible",
        }
    )
    assert not is_core_result_output_anchor(
        {
            "description": "Open scoring panel",
            "expected_result": "Star rating and disabled button are visible",
        }
    )


def test_materialize_workflow_contract_case_filters_internal_stage_fields() -> None:
    materialized = materialize_workflow_contract_case(
        "draft:save",
        {
            "label": "保存草稿",
            "stage_kind": "commit",
            "module": "draft_saved_state",
            "domain": "作文批改",
            "test_steps": ["draft_saved_state", "点击保存草稿"],
            "actor": "teacher",
            "main_path_step": False,
        },
    )

    assert materialized is not None
    assert materialized["id"] == "TC-CONTRACT-DRAFT-SAVE"
    assert materialized["test_module"] == "作文批改"
    assert materialized["steps"] == ["点击保存草稿"]
    assert materialized["test_input"] == "保存草稿"
    assert materialized["expected_result"] == "保存草稿完成，保存结果展示成功状态"
    assert materialized["priority"] == "P1"
    assert materialized["role"] == "supervisor"
    assert materialized["workflow_contract_materialized_case"] is True


def test_materialize_workflow_contract_case_rejects_internal_label() -> None:
    assert materialize_workflow_contract_case("draft_state", {"label": "draft_saved_state"}) is None


def test_workflow_bridge_case_requires_previous_stage_available() -> None:
    main_chain_stages = [
        ("entry", "Open entry", (("open",),)),
        ("commit", "Submit", (("submit",),)),
    ]
    meta_by_key = {
        "entry": {"allow_bridge": True, "label": "Open entry", "actor": "student"},
        "commit": {
            "allow_bridge": True,
            "label": "Submit form",
            "state_in": "entry_opened",
            "state_out": "submitted",
            "actor": "teacher",
        },
    }

    assert (
        workflow_bridge_case(
            "commit",
            stage_meta_by_key=meta_by_key,
            main_chain_stages=main_chain_stages,
            selected_stage_keys=set(),
        )
        is None
    )
    bridge = workflow_bridge_case(
        "commit",
        stage_meta_by_key=meta_by_key,
        main_chain_stages=main_chain_stages,
        selected_stage_keys={"entry"},
    )

    assert bridge is not None
    assert bridge["id"] == "TC-BRIDGE-COMMIT"
    assert bridge["description"] == "Submit form"
    assert bridge["preconditions"] == ["entry_opened"]
    assert bridge["expected_result"] == "submitted"
    assert bridge["priority"] == "P0"
    assert bridge["role"] == "supervisor"
    assert bridge["workflow_blueprint_bridge"] is True
