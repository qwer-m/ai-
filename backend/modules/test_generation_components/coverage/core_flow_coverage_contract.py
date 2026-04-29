"""
核心闭环覆盖契约 — deterministic mapper。

基于 description + test_module + steps + expected_result 综合判断，
使用必要关键词组合，避免单关键词误匹配。

每个 core_flow 都有独立的命中规则，不可用泛化关键词替代。
"""

from __future__ import annotations

from typing import Any

CORE_FLOWS: list[dict[str, Any]] = [
    {
        "flow_id": "paid_gate",
        "label": "付费拦截",
        "require_all": ["付费", "拦截"],
        "require_any": ["支付", "订阅", "paywall", "购买", "未付费", "未订阅"],
        "forbid_any": [],
    },
    {
        "flow_id": "textbook_grade_isolation",
        "label": "教材/年级切换与数据隔离",
        "require_all": ["教材", "年级"],
        "require_any": ["切换", "更换", "数据隔离", "换教材", "版本"],
        "forbid_any": [],
    },
    {
        "flow_id": "weekday_review_to_workbook",
        "label": "周中批改生成习题本",
        "require_all": ["周中", "批改"],
        "require_any": ["习题本", "拍照批改", "OCR", "上传", "批改结果", "错题本"],
        "forbid_any": [],
    },
    {
        "flow_id": "workbook_statistics",
        "label": "习题本统计：总题数/正确数/错误数",
        "require_all": ["习题本"],
        "require_any": ["总题数", "正确数", "错误数", "统计", "正确率", "题数", "正确题目", "错题数"],
        "forbid_any": [],
    },
    {
        "flow_id": "wrong_only_filter",
        "label": "只看错题筛选",
        "require_all": ["只看错题"],
        "require_any": ["错题筛选", "错题过滤", "错题列表", "错误题目列表"],
        "forbid_any": [],
    },
    {
        "flow_id": "mastery_calculation_level",
        "label": "知识点掌握度计算/等级",
        "require_all": ["掌握"],
        "require_any": ["掌握度", "掌握等级", "薄弱", "一般", "熟练", "能力模型"],
        "forbid_any": [],
    },
    {
        "flow_id": "weekend_classification",
        "label": "周末提升第一步分类：重点加强/需要注意",
        "require_all": ["周末"],
        "require_any": ["重点加强", "需要注意", "第一步", "分类", "薄弱点"],
        "forbid_any": [],
    },
    {
        "flow_id": "no_weekday_data_fallback",
        "label": "无周中数据兜底",
        "require_all": ["周中"],
        "require_any": ["无周中", "兜底", "无数据", "缺省", "无习题", "无学习记录"],
        "forbid_any": [],
    },
    {
        "flow_id": "all_correct_history_review",
        "label": "全部正确历史巩固/通用推荐",
        "require_all": ["全部正确"],
        "require_any": ["历史巩固", "通用推荐", "巩固", "推荐", "无错题"],
        "forbid_any": [],
    },
    {
        "flow_id": "weekend_completion_sync",
        "label": "周末学习完成状态同步",
        "require_all": ["周末"],
        "require_any": ["完成", "状态同步", "学习完成", "学习报告", "周末完成", "状态流转", "学习完成啦"],
        "forbid_any": [],
    },
    {
        "flow_id": "supervisor_report_generation",
        "label": "督导端学习成长报告生成",
        "require_all": ["督导端"],
        "require_any": ["学习成长报告", "成长报告", "报告生成", "报告分享", "报告数据", "报告趋势"],
        "forbid_any": [],
    },
    {
        "flow_id": "unauthorized_data_isolation",
        "label": "权限/越权/数据隔离",
        "require_all": [],
        "require_any": ["权限", "越权", "无权限", "未授权", "访问控制", "鉴权", "无权"],
        "forbid_any": [],
    },
]


def _combined_text(case: dict[str, Any]) -> str:
    """合并用例全部文本供匹配"""
    parts: list[str] = [
        str(case.get("description") or ""),
        str(case.get("test_module") or ""),
    ]
    steps = case.get("steps")
    if isinstance(steps, list):
        parts.append(" ".join(str(s) for s in steps))
    elif isinstance(steps, str):
        parts.append(steps)
    parts.append(str(case.get("expected_result") or ""))
    return " ".join(parts)


