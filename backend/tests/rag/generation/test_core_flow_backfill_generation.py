from __future__ import annotations

import copy
import json

from modules.test_generation_components.coverage.core_flow_backfill import plan_core_flow_backfill
from modules.test_generation_components.coverage.core_flow_backfill_generation import (
    generate_core_flow_backfill_candidates,
)
import modules.test_generation_components.coverage.core_flow_backfill_generation as generation_mod


class FakeLLMClient:
    def __init__(self, payload: list[dict] | str):
        self.payload = payload
        self.calls: list[dict] = []
        self.max_tokens = 2048

    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs) -> str:  # noqa: ANN001
        self.calls.append(
            {
                "requirement": requirement,
                "prompt": prompt,
                "db": db,
                "kwargs": kwargs,
            }
        )
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def _existing_cases() -> list[dict]:
    return [
        {
            "id": "TC-001",
            "description": "验证普通列表加载",
            "test_module": "作业列表",
            "steps": ["1. 打开页面", "2. 查看列表"],
            "test_input": "无",
            "expected_result": "列表显示成功",
            "priority_final": "P1",
        },
        {
            "id": "TC-002",
            "description": "验证详情页打开",
            "test_module": "作业列表",
            "steps": ["1. 点击题目", "2. 打开详情"],
            "test_input": "无",
            "expected_result": "进入详情页",
            "priority_final": "P1",
        },
    ]


def _existing_cases_8() -> list[dict]:
    rows: list[dict] = []
    for idx in range(1, 9):
        rows.append(
            {
                "id": f"TC-{idx:03d}",
                "description": f"普通页面回归检查-{idx}",
                "test_module": "基础模块",
                "steps": ["1. 打开页面", "2. 查看元素"],
                "test_input": "无",
                "expected_result": "页面文案等于“基础页面”",
                "priority_final": "P1",
            }
        )
    return rows


def _build_backfill_plan(existing_cases: list[dict], max_backfill_cases: int = 12) -> dict:
    return plan_core_flow_backfill(
        requirement_context="周中周末学习闭环测试",
        existing_cases=existing_cases,
        max_backfill_cases=max_backfill_cases,
    )


def _valid_paid_gate_candidate() -> dict:
    return {
        "case_id": "X-1",
        "description": "未付费用户触发付费拦截",
        "test_module": "付费拦截",
        "preconditions": ["未付费用户已登录"],
        "steps": ["1. 点击学习入口", "2. 观察拦截弹窗"],
        "test_input": "未付费账户",
        "expected_result": "页面文案等于‘开通订阅后继续学习’，且不创建学习任务记录",
        "priority": "P0",
        "model_priority": "P0",
        "source_flow_key": "paid_gate",
        "source_flow_name": "付费拦截",
        "backfill_generated": True,
    }


def _valid_wrong_only_filter_candidate() -> dict:
    return {
        "case_id": "X-2",
        "description": "只看错题筛选仅展示错题",
        "test_module": "习题本",
        "preconditions": ["存在2道错题和3道正确题"],
        "steps": ["1. 打开只看错题筛选", "2. 查看错题列表"],
        "test_input": "筛选条件=只看错题",
        "expected_result": "列表数量等于2，且每条记录状态=错误",
        "priority": "P1",
        "model_priority": "P1",
        "source_flow_key": "wrong_only_filter",
        "source_flow_name": "只看错题筛选",
        "backfill_generated": True,
    }


