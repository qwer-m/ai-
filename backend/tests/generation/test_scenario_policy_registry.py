import json
import importlib
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import (
    analyze_case_structure,
    classify_case_scenario_key,
    govern_cases_by_flow_structure,
    summarize_duplicate_excess_by_policy,
)
from modules.test_generation_components.coverage import coverage_analyzer
from modules.test_generation_components.coverage import coverage_case_classifier
from modules.test_generation_components.coverage import scenario_registry
from modules.test_generation_components.coverage import registry_candidate_store
from modules.test_generation_components.coverage.scenario_registry import (
    infer_primary_domain_tag,
    infer_domain_tags,
    scenario_registry_meta,
)

feedback_control = importlib.import_module(
    "modules.test_generation_components.control.build_feedback_control_state"
)


def test_scenario_registry_is_loaded_from_data_file() -> None:
    data_path = Path(scenario_registry.__file__).with_name("scenario_registry_data.json")
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    meta = scenario_registry_meta()

    assert payload["version"] == meta["scenario_policy_registry_version"]
    assert len(payload["domains"]) == meta["domain_policy_registered_count"]
    assert len(payload["scenarios"]) == meta["scenario_policy_registered_count"]
    assert payload["domains"] == []
    assert meta["domain_policy_registered_count"] == 0
    assert "manual_seed" not in meta["scenario_policy_sources"]
    assert "讲错题接入AI.pdf" not in meta["scenario_policy_documents"]
    scenario_keys = {
        scenario["key"] for scenario in payload["scenarios"]
    }
    assert {
        "title_format",
        "network_error",
        "permission",
    } <= scenario_keys
    assert not {"bad_image_review", "quota_exhaustion"} & scenario_keys
    question_only_keywords = [
        keyword
        for scenario in payload["scenarios"]
        for keyword in scenario.get("keywords", [])
        if keyword and set(keyword) == {"?"}
    ]
    assert question_only_keywords == []
    keywords_by_key = {
        scenario["key"]: set(scenario.get("keywords", []))
        for scenario in payload["scenarios"]
    }
    assert {"筛选", "过滤", "开关", "只看"} <= keywords_by_key["filter_toggle"]
    assert {"空状态", "暂无", "无记录", "无数据"} <= keywords_by_key["empty_state"]
    assert {"来源", "标签", "配置"} <= keywords_by_key["source_consistency"]
    assert {"手动", "判定", "修正", "更正"} <= keywords_by_key["manual_correction"]
    assert {"反馈", "处理"} <= keywords_by_key["feedback"]
    assert {"打印", "导出"} <= keywords_by_key["print_export"]
    assert not {
        "pdf_download_content",
        "essay_empty_state",
        "delete_restore_unsubmitted",
        "featured_sorting",
        "essay_submission_failure_reason",
        "essay_polish_copy",
        "essay_critique_button_availability",
        "my_essay_limit",
        "nonlinear_course_unlock",
        "free_first_lesson",
        "secret_overlay",
        "essay_submission_success_state",
        "critique_limit",
        "star_rating",
        "sentence_comment_jump",
        "hot_recommend_entry",
        "secret_entry_list",
        "essay_sample_numbering",
        "original_image_toggle",
        "submission_success_state",
        "community_empty_state",
        "full_text_copy",
        "polish_original_compare",
        "upload_image_management",
        "essay_limit_20",
        "technique_practice_answer",
    } & scenario_keys
    assert not {"essay_writing", "course_access"} & {
        domain["key"] for domain in payload["domains"]
    }


def test_coverage_policy_uses_registry_as_single_source_of_truth() -> None:
    registry_patterns = scenario_registry.scenario_pattern_entries(
        include_domain_specific=True
    )
    registry_default_caps = scenario_registry.default_scenario_caps()
    registry_mode_caps = scenario_registry.mode_scenario_caps()

    assert coverage_case_classifier._SCENARIO_PATTERNS == registry_patterns
    assert coverage_analyzer._DEFAULT_SCENARIO_CAPS == registry_default_caps
    for mode, caps in coverage_analyzer._SCENARIO_CAPS_BY_MODE.items():
        explicit_registry_caps = registry_mode_caps.get(mode, {})
        for scenario_key in registry_default_caps:
            if scenario_key in caps:
                assert caps[scenario_key] == explicit_registry_caps[scenario_key]


