from __future__ import annotations

from modules.test_generation_components.control.current_requirement_blueprint import (
    CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE,
    CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE,
    extract_current_requirement_blueprints,
    normalize_current_requirement_blueprint_payload,
)


class _ErrorClient:
    def generate_response(self, *_args, **_kwargs):
        return "Error: Empty response from model"


def test_current_requirement_blueprint_uses_deterministic_fallback_on_model_error() -> None:
    requirement = """
    论坛模块支持用户进入论坛列表，查看帖子详情。
    用户可以编辑帖子内容、上传图片并预览发布效果。
    用户点击发布后，帖子应保存成功，并在论坛列表和详情页展示最新内容。
    其他用户刷新后可以看到更新后的帖子，并完成评论互动。
    """

    blueprints, diagnostics = extract_current_requirement_blueprints(
        client=_ErrorClient(),
        requirement_text=requirement,
        project_id=10,
        user_id=1,
    )

    assert diagnostics["current_requirement_blueprint_status"] == "fallback_applied_after_model_error"
    assert diagnostics["current_requirement_blueprint_fallback"] is True
    assert diagnostics["current_requirement_blueprint_count"] == 1
    assert diagnostics["current_requirement_blueprint_step_count"] >= 6
    blueprint = blueprints[0]
    assert blueprint["repository_source"] == CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE
    assert blueprint["source_type"] == CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE
    assert blueprint["fallback"] is True
    assert blueprint["allow_final_materialization"] is False
    stage_kinds = [step["stage_kind"] for step in blueprint["steps"]]
    assert "commit" in stage_kinds
    assert "downstream_visibility" in stage_kinds
    assert "completion_sync" in stage_kinds
    assert all(step["allow_bridge"] is False for step in blueprint["steps"])


def test_current_requirement_blueprint_records_requirement_understanding_stats() -> None:
    requirement = """
论坛详情页支持用户进入评论区并发表回复。

[Requirement Understanding]
{"version":"requirement-understanding-v1","visual_fact_count":2,"invalid_visual_block_count":1,"visual_facts":[{"source":"pdf_visual:X46.jpg","text":"回复按钮可见"}]}
"""

    _blueprints, diagnostics = extract_current_requirement_blueprints(
        client=_ErrorClient(),
        requirement_text=requirement,
        project_id=10,
        user_id=1,
    )

    assert diagnostics["requirement_understanding_used"] is True
    assert diagnostics["requirement_understanding_visual_fact_count"] == 2
    assert diagnostics["requirement_understanding_invalid_visual_block_count"] == 1


def test_current_requirement_blueprint_reclassifies_weak_downstream_detail_step() -> None:
    blueprints = normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "forum_flow",
                    "name": "forum flow",
                    "steps": [
                        {
                            "id": "entry",
                            "label": "Open forum home",
                            "action": "Open forum home",
                            "stage_kind": "entry",
                        },
                        {
                            "id": "publish",
                            "label": "Publish post",
                            "action": "Submit and publish post",
                            "stage_kind": "commit",
                        },
                        {
                            "id": "detail",
                            "label": "Open post detail",
                            "action": "Open post detail",
                            "stage_kind": "downstream_visibility",
                        },
                    ],
                }
            ]
        },
        requirement_text="A user opens the forum, publishes a post, then opens the post detail page.",
    )

    steps = blueprints[0]["steps"]
    assert steps[2]["stage_kind"] == "consume"
    assert steps[2]["stage_kind_original"] == "downstream_visibility"
    assert steps[2]["stage_kind_adjusted"] is True


def test_current_requirement_blueprint_keeps_real_downstream_visibility_step() -> None:
    blueprints = normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "forum_flow",
                    "name": "forum flow",
                    "steps": [
                        {
                            "id": "entry",
                            "label": "Open forum home",
                            "action": "Open forum home",
                            "stage_kind": "entry",
                        },
                        {
                            "id": "publish",
                            "label": "Publish post",
                            "action": "Submit and publish post",
                            "stage_kind": "commit",
                        },
                        {
                            "id": "visible",
                            "label": "Verify post visibility",
                            "action": "Verify the published post is visible in the forum list and detail page",
                            "stage_kind": "downstream_visibility",
                        },
                    ],
                }
            ]
        },
        requirement_text="A user publishes a post and verifies it is visible in the forum list.",
    )

    steps = blueprints[0]["steps"]
    assert steps[2]["stage_kind"] == "downstream_visibility"
    assert steps[2]["stage_kind_adjusted"] is False