def map_case_to_core_flows(case: dict[str, Any]) -> dict[str, str]:
    """
    对单条用例做 deterministic 核心闭环匹配。

    Returns:
        {flow_id: evidence} — 命中的闭环及其证据
    """
    if not isinstance(case, dict):
        return {}

    text = _combined_text(case)
    hits: dict[str, str] = {}
    for flow in CORE_FLOWS:
        flow_id = str(flow["flow_id"])
        require_all: list[str] = flow.get("require_all") or []
        require_any: list[str] = flow.get("require_any") or []
        forbid_any: list[str] = flow.get("forbid_any") or []

        # 必须全部命中 require_all
        if require_all:
            if not all(kw in text for kw in require_all):
                continue
        # 至少命中一个 require_any
        if require_any:
            if not any(kw in text for kw in require_any):
                continue
        # 不能出现 forbid_any 中的任何词
        if forbid_any:
            if any(kw in text for kw in forbid_any):
                continue

        matched = [kw for kw in (*require_all, *require_any) if kw in text]
        hits[flow_id] = "matched: " + ", ".join(matched[:6])

    return hits


def audit_core_flow_coverage(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    对最终用例列表执行核心闭环覆盖审计。

    Returns:
        {
            "core_flow_covered_count": int,
            "core_flow_required_count": int,
            "core_flow_coverage_ratio": float,
            "core_flow_coverage_passed": bool,
            "missing_core_flows": [...],
            "coverage_detail": {flow_id: {covered, case_ids, evidence}},
            "false_positive_guard_notes": [...],
        }
    """
    case_items = [c for c in (cases or []) if isinstance(c, dict)]
    case_total = len(case_items)

    coverage_detail: dict[str, dict[str, Any]] = {}
    for flow in CORE_FLOWS:
        coverage_detail[flow["flow_id"]] = {
            "label": flow["label"],
            "covered": False,
            "matched_case_ids": [],
            "evidence": [],
        }

    for idx, case in enumerate(case_items):
        case_id = str(case.get("id") or case.get("case_id") or f"ROW-{idx + 1:03d}")
        hits = map_case_to_core_flows(case)
        for flow_id, evidence in hits.items():
            fd = coverage_detail.get(flow_id)
            if fd is None:
                continue
            fd["covered"] = True
            fd["matched_case_ids"].append(case_id)
            fd["evidence"].append(
                {
                    "case_id": case_id,
                    "description": str(case.get("description") or "")[:80],
                    "evidence": evidence,
                }
            )

    covered_count = sum(1 for fd in coverage_detail.values() if fd["covered"])
    required_count = len(CORE_FLOWS)

    missing_flows: list[dict[str, str]] = [
        {"flow_id": fid, "label": fd["label"]}
        for fid, fd in coverage_detail.items()
        if not fd["covered"]
    ]

    # 生成误报防护注释
    false_positive_guard_notes: list[str] = []
    if case_total < required_count:
        false_positive_guard_notes.append(
            f"用例总数({case_total}) < 核心闭环数({required_count})，"
            f"理论最大覆盖={case_total}/{required_count}"
        )
    single_module_cases = len({str(c.get("test_module") or "") for c in case_items})
    if single_module_cases <= 1 and case_total > 0:
        false_positive_guard_notes.append(
            f"所有{case_total}条用例集中在仅{single_module_cases}个模块，覆盖广度严重不足"
        )

    return {
        "core_flow_covered_count": int(covered_count),
        "core_flow_required_count": int(required_count),
        "core_flow_coverage_ratio": round(covered_count / required_count, 4) if required_count else 0.0,
        "core_flow_coverage_passed": bool(covered_count >= max(8, required_count * 2 // 3)),
        "missing_core_flows": missing_flows,
        "coverage_detail": coverage_detail,
        "false_positive_guard_notes": false_positive_guard_notes,
    }