def test_scenario_registry_allows_general_policies_without_domain_profiles() -> None:
    domains = scenario_registry._build_domain_policies({"domains": []})
    scenarios = scenario_registry._build_scenario_policies(
        {"scenarios": [{"key": "generic_save", "keywords": ["save"], "domain": "general"}]}
    )

    assert domains == ()
    scenario_registry._validate_registry_links(domains, scenarios)


def test_scenario_registry_loader_skips_disabled_entries() -> None:
    payload = {
        "domains": [
            {
                "key": "enabled_domain",
                "hints": ["enabled"],
                "status": "active",
            },
            {
                "key": "disabled_domain",
                "hints": ["disabled"],
                "status": "disabled",
            },
        ],
        "scenarios": [
            {
                "key": "enabled_scenario",
                "keywords": ["enabled"],
                "status": "active",
            },
            {
                "key": "disabled_scenario",
                "keywords": ["disabled"],
                "status": "disabled",
            },
        ],
    }

    domains = scenario_registry._build_domain_policies(payload)
    scenarios = scenario_registry._build_scenario_policies(payload)

    assert [policy.key for policy in domains] == ["enabled_domain"]
    assert [policy.key for policy in scenarios] == ["enabled_scenario"]


def test_scenario_registry_loads_judge_duplicate_policy_fields() -> None:
    payload = {
        "scenarios": [
            {
                "key": "custom_judge_policy",
                "keywords": ["custom"],
                "judge_threshold": {"score": 0.42, "overlap": 9},
                "cross_module": False,
            }
        ],
    }

    scenarios = scenario_registry._build_scenario_policies(payload)

    assert scenarios[0].judge_score_threshold == 0.42
    assert scenarios[0].judge_overlap_threshold == 9
    assert scenarios[0].cross_module is False


def test_scenario_classification_accepts_alias_fields() -> None:
    case = {
        "caseId": "TC-ALIAS",
        "testModule": "Network recovery",
        "title": "retry after network interruption",
        "expectedResult": "network error message is displayed and retry succeeds",
        "testSteps": ["disconnect network", "click retry"],
    }

    assert classify_case_scenario_key(case, "stage:recovery") == "stage:recovery:network_error:obj:network_recovery_retry"


def test_scenario_registry_rejects_duplicate_active_keys() -> None:
    payload = {
        "domains": [
            {"key": "same_domain", "hints": ["a"]},
            {"key": "same_domain", "hints": ["b"]},
        ],
        "scenarios": [
            {"key": "same_scenario", "keywords": ["a"]},
            {"key": "same_scenario", "keywords": ["b"]},
        ],
    }

    with pytest.raises(ValueError, match="duplicate domain policy key"):
        scenario_registry._build_domain_policies(payload)

    with pytest.raises(ValueError, match="duplicate scenario family policy key"):
        scenario_registry._build_scenario_policies(payload)


def test_scenario_registry_rejects_unknown_scenario_domains() -> None:
    domains = scenario_registry._build_domain_policies(
        {"domains": [{"key": "known_domain", "hints": ["known"]}]}
    )
    scenarios = scenario_registry._build_scenario_policies(
        {"scenarios": [{"key": "case_family", "keywords": ["case"], "domain": "missing_domain"}]}
    )

    with pytest.raises(ValueError, match="unknown domains"):
        scenario_registry._validate_registry_links(domains, scenarios)


def _project_profile(label: str, mode: str = "full_functional_regression") -> dict:
    return {
        "confidence": 0.9,
        "flow_outline": {
            "flow_order": ["stage"],
            "flow_labels": {"stage": label},
            "cross_cutting": [],
            "cross_cutting_labels": {},
        },
        "scenario_cluster_policy": {"coverage_mode": mode},
    }


