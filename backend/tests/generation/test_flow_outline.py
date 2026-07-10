import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.flow_outline import extract_flow_outline


def test_current_forum_comment_reply_flow_preempts_unrelated_project_profile() -> None:
    requirement = """
    论坛互动需求：
    1. 论坛入口：用户查看帖子流并进入帖子。
    2. 帖子阅读：用户阅读主帖内容。
    3. 发表评论：登录用户在帖子下发布评论。
    4. 回复评论：用户针对已有评论进行回复。
    """
    cases = [
        {
            "id": "TC-001",
            "test_module": "论坛入口",
            "description": "用户进入论坛入口查看帖子流",
            "steps": ["打开论坛入口", "查看帖子流"],
            "expected_result": "帖子流按最新互动时间展示",
        },
        {
            "id": "TC-002",
            "test_module": "帖子阅读",
            "description": "用户从论坛入口进入帖子阅读",
            "steps": ["点击帖子标题"],
            "expected_result": "展示主帖内容和评论区",
        },
        {
            "id": "TC-003",
            "test_module": "发表评论",
            "description": "登录用户在帖子阅读下方发表评论",
            "steps": ["输入评论内容", "点击发布评论"],
            "expected_result": "新评论出现在评论列表顶部",
        },
        {
            "id": "TC-004",
            "test_module": "回复评论",
            "description": "用户针对已有评论发起回复",
            "steps": ["点击评论回复", "输入回复内容", "提交回复"],
            "expected_result": "回复内容挂载在对应评论下方",
        },
    ]
    project_profile = {
        "confidence": 0.95,
        "profile_source": "project_profile",
        "flow_outline": {
            "flow_order": ["order_list", "payment_confirm", "invoice_download"],
            "flow_labels": {
                "order_list": "订单列表",
                "payment_confirm": "支付确认",
                "invoice_download": "发票下载",
            },
            "cross_cutting": [],
            "cross_cutting_labels": {},
        },
    }

    outline = extract_flow_outline(requirement, cases, project_profile=project_profile)
    flow_labels = [outline["flow_labels"][key] for key in outline["flow_order"]]

    assert outline["source"] != "project_profile"
    assert flow_labels == ["论坛入口", "帖子阅读", "发表评论", "回复评论"]
    assert outline["flow_order"] != ["order_list", "payment_confirm", "invoice_download"]


def test_current_document_headings_preempt_polluted_case_modules() -> None:
    requirement = """
    1. Forum Home:
    2. Topic Detail:
    3. Reply Submit:
    """
    cases = [
        {
            "id": "TC-001",
            "test_module": "Admin Console",
            "description": "Admin audits a legacy topic",
            "expected_result": "Audit status is visible",
        },
        {
            "id": "TC-002",
            "test_module": "Forum Home",
            "description": "User opens forum home",
            "expected_result": "Topic list is visible",
        },
        {
            "id": "TC-003",
            "test_module": "Topic Detail",
            "description": "User opens topic detail",
            "expected_result": "Post content is visible",
        },
    ]

    outline = extract_flow_outline(requirement, cases)
    flow_labels = [outline["flow_labels"][key] for key in outline["flow_order"]]

    assert flow_labels == ["Forum Home", "Topic Detail", "Reply Submit"]
    assert "Admin Console" not in flow_labels


def test_document_attribute_headings_do_not_count_as_flow_stages() -> None:
    requirement = """
    1. Requirement Background:
    2. Forum Home:
    3. Content Areas:
    Official zone:
    Official icon:
    Post title:
    Post time:
    Featured:
    4. Topic Detail:
    5. Reply Message:
    """

    outline = extract_flow_outline(requirement, [])
    flow_labels = [outline["flow_labels"][key] for key in outline["flow_order"]]

    assert flow_labels == ["Forum Home", "Topic Detail", "Reply Message"]
    assert "Requirement Background" not in flow_labels
    assert "Official icon" not in flow_labels
    assert "Post title" not in flow_labels
    assert "Featured" not in flow_labels


def test_chinese_numbered_field_headings_are_not_required_flow_stages() -> None:
    requirement = """
    1整体修改:
    2内容分区:
    3论坛首页:
    官方图标:
    帖子标题:
    发帖时间:
    精选:
    4回复消息:
    """

    outline = extract_flow_outline(requirement, [])
    flow_labels = [outline["flow_labels"][key] for key in outline["flow_order"]]

    assert flow_labels == ["论坛首页", "回复消息"]
    assert "整体修改" not in flow_labels
    assert "内容分区" not in flow_labels
    assert "帖子标题" not in flow_labels
    assert "精选" not in flow_labels
