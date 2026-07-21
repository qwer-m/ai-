import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import (
    analyze_case_structure,
    classify_case_scenario_key,
    extract_flow_outline,
    govern_cases_by_flow_structure,
)
from modules.test_generation_components.control.project_profile_activation import (
    build_project_profile,
    merge_project_profile_control_state,
)


def test_extract_flow_outline_applies_data_flow_over_incidental_overview_mentions() -> None:
    requirement = """
    Product overview mentions Dashboard as a user entry point.
    1. Upload Center: users upload files and submit an import task.
    2. Review Queue: reviewers approve or reject parsed records.
    3. Dashboard: users see filtered records and statistics.
    4. Report Center: users export and share the weekly report.
    """
    cases = [
        {"id": "TC-001", "test_module": "Dashboard", "description": "dashboard title", "expected_result": "ok"},
        {"id": "TC-002", "test_module": "Upload Center", "description": "upload file", "expected_result": "ok"},
        {"id": "TC-003", "test_module": "Review Queue", "description": "review record", "expected_result": "ok"},
        {"id": "TC-004", "test_module": "Report Center", "description": "share report", "expected_result": "ok"},
    ]

    outline = extract_flow_outline(requirement, cases)

    assert [outline["flow_labels"][key] for key in outline["flow_order"]] == [
        "Upload Center",
        "Review Queue",
        "Dashboard",
        "Report Center",
    ]
    assert all(not key.startswith(("photo_", "workbook", "weekend")) for key in outline["flow_order"])


def test_flow_stage_matching_normalizes_pdf_user_glyph_variants() -> None:
    requirement = "1. 用戶详情弹窗:"
    cases = [
        {
            "id": "TC-001",
            "test_module": "用户详情弹窗",
            "description": "查看用户详情",
            "expected_result": "用户详情弹窗展示",
        }
    ]

    structure = analyze_case_structure(requirement, cases)

    assert structure["missing_flow_stage_count"] == 0


def test_analyze_case_structure_flags_scenario_duplicates_and_flow_inversion() -> None:
    requirement = """
    1. Upload Center: users upload files.
    2. Review Queue: reviewers approve records.
    3. Dashboard: users view statistics.
    """
    cases = [
        {
            "id": "TC-001",
            "description": "Dashboard statistics are shown correctly",
            "test_module": "Dashboard",
            "steps": ["Open dashboard"],
            "expected_result": "Total count and failed count are visible",
        },
        {
            "id": "TC-002",
            "description": "Review Queue manual correction updates the record",
            "test_module": "Review Queue",
            "steps": ["Open detail", "Click correct"],
            "expected_result": "Record status changes",
        },
        {
            "id": "TC-003",
            "description": "Dashboard statistics recalculate after filtering",
            "test_module": "Dashboard",
            "steps": ["Open dashboard", "Apply filter"],
            "expected_result": "Total count and failed count are recalculated",
        },
    ]

    structure = analyze_case_structure(requirement, cases)
    rows = structure["rows"]

    assert structure["duplicate_cluster_count"] == 1
    assert rows[0]["scenario_key"] == rows[2]["scenario_key"]
    assert rows[2]["is_scenario_duplicate"] is True
    assert rows[1]["flow_stage_label"] == "Review Queue"
    assert rows[1]["misordered_against_requirement_flow"] is True


