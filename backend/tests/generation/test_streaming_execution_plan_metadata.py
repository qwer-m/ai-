from __future__ import annotations

from typing import Any

from modules.test_generation_components.postprocess.execution_plan_case_state import (
    main_chain_precondition_conflict_reason,
)
from modules.test_generation_components.postprocess.streaming_execution_plan_metadata import (
    apply_execution_plan_metadata,
)


def _essay_review_workflow_blueprints() -> list[dict[str, Any]]:
    return [
        {
            "id": "essay_review_publish",
            "source": "current_requirement_blueprint",
            "steps": [
                {
                    "id": "open_entry",
                    "label": "open correction entry",
                    "action": "open correction entry",
                    "actor": "supervisor",
                    "state_in": "prepared",
                    "state_out": "entry_opened",
                    "stage_kind": "entry",
                    "keywords": ["open correction entry"],
                },
                {
                    "id": "submit_result",
                    "label": "submit correction result",
                    "action": "submit correction result",
                    "actor": "supervisor",
                    "state_in": "entry_opened",
                    "state_out": "correction_published",
                    "stage_kind": "commit",
                    "keywords": ["submit correction result"],
                },
                {
                    "id": "student_visible",
                    "label": "latest correction result visible",
                    "action": "latest correction result visible",
                    "actor": "student",
                    "state_in": "correction_published",
                    "state_out": "student_result_visible",
                    "stage_kind": "downstream_visibility",
                    "keywords": ["latest correction result visible"],
                },
            ],
        }
    ]


def _essay_review_cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "raw-1",
            "test_module": "作文批改",
            "description": "Supervisor opens the correction entry for a submitted essay",
            "preconditions": ["supervisor account has a submitted essay"],
            "steps": ["open correction entry"],
            "test_input": "submitted essay",
            "expected_result": "correction entry is ready",
            "priority": "P1",
            "role": "supervisor",
        },
        {
            "id": "raw-2",
            "test_module": "作文批改",
            "description": "Supervisor submit correction result after review",
            "preconditions": ["correction entry is ready"],
            "steps": ["submit correction result"],
            "test_input": "real rubric feedback",
            "expected_result": "submit success and correction result saved",
            "priority": "P1",
            "role": "supervisor",
        },
        {
            "id": "raw-3",
            "test_module": "作文批改",
            "description": "Student sees latest correction result visible in the essay detail",
            "preconditions": ["correction result has been submitted"],
            "steps": ["open essay detail", "verify latest correction result visible"],
            "test_input": "student essay",
            "expected_result": "latest correction result visible and synced to student",
            "priority": "P1",
            "role": "student",
        },
        {
            "id": "raw-4",
            "test_module": "作文批改",
            "description": "Student checks result list tooltip display text",
            "preconditions": ["student has correction result"],
            "steps": ["open result list", "hover tooltip"],
            "test_input": "student essay",
            "expected_result": "tooltip display text is readable",
            "priority": "P0",
            "role": "student",
        },
    ]


def test_apply_execution_plan_metadata_materializes_chain_annotations() -> None:
    annotated, summary = apply_execution_plan_metadata(
        _essay_review_cases(),
        workflow_blueprints=_essay_review_workflow_blueprints(),
    )

    main_cases = [item for item in annotated if item.get("execution_group") == "main_smoke"]
    display_cases = [item for item in annotated if item.get("execution_group") == "display"]

    assert summary["workflow_blueprint_source"] == "current_requirement_blueprint"
    assert summary["linear_executable"] is True
    assert summary["main_chain_case_count"] == 3
    assert summary["main_chain_stage_order"] == ["open_entry", "submit_result", "student_visible"]
    assert summary["state_conflict_count"] == 0
    assert summary["semantic_conflict_count"] == 0
    assert [item["id"] for item in main_cases] == ["TC-001", "TC-002", "TC-003"]
    assert [item.get("depends_on") for item in main_cases] == [[], ["TC-001"], ["TC-002"]]
    assert [item.get("main_chain_stage_kind") for item in main_cases] == [
        "entry",
        "commit",
        "downstream_visibility",
    ]
    assert [(item.get("source_state"), item.get("target_state")) for item in main_cases] == [
        ("prepared", "entry_opened"),
        ("entry_opened", "correction_published"),
        ("correction_published", "student_result_visible"),
    ]
    assert [item.get("role") for item in main_cases] == ["supervisor", "supervisor", "student"]
    assert [item.get("priority") for item in main_cases] == ["P0", "P0", "P0"]

    assert len(display_cases) == 1
    assert display_cases[0]["id"] == "TC-004"
    assert display_cases[0]["priority"] == "P1"
    assert display_cases[0]["priority_decision_source"] == "execution_plan_non_main_p0_demoted"