def test_general_empty_state_family_applies_without_domain_profile() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "记录列表无数据时展示空状态",
            "test_module": "记录列表",
            "expected_result": "列表显示暂无数据且不展示记录卡片",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "记录列表为空时展示无数据占位",
            "test_module": "记录列表",
            "expected_result": "页面显示暂无数据的空状态占位",
            "priority": "P1",
        },
    ]

    scenario_keys = [
        classify_case_scenario_key(case, "stage:记录列表")
        for case in cases
    ]
    governed, summary = govern_cases_by_flow_structure(
        "记录列表在无数据时显示空状态。",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("记录列表", "standard_regression"),
    )

    assert all("empty_state" in key for key in scenario_keys)
    assert summary["scenario_cap_policy"]["empty_state"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-001"]


def test_removed_product_terms_do_not_activate_hidden_domain_profiles() -> None:
    for requirement_text in (
        "AI评分、错题讲解、语音录制和追问。",
        "课程排期、学习计划、本周任务和顺延。",
    ):
        assert infer_domain_tags(requirement_text) == set()
        assert infer_primary_domain_tag(requirement_text) == ""


def test_forum_requirement_does_not_leak_ai_wrong_question_policy() -> None:
    requirement = (
        "论坛优化：本期优化论坛内部体验，包含帖子详情、回复、发帖、作文区和消息。"
        "专项计划学习报告只作为论坛内关联入口，点击学习报告按钮进入报告列表页。"
        "会员权限、详情页按钮、完成和未完成状态都属于当前论坛需求描述。"
    )
    cases = [
        {
            "id": "TC-001",
            "test_module": "帖子详情",
            "description": "帖子详情页回复按钮完成二级回复展示",
            "steps": ["进入帖子详情", "点击回复按钮", "提交回复"],
            "expected_result": "回复内容展示在对应评论下方",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "test_module": "专项计划学习报告",
            "description": "点击学习报告按钮进入专项计划报告列表页",
            "steps": ["进入论坛", "点击学习报告按钮"],
            "expected_result": "进入本学科本周期报告列表页",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "test_module": "论坛首页",
            "description": "非会员用户帖子不展示会员标签",
            "steps": ["进入论坛首页", "查看非会员用户帖子"],
            "expected_result": "会员标签不展示，其他用户信息正常展示",
            "priority": "P2",
        },
    ]

    structure = analyze_case_structure(requirement, cases)
    scenario_keys = [str(row.get("scenario_key") or "") for row in structure["rows"]]
    governed, summary = govern_cases_by_flow_structure(requirement, cases, renumber_ids=False)

    assert "course_access" not in set(structure["domain_tags"])
    assert not any("ai_supervisor_detail_button_state" in key for key in scenario_keys)
    assert not any("essay_critique_button_availability" in key for key in scenario_keys)
    assert not any("original_image_toggle" in key for key in scenario_keys)
    assert not any("featured_sorting" in key for key in scenario_keys)
    assert "ai_supervisor_detail_button_state" not in summary["scenario_cap_policy"]
    assert "essay_critique_button_availability" not in summary["scenario_cap_policy"]
    assert "original_image_toggle" not in summary["scenario_cap_policy"]
    assert "featured_sorting" not in summary["scenario_cap_policy"]
    assert "讲错题接入AI.pdf" not in summary["scenario_policy_documents"]
    matched_domains = {
        str(item.get("domain") or "")
        for item in summary["registry_impact"]["policies_matched"]
    }
    assert matched_domains <= {"general"}
    assert not any(
        item.get("key") == "ai_supervisor_detail_button_state"
        for item in summary["registry_impact"]["policies_matched"]
    )
    assert "ai_supervisor_detail_button_state" not in summary["registry_impact"][
        "cross_module_policies_in_effect"
    ]
    assert len(governed) == 3
    assert {case["id"] for case in governed} == {"TC-001", "TC-002", "TC-003"}


def test_runtime_registry_ignores_unregistered_primary_domain_names() -> None:
    general = dict(scenario_registry.scenario_pattern_entries())
    with_unknown_domain = dict(
        scenario_registry.scenario_pattern_entries(primary_domain="unregistered_product")
    )

    assert general == with_unknown_domain


def test_duplicate_excess_counts_only_cases_over_general_scenario_cap() -> None:
    requirement = "统计记录总数。"
    cases = [
        {
            "id": "TC-001",
            "description": "统计记录总数并展示数量",
            "test_module": "记录统计",
            "expected_result": "总数显示为 10",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "统计记录总数并展示数量",
            "test_module": "记录统计",
            "expected_result": "总数显示为 10",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "统计记录总数并展示数量",
            "test_module": "记录统计",
            "expected_result": "总数显示为 10",
            "priority": "P1",
        },
    ]
    project_profile = _project_profile("记录统计", "standard_regression")

    structure_two = analyze_case_structure(requirement, cases[:2])
    excess_two = summarize_duplicate_excess_by_policy(
        structure_two,
        project_profile=project_profile,
    )
    structure_three = analyze_case_structure(requirement, cases)
    excess_three = summarize_duplicate_excess_by_policy(
        structure_three,
        project_profile=project_profile,
    )

    assert excess_two["raw_duplicate_case_count"] >= 1
    assert excess_two["duplicate_excess_case_count"] == 0
    assert excess_three["duplicate_excess_case_count"] == 1


def test_disabled_scenario_pruning_also_preserves_intent_clusters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "验证保存计划后首页任务卡片刷新",
            "test_module": "排课-计划保存",
            "expected_result": "首页任务卡片展示最新课程计划",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "验证保存计划后已有计划列表刷新",
            "test_module": "排课-已有计划",
            "expected_result": "已有计划列表展示最新课程计划",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "验证保存计划后学生端本周进度刷新",
            "test_module": "学生端-首页",
            "expected_result": "本周进度展示最新课程计划",
            "priority": "P1",
        },
    ]

    def fake_analyze_case_structure(
        requirement_context: str,
        normalized_cases: list[dict],
        *,
        project_profile: dict | None = None,
    ) -> dict:
        return {
            "flow_outline": {
                "flow_order": ["commit"],
                "flow_labels": {"commit": "保存生效"},
                "cross_cutting": [],
                "cross_cutting_labels": {},
            },
            "rows": [
                {
                    "candidate_index": index,
                    "flow_stage": "commit",
                    "flow_rank": 0,
                    "cross_cutting": [],
                    "scenario_key": f"commit:semantic:{index}",
                }
                for index in range(1, len(normalized_cases) + 1)
            ],
            "duplicate_clusters": [
                {
                    "cluster_id": "SC-001",
                    "scenario_key": "commit:intent:save:plan:visible",
                    "group_type": "intent",
                    "size": len(normalized_cases),
                    "candidate_indices": list(range(1, len(normalized_cases) + 1)),
                }
            ],
            "duplicate_cluster_count": 1,
            "misordered_count": 0,
            "missing_flow_stage_count": 0,
        }

    monkeypatch.setattr(
        coverage_analyzer,
        "analyze_case_structure",
        fake_analyze_case_structure,
    )

    project_profile = {
        "scenario_cluster_policy": {
            "disable_scenario_pruning": True,
            "intent_duplicate_cap": 1,
            "coverage_mode": "full_functional_regression",
        }
    }
    governed, summary = govern_cases_by_flow_structure(
        "近期课程排课：保存计划后下游端同步展示。",
        cases,
        renumber_ids=False,
        project_profile=project_profile,
    )
    duplicate_excess = summarize_duplicate_excess_by_policy(
        fake_analyze_case_structure(
            "近期课程排课：保存计划后下游端同步展示。",
            cases,
            project_profile=project_profile,
        ),
        project_profile=project_profile,
    )

    assert [case["id"] for case in governed] == ["TC-001", "TC-002", "TC-003"]
    assert summary["scenario_duplicate_pruned_count"] == 0
    assert summary["scenario_cap_policy"] == {}
    assert duplicate_excess["raw_duplicate_cluster_count"] == 1
    assert duplicate_excess["duplicate_excess_case_count"] == 0