def test_govern_cases_by_flow_structure_reorders_and_prunes_pattern_duplicates() -> None:
    requirement = """
    1. Upload Center: users upload files.
    2. Review Queue: reviewers approve records.
    3. Dashboard: users view statistics.
    """
    cases = [
        {
            "id": "TC-001",
            "description": "Dashboard statistics show total and failure counts",
            "test_module": "Dashboard",
            "steps": ["Open dashboard", "Check total", "Check failed"],
            "expected_result": "Statistics are correct",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Review Queue manual correction updates a record",
            "test_module": "Review Queue",
            "steps": ["Open detail", "Click correct"],
            "expected_result": "Record status changes",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Dashboard statistics recalculate after filtering",
            "test_module": "Dashboard",
            "steps": ["Open dashboard", "Apply filter"],
            "expected_result": "Total and failure counts are recalculated",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "Dashboard statistics show total and failure count summary",
            "test_module": "Dashboard",
            "steps": ["Open dashboard"],
            "expected_result": "Statistics total and failure counts are correct",
            "priority": "P2",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(requirement, cases, start_id=1, renumber_ids=True)

    assert summary["applied"] is True
    assert summary["flow_reordered"] is True
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["test_module"] for case in governed] == ["Review Queue", "Dashboard", "Dashboard"]
    assert [case["id"] for case in governed] == ["TC-001", "TC-002", "TC-003"]


def test_current_requirement_order_preempts_project_profile_for_governance() -> None:
    requirement = """
    Topic Detail is described first in this document.
    Forum Home is described later.
    """
    project_profile = {
        "confidence": 0.9,
        "profile_source": "project_config",
        "flow_outline": {
            "flow_order": ["forum_home", "topic_detail"],
            "flow_labels": {
                "forum_home": "Forum Home",
                "topic_detail": "Topic Detail",
            },
            "cross_cutting": [],
            "cross_cutting_labels": {},
        },
    }
    cases = [
        {
            "id": "TC-001",
            "description": "Topic Detail shows the post content",
            "test_module": "Topic Detail",
            "steps": ["Open topic detail"],
            "expected_result": "Post content is visible",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Forum Home lists active topics",
            "test_module": "Forum Home",
            "steps": ["Open forum home"],
            "expected_result": "Topic list is visible",
            "priority": "P0",
        },
    ]

    structure = analyze_case_structure(requirement, cases, project_profile=project_profile)
    governed, summary = govern_cases_by_flow_structure(
        requirement,
        cases,
        project_profile=project_profile,
        start_id=1,
        renumber_ids=True,
    )

    assert structure["flow_outline"]["source"] != "project_config"
    assert summary["flow_reordered"] is False
    assert [case["test_module"] for case in governed] == ["Topic Detail", "Forum Home"]


def test_project_profile_data_flow_order_supplements_when_current_flow_missing() -> None:
    project_profile = build_project_profile(
        requirement_text="",
        module_order_hint=["Operation Report", "Import Entry", "Review Queue", "Record List"],
        module_order_source="test_hint",
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Operation Report share link works",
            "expected_result": "Share link is created",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Import Entry accepts files",
            "expected_result": "Files upload successfully",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Review Queue manual correction works",
            "expected_result": "Record is corrected",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Record List title format is correct",
            "expected_result": "Title is correct",
            "priority": "P0",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "",
        cases,
        project_profile=project_profile,
        start_id=1,
        renumber_ids=True,
    )

    assert summary["flow_reordered"] is True
    assert project_profile["flow_outline"]["data_flow_edges"]
    descriptions = [case["description"] for case in governed]
    assert descriptions[:2] == [
        "Import Entry accepts files",
        "Review Queue manual correction works",
    ]
    assert set(descriptions[2:]) == {
        "Record List title format is correct",
        "Operation Report share link works",
    }


def test_low_confidence_project_profile_does_not_merge_flow_profile() -> None:
    state = merge_project_profile_control_state(
        {},
        {
            "confidence": 0.0,
            "profile_source": "document_extracted",
            "flow_outline": {
                "flow_order": ["admin_console", "forum_home"],
                "flow_labels": {
                    "admin_console": "Admin Console",
                    "forum_home": "Forum Home",
                },
            },
        },
    )

    assert "project_profile" not in state.source_meta
    assert state.source_meta["project_profile_gate"]["allowed"] is False
    assert state.source_meta["project_profile_gate"]["reason"] == "low_project_profile_confidence"


def test_govern_cases_by_flow_structure_uses_scenario_specific_caps() -> None:
    project_profile = {
        "confidence": 0.9,
        "flow_outline": {
            "flow_order": ["dashboard"],
            "flow_labels": {"dashboard": "Dashboard"},
            "cross_cutting": [],
            "cross_cutting_labels": {},
        },
    }
    cases = [
        {
            "id": "TC-001",
            "description": "Dashboard title format displays correctly",
            "test_module": "Dashboard",
            "expected_result": "Title is shown",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Dashboard title format follows current week",
            "test_module": "Dashboard",
            "expected_result": "Title week is correct",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "Dashboard statistics show total count",
            "test_module": "Dashboard",
            "expected_result": "Total count is correct",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Dashboard statistics show total count after filtering",
            "test_module": "Dashboard",
            "expected_result": "Total count is recalculated",
            "priority": "P1",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "",
        cases,
        project_profile=project_profile,
        max_per_scenario=2,
        renumber_ids=False,
    )

    assert summary["scenario_duplicate_pruned_count"] == 1
    assert summary["scenario_cap_policy"]["title_format"] == 1
    assert summary["scenario_cap_policy"]["statistics"] == 2
    assert [case["id"] for case in governed] == ["TC-001", "TC-003", "TC-004"]


def test_module_suffixes_are_normalized_for_stage_and_intent_duplicates() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Settings entry opens the account settings page",
            "test_module": "Settings / Navigation",
            "steps": ["Click Settings"],
            "expected_result": "Navigates to the account settings page",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Settings entry click opens account settings",
            "test_module": "Settings - Display",
            "steps": ["Tap Settings entry"],
            "expected_result": "Navigates to the account settings page",
            "priority": "P2",
        },
    ]

    structure = analyze_case_structure("", cases)
    rows = structure["rows"]

    assert rows[0]["flow_stage"] == rows[1]["flow_stage"]
    assert rows[0]["intent_signature"] == rows[1]["intent_signature"]
    assert structure["duplicate_cluster_count"] == 1
    assert rows[1]["is_scenario_duplicate"] is True


