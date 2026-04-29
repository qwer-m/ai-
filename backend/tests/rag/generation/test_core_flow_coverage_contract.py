"""
测试 deterministic core_flow_coverage_contract mapper。

验证：
- 只出现"知识点标签"不能命中 mastery_calculation_level
- 只出现"空状态"不能命中 no_weekday_data_fallback
- "只看错题筛选"能正确命中 wrong_only_filter
- "督导端学习成长报告生成"能正确命中 supervisor_report_generation
- coverage audit 能输出 missing_core_flows
"""

from __future__ import annotations

from modules.test_generation_components.coverage.core_flow_coverage_contract import (
    CORE_FLOWS,
    _combined_text,
    map_case_to_core_flows,
    audit_core_flow_coverage,
)


def test_knowledge_point_label_does_not_hit_mastery():
    """只出现"知识点标签"不能命中 mastery_calculation_level"""
    case = {
        "id": "TC-001",
        "description": "验证AI自动识别的知识点标签与题目内容匹配的准确性",
        "test_module": "作业题目列表瀑布流卡片",
        "steps": ["1. 进入题目列表页", "2. 逐一检查每道题的知识点标签"],
        "expected_result": "所有题目知识点标签与题目实际考查的知识点相符",
    }
    hits = map_case_to_core_flows(case)
    assert "mastery_calculation_level" not in hits, (
        f"仅出现'知识点标签'不应命中 mastery_calculation_level, 实际命中: {hits}"
    )


def test_empty_state_does_not_hit_no_weekday_fallback():
    """只出现"空状态"不能命中 no_weekday_data_fallback"""
    case = {
        "id": "TC-007",
        "description": "验证本次作业无题目时，题目列表页的空状态展示",
        "test_module": "作业题目列表瀑布流卡片",
        "steps": ["1. 进入作业题目列表页面"],
        "expected_result": "页面显示友好的空状态占位（如'暂无题目'文案），无任何题目卡片",
    }
    hits = map_case_to_core_flows(case)
    assert "no_weekday_data_fallback" not in hits, (
        f"仅出现'空状态'不应命中 no_weekday_data_fallback, 实际命中: {hits}"
    )


def test_wrong_only_filter_correctly_hits():
    """"只看错题筛选"能正确命中 wrong_only_filter"""
    case = {
        "id": "TC-100",
        "description": "验证只看错题筛选功能",
        "test_module": "习题本模块",
        "steps": ["1. 进入习题本", "2. 点击'只看错题'", "3. 检查题目列表仅展示错题"],
        "expected_result": "仅展示错题，正确题目不显示",
    }
    hits = map_case_to_core_flows(case)
    assert "wrong_only_filter" in hits, (
        f"'只看错题筛选'应命中 wrong_only_filter, 实际命中: {hits}"
    )


def test_supervisor_report_correctly_hits():
    """"督导端学习成长报告生成"能正确命中 supervisor_report_generation"""
    case = {
        "id": "TC-200",
        "description": "学生完成学习后，督导端学习成长报告实时生成",
        "test_module": "督导端-报告生成",
        "steps": ["1. 学生完成周末学习流程", "2. 督导端刷新", "3. 查看学习成长报告"],
        "expected_result": "督导端学习成长报告正确生成，包含学习数据和趋势分析",
    }
    hits = map_case_to_core_flows(case)
    assert "supervisor_report_generation" in hits, (
        f"'督导端学习成长报告生成'应命中 supervisor_report_generation, 实际命中: {hits}"
    )


def test_paid_gate_correctly_hits():
    """付费拦截应正确命中 paid_gate"""
    case = {
        "id": "TC-300",
        "description": "验证未付费用户访问学习功能时被付费拦截",
        "test_module": "付费拦截",
        "steps": ["1. 未付费用户登录", "2. 点击学习入口", "3. 观察付费拦截页面"],
        "expected_result": "弹出付费拦截提示，用户无法进入学习流程",
    }
    hits = map_case_to_core_flows(case)
    assert "paid_gate" in hits, (
        f"'付费拦截'应命中 paid_gate, 实际命中: {hits}"
    )


def test_weekend_classification_correctly_hits():
    """周末提升第一步分类应正确命中 weekend_classification"""
    case = {
        "id": "TC-400",
        "description": "周末提升第一步根据薄弱点将学生分为重点加强和需要注意两类",
        "test_module": "周末提升-第一步分类",
        "steps": ["1. 进入周末提升", "2. 查看第一步分类结果"],
        "expected_result": "正确展示重点加强和需要注意两类学生，分类基于薄弱点数据",
    }
    hits = map_case_to_core_flows(case)
    assert "weekend_classification" in hits, (
        f"'周末提升第一步分类'应命中 weekend_classification, 实际命中: {hits}"
    )


def test_weekday_review_to_workbook_hits():
    """周中批改生成习题本应正确命中"""
    case = {
        "id": "TC-500",
        "description": "周中拍照批改后自动生成习题本，包含全部批改题目",
        "test_module": "周中批改-习题本",
        "steps": ["1. 上传作业照片", "2. 等待OCR批改完成", "3. 查看生成的习题本"],
        "expected_result": "习题本包含全部批改题目和批改结果",
    }
    hits = map_case_to_core_flows(case)
    assert "weekday_review_to_workbook" in hits, (
        f"'周中批改生成习题本'应命中 weekday_review_to_workbook, 实际命中: {hits}"
    )