def test_apply_execution_plan_metadata_revalidates_materialized_transition_before_selecting_stage() -> None:
    workflow_blueprints = _essay_review_workflow_blueprints()
    workflow_blueprints[0]["steps"][2]["keywords"] = ["latest correction result"]
    cases = _essay_review_cases() + [
        {
            "id": "raw-weak-detail",
            "test_module": "Essay detail",
            "description": "Student opens latest correction result detail",
            "preconditions": ["student has a correction result"],
            "steps": ["open latest correction result detail"],
            "test_input": "student essay",
            "expected_result": "correction result detail page opens",
            "priority": "P0",
            "role": "student",
        }
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)

    main_descriptions = [
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ]
    assert "Student sees latest correction result visible in the essay detail" in main_descriptions
    assert "Student opens latest correction result detail" not in main_descriptions
    excluded = summary.get("main_chain_excluded_candidates") or []
    weak_exclusions = [
        item for item in excluded
        if item.get("case_id") == "raw-weak-detail" and item.get("stage_key") == "student_visible"
    ]
    assert weak_exclusions
    assert weak_exclusions[0]["reason"] == "stage_action_not_supported_by_case_text"


def test_apply_execution_plan_metadata_rejects_display_case_with_mismatched_blueprint_object() -> None:
    workflow_blueprints = [
        {
            "id": "forum_post_reply_flow",
            "source": "current_requirement_blueprint",
            "steps": [
                {
                    "id": "entry",
                    "label": "进入论坛首页",
                    "action": "进入论坛首页",
                    "module": "论坛首页",
                    "actor": "student",
                    "state_in": "initial",
                    "state_out": "forum_home",
                    "stage_kind": "entry",
                    "match_keywords": ["论坛首页"],
                },
                {
                    "id": "configure",
                    "label": "选择分区与版块",
                    "action": "选择内容分区与版块",
                    "module": "论坛发帖与互动",
                    "actor": "student",
                    "state_in": "forum_home",
                    "state_out": "tab_selected",
                    "stage_kind": "configure",
                    "match_keywords": ["选择分区", "版块"],
                },
                {
                    "id": "edit",
                    "label": "编辑帖子正文",
                    "action": "编辑帖子标题正文和图片",
                    "module": "发帖页",
                    "actor": "student",
                    "state_in": "tab_selected",
                    "state_out": "editing_post",
                    "stage_kind": "edit",
                    "match_keywords": ["编辑帖子"],
                },
                {
                    "id": "preview",
                    "label": "查看帖子详情预览",
                    "action": "查看帖子详情预览内容",
                    "module": "帖子详情",
                    "actor": "student",
                    "state_in": "editing_post",
                    "state_out": "post_detail",
                    "stage_kind": "preview",
                    "match_keywords": ["详情"],
                },
                {
                    "id": "commit",
                    "label": "提交回帖回复",
                    "action": "提交回帖回复",
                    "module": "帖子详情-回复",
                    "actor": "student",
                    "state_in": "post_detail",
                    "state_out": "reply_submitted",
                    "stage_kind": "commit",
                    "match_keywords": ["提交回复", "回帖"],
                },
                {
                    "id": "downstream_visibility",
                    "label": "回复消息展示",
                    "action": "查看回复消息展示",
                    "module": "消息-回复消息",
                    "actor": "student",
                    "state_in": "reply_submitted",
                    "state_out": "message_viewed",
                    "stage_kind": "downstream_visibility",
                    "match_keywords": ["回复消息"],
                },
            ],
        }
    ]
    cases = [
        {
            "id": "raw-display-entry",
            "test_module": "作文区",
            "description": "作文区精选TAB展示-仅展示后台标记精选标签的作品并按热门排序",
            "steps": ["进入作文区论坛首页", "点击精选TAB", "检查列表内容与排序"],
            "expected_result": "精选作品按热门排序展示",
            "priority": "P0",
        },
        {
            "id": "raw-entry",
            "test_module": "论坛首页-内容列表",
            "description": "进入论坛首页内容列表",
            "steps": ["进入论坛首页", "查看内容列表加载完成"],
            "expected_result": "论坛首页内容列表可操作",
            "priority": "P0",
        },
        {
            "id": "raw-configure",
            "test_module": "论坛发帖与互动",
            "description": "选择分区与版块",
            "steps": ["选择内容分区", "选择发帖版块"],
            "expected_result": "分区与版块选择成功",
            "priority": "P0",
        },
        {
            "id": "raw-edit",
            "test_module": "发帖页",
            "description": "编辑帖子标题正文和图片",
            "steps": ["输入标题", "填写帖子正文", "上传图片"],
            "expected_result": "帖子内容进入可预览状态",
            "priority": "P0",
        },
        {
            "id": "raw-display-preview",
            "test_module": "作文区",
            "description": "作文详情页-展示图片时点击缩略图放大查看及左右滑动",
            "steps": ["进入勾选展示图片的作文详情页", "点击缩略图放大查看"],
            "expected_result": "图片可放大并左右滑动展示",
            "priority": "P0",
        },
        {
            "id": "raw-preview",
            "test_module": "帖子详情",
            "description": "帖子详情页预览发帖内容",
            "steps": ["进入帖子详情页", "检查标题、正文和图片内容"],
            "expected_result": "帖子详情内容与编辑内容一致",
            "priority": "P0",
        },
        {
            "id": "raw-commit",
            "test_module": "帖子详情-回复",
            "description": "提交回帖回复",
            "steps": ["点击回复", "输入评论内容", "提交回复"],
            "expected_result": "回复提交成功",
            "priority": "P0",
        },
        {
            "id": "raw-downstream",
            "test_module": "消息-回复消息",
            "description": "二级评论消息展示：回复评论的消息也需要在消息列表中展示",
            "steps": ["进入消息页面的回复TAB", "查看回复消息列表"],
            "expected_result": "新提交的回复消息在回复消息列表中展示",
            "priority": "P0",
        },
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)

    main_descriptions = [
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ]
    assert summary["linear_executable"] is True
    assert summary["main_chain_case_count"] == 6
    assert not any("作文区精选TAB展示" in description for description in main_descriptions)
    assert not any("作文详情页-展示图片" in description for description in main_descriptions)
    assert any("进入论坛首页内容列表" in description for description in main_descriptions)
    assert any("帖子详情页预览发帖内容" in description for description in main_descriptions)

    excluded = summary.get("main_chain_excluded_candidates") or []
    excluded_reasons = {
        item.get("reason")
        for item in excluded
        if "作文区精选TAB展示" in str(item.get("description") or "")
        or "作文详情页-展示图片" in str(item.get("description") or "")
    }
    assert excluded_reasons & {
        "display_only",
        "stage_module_not_aligned_with_blueprint",
        "stage_object_not_supported_by_case_text",
    }
    assert {
        "stage_module_not_aligned_with_blueprint",
        "stage_object_not_supported_by_case_text",
    } & excluded_reasons
    assert "stage_object_not_supported_by_case_text" in excluded_reasons