def test_governance_prefers_clear_single_purpose_case_over_complex_duplicate() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Report export downloads a PDF",
            "test_module": "Report / Export",
            "steps": [
                "Open report",
                "Click export",
                "Choose PDF",
                "Check file name",
                "Open file",
                "Check totals",
                "Check charts",
            ],
            "expected_result": (
                "The PDF downloads successfully, shows the file name, includes totals, includes charts, "
                "and keeps the current filters and date range."
            ),
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Report export downloads a PDF file",
            "test_module": "Report - Export",
            "steps": ["Open report", "Click export PDF"],
            "expected_result": "A PDF file is downloaded successfully.",
            "priority": "P1",
        },
    ]

    governed, summary = govern_cases_by_flow_structure("", cases, max_per_scenario=1, renumber_ids=False)

    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-002"]


def test_full_mode_preserves_explicit_scenario_caps() -> None:
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Dashboard title format displays correctly variant {idx}",
            "test_module": "Dashboard",
            "steps": ["Open dashboard"],
            "expected_result": "Dashboard title format is correct",
            "priority": "P1",
        }
        for idx in range(1, 4)
    ]

    default_governed, default_summary = govern_cases_by_flow_structure(
        "",
        cases,
        max_per_scenario=2,
        renumber_ids=False,
    )
    full_governed, full_summary = govern_cases_by_flow_structure(
        "",
        cases,
        max_per_scenario=2,
        renumber_ids=False,
        project_profile={
            "confidence": 0.9,
            "flow_outline": {
                "flow_order": ["dashboard"],
                "flow_labels": {"dashboard": "Dashboard"},
                "cross_cutting": [],
                "cross_cutting_labels": {},
            },
            "scenario_cluster_policy": {"coverage_mode": "full_functional_regression"},
        },
    )

    assert len(default_governed) == 1
    assert default_summary["scenario_cap_policy"]["title_format"] == 1
    assert len(full_governed) == 1
    assert full_summary["scenario_cap_policy"]["title_format"] == 1