def _candidate_for_flow(flow_key: str, flow_name: str) -> dict:
    payload = {
        "case_id": "X",
        "description": f"{flow_name} 覆盖用例",
        "test_module": flow_name,
        "preconditions": ["已登录"],
        "steps": ["1. 执行步骤", "2. 观察结果"],
        "test_input": "无",
        "expected_result": "页面文案等于“可验证结果”",
        "priority": "P1",
        "model_priority": "P1",
        "source_flow_key": flow_key,
        "source_flow_name": flow_name,
        "backfill_generated": True,
    }
    flow_text_map = {
        "paid_gate": ("付费拦截", "未付费用户触发 paywall 拦截"),
        "textbook_grade_isolation": ("教材年级切换", "切换教材与年级后数据隔离"),
        "weekday_review_to_workbook": ("周中批改习题本", "周中 OCR 批改后生成习题本"),
        "workbook_statistics": ("习题本统计", "习题本总题数=正确数+错误数"),
        "wrong_only_filter": ("只看错题", "只看错题筛选后仅展示错题列表"),
        "mastery_calculation_level": ("掌握度等级", "掌握等级按薄弱/一般/熟练展示"),
        "weekend_classification": ("周末分类", "周末第一步分类为重点加强或需要注意"),
        "no_weekday_data_fallback": ("周中无数据兜底", "周中无数据时展示兜底提示"),
        "all_correct_history_review": ("全部正确推荐", "全部正确后进入历史巩固通用推荐"),
        "weekend_completion_sync": ("周末完成同步", "周末学习完成后状态同步并可查看学习报告"),
        "supervisor_report_generation": ("督导端报告", "督导端生成学习成长报告并校验报告数据"),
        "unauthorized_data_isolation": ("权限隔离", "权限不足越权访问被拦截且数据隔离"),
    }
    desc, expected = flow_text_map.get(flow_key, (flow_name, "页面文案等于“可验证结果”"))
    payload["description"] = desc
    payload["test_module"] = flow_name
    payload["expected_result"] = expected
    return payload


def _full_12_flow_payload(plan: dict) -> list[dict]:
    rows: list[dict] = []
    for item in plan.get("backfill_plan") or []:
        flow_key = str(item.get("flow_key") or "")
        flow_name = str(item.get("flow_name") or flow_key)
        rows.append(_candidate_for_flow(flow_key, flow_name))
    return rows


def test_backfill_generation_dry_run_core_behaviors() -> None:
    existing = _existing_cases()
    existing_snapshot = copy.deepcopy(existing)
    plan = _build_backfill_plan(existing)

    candidate_1 = _valid_paid_gate_candidate()
    candidate_1["coverage_tags"] = ["wrong_only_filter"]
    candidate_2 = _valid_wrong_only_filter_candidate()

    fake_client = FakeLLMClient([candidate_1, candidate_2])
    result = generate_core_flow_backfill_candidates(
        requirement_context="周中周末学习闭环测试",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=fake_client,
        max_candidates=12,
    )

    # 1. backfill generation 不修改 existing_cases
    assert existing == existing_snapshot

    # 2. 每个 candidate 带 source_flow_key
    for case in result["generated_backfill_candidate_cases"]:
        assert "source_flow_key" in case

    # 8. 合格 candidate 进入 accepted_backfill_cases
    accepted = result["accepted_backfill_cases"]
    assert len(accepted) >= 2

    # 9. merged_preview_cases = existing_cases + accepted（本测试不触发上限截断）
    merged = result["merged_preview_cases"]
    assert len(merged) == len(existing) + len(accepted)

    # 10. coverage_after_merged_preview 高于 coverage_before
    before = int(result["coverage_before"]["core_flow_covered_count"])
    after = int(result["coverage_after_merged_preview"]["core_flow_covered_count"])
    assert after > before

    # 11. dry_run 输出 backfill_not_applied=true
    assert result["dry_run"] is True
    assert result["backfill_not_applied"] is True

    # 12. generation report 文件结构完整（核心字段存在）
    required_keys = {
        "backfill_generation_mode",
        "generated_backfill_candidate_cases",
        "raw_backfill_response",
        "generation_errors",
        "accepted_backfill_cases",
        "rejected_backfill_cases",
        "merged_preview_cases",
        "quality_metrics",
        "coverage_before",
        "coverage_after_candidates",
        "coverage_after_merged_preview",
        "newly_covered_flows",
        "still_missing_core_flows",
        "backfill_not_applied",
        "dry_run",
    }
    assert required_keys.issubset(set(result.keys()))