def test_apply_execution_plan_metadata_prefers_semantic_stage_match_over_weak_p0_overlap() -> None:
    reply = "回复"
    message = "消息"
    like = "点赞"
    audit = "审核"
    backend = "后台"
    approve = "通过"

    workflow_blueprints = [
        {
            "id": "forum_opt",
            "source": "current_requirement_blueprint",
            "steps": [
                {
                    "id": "commit",
                    "label": f"提交{reply}评论",
                    "action": f"点击{reply}按钮提交评论",
                    "state_in": "post_previewed",
                    "state_out": "reply_committed",
                    "stage_kind": "commit",
                    "match_keywords": [f"提交{reply}", f"{reply}评论"],
                },
                {
                    "id": "downstream_visibility",
                    "label": f"{reply}{message}展示",
                    "action": f"进入{message}页查看{reply}与{like}",
                    "state_in": "reply_committed",
                    "state_out": "msg_viewed",
                    "stage_kind": "downstream_visibility",
                    "match_keywords": [reply, like, message],
                },
                {
                    "id": "consume",
                    "label": f"{backend}{audit}帖子与{reply}内容",
                    "action": f"{backend}{audit}帖子与{reply}内容",
                    "state_in": "msg_viewed",
                    "state_out": "post_reviewed",
                    "stage_kind": "consume",
                    "match_keywords": [f"{backend}{audit}", f"{audit}{backend}", approve],
                },
            ],
        }
    ]
    weak_message_description = f"置顶帖显示元素-不显示{reply}量/{like}量"
    strong_message_description = f"{reply}帖子后在{reply}{message}Tab中可见"
    weak_audit_description = f"{audit}{backend}-按发帖/{reply}内容模糊搜索"
    strong_audit_description = f"{audit}{backend}-帖子列表页{approve}审核操作"
    cases = [
        {
            "id": "weak-message-overlap",
            "test_module": "论坛首页",
            "description": weak_message_description,
            "steps": ["进入官方区", "查看置顶帖"],
            "expected_result": f"不显示浏览量/{reply}量/{like}量",
            "priority": "P0",
        },
        {
            "id": "strong-message",
            "test_module": f"跨模块{message}",
            "description": strong_message_description,
            "steps": [
                f"用户B{reply}用户A的帖子",
                f"用户A进入{message}Tab",
                f"切换至{reply}{message}子Tab",
            ],
            "expected_result": f"用户A的{reply}{message}Tab中显示用户B的{reply}内容",
            "priority": "P1",
        },
        {
            "id": "weak-audit-module-only",
            "test_module": f"{audit}{backend}",
            "description": weak_audit_description,
            "steps": [f"在{backend}搜索框输入关键词", "点击搜索"],
            "expected_result": "搜索结果支持模糊匹配",
            "priority": "P0",
        },
        {
            "id": "strong-audit",
            "test_module": f"{audit}{backend}",
            "description": strong_audit_description,
            "steps": [
                f"进入{audit}{backend}帖子列表页",
                f"点击【{approve}】按钮{audit}一条帖子",
            ],
            "expected_result": f"点击【{approve}】后帖子{audit}状态变为{approve}",
            "priority": "P1",
        },
        {
            "id": "commit",
            "test_module": "论坛发帖与互动",
            "description": f"提交{reply}评论",
            "steps": [f"点击{reply}按钮提交评论"],
            "expected_result": f"{reply}提交成功",
            "priority": "P0",
        },
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)

    main_descriptions = {
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    }
    assert strong_message_description in main_descriptions
    assert strong_audit_description in main_descriptions
    assert weak_message_description not in main_descriptions
    assert weak_audit_description not in main_descriptions
    excluded = summary.get("main_chain_excluded_candidates") or []
    excluded_by_original_id = {
        item.get("case_id"): item.get("reason")
        for item in excluded
        if item.get("case_id") in {"weak-message-overlap", "weak-audit-module-only"}
    }
    assert excluded_by_original_id == {
        "weak-message-overlap": "stage_action_not_supported_by_case_text",
        "weak-audit-module-only": "stage_action_not_supported_by_case_text",
    }