def test_full_mode_does_not_apply_removed_document_specific_caps() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "删除已发布作文后恢复未投稿状态",
            "test_module": "我的作文",
            "steps": ["删除已发布作品"],
            "expected_result": "作品删除成功，我的作文状态恢复未投稿",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "删除作文圈已发布作品后恢复未投稿",
            "test_module": "作文圈",
            "steps": ["删除已发布作文圈作品"],
            "expected_result": "作文圈移除该作品，我的作文恢复未投稿",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "投稿成功后返回批改详情页显示审核中",
            "test_module": "作文投稿",
            "steps": ["提交投稿", "关闭投稿成功弹窗"],
            "expected_result": "投稿成功后作品状态变为审核中",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "提交投稿后显示投稿成功弹窗并进入审核中",
            "test_module": "作文投稿",
            "steps": ["点击提交投稿"],
            "expected_result": "显示投稿成功弹窗，返回后按钮状态为审核中",
            "priority": "P1",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "",
        cases,
        max_per_scenario=2,
        renumber_ids=False,
        project_profile={
            "confidence": 0.9,
            "flow_outline": {
                "flow_order": ["my_essay", "submission"],
                "flow_labels": {"my_essay": "我的作文", "submission": "作文投稿"},
                "cross_cutting": [],
                "cross_cutting_labels": {},
            },
            "scenario_cluster_policy": {"coverage_mode": "full_functional_regression"},
        },
    )

    assert "delete_restore_unsubmitted" not in summary["scenario_cap_policy"]
    assert "submission_success_state" not in summary["scenario_cap_policy"]
    assert summary["scenario_duplicate_pruned_count"] == 0
    assert [case["id"] for case in governed] == ["TC-001", "TC-002", "TC-003", "TC-004"]


def test_removed_document_specific_scenario_is_not_classified() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "AI批改-OCR识别服务返回超时或异常时，系统应显示批改失败提示并提供重试",
            "test_module": "作文批改-批改流程",
            "preconditions": ["学生已上传作文图片并点击去批改"],
            "steps": ["等待OCR识别请求超时或返回错误", "点击重试按钮"],
            "expected_result": "显示批改失败提示，并提供重试按钮；点击重试重新发起OCR识别",
        },
        {
            "id": "TC-002",
            "description": "上传图片数量超过上限时提示并保留已上传图片",
            "test_module": "作文批改",
            "preconditions": ["学生已进入作文批改上传页"],
            "steps": ["继续选择额外作文图片", "查看上传结果"],
            "expected_result": "系统提示图片数量超过上限，额外图片不加入上传区，已上传图片顺序和缩略图保持不变",
        },
    ]

    scenario_keys = [classify_case_scenario_key(case, "stage:作文批改") for case in cases]
    structure = analyze_case_structure("作文批改流程", cases)

    assert all(key != "global:upload_image_management" for key in scenario_keys)
    assert structure["duplicate_cluster_count"] == 0


def test_explicit_execution_sequence_suppresses_document_flow_misorder_noise() -> None:
    requirement = """
    1. Upload Center: users upload files.
    2. Review Queue: reviewers approve records.
    3. Dashboard: users view statistics.
    """
    cases = [
        {
            "id": "TC-001",
            "description": "Dashboard statistics are shown correctly",
            "test_module": "Dashboard",
            "expected_result": "Total count is visible",
            "execution_sequence": 1,
        },
        {
            "id": "TC-002",
            "description": "Review Queue manual correction updates the record",
            "test_module": "Review Queue",
            "expected_result": "Record status changes",
            "execution_sequence": 2,
        },
    ]

    structure = analyze_case_structure(requirement, cases)

    assert structure["misordered_count"] == 0


def test_chinese_business_flow_orders_entry_review_artifact_plan_report() -> None:
    modules = [
        "记录列表",
        "执行计划",
        "运行报告",
        "记录详情",
        "审批复核",
        "文件导入入口",
        "访问额度",
        "全局异常",
        "历史恢复",
    ]
    project_profile = build_project_profile(
        requirement_text="",
        module_order_hint=modules,
        module_order_source="test_hint",
    )
    cases = [
        {
            "id": f"TC-{index:03d}",
            "description": module,
            "test_module": module,
            "expected_result": "ok",
            "priority": "P0",
        }
        for index, module in enumerate(modules, start=1)
    ]

    governed, summary = govern_cases_by_flow_structure(
        "",
        cases,
        project_profile=project_profile,
        renumber_ids=False,
    )

    assert summary["flow_reordered"] is True
    ordered_modules = [case["test_module"] for case in governed]
    assert ordered_modules[:2] == [
        "文件导入入口",
        "审批复核",
    ]
    assert set(ordered_modules[2:6]) == {
        "记录列表",
        "记录详情",
        "执行计划",
        "运行报告",
    }
    assert ordered_modules[6:] == [
        "访问额度",
        "全局异常",
        "历史恢复",
    ]