def test_llm_reported_coverage_tags_not_trusted() -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)
    fake_client = FakeLLMClient(
        [
            {
                "case_id": "X-3",
                "description": "普通展示检查",
                "test_module": "普通模块",
                "preconditions": ["已登录"],
                "steps": ["1. 打开页面", "2. 看结果"],
                "test_input": "无",
                "expected_result": "页面文案等于首页",
                "priority": "P1",
                "model_priority": "P1",
                "source_flow_key": "paid_gate",
                "source_flow_name": "付费拦截",
                "backfill_generated": True,
                "coverage_tags": ["paid_gate", "wrong_only_filter"],
            }
        ]
    )

    result = generate_core_flow_backfill_candidates(
        requirement_context="测试覆盖标签不可信",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=fake_client,
        max_candidates=12,
    )
    rejected = result["rejected_backfill_cases"]
    assert len(rejected) == 1
    assert rejected[0]["rejection_reason"] == "source_flow_not_matched_by_mapper"


def test_non_assertable_phrases_are_rejected() -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)
    fake_client = FakeLLMClient(
        [
            {
                **_valid_paid_gate_candidate(),
                "case_id": "X-4",
                "expected_result": "对应内容一致",
            },
            {
                **_valid_wrong_only_filter_candidate(),
                "case_id": "X-5",
                "expected_result": "关键结果可核对",
            },
        ]
    )

    result = generate_core_flow_backfill_candidates(
        requirement_context="测试弱断言短语",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=fake_client,
        max_candidates=12,
    )

    rejected = result["rejected_backfill_cases"]
    assert len(rejected) == 2
    for row in rejected:
        assert row["rejection_reason"] == "non_assertable_expected_result"


def test_missing_priority_final_is_rejected(monkeypatch) -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)

    def _fake_apply(cases: list[dict]) -> list[dict]:
        # 模拟 priority 语义层失效，priority_final 不落地
        return [dict(item) for item in cases]

    monkeypatch.setattr(generation_mod, "_apply_priority_semantics", _fake_apply)

    fake_client = FakeLLMClient([_valid_paid_gate_candidate()])
    result = generate_core_flow_backfill_candidates(
        requirement_context="测试 priority_final 缺失",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=fake_client,
        max_candidates=12,
    )

    rejected = result["rejected_backfill_cases"]
    assert len(rejected) == 1
    assert rejected[0]["rejection_reason"] == "invalid_priority_final"


def test_source_flow_not_matched_by_mapper_is_rejected() -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)
    bad = _valid_paid_gate_candidate()
    bad["description"] = "普通操作流程"
    bad["test_module"] = "普通模块"
    bad["steps"] = ["1. 打开页面", "2. 点击按钮"]
    bad["expected_result"] = "页面文案等于首页"

    result = generate_core_flow_backfill_candidates(
        requirement_context="测试 mapper 命中",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=FakeLLMClient([bad]),
        max_candidates=12,
    )

    rejected = result["rejected_backfill_cases"]
    assert len(rejected) == 1
    assert rejected[0]["rejection_reason"] == "source_flow_not_matched_by_mapper"


def test_coverage_first_preview_keeps_all_required_backfills_and_trims_primary() -> None:
    existing = _existing_cases_8()
    plan = _build_backfill_plan(existing, max_backfill_cases=12)
    payload = _full_12_flow_payload(plan)

    result = generate_core_flow_backfill_candidates(
        requirement_context="覆盖优先预览选择策略验证",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=FakeLLMClient(payload),
        max_candidates=12,
    )

    assert len(result["accepted_backfill_cases"]) == 12
    assert len(result["rejected_backfill_cases"]) == 0
    assert int(result["coverage_after_candidates"]["core_flow_covered_count"]) == 12
    assert int(result["coverage_after_merged_preview"]["core_flow_covered_count"]) == 12
    assert result["still_missing_core_flows"] == []

    assert int(result["accepted_for_preview_count"]) == 12
    assert int(result["primary_retained_count"]) == 6
    assert int(result["primary_trimmed_count"]) == 2
    assert int(result["backfill_retained_count"]) == 12
    assert int(result["backfill_trimmed_count"]) == 0
    assert bool(result["coverage_first_selection_applied"]) is True

    retained_backfill_ids = set(result.get("retained_backfill_case_ids") or [])
    assert "BF-011" in retained_backfill_ids
    assert "BF-012" in retained_backfill_ids

    merged = result.get("merged_preview_cases") or []
    assert len(merged) == 18
    merged_flow_keys = {str(item.get("source_flow_key") or "") for item in merged if isinstance(item, dict)}
    assert "supervisor_report_generation" in merged_flow_keys
    assert "unauthorized_data_isolation" in merged_flow_keys

    trimmed_primary_ids = set(result.get("trimmed_primary_case_ids") or [])
    assert len(trimmed_primary_ids) == 2
    assert trimmed_primary_ids.issubset({f"TC-{idx:03d}" for idx in range(1, 9)})