def test_main_chain_rejects_completed_action_and_unproduced_precondition_state() -> None:
    workflow_blueprints = [
        {
            "id": "forum_publish_flow",
            "source": "current_requirement_blueprint",
            "steps": [
                {
                    "id": "entry",
                    "label": "Open forum home",
                    "action": "Open forum home through entry button",
                    "state_in": "initial",
                    "state_out": "forum_home",
                    "stage_kind": "entry",
                    "keywords": ["forum home"],
                },
                {
                    "id": "configure",
                    "label": "Select forum zone",
                    "action": "Select forum zone",
                    "state_in": "forum_home",
                    "state_out": "zone_selected",
                    "stage_kind": "configure",
                    "keywords": ["select forum zone"],
                },
                {
                    "id": "edit",
                    "label": "Edit post body",
                    "action": "Edit post title and body",
                    "state_in": "zone_selected",
                    "state_out": "post_editing",
                    "stage_kind": "edit",
                    "keywords": ["edit post"],
                },
                {
                    "id": "preview",
                    "label": "Preview edited post",
                    "action": "Preview edited post content",
                    "state_in": "post_editing",
                    "state_out": "post_ready",
                    "stage_kind": "preview",
                    "keywords": ["preview edited post"],
                },
                {
                    "id": "commit",
                    "label": "Submit post",
                    "action": "Submit post",
                    "state_in": "post_ready",
                    "state_out": "post_submitted",
                    "stage_kind": "commit",
                    "keywords": ["submit post"],
                },
                {
                    "id": "consume",
                    "label": "Open submitted post detail",
                    "action": "Open submitted post detail",
                    "state_in": "post_submitted",
                    "state_out": "post_visible",
                    "stage_kind": "consume",
                    "keywords": ["submitted post detail"],
                },
            ],
        }
    ]
    cases = [
        {
            "id": "entry",
            "test_module": "Forum",
            "description": "Open forum home through entry button",
            "preconditions": ["User is logged in"],
            "steps": ["Click forum entry button"],
            "expected_result": "Forum home opens",
            "priority": "P1",
        },
        {
            "id": "configure",
            "test_module": "Forum",
            "description": "Select forum zone",
            "preconditions": ["Forum home is open"],
            "steps": ["Select official zone"],
            "expected_result": "Selected zone is active",
            "priority": "P1",
        },
        {
            "id": "wrong-edit-completed",
            "test_module": "Forum",
            "description": "Edit post title and body then submit post",
            "preconditions": ["A forum zone is selected"],
            "steps": ["Edit post", "Submit post"],
            "expected_result": "Post submitted successfully",
            "priority": "P0",
        },
        {
            "id": "edit",
            "test_module": "Forum",
            "description": "Edit post title and body",
            "preconditions": ["A forum zone is selected"],
            "steps": ["Edit post title", "Edit post body"],
            "expected_result": "Edited content is ready for preview",
            "priority": "P1",
        },
        {
            "id": "preview",
            "test_module": "Forum",
            "description": "Preview edited post content",
            "preconditions": ["Post content is being edited"],
            "steps": ["Open preview"],
            "expected_result": "Edited title and body are shown in preview",
            "priority": "P1",
        },
        {
            "id": "commit",
            "test_module": "Forum",
            "description": "Submit post",
            "preconditions": ["Post preview is ready"],
            "steps": ["Click submit"],
            "expected_result": "Post submitted successfully",
            "priority": "P1",
        },
        {
            "id": "wrong-consume-message",
            "test_module": "Message",
            "description": "Open submitted post detail from approval message",
            "preconditions": ["User has an approved system message"],
            "steps": ["Click approval message"],
            "expected_result": "Submitted post detail opens",
            "priority": "P0",
        },
        {
            "id": "consume",
            "test_module": "Forum",
            "description": "Open submitted post detail",
            "preconditions": ["Post is submitted"],
            "steps": ["Click submitted post card"],
            "expected_result": "Submitted post detail opens",
            "priority": "P1",
        },
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)
    main_descriptions = [
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ]
    assert summary["linear_executable"] is True
    assert "Edit post title and body" in main_descriptions
    assert "Edit post title and body then submit post" not in main_descriptions
    assert "Open submitted post detail" in main_descriptions
    assert "Open submitted post detail from approval message" not in main_descriptions
    excluded = {
        (item.get("case_id"), item.get("stage_key")): item.get("reason")
        for item in (summary.get("main_chain_excluded_candidates") or [])
    }
    assert excluded[("wrong-edit-completed", "edit")] == "case_goal_spans_commit_stage"
    assert excluded[("wrong-consume-message", "consume")] == "precondition_state_not_produced_by_previous_stage"


def test_learning_completion_phrase_does_not_produce_generic_completed_state() -> None:
    current_case = {"preconditions": ["流程已完成"]}

    assert main_chain_precondition_conflict_reason(
        {"expected_result": "完成学习"},
        current_case,
    ) == "precondition_state_not_produced_by_previous_stage"
    assert main_chain_precondition_conflict_reason(
        {"expected_result": "流程已完成"},
        current_case,
    ) == ""