def test_priority_pool_query_without_retrieval_hit_does_not_scan_full_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_control, "retrieve_priority_sample_patterns", lambda **_: [])
    requirement_text = (
        "讲错题接入AI：点击对话、继续录音、"
        "学员回答、AI追问、四轮对话、评分弹窗、"
        "综合评分、答非所问，页面中只是顺带提到学习计划入口。"
    )
    samples = [
        {
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "pattern_confidence": 0.95,
            "pattern_summary": "讲错题 AI 追问 语音录制 评分 答非所问",
            "title": "AI wrong-question sample",
        },
        {
            "signal_type": "positive",
            "pattern_usage": "prefer",
            "pattern_confidence": 0.95,
            "pattern_summary": "近期课程 排课 新增计划 已有计划 课程管理 学习计划 讲错题入口",
            "title": "schedule sample",
        },
    ]

    selected, meta = feedback_control._select_priority_pool_samples_by_requirement(
        samples=samples,
        project_id=1,
        user_id=1,
        requirement_text=requirement_text,
    )

    assert selected == []
    assert meta["retrieval_fallback"] == "retrieval_no_match"
    assert meta["retrieval_domain_filter_applied"] is False
    assert meta["retrieval_domain_skipped_sample_count"] == 0


def test_duplicate_policy_runs_even_without_flow_outline() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "记录列表无数据时展示空状态",
            "test_module": "记录列表",
            "expected_result": "显示暂无数据且不展示记录卡片",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "记录列表无数据时展示空状态",
            "test_module": "记录列表",
            "expected_result": "显示暂无数据且不展示记录卡片",
            "priority": "P1",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "",
        cases,
        renumber_ids=False,
        project_profile={},
    )

    assert summary["applied"] is True
    assert summary["flow_reordered"] is False
    assert summary["scenario_cap_policy"]["empty_state"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-001"]


def test_pending_registry_candidates_do_not_become_runtime_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scenario_registry,
        "_load_candidate_payload",
        lambda: {
            "version": 1,
            "candidates": [
                {
                    "key": "candidate_pending_only",
                    "keywords": ["pending-only"],
                    "status": "pending",
                    "proposed_default_cap": 3,
                },
                {
                    "key": "candidate_accepted",
                    "keywords": ["accepted-only"],
                    "status": "accepted",
                    "proposed_default_cap": 2,
                },
            ],
        },
    )

    policies = scenario_registry._load_candidate_policies()

    assert [policy.key for policy in policies] == ["candidate_accepted"]
    assert scenario_registry._candidate_status_count("pending") == 1