def test_coverage_first_preview_uses_configured_min_max_window() -> None:
    existing = _existing_cases_8()
    plan = _build_backfill_plan(existing, max_backfill_cases=12)
    payload = _full_12_flow_payload(plan)

    result = generate_core_flow_backfill_candidates(
        requirement_context="覆盖优先预览区间参数验证",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=FakeLLMClient(payload),
        max_candidates=12,
        preview_min_total=10,
        preview_max_total=14,
    )

    merged = result.get("merged_preview_cases") or []
    assert len(merged) == 14
    assert int(result["primary_retained_count"]) == 2
    assert int(result["primary_trimmed_count"]) == 6
    assert int(result["backfill_retained_count"]) == 12
    assert int(result["backfill_trimmed_count"]) == 0
    assert result["quality_metrics"]["merged_preview_target_range"] == {
        "min": 10,
        "max": 14,
        "actual": 14,
    }


def test_literal_eval_fallback_accepts_list_of_dicts_only() -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)
    payload = str([_valid_paid_gate_candidate()])

    result = generate_core_flow_backfill_candidates(
        requirement_context="测试 literal_eval list[dict] 容错",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=FakeLLMClient(payload),
        max_candidates=12,
    )

    assert result["generation_errors"] == []
    assert len(result["generated_backfill_candidate_cases"]) == 1
    assert len(result["accepted_backfill_cases"]) == 1
    assert result["accepted_backfill_cases"][0]["source_flow_key"] == "paid_gate"


def test_literal_eval_fallback_rejects_non_dict_elements() -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)
    mixed_payload = (
        "["
        "{'case_id': 'X-9', 'description': '未付费用户触发付费拦截', 'test_module': '付费拦截', "
        "'preconditions': ['未付费用户已登录'], 'steps': ['1. 点击学习入口'], 'test_input': '未付费账户', "
        "'expected_result': '页面文案等于“开通订阅后继续学习”', 'priority': 'P0', 'model_priority': 'P0', "
        "'source_flow_key': 'paid_gate', 'source_flow_name': '付费拦截', 'backfill_generated': True},"
        "123"
        "]"
    )

    result = generate_core_flow_backfill_candidates(
        requirement_context="测试 literal_eval 非 dict 元素拒绝",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=FakeLLMClient(mixed_payload),
        max_candidates=12,
    )

    assert {"reason": "invalid_json"} in result["generation_errors"]
    assert result["generated_backfill_candidate_cases"] == []
    assert result["accepted_backfill_cases"] == []
    assert result["rejected_backfill_cases"] == []


def test_literal_eval_fallback_still_runs_quality_gate_and_mapper() -> None:
    existing = _existing_cases()
    plan = _build_backfill_plan(existing)
    payload = str(
        [
            {
                **_valid_paid_gate_candidate(),
                "case_id": "X-10",
                "expected_result": "对应内容一致",
            }
        ]
    )

    result = generate_core_flow_backfill_candidates(
        requirement_context="测试 literal_eval 后仍走 quality gate",
        existing_cases=existing,
        backfill_plan=plan,
        llm_client=FakeLLMClient(payload),
        max_candidates=12,
    )

    assert result["generation_errors"] == []
    assert len(result["generated_backfill_candidate_cases"]) == 1
    assert result["accepted_backfill_cases"] == []
    assert len(result["rejected_backfill_cases"]) == 1
    assert result["rejected_backfill_cases"][0]["rejection_reason"] == "non_assertable_expected_result"
