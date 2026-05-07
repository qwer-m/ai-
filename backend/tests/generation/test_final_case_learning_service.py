import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.services.final_case_learning_service import (
    build_learning_samples_from_final_cases,
    parse_test_cases_payload,
)
from routers.automation.test_generation_history_routes import FinalCaseLearningRequest


def test_final_manual_business_extension_is_positive_not_negative() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证二轮复习模块在首页展示",
            "test_module": "高中首页",
            "steps": ["打开首页", "查看二轮复习模块"],
            "expected_result": "首页展示二轮复习模块，位置在查漏补缺与真题套卷之间",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证未开卡用户在小程序和书房端均不可进入二轮复习课程",
            "test_module": "跨端权限",
            "steps": ["使用未开卡账号分别进入小程序和书房端", "点击二轮复习课程"],
            "expected_result": "两个端均拦截进入并展示开卡/权限提示，后台无学习进度写入",
            "priority": "P1",
        }
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="高中首页新增二轮复习模块，位于查漏补缺与真题套卷之间",
        generation_id=123,
    )

    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["diagnostics"]["manual_business_extension_count"] == 1
    assert result["diagnostics"]["negative_sample_count"] == 0
    sample = result["positive_samples"][0]
    assert sample["signal_type"] == "positive"
    assert sample["pattern_usage"] == "prefer"
    assert sample["manual_business_extension"] is True
    assert "cross_system" in sample["pattern_category"] or "permission" in sample["pattern_category"]


def test_ai_only_is_not_negative_without_clear_quality_failure() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证首页模块顺序",
            "test_module": "高中首页",
            "steps": ["打开首页", "记录模块顺序"],
            "expected_result": "模块顺序为标准化自习、查漏补缺、二轮复习、真题套卷、名师精品课",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证TA端课程管理中二轮复习课程状态同步",
            "test_module": "TA端课程管理",
            "steps": ["学生完成课程节点", "TA端查看课程状态"],
            "expected_result": "TA端展示的课程完成状态与学生端一致",
            "priority": "P1",
        }
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="首页新增二轮复习模块",
    )

    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["diagnostics"]["negative_sample_count"] == 0


def test_ai_only_with_non_assertable_expected_result_becomes_negative() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证按钮正常展示",
            "test_module": "页面展示",
            "steps": ["打开页面", "查看按钮"],
            "expected_result": "正常展示",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证退费后课程权限和订单状态同步回滚",
            "test_module": "交易与权限",
            "steps": ["购买课程", "发起退费", "查看课程权限和订单状态"],
            "expected_result": "退费成功后课程权限收回，订单状态为已退费，学习入口不可进入",
            "priority": "P0",
        }
    ]

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="首页新增二轮复习模块",
    )

    assert result["diagnostics"]["positive_sample_count"] == 1
    assert result["diagnostics"]["negative_sample_count"] == 1
    negative = result["negative_samples"][0]
    assert negative["signal_type"] == "negative"
    assert negative["pattern_usage"] == "avoid"
    assert negative["pattern_category"] == "non_assertable_expected_result"


def test_final_case_learning_samples_attach_quality_ledger_scope_and_confidence() -> None:
    generated_cases = [
        {
            "id": "TC-AI-001",
            "description": "验证按钮正常展示",
            "test_module": "页面展示",
            "steps": ["打开页面", "查看按钮"],
            "expected_result": "正常展示",
            "priority": "P1",
        }
    ]
    final_cases = [
        {
            "id": "TC-H-001",
            "description": "验证跨端学习状态同步",
            "test_module": "跨端状态",
            "steps": ["学生端完成课程", "管理端查看状态"],
            "expected_result": "管理端展示的完成状态与学生端一致",
            "priority": "P1",
        }
    ]
    quality_ledger = {
        "generation_id": 460,
        "quality_assessment": "high",
        "final_count": 83,
        "coverage": {
            "coverage_rate": 0.98,
            "missing_rules_count": 1,
            "non_blocking_rules_count": 7,
        },
        "review": {"candidate_total": 100, "retained_total": 83},
        "judge": {"rejected_out_count": 0, "pending_out_count": 0},
        "context": {"snapshot_used": True, "fusion_mode": "snapshot+rag"},
    }

    result = build_learning_samples_from_final_cases(
        generated_cases=generated_cases,
        final_cases=final_cases,
        requirement_text="学习状态需要跨端一致",
        generation_id=460,
        quality_ledger=quality_ledger,
    )

    assert result["diagnostics"]["quality_ledger_attached"] is True
    positive = result["positive_samples"][0]
    negative = result["negative_samples"][0]
    assert positive["pattern_scope"] == "project"
    assert positive["quality_ledger"]["generation_id"] == 460
    assert positive["quality_ledger"]["coverage_rate"] == 0.98
    assert positive["pattern_confidence"] > negative["pattern_confidence"]


def test_final_case_learning_positive_samples_use_pattern_grain() -> None:
    final_title = "Verify switching back to course A keeps progress after operating course B"
    result = build_learning_samples_from_final_cases(
        generated_cases=[],
        final_cases=[
            {
                "id": "TC-H-001",
                "description": final_title,
                "test_module": "course progress consistency",
                "steps": ["complete course A", "switch to course B", "switch back to course A"],
                "expected_result": "course A progress remains unchanged after switching back",
                "priority": "P1",
            }
        ],
        requirement_text="course learning progress must be retained",
        generation_id=461,
    )

    positive = result["positive_samples"][0]
    assert positive["pattern_grain"] == "pattern"
    assert positive["source_case_title"] == final_title
    assert positive["pattern_scope"] == "project"
    assert positive["pattern_summary"] != final_title
    assert "state transition" in positive["pattern_summary"]


def test_parse_csv_final_cases_with_chinese_headers() -> None:
    csv_text = (
        "编号,用例标题,模块,测试步骤,预期结果,优先级\n"
        "TC-001,验证OPS端课程上下架影响书房端入口,跨端发布,"
        "OPS下架课程后书房端刷新,书房端不再展示该课程入口,P1\n"
    )

    cases = parse_test_cases_payload(csv_text)

    assert len(cases) == 1
    assert cases[0]["id"] == "TC-001"
    assert cases[0]["description"] == "验证OPS端课程上下架影响书房端入口"
    assert cases[0]["priority"] == "P1"


def test_final_case_learning_request_defaults_to_dry_run() -> None:
    req = FinalCaseLearningRequest()

    assert req.dry_run is True
