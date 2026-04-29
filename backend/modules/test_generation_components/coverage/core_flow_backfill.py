"""
核心闭环覆盖回填 planner — shadow mode（dry-run 专用）。

本轮不调用大模型、不修改最终结果、不阻断落库。
只根据 missing_core_flows 生成明确的 backfill spec，供后续 LLM 定向生成。

每个 backfill spec 包含：
- flow_key / flow_name
- required_focus：明确的验证焦点描述
- suggested_priority：建议优先级
- must_include_assertions：必须包含的具体断言
- avoid_overlap_with_case_ids：避免与已有用例重合
"""

from __future__ import annotations

from typing import Any

from .core_flow_coverage_contract import (
    CORE_FLOWS,
    _combined_text,
    audit_core_flow_coverage,
    map_case_to_core_flows,
)

BACKFILL_SPECS: dict[str, dict[str, Any]] = {
    "paid_gate": {
        "flow_name": "付费拦截",
        "required_focus": (
            "未付费/未订阅用户点击学习入口时，被统一付费弹窗阻断，不可绕过、不可进入学习页面"
        ),
        "suggested_priority": "P0",
        "must_include_assertions": [
            "弹窗必须出现且不可关闭绕过",
            "弹窗文案包含'开启完整学习体验'或等价付费引导文案",
            "用户未成功进入学习页面",
            "不创建学习任务记录",
            "不产生任何学习数据变更",
        ],
    },
    "textbook_grade_isolation": {
        "flow_name": "教材/年级切换与数据隔离",
        "required_focus": (
            "切换教材版本和年级后，学习数据、进度、统计严格按 (教材+年级) 维度隔离"
        ),
        "suggested_priority": "P0",
        "must_include_assertions": [
            "切换教材后原教材数据不再展示",
            "切换年级后原年级数据不再展示",
            "不同教材/年级组合各自统计独立",
            "切换后不应出现数据串扰（如教材A的题出现在教材B的习题本中）",
        ],
    },
    "weekday_review_to_workbook": {
        "flow_name": "周中批改生成习题本",
        "required_focus": (
            "周中拍照上传作业后，OCR 批改完成，自动生成包含全部题目和批改结果的习题本"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "上传后 OCR 批改完成，习题本入口可见",
            "习题本包含全部批改题目（含正确和错误题目）",
            "每题展示批改结果（正确/错误标记）",
            "错误题目附带正确答案或解析",
        ],
    },
    "workbook_statistics": {
        "flow_name": "习题本统计：总题数/正确数/错误数",
        "required_focus": (
            "习题本统计区域正确展示总题数、正确题数、错误题数，三者逻辑一致"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "总题数 = 正确题数 + 错误题数",
            "正确题数和错误题数与实际批改结果一致",
            "统计数字随批改结果变化实时更新",
            "无题目时统计展示为 0（不可为负数或异常值）",
        ],
    },
    "wrong_only_filter": {
        "flow_name": "只看错题筛选",
        "required_focus": (
            "开启'只看错题'筛选后，仅展示错误题目卡片，正确题目不可见"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "开启筛选后正确题卡不可见",
            "展示的错误题卡数量等于实际错误题数",
            "错误题卡信息完整（题目文本、知识点标签、正确答案/解析）",
            "关闭筛选后全部题目恢复展示",
            "筛选状态切换无数据丢失或闪动",
        ],
    },
    "mastery_calculation_level": {
        "flow_name": "知识点掌握度计算/等级",
        "required_focus": (
            "基于学生答题数据，计算每个知识点的掌握等级（薄弱/一般/熟练），展示在能力模型或掌握度页面"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "每个知识点展示等级（薄弱/一般/熟练）",
            "等级与答题正确率逻辑一致（如正确率<60%→薄弱）",
            "无答题数据时展示兜底状态（非错误/非空白）",
            "掌握度随新答题数据实时刷新",
        ],
    },
    "weekend_classification": {
        "flow_name": "周末提升第一步分类：重点加强/需要注意",
        "required_focus": (
            "周末提升第一步根据薄弱点诊断，将学生分为'重点加强'和'需要注意'两类"
        ),
        "suggested_priority": "P0",
        "must_include_assertions": [
            "正确分类为'重点加强'和'需要注意'两类",
            "分类依据薄弱点数据（非随机）",
            "每类下展示对应的知识点或模块列表",
            "薄弱点数据缺失时展示兜底分类（不抛错、不空白）",
        ],
    },
    "no_weekday_data_fallback": {
        "flow_name": "无周中数据兜底",
        "required_focus": (
            "当学生无周中学习数据记录时，周末提升页面展示友好的兜底提示，不崩溃、不白屏"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "展示友好兜底文案（如'暂无周中数据'或等价提示）",
            "页面不崩溃、不白屏、不死循环",
            "不展示错误数据或脏数据",
            "兜底页面提供可操作入口（如引导去完成周中练习）",
        ],
    },
    "all_correct_history_review": {
        "flow_name": "全部正确历史巩固/通用推荐",
        "required_focus": (
            "当学生全部题目回答正确时，进入历史巩固页面，展示通用推荐内容"
        ),
        "suggested_priority": "P2",
        "must_include_assertions": [
            "全部正确时不进入错题相关流程",
            "展示历史巩固或通用推荐内容",
            "推荐内容非空且与学科/年级相关",
            "无巩固历史时展示兜底推荐（非空白）",
        ],
    },
    "weekend_completion_sync": {
        "flow_name": "周末学习完成状态同步",
        "required_focus": (
            "学生完成所有周末学习任务后，状态标记为完成，学习报告可生成"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "完成全部任务后状态变为'完成'或等价标记",
            "任务全部完成前状态不可为'完成'",
            "状态同步后首页展示完成态（如完成角标/完成文案）",
            "完成后学习报告触发生成（或可手动生成）",
        ],
    },
    "supervisor_report_generation": {
        "flow_name": "督导端学习成长报告生成",
        "required_focus": (
            "学生完成学习后，督导端可查看/生成学习成长报告，包含学习数据和趋势分析"
        ),
        "suggested_priority": "P1",
        "must_include_assertions": [
            "督导端报告入口可见且可点击",
            "报告包含学生近期学习数据（正确率/薄弱项/趋势等）",
            "报告数据与学生实际答题数据一致",
            "无数据时报告展示兜底状态（不空白、不报错）",
            "报告支持分享或导出（如存在此功能）",
        ],
    },
    "unauthorized_data_isolation": {
        "flow_name": "权限/越权/数据隔离",
        "required_focus": (
            "无权限用户无法访问被保护的功能或数据，鉴权拦截不泄露数据"
        ),
        "suggested_priority": "P0",
        "must_include_assertions": [
            "无权限用户访问时返回鉴权失败（403/拦截页面）",
            "不返回被保护数据（不泄露其他学生/班级数据）",
            "鉴权拦截后有清晰提示（非白屏/非报错堆栈）",
            "同校/同班但不同学生的数据互相不可见（数据隔离）",
        ],
    },
}


