from __future__ import annotations

from modules.testing.test_generation_components.postprocess.streaming_execution_plan_helpers import (
    empty_execution_plan_summary,
    infer_role,
    infer_group,
    infer_workflow_phase,
    infer_workflow_stage_kind,
    is_core_result_output_anchor,
    is_display_only_workflow_text,
    is_low_value_main_chain_p0,
    MainChainExclusionRecorder,
    main_chain_stages_from_blueprints,
    pattern_match_score,
    selected_stage_state_conflicts,
    session_key_for_role,
    stage_match_patterns,
    token_hit,
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


def test_workflow_blueprint_source_label_prefers_current_requirement() -> None:
    assert (
        workflow_blueprint_source_label(
            [{"repository_source": "current_requirement_blueprint"}],
        )
        == "current_requirement_blueprint"
    )
    assert workflow_blueprint_source_label([{"source": "feedback"}]) == "feedback_control_state"
    assert workflow_blueprint_source_label([]) == "none"


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
    assert stage_match_patterns({"keywords": ["Save", "Submit"]}) == (
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
    assert infer_workflow_stage_kind("系统自动打分并给出评分") == "unknown"
    assert infer_workflow_phase("系统自动打分并给出评分") == 90
    assert infer_workflow_stage_kind("评分结果为85分") == "unknown"
    assert infer_workflow_phase("评分结果为85分") == 90
    assert infer_workflow_stage_kind("开始学习课程") == "unknown"
    assert infer_workflow_phase("开始学习课程") == 90
    assert infer_workflow_stage_kind("learn lesson") == "unknown"
    assert infer_workflow_phase("learn lesson") == 90
    assert infer_workflow_stage_kind("点击进入学习页面") == "consume"
    assert infer_workflow_phase("点击进入学习页面") == 10
    assert infer_workflow_stage_kind("预览检查结果") == "preview"
    assert infer_workflow_phase("预览检查结果") == 50


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
            "path_type": "positive",
            "can_advance_main_flow": True,
            "confidence": 0.9,
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
        step_meta={
            "path_type": "negative",
            "destructive": True,
            "blocking": True,
            "can_advance_main_flow": False,
            "confidence": 0.35,
        },
        destructive_action_tokens=("delete",),
        blocking_negative_tokens=("timeout",),
    )

    assert transition["path_type"] == "negative"
    assert transition["blocking"] is True
    assert transition["destructive"] is True
    assert transition["can_advance_main_flow"] is False
    assert transition["state_transition_confidence"] == 0.35


def test_workflow_transition_for_case_does_not_infer_contract_from_case_text() -> None:
    transition = workflow_transition_for_case(
        {"description": "Delete draft and trigger timeout"},
        stage_label="Delete draft",
        destructive_action_tokens=("delete",),
        blocking_negative_tokens=("timeout",),
    )

    assert transition["path_type"] == ""
    assert transition["blocking"] is False
    assert transition["destructive"] is False
    assert transition["can_advance_main_flow"] is False


def test_display_only_workflow_text_ignores_real_actions_and_downstream_visibility() -> None:
    display_tokens = ("文案", "列表", "layout")
    downstream_tokens = ("同步", "visible")

    assert is_display_only_workflow_text(
        "列表文案样式检查",
        display_only_tokens=display_tokens,
        downstream_visibility_tokens=downstream_tokens,
    )
    assert is_display_only_workflow_text(
        "learn card layout",
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


def test_role_session_helpers() -> None:
    assert infer_role({"description": "student opens course", "role": ""}) == "business_user"
    assert infer_role({"description": "generic workflow", "role": "content_editor"}) == "content_editor"
    for protocol_role in ("admin", "guest", "authenticated", "anonymous"):
        assert infer_role({"description": "generic workflow", "role": protocol_role}) == protocol_role
        assert session_key_for_role(protocol_role) == f"{protocol_role}_session"
    assert session_key_for_role("student") == "student_session"
    assert session_key_for_role("content_editor") == "content_editor_session"


def test_low_value_main_chain_p0_ignores_pending_only_status() -> None:
    assert is_low_value_main_chain_p0(
        {
            "description": "Review state remains pending",
            "expected_result": "State remains unchanged and visible",
        }
    )
    assert not is_low_value_main_chain_p0(
        {
            "description": "Submit form",
            "expected_result": "Submit success and processing result is generated",
        }
    )


def test_core_result_output_anchor_rejects_detail_only_display() -> None:
    assert is_core_result_output_anchor(
        {
            "description": "Open downstream output",
            "expected_result": "The output is available",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "downstream_visibility",
        }
    )
    assert not is_core_result_output_anchor(
        {
            "description": "Open auxiliary display panel",
            "expected_result": "Copy and layout are visible",
            "execution_group": "display",
            "priority_reasons": ["structural_p2_low_value_signal"],
        }
    )


def test_infer_group_prefers_existing_structure_before_generic_text() -> None:
    assert infer_group(
        {
            "execution_group": "boundary",
            "description": "ordinary validation",
        },
        in_main_chain=False,
    ) == "boundary"
    assert infer_group(
        {
            "main_chain_stage_kind": "preview",
            "description": "ordinary validation",
        },
        in_main_chain=False,
    ) == "display"