def test_current_requirement_blueprint_repairs_post_commit_edit_as_downstream_visibility() -> None:
    blueprints = normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "forum_flow",
                    "name": "forum flow",
                    "steps": [
                        {
                            "id": "entry",
                            "label": "Open forum home",
                            "action": "Open forum home",
                            "stage_kind": "entry",
                        },
                        {
                            "id": "edit",
                            "label": "Write reply",
                            "action": "Edit reply content",
                            "stage_kind": "edit",
                        },
                        {
                            "id": "publish",
                            "label": "Publish reply",
                            "action": "Submit and publish reply",
                            "stage_kind": "commit",
                        },
                        {
                            "id": "message",
                            "label": "Reply message visible",
                            "action": "Edit reply content check and verify reply notification message appears in the message tab",
                            "stage_kind": "edit",
                        },
                    ],
                }
            ]
        },
        requirement_text="After a user publishes a reply, the recipient sees a reply message in the message tab.",
    )

    steps = blueprints[0]["steps"]
    assert [step["stage_kind"] for step in steps] == [
        "entry",
        "edit",
        "commit",
        "downstream_visibility",
    ]
    assert steps[3]["stage_kind_original"] == "edit"
    assert steps[3]["stage_kind_adjusted"] is True
    assert steps[3]["stage_kind_adjustment_reason"] == "post_commit_closure_repair"


def test_current_requirement_blueprint_preserves_preview_identifier_with_detail_content() -> None:
    blueprints = normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "forum_flow",
                    "name": "forum flow",
                    "steps": [
                        {
                            "id": "entry",
                            "label": "Open forum home",
                            "action": "Open forum home",
                            "stage_kind": "entry",
                        },
                        {
                            "id": "preview",
                            "label": "查看帖子详情内容",
                            "action": "检查帖子详情内容是否与编辑内容一致",
                            "stage_kind": "edit",
                        },
                        {
                            "id": "publish",
                            "label": "Publish post",
                            "action": "Submit and publish post",
                            "stage_kind": "commit",
                        },
                    ],
                }
            ]
        },
        requirement_text="User checks post detail content before publishing.",
    )

    steps = blueprints[0]["steps"]
    assert steps[1]["stage_kind"] == "preview"
    assert steps[1]["stage_kind_original"] == "edit"
    assert steps[1]["stage_kind_adjusted"] is True


def test_current_requirement_blueprint_preserves_downstream_identifier_with_message_visibility() -> None:
    blueprints = normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "forum_flow",
                    "name": "forum flow",
                    "steps": [
                        {
                            "id": "entry",
                            "label": "Open forum home",
                            "action": "Open forum home",
                            "stage_kind": "entry",
                        },
                        {
                            "id": "publish",
                            "label": "Publish reply",
                            "action": "Submit and publish reply",
                            "stage_kind": "commit",
                        },
                        {
                            "id": "downstream_visibility",
                            "label": "回复消息展示",
                            "action": "进入消息页查看回复消息列表",
                            "stage_kind": "consume",
                        },
                    ],
                }
            ]
        },
        requirement_text="After publishing a reply, the recipient can see the reply message.",
    )

    steps = blueprints[0]["steps"]
    assert steps[2]["stage_kind"] == "downstream_visibility"
    assert steps[2]["stage_kind_original"] == "consume"
    assert steps[2]["stage_kind_adjusted"] is True


def test_current_requirement_blueprint_reclassifies_click_only_edit_as_consume() -> None:
    blueprints = normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "forum_flow",
                    "name": "forum flow",
                    "steps": [
                        {
                            "id": "entry",
                            "label": "Open forum home",
                            "action": "Open forum home",
                            "stage_kind": "entry",
                        },
                        {
                            "id": "edit",
                            "label": "点击发帖按钮",
                            "action": "点击发帖按钮",
                            "stage_kind": "edit",
                        },
                        {
                            "id": "publish",
                            "label": "Publish post",
                            "action": "Submit and publish post",
                            "stage_kind": "commit",
                        },
                    ],
                }
            ]
        },
        requirement_text="User opens the posting page before writing the post.",
    )

    steps = blueprints[0]["steps"]
    assert steps[1]["stage_kind"] == "consume"
    assert steps[1]["stage_kind_original"] == "edit"
    assert steps[1]["stage_kind_adjusted"] is True