def test_textbook_grade_isolation_hits():
    """教材/年级切换与数据隔离应正确命中"""
    case = {
        "id": "TC-600",
        "description": "切换教材和年级版本后，学生数据按教材年级隔离",
        "test_module": "教材年级管理",
        "steps": ["1. 更换教材年级版本", "2. 查看学生数据"],
        "expected_result": "不同教材版本和年级数据互相隔离，不影响各自统计",
    }
    hits = map_case_to_core_flows(case)
    assert "textbook_grade_isolation" in hits, (
        f"'教材/年级切换与数据隔离'应命中 textbook_grade_isolation, 实际命中: {hits}"
    )


def test_workbook_statistics_hits():
    """习题本统计应正确命中"""
    case = {
        "id": "TC-700",
        "description": "验证习题本统计中总题数、正确数和错误数的准确性",
        "test_module": "习题本-统计",
        "steps": ["1. 进入习题本统计页", "2. 查看总题数、正确数、错误数"],
        "expected_result": "总题数=正确数+错误数，统计准确",
    }
    hits = map_case_to_core_flows(case)
    assert "workbook_statistics" in hits, (
        f"'习题本统计'应命中 workbook_statistics, 实际命中: {hits}"
    )


def test_all_correct_history_hits():
    """全部正确历史巩固应正确命中"""
    case = {
        "id": "TC-800",
        "description": "全部正确时进入历史巩固页面，展示通用推荐",
        "test_module": "全部正确历史巩固",
        "steps": ["1. 全部正确完成习题", "2. 查看巩固推荐"],
        "expected_result": "展示通用推荐和巩固内容",
    }
    hits = map_case_to_core_flows(case)
    assert "all_correct_history_review" in hits, (
        f"'全部正确历史巩固'应命中 all_correct_history_review, 实际命中: {hits}"
    )


def test_weekend_completion_sync_hits():
    """周末学习完成状态同步应正确命中"""
    case = {
        "id": "TC-900",
        "description": "周末学习全部完成后，状态同步为完成，生成学习报告",
        "test_module": "周末学习-状态流转",
        "steps": ["1. 完成所有周末任务", "2. 查看学习状态", "3. 查看学习报告"],
        "expected_result": "状态显示为完成，学习报告正确生成",
    }
    hits = map_case_to_core_flows(case)
    assert "weekend_completion_sync" in hits, (
        f"'周末学习完成状态同步'应命中 weekend_completion_sync, 实际命中: {hits}"
    )


def test_unauthorized_isolation_hits():
    """权限/越权/数据隔离应正确命中"""
    case = {
        "id": "TC-1000",
        "description": "验证无权访问的用户访问时被鉴权拦截且数据不泄露",
        "test_module": "权限模块",
        "steps": ["1. 使用无权限账号登录", "2. 尝试访问他人数据"],
        "expected_result": "鉴权失败，拒绝访问，数据隔离有效",
    }
    hits = map_case_to_core_flows(case)
    assert "unauthorized_data_isolation" in hits, (
        f"'权限/越权/数据隔离'应命中 unauthorized_data_isolation, 实际命中: {hits}"
    )


def test_no_weekday_data_fallback_hits():
    """无周中数据兜底应正确命中"""
    case = {
        "id": "TC-1100",
        "description": "无周中数据时展示兜底页面",
        "test_module": "周末提升-数据兜底",
        "steps": ["1. 进入周末提升页面", "2. 查看无周中数据的兜底展示"],
        "expected_result": "展示友好兜底提示，不展示空错误",
    }
    hits = map_case_to_core_flows(case)
    assert "no_weekday_data_fallback" in hits, (
        f"'无周中数据兜底'应命中 no_weekday_data_fallback, 实际命中: {hits}"
    )


def test_audit_outputs_missing_core_flows():
    """coverage audit 能输出 missing_core_flows"""
    cases = [
        {
            "id": "TC-A01",
            "description": "验证瀑布流卡片展示",
            "test_module": "作业题目列表",
            "steps": ["1. 进入页面", "2. 查看卡片"],
            "expected_result": "卡片展示正常",
        },
    ]
    result = audit_core_flow_coverage(cases)
    assert "missing_core_flows" in result
    assert len(result["missing_core_flows"]) >= 11
    assert result["core_flow_covered_count"] <= 1
    assert result["core_flow_coverage_passed"] is False
    assert len(result["false_positive_guard_notes"]) >= 1


def test_all_12_flows_defined():
    """确保 12 个核心闭环全部定义"""
    assert len(CORE_FLOWS) == 12, f"应定义 12 个核心闭环, 实际: {len(CORE_FLOWS)}"
    flow_ids = {f["flow_id"] for f in CORE_FLOWS}
    expected_ids = {
        "paid_gate",
        "textbook_grade_isolation",
        "weekday_review_to_workbook",
        "workbook_statistics",
        "wrong_only_filter",
        "mastery_calculation_level",
        "weekend_classification",
        "no_weekday_data_fallback",
        "all_correct_history_review",
        "weekend_completion_sync",
        "supervisor_report_generation",
        "unauthorized_data_isolation",
    }
    assert flow_ids == expected_ids, f"flow_ids 不匹配, 差值: {flow_ids ^ expected_ids}"


def test_combined_text_includes_all_fields():
    """_combined_text 应包含所有字段"""
    case = {
        "description": "A",
        "test_module": "B",
        "steps": ["C1", "C2"],
        "expected_result": "D",
    }
    text = _combined_text(case)
    assert "A" in text
    assert "B" in text
    assert "C1" in text
    assert "C2" in text
    assert "D" in text


def test_empty_case_returns_empty_hits():
    """空用例或非 dict 用例返回空"""
    assert map_case_to_core_flows({}) == {}
    assert map_case_to_core_flows(None) == {}
    assert map_case_to_core_flows("not a dict") == {}
