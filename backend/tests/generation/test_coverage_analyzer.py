import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage


def test_rule_level_diagnostics_contains_missing_types() -> None:
    requirement_context = """
【Requirements - 按业务分组】

### biz_key: org_close_rule（当前业务）

* REQ-023: 关闭机构前必须校验余额为0
* REQ-024: 存在未结算订单时禁止关闭
* REQ-025: 关闭失败时返回异常提示
"""
    cases = [
        {
            "id": "TC-001",
            "description": "验证关闭机构主流程",
            "test_module": "机构关闭",
            "preconditions": [],
            "steps": ["余额为0", "提交关闭"],
            "test_input": "机构=A",
            "expected_result": "关闭成功",
            "priority": "P0",
        }
    ]

    result = analyze_coverage(requirement_context, cases)
    assert result["total_rules"] >= 3
    assert "REQ-023" in result["covered_rules"]
    assert "REQ-024" in result["missing_rules"] or "REQ-025" in result["missing_rules"]
    assert isinstance(result["rule_diagnostics"], list)
    assert any(item["rule_id"] == "REQ-025" and "exception" in item["missing_types"] for item in result["rule_diagnostics"])


def test_rule_match_supports_req_id_and_sentence() -> None:
    requirement = "REQ-100 用户名长度边界值20。REQ-101 登录失败超过5次触发异常提示。"
    cases = [
        {
            "id": "TC-100",
            "description": "验证REQ-100 用户名最大长度边界",
            "test_module": "登录",
            "preconditions": [],
            "steps": ["输入长度20用户名"],
            "test_input": "username=20chars",
            "expected_result": "提交成功",
            "priority": "P1",
        }
    ]
    result = analyze_coverage(requirement, cases)
    assert "REQ-100" in result["covered_rules"]
    assert "REQ-101" in result["missing_rules"]
    assert 0 <= result["coverage_rate"] <= 1


def test_requirement_discussion_questions_do_not_become_hard_missing_rules() -> None:
    requirement = """
是否保留打印按钮
按钮颜色要不要改？
表如何展示?
这个这一期不做单独的点击
这是点击“编辑报告”时的异常
b. 功能没有按照功能类型区分，结构需要调整
必须隐藏旧入口
固定显示本周学习时长排行榜
"""
    cases = [
        {
            "id": "TC-001",
            "description": "验证旧入口隐藏",
            "test_module": "首页",
            "preconditions": ["已登录"],
            "steps": ["1. 打开首页"],
            "test_input": "无",
            "expected_result": "旧入口不可见，用户无法点击进入旧页面",
            "priority": "P1",
        }
    ]

    result = analyze_coverage(requirement, cases)
    rule_texts = [str(item.get("rule_text") or "") for item in result["rule_diagnostics"]]

    assert not any("是否保留打印按钮" in text for text in rule_texts)
    assert not any("按钮颜色要不要改" in text for text in rule_texts)
    assert not any("表如何展示" in text for text in rule_texts)
    assert not any("这一期不做" in text for text in rule_texts)
    assert not any("这是点击" in text for text in rule_texts)
    assert not any("功能没有按照功能类型区分" in text for text in rule_texts)
    assert any("必须隐藏旧入口" in text for text in rule_texts)
    assert any("固定显示本周学习时长排行榜" in text for text in rule_texts)


def test_generic_display_headings_are_diagnostic_not_blocking_rules() -> None:
    requirement = """
页面布局与展示
必须隐藏旧入口
"""
    cases = []

    result = analyze_coverage(requirement, cases)
    diagnostics = [item for item in result["rule_diagnostics"] if isinstance(item, dict)]
    generic_rows = [item for item in diagnostics if item.get("rule_text") == "页面布局与展示"]

    assert result["total_extracted_rules"] == 2
    assert result["total_rules"] == 1
    assert result["missing_rules"] == ["RULE-002"]
    assert generic_rows
    assert generic_rows[0].get("blocking") is False
    assert generic_rows[0].get("non_blocking_reason") == "generic_display_heading"


def test_limit_words_count_as_boundary_case_coverage() -> None:
    requirement = "RULE-001: 他人作文列表展示精选的作文，最多展示20条"
    cases = [
        {
            "id": "TC-001",
            "description": "他人作文列表最多展示20条边界验证",
            "test_module": "作文区-列表",
            "preconditions": ["用户已登录"],
            "steps": ["进入他人作文列表", "查看精选作文展示数量"],
            "test_input": "精选作文数量超过20条",
            "expected_result": "列表最多展示20条精选作文，第21条不展示",
            "priority": "P1",
        }
    ]

    result = analyze_coverage(requirement, cases)
    row = next(item for item in result["rule_diagnostics"] if item["rule_id"] == "RULE-001")

    assert "RULE-001" in result["covered_rules"]
    assert "boundary" in row["coverage_types"]
    assert "boundary" not in row["missing_types"]


def test_wrapped_requirement_fragments_are_covered_as_one_rule() -> None:
    requirement = """
iii. 每次展开5
条，若还有
信息被隐藏
则显示按
钮：展开更多
回复
"""
    cases = [
        {
            "id": "TC-001",
            "description": "回复的回复默认展示3条，超过3条显示展开按钮并每次展开5条",
            "test_module": "帖子详情-二级评论展开",
            "preconditions": ["一级评论下有8条二级回复"],
            "steps": ["进入帖子详情", "点击展开N条回复", "继续点击展开更多回复"],
            "test_input": "8条二级回复",
            "expected_result": "点击后展示8条，无剩余隐藏则不再显示展开按钮；若超过8条则显示'展开更多回复'",
            "priority": "P1",
        }
    ]

    result = analyze_coverage(requirement, cases)
    rule_texts = [str(item.get("rule_text") or "") for item in result["rule_diagnostics"]]

    assert result["missing_rules"] == []
    assert not any(text == "信息被隐藏" for text in rule_texts)
    assert any("每次展开5条" in text and "展开更多回复" in text for text in rule_texts)