def plan_core_flow_backfill(
    requirement_context: str,
    existing_cases: list[dict[str, Any]],
    coverage_audit: dict[str, Any] | None = None,
    max_backfill_cases: int = 12,
) -> dict[str, Any]:
    """
    根据 missing_core_flows 生成 backfill plan。

    不修改 existing_cases，不调用 LLM，纯 deterministic。

    Args:
        requirement_context: 原始需求上下文
        existing_cases: 已有用例列表
        coverage_audit: 已有的覆盖率审计结果（若为 None 则自动审计）
        max_backfill_cases: 回填用例上限

    Returns:
        backfill plan dict
    """
    case_items = [c for c in (existing_cases or []) if isinstance(c, dict)]
    primary_count = len(case_items)

    existing_ids = [str(c.get("id") or c.get("case_id") or "") for c in case_items]

    if coverage_audit is None:
        coverage_audit = audit_core_flow_coverage(case_items)

    missing_flows = coverage_audit.get("missing_core_flows") or []
    coverage_before = int(coverage_audit.get("core_flow_covered_count") or 0)
    coverage_ratio_before = float(coverage_audit.get("core_flow_coverage_ratio") or 0.0)

    backfill_plan: list[dict[str, Any]] = []
    for mf in missing_flows:
        flow_id = str(mf.get("flow_id") or "")
        spec = BACKFILL_SPECS.get(flow_id)
        if spec is None:
            continue
        backfill_plan.append({
            "flow_key": flow_id,
            "flow_name": str(spec["flow_name"]),
            "required_focus": str(spec["required_focus"]),
            "suggested_priority": str(spec["suggested_priority"]),
            "must_include_assertions": list(spec["must_include_assertions"]),
            "avoid_overlap_with_case_ids": list(existing_ids),
            "target_case_count": 1,
        })

    planned_count = len(backfill_plan)
    planned_count = min(planned_count, int(max_backfill_cases))

    theoretical_coverage_after = coverage_before + planned_count
    theoretical_ratio_after = round(
        theoretical_coverage_after / max(1, coverage_audit.get("core_flow_required_count", 12)), 4
    )

    still_missing = [
        {"flow_id": item["flow_key"], "flow_name": item["flow_name"]}
        for idx, item in enumerate(backfill_plan)
        if idx < planned_count
    ][:0]
    still_missing = [
        mf for mf in missing_flows
        if mf["flow_id"] not in {bp["flow_key"] for bp in backfill_plan[:planned_count]}
    ]

    return {
        "enabled": True,
        "dry_run": True,
        "primary_case_count": int(primary_count),
        "missing_core_flows_before": [{"flow_id": mf["flow_id"], "label": mf["label"]} for mf in missing_flows],
        "planned_backfill_count": int(planned_count),
        "target_backfill_case_count": int(planned_count),
        "max_backfill_cases": int(max_backfill_cases),
        "backfill_plan": backfill_plan[:planned_count],
        "generated_backfill_candidate_count": 0,
        "accepted_backfill_candidate_count": 0,
        "coverage_before": {
            "covered_count": int(coverage_before),
            "required_count": int(coverage_audit.get("core_flow_required_count", 12)),
            "coverage_ratio": float(coverage_ratio_before),
            "coverage_passed": bool(coverage_audit.get("core_flow_coverage_passed")),
        },
        "coverage_after_theoretical": {
            "covered_count": int(theoretical_coverage_after),
            "required_count": int(coverage_audit.get("core_flow_required_count", 12)),
            "coverage_ratio": float(theoretical_ratio_after),
            "coverage_passed": bool(theoretical_coverage_after >= max(8, coverage_audit.get("core_flow_required_count", 12) * 2 // 3)),
        },
        "still_missing_core_flows": still_missing,
        "backfill_not_applied": True,
    }
