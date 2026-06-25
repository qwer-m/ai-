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
    assert meta["scenario_policy_sources"]["manual_seed"] >= 1
    assert "讲错题接入AI.pdf" in meta["scenario_policy_documents"]
    assert "ai_answer_irrelevant_score_zero" in {
        scenario["key"] for scenario in payload["scenarios"]
    }
    assert {
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
    } <= {
        scenario["key"] for scenario in payload["scenarios"]
    }


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


def test_ai_wrong_question_duplicate_families_use_registry_caps() -> None:
    cases = [
        {
            "id": "TC-016",
            "description": "验证学员回答答非所问时，准确性直接记为0分",
            "test_module": "学员端AI评分",
            "steps": ["学员输入今天天气很好"],
            "expected_result": "评分结果显示准确性分数为0分，其他维度正常评分",
            "priority": "P0",
        },
        {
            "id": "TC-024",
            "description": "验证学员端回答答非所问时准确性自动0分，且总分按规则计算",
            "test_module": "学员端AI讲错题评分",
            "steps": ["输入“今天天气不错”"],
            "expected_result": "准确性维度得0分，综合评分低于8分",
            "priority": "P1",
        },
        {
            "id": "TC-037",
            "description": "验证评分规则：答非所问时，准确性直接0分",
            "test_module": "学员端AI讲错题交互-评分机制",
            "steps": ["回答与问题无关的内容"],
            "expected_result": "准确性得分为0分，并标注答非所问",
            "priority": "P2",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "讲错题AI评分",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("学员端AI评分"),
    )

    assert summary["scenario_policy_registry_version"] == 1
    assert summary["scenario_cap_policy"]["ai_answer_irrelevant_score_zero"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 2
    assert [case["id"] for case in governed] == ["TC-016"]


def test_ai_completed_reentry_variants_collapse_to_readonly_family() -> None:
    cases = [
        {
            "id": "TC-022",
            "description": "验证学员端重复进入已完成的讲错题页面，显示历史对话且所有交互按钮置灰不可操作",
            "test_module": "学员端AI讲错题-重复进入",
            "expected_result": "页面保留所有历史对话内容；输入框、录音按钮均置灰",
            "priority": "P1",
        },
        {
            "id": "TC-056",
            "description": "验证已完成讲错题后学员再次进入该讲错题页面，无法重复打分且不能继续回答",
            "test_module": "学员端AI讲错题重复进入",
            "expected_result": "展示之前的完整历史对话，输入框和语音录制按钮置灰或移除",
            "priority": "P2",
        },
    ]

    governed, summary = govern_cases_by_flow_structure(
        "讲错题已完成重复进入",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("学员端AI讲错题", "expanded_regression"),
    )

    assert summary["scenario_cap_policy"]["ai_completed_reentry_readonly"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-022"]


def test_schedule_duplicate_families_are_specific_before_generic_empty_state() -> None:
    cases = [
        {
            "id": "TC-007",
            "description": "首页-本周进度模块：当本周无课程时，空状态展示'本周暂无学习计划'",
            "test_module": "首页-本周进度模块",
            "expected_result": "本周进度区域显示文案'本周暂无学习计划'",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "首页-本周进度模块：空状态（无课程）旧版本显示“本周暂无学习计划”",
            "test_module": "首页-本周进度模块",
            "expected_result": "本周进度模块展示“本周暂无学习计划”，不显示排行榜和进度条",
            "priority": "P1",
        },
    ]

    scenario_keys = [
        classify_case_scenario_key(case, "stage:首页-本周进度模块")
        for case in cases
    ]
    governed, summary = govern_cases_by_flow_structure(
        "近期课程 首页 本周进度",
        cases,
        renumber_ids=False,
        project_profile=_project_profile("首页-本周进度模块", "standard_regression"),
    )

    assert all("schedule_week_progress_empty" in key for key in scenario_keys)
    assert summary["scenario_cap_policy"]["schedule_week_progress_empty"] == 1
    assert summary["scenario_duplicate_pruned_count"] == 1
    assert [case["id"] for case in governed] == ["TC-007"]


def test_domain_registry_keeps_only_dominant_domain_for_mixed_requirement_text() -> None:
    ai_text = (
        "讲错题接入AI，覆盖点击对话、"
        "继续录音、学员回答、追问、"
        "四轮对话、评分弹窗、综合评分、"
        "答非所问、去日清，但页面中偶然提到学习计划入口。"
    )
    schedule_text = (
        "近期课程和排课：新增计划、已有计划、"
        "编辑计划、课程规划、课程管理、"
        "学习计划、本周进度、本周任务、课堂管理、"
        "顺延和防抄答案，但某个按钮可能跳到讲错题。"
    )

    assert infer_domain_tags(ai_text) == {"ai_wrong_question_teaching"}
    assert infer_domain_tags(schedule_text) == {"recent_course_scheduling"}
    assert infer_primary_domain_tag(ai_text) == "ai_wrong_question_teaching"
    assert infer_primary_domain_tag(schedule_text) == "recent_course_scheduling"


def test_schedule_requirement_does_not_use_ai_supervisor_duplicate_family() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "首页本周任务-未完成课程按钮状态和详情入口",
            "test_module": "首页-本周任务模块",
            "steps": ["进入首页", "查看未完成课程卡片"],
            "expected_result": "未完成课程显示学习按钮可点击，详情入口展示课程标题",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "首页本周任务-已完成课程复习按钮置灰",
            "test_module": "首页-本周任务模块",
            "steps": ["进入首页", "查看已完成课程卡片"],
            "expected_result": "已完成课程显示复习按钮置灰，点击详情不触发讲错题督导页",
            "priority": "P1",
        },
    ]
    requirement = (
        "近期课程和排课：本周任务、"
        "学习计划、新增计划、已有计划、"
        "课程管理和防抄答案。"
    )

    structure = analyze_case_structure(requirement, cases)
    scenario_keys = [str(row.get("scenario_key") or "") for row in structure["rows"]]
    governed, summary = govern_cases_by_flow_structure(
        requirement,
        cases,
        renumber_ids=False,
        project_profile=_project_profile("首页-本周任务模块"),
    )

    assert structure["primary_domain"] == "recent_course_scheduling"
    assert not any("ai_supervisor_detail_button_state" in key for key in scenario_keys)
    assert "ai_supervisor_detail_button_state" not in summary["scenario_cap_policy"]
    assert [case["id"] for case in governed] == ["TC-001", "TC-002"]


def test_duplicate_excess_counts_only_cases_over_scenario_cap() -> None:
    requirement = "近期课程排课：已有计划、编辑计划、下架和二次确认。"
    cases = [
        {
            "id": "TC-001",
            "description": "排课已有计划-编辑计划后保存生效",
            "test_module": "排课-已有计划",
            "expected_result": "已有计划更新并保存成功",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "排课已有计划-下架时需要二次确认",
            "test_module": "排课-已有计划",
            "expected_result": "下架已有计划时弹出二次确认",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "排课已有计划-编辑下架后列表刷新",
            "test_module": "排课-已有计划",
            "expected_result": "编辑或下架后已有计划列表刷新",
            "priority": "P1",
        },
    ]
    project_profile = _project_profile("排课-已有计划", "standard_regression")

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


def test_priority_pool_retrieval_filters_by_primary_requirement_domain(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert meta["retrieval_query_primary_domain"] == "ai_wrong_question_teaching"
    assert meta["retrieval_domain_filter_applied"] is True
    assert meta["retrieval_domain_skipped_sample_count"] == 1
    assert [item["title"] for item in selected] == ["AI wrong-question sample"]


def test_duplicate_policy_runs_even_without_flow_outline() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "验证学员回答答非所问时准确性直接0分",
            "test_module": "学员端AI评分",
            "expected_result": "准确性得0分",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "答非所问时准确性计为0分并影响总分",
            "test_module": "评分规则",
            "expected_result": "准确性为0分，其他维度正常",
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
    assert summary["scenario_cap_policy"]["ai_answer_irrelevant_score_zero"] == 1
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

    assert [candidate.key for candidate in result] == ["candidate_ai_gap_permission"]
    assert [payload["domain"] for payload in proposed_payloads] == ["ai_wrong_question_teaching"]