def test_registry_candidate_proposal_skips_general_ui_and_cross_domain_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposed_payloads: list[dict[str, object]] = []

    def fake_propose_registry_candidate(**kwargs: object):
        proposed_payloads.append(dict(kwargs))
        return registry_candidate_store.RegistryCandidate(
            key=str(kwargs["key"]),
            keywords=tuple(kwargs["keywords"]),
            proposed_action=str(kwargs["proposed_action"]),
            domain=str(kwargs["domain"]),
        )

    monkeypatch.setattr(
        registry_candidate_store,
        "propose_registry_candidate",
        fake_propose_registry_candidate,
    )

    result = registry_candidate_store.propose_from_recurring_signals(
        signals=[],
        patterns=[
            {
                "pattern_id": "general-ui",
                "sample_count": 5,
                "avg_weight": 0.9,
                "pattern_scope": "general",
                "pattern_category": "ui_copy",
                "cluster_key": "display",
                "pattern_canonical": "static ui display button copy check",
            },
            {
                "pattern_id": "cross-domain",
                "sample_count": 5,
                "avg_weight": 0.9,
                "pattern_scope": "recent_course_scheduling",
                "pattern_category": "mixed",
                "cluster_key": "ai",
                "pattern_canonical": "讲错题 AI 追问 语音录制 评分 答非所问",
            },
            {
                "pattern_id": "accepted-domain",
                "sample_count": 5,
                "avg_weight": 0.9,
                "pattern_scope": "ai_wrong_question_teaching",
                "pattern_category": "ai_gap",
                "cluster_key": "permission",
                "pattern_canonical": "讲错题 麦克风权限 语音录制 学员回答 追问",
            },
        ],
    )

    assert result == []
    assert proposed_payloads == []
