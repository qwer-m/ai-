""""Priority/P0 anchor configuration loader.

Reads optional postprocess_priority_config_data.json and exposes keyword lists,
scoring deltas, and anchor rules. If the file is absent, callers receive empty
configuration so generation can proceed without a bundled domain template.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).with_name("postprocess_priority_config_data.json")


def _load_payload() -> dict[str, object]:
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise ValueError("priority config data must be a JSON object")
    return payload


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values if str(v or "").strip())


def _string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if str(v or "").strip()]


def _keyword_pair_tuple(values: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(values, list):
        return ()
    result: list[tuple[str, tuple[str, ...]]] = []
    for item in values:
        if not isinstance(item, list) or len(item) < 2:
            continue
        key = str(item[0] or "").strip()
        if not key:
            continue
        keywords = _string_tuple(item[1])
        if keywords:
            result.append((key, keywords))
    return tuple(result)


def _int_dict(values: object) -> dict[str, int]:
    if not isinstance(values, dict):
        return {}
    return {str(k): int(v) for k, v in values.items()}


_PAYLOAD = _load_payload()

_DEFAULT_REASONING_LEAKAGE_SIGNALS = (
    "假设",
    "为简单设",
    "或类似",
    "这里不成立",
    "实际应该",
    "实际触发条件",
    "针对",
    "除非是",
    "不应有",
    "不应该",
    "模型",
    "推理",
    "reasoning",
    "assume",
    "assuming",
    "probably",
    "maybe",
    "需求未明确",
    "假设",
    "可能",
    "实际应",
    "实际应该",
    "此处",
    "此处假设",
    "暂按",
    "暂时按",
    "需考虑",
    "need product confirm",
    "product confirm",
    "requirement unclear",
)

_DEFAULT_INVALID_CASE_QUALITY_MARKERS = (
    "invalid",
    "invalid_case",
    "reject",
    "rejected",
)

_DEFAULT_QUALITY_CHECK_FIELDS = (
    "case_quality",
    "quality",
)

_DEFAULT_P1_KEYWORDS = (
    "paywall",
    "payment gate",
    "learning entry",
    "ai scoring",
    "wrong question",
    "error collection",
    "workflow",
    "state",
    "sync",
    "submit",
    "publish",
    "approval",
    "member",
    "permission",
    "upload",
    "generate",
    "result",
)

_DEFAULT_STRONG_P0_PAYMENT_GATE_TOKENS = (
    "paywall",
    "payment gate",
    "unpaid",
    "subscribe",
    "learning entry",
    "blocks access",
    "cannot continue",
    "付费",
    "支付",
    "拦截",
    "会员",
    "锁定",
)

_DEFAULT_STRONG_P0_AI_SCORING_TOKENS = (
    "ai scoring",
    "auto score",
    "automatic scoring",
    "ocr upload",
    "answer sheet",
    "智能判分",
    "自动判分",
    "评分",
)

_DEFAULT_STRONG_P0_WRONG_COLLECTION_TOKENS = (
    "wrong question",
    "wrong question collection",
    "error collection",
    "wrong answers",
    "错题",
    "错题归集",
    "错题本",
)

_DEFAULT_STRONG_P0_SUBMIT_REPORT_TOKENS = (
    "submit success",
    "publish",
    "approval",
    "approved",
    "upload",
    "generate result",
    "generated result",
    "上传",
    "生成",
    "生成批改结果",
    "提交成功",
    "投稿",
    "审核通过",
)

_DEFAULT_P0_CRITICAL_FAMILIES = (
    ("generation_result", ("upload", "generate", "result")),
    ("generation_result", ("generate", "correction result")),
    ("generation_result", ("generated", "correction result")),
    ("result_display", ("four modules",)),
    ("result_display", ("feedback modules",)),
    ("result_display", ("result details",)),
    ("submission", ("submit", "success")),
    ("submission", ("submit", "pending review")),
    ("approval", ("review approved",)),
    ("approval", ("approval passed",)),
    ("approval", ("approved work",)),
    ("permission", ("first lesson",)),
    ("permission", ("all courses", "member")),
    ("permission", ("locked", "paywall")),
    ("permission", ("member", "course access")),
    ("community_detail", ("community detail",)),
    ("community_detail", ("work detail", "approved")),
    ("generation_result", ("\u4e0a\u4f20", "\u751f\u6210", "\u7ed3\u679c")),
    ("generation_result", ("\u751f\u6210", "\u6279\u6539\u7ed3\u679c")),
    ("result_display", ("\u56db\u4e2a\u6a21\u5757",)),
    ("result_display", ("\u56db\u90e8\u5206",)),
    ("submission", ("\u63d0\u4ea4\u6210\u529f",)),
    ("submission", ("\u8fdb\u5165\u5ba1\u6838\u4e2d",)),
    ("approval", ("\u5ba1\u6838\u901a\u8fc7",)),
    ("permission", ("\u7b2c\u4e00\u8bfe",)),
    ("permission", ("\u5168\u90e8\u8bfe\u7a0b", "\u4f1a\u5458")),
)

_DEFAULT_P0_CORE_TOKENS = (
    "upload",
    "submit",
    "submit success",
    "publish",
    "approval",
    "approved",
    "review approved",
    "approval passed",
    "permission",
    "member",
    "vip",
    "locked",
    "paywall",
    "first lesson",
    "all courses",
    "generate result",
    "generated result",
    "generate correction",
    "correction result",
    "four modules",
    "feedback modules",
    "result details",
    "community detail",
    "\u4e0a\u4f20",
    "\u63d0\u4ea4",
    "\u63d0\u4ea4\u6210\u529f",
    "\u6295\u7a3f",
    "\u53d1\u5e03",
    "\u5ba1\u6838",
    "\u5ba1\u6838\u901a\u8fc7",
    "\u6743\u9650",
    "\u4f1a\u5458",
    "\u9501\u5b9a",
    "\u7b2c\u4e00\u8bfe",
    "\u5168\u90e8\u8bfe\u7a0b",
    "\u751f\u6210\u7ed3\u679c",
    "\u751f\u6210\u6279\u6539\u7ed3\u679c",
    "\u6279\u6539\u7ed3\u679c",
    "\u56db\u4e2a\u6a21\u5757",
    "\u56db\u90e8\u5206",
)

_DEFAULT_P0_LOW_VALUE_TOKENS = (
    "copy",
    "toast",
    "tooltip",
    "record limit",
    "records limit",
    "maximum records",
    "max records",
    "drag sort",
    "drag sorted",
    "delete thumbnail",
    "deleted",
    "delete image",
    "remove image",
    "force close",
    "kill app",
    "48h",
    "48 hours",
    "remains pending",
    "pending status remains",
    "pending status",
    "star rating",
    "stars",
    "button disabled",
    "disabled button",
    "0 images",
    "editable title",
    "title body",
    "\u590d\u5236",
    "\u63d0\u793a",
    "\u6700\u592720\u6761",
    "\u4e0a\u9650",
    "\u62d6\u52a8\u6392\u5e8f",
    "\u5220\u9664\u56fe\u7247",
    "\u5220\u9664\u7f29\u7565\u56fe",
    "\u5f3a\u5236\u9000\u51fa",
    "48\u5c0f\u65f6",
    "\u72b6\u6001\u4fdd\u6301",
    "\u661f\u661f\u8bc4\u5206",
    "\u8bc4\u5206\u5c55\u793a",
    "\u6309\u94ae\u4e0d\u53ef\u70b9",
    "\u7f6e\u7070",
    "0\u5f20",
    "\u6807\u9898\u6b63\u6587",
    "\u53ef\u7f16\u8f91",
)

_DEFAULT_P0_ESSAY_DOMAIN_PRIMARY_TOKENS = (
    "essay",
    "composition",
    "writing",
    "correction",
    "correction result",
    "\u4f5c\u6587",
    "\u6279\u6539",
    "\u6295\u7a3f",
)

_DEFAULT_P0_ESSAY_DOMAIN_POSITIVE_TOKENS = (
    "essay",
    "composition",
    "writing",
    "correction",
    "\u4f5c\u6587",
    "\u6279\u6539",
    "\u6295\u7a3f",
)

_DEFAULT_P0_ESSAY_DOMAIN_NEGATIVE_TOKENS = (
    "schedule",
    "course schedule",
    "recent course",
    "lesson plan",
    "\u6392\u8bfe",
    "\u8bfe\u7a0b\u65f6\u95f4",
    "\u8fd1\u671f\u8bfe\u7a0b",
    "\u5b66\u4e60\u8ba1\u5212",
)

_DEFAULT_P0_ESSAY_EXCLUSION_TOKENS = (
    "essay",
    "composition",
    "essay submission",
    "\u4f5c\u6587",
    "\u6279\u6539",
    "\u6295\u7a3f",
    "\u4f5c\u54c1",
)

_DEFAULT_UNCERTAIN_REQUIREMENT_SIGNALS = (
    "需教研确认",
    "需要讨论",
    "本期可以不做",
    "本期可以不要",
    "暂不支持",
    "模型不支持",
    "小学没定位模型",
    "由教研提供",
    "待确认",
    "待讨论",
    "to be confirmed",
    "need discussion",
    "optional this phase",
    "model not supported",
)

_DEFAULT_P0_KEYWORDS = (
    "paywall",
    "payment gate",
    "payment blocked",
    "permission denied",
    "unauthorized",
    "access denied",
    "data isolation",
    "隔离",
    "越权",
    "未授权",
    "权限",
    "付费",
    "阻断",
    "主流程",
    "闭环",
    "报告生成失败",
)

_DEFAULT_P2_KEYWORDS = (
    "流畅",
    "卡顿",
    "性能",
    "兼容",
    "文案",
    "提示",
    "展示",
    "缩放",
    "滑动",
    "ui",
    "display",
    "layout",
    "performance",
)

_DEFAULT_STRONG_P0_WEEK_BOUNDARY_TOKENS = (
    "周次切换",
    "教学周",
    "周日24",
    "时间边界",
    "补做期",
    "历史周",
    "补做规则",
    "week switch",
    "history week",
)

_DEFAULT_UI_KEYWORDS = (
    "入口",
    "图标",
    "按钮",
    "展示",
    "布局",
    "占位",
    "置灰",
    "文案",
    "显示",
    "隐藏",
    "可点击",
    "样式",
    "icon",
    "button",
    "display",
    "layout",
    "placeholder",
    "style",
)

_DEFAULT_UI_RISK_WORDS = (
    "异常",
    "失败",
    "错误",
    "权限",
    "安全",
    "并发",
    "性能",
    "exception",
    "error",
    "security",
    "permission",
)

_DEFAULT_MAIN_WORKFLOW_TOKENS = (
    "登录",
    "下单",
    "支付",
    "提交",
    "保存",
    "发布",
    "审批",
    "核心查询",
    "核心流程",
    "checkout",
    "order",
    "payment",
    "submit",
    "save",
    "publish",
    "approve",
    "login",
)

_DEFAULT_CROSS_PAGE_FLOW_TOKENS = (
    "cross-page",
    "cross page",
    "cross module",
    "page jump",
    "navigation chain",
    "跨页",
    "跨页面",
    "页面跳转",
    "跨模块",
    "跳转",
)

_DEFAULT_STATE_TRANSITION_TOKENS = (
    "state transition",
    "state-transition",
    "state flow",
    "status change",
    "状态流转",
    "状态迁移",
    "状态变更",
    "中断",
    "恢复",
    "重进",
)

_DEFAULT_PREFERRED_PATTERN_TEXT_TOKENS = (
    "preferred pattern",
    "core flow closure",
    "cross-page flow",
    "multi-step interaction",
    "key-path coverage",
    "核心流程闭环",
    "跨页面流程",
    "多步骤交互",
    "状态流转",
    "关键路径覆盖",
)

_DEFAULT_PREFERRED_PATTERN_CATEGORIES = {
    "core_flow_closure",
    "cross_page_flow",
    "multi_step_interaction",
    "state_transition",
    "key_path_coverage",
    "complex_business_combination",
    "high_value_assertion",
    "boundary_effective_coverage",
    "核心流程闭环",
    "跨页面流程",
    "多步骤交互",
    "状态流转",
    "关键路径覆盖",
    "复杂业务组合",
    "高价值断言",
    "边界有效覆盖",
}

_DEFAULT_REUSE_RISK_TOKENS = (
    "复用",
    "沿用",
    "原模块",
    "原页面",
    "旧按钮",
    "旧文案",
    "旧跳转",
    "残留",
    "返回首页",
    "返回列表",
    "回首页",
    "回列表",
    "返回目标",
    "不串课文",
    "不串单元",
    "串原模块",
    "reuse",
    "reused",
    "legacy behavior",
    "legacy button",
    "wrong return target",
    "return home",
    "return list",
    "shared page",
    "shared flow",
    "context leak",
)

_DEFAULT_BLOCKING_TOKENS = (
    "阻断",
    "无法继续",
    "不可继续",
    "无法提交",
    "无法保存",
    "流程中断",
    "系统不可用",
    "阻塞",
    "blocked",
    "blocker",
    "cannot continue",
    "service unavailable",
)

_DEFAULT_SEVERE_DATA_RISK_TOKENS = (
    "数据丢失",
    "数据错误",
    "状态污染",
    "脏数据",
    "金额错误",
    "重复扣款",
    "错账",
    "账务错误",
    "data loss",
    "data corruption",
    "amount mismatch",
    "state corruption",
)

_DEFAULT_SEVERE_SECURITY_RISK_TOKENS = (
    "越权",
    "权限绕过",
    "提权",
    "敏感数据泄露",
    "认证绕过",
    "鉴权绕过",
    "sql injection",
    "xss",
    "csrf",
    "auth bypass",
    "privilege escalation",
    "security breach",
)

_DEFAULT_BEHAVIOR_DEPTH_TOKENS = (
    "状态",
    "恢复",
    "重试",
    "回滚",
    "幂等",
    "一致",
    "不丢上下文",
    "不串课文",
    "不错跳",
    "上下文保持",
    "断言",
    "assert",
    "state transition",
    "context",
    "consistent",
    "resume",
    "rollback",
    "idempotent",
)

_DEFAULT_STATE_GUARD_TOKENS = (
    "不串课文",
    "不串单元",
    "不丢上下文",
    "不错误推进",
    "不标记完成",
    "保持当前节点",
    "context preserved",
    "no wrong progression",
    "keep current node",
    "no cross-unit leak",
    "no cross-lesson leak",
)

_DEFAULT_IMPORTANT_NON_BLOCKING_TOKENS = (
    "重要",
    "关键功能",
    "重要流程",
    "核心功能",
    "important",
    "critical flow",
)

_DEFAULT_HIGH_FREQUENCY_TOKENS = (
    "高频",
    "频繁",
    "常用",
    "daily",
    "high frequency",
    "frequent",
)

_DEFAULT_IMPORTANT_DETAIL_NAVIGATION_TOKENS = (
    "分句点评",
    "划线句子",
    "点评跳转",
    "sentence comment",
    "underlined sentence",
    "comment jump",
)

_DEFAULT_IMPORTANT_CONTENT_LIMIT_TOKENS = (
    "我的作文最多20条",
    "我的作文最多 20 条",
    "作品最多20条",
    "作品最多 20 条",
    "my essays max 20",
    "my compositions max 20",
)

_DEFAULT_USABILITY_DEGRADED_TOKENS = (
    "体验劣化",
    "体验差",
    "功能异常但可用",
    "可继续",
    "degraded",
    "still usable",
    "usability",
)

_DEFAULT_IMPORTANT_REGRESSION_TOKENS = (
    "重要回归",
    "关键回归",
    "regression",
    "回归验证",
    "历史缺陷复测",
)

_DEFAULT_FOCUS_BOUNDARY_TOKENS = (
    "边界",
    "最大",
    "最小",
    "临界",
    "boundary",
    "max",
    "min",
)

_DEFAULT_FOCUS_EXCEPTION_TOKENS = (
    "异常",
    "失败",
    "错误",
    "拒绝",
    "exception",
    "error",
    "invalid",
)

_DEFAULT_FOCUS_STATE_TOKENS = (
    "状态",
    "流转",
    "state",
    "transition",
)

_DEFAULT_SCORING_DELTAS = {
    "main_workflow_hit": 25,
    "workflow_blocking": 25,
    "severe_data_risk": 20,
    "severe_security_risk": 20,
    "case_level_release_blocking": 20,
    "important_non_blocking_flow": 12,
    "high_frequency_main_flow": 10,
    "important_detail_navigation": 35,
    "important_personal_content_limit": 35,
    "degraded_but_usable": 10,
    "important_regression_validation": 8,
    "reuse_risk_hit": 8,
    "boundary_or_low_risk_validation": -10,
    "long_tail_or_supplemental": -12,
    "non_critical_perf_or_ui": -15,
    "completeness_only": -12,
    "core_workflow_rule_hit": 8,
    "release_blocking_rule_hit": 12,
    "release_blocking_rule_hit_rule_only": 4,
    "security_or_data_critical_rule_hit": 15,
    "missing_critical_rule_hit": 10,
    "unique_critical_rule_coverage": 5,
    "redundant_covered_normal_rules": -10,
    "low_risk_supplemental_rule_only": -8,
    "structural_p2_low_value_signal": -10,
    "no_coverage_information_gain": -10,
    "coverage_gain_non_positive": -6,
    "p2_cap_no_coverage_gain_without_hard_guard": -12,
    "low_risk_only_covered_rules_penalty": -8,
    "p2_cap_low_risk_only_covered_rules": -12,
    "structural_display_mapping_penalty": -6,
    "p2_cap_display_mapping_scenario": -10,
}


# -- P0 anchor rules (result_postprocess.py) --

def p0_critical_families() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return _keyword_pair_tuple(_PAYLOAD.get("p0_critical_families")) or _DEFAULT_P0_CRITICAL_FAMILIES


def p0_core_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_core_tokens")) or _DEFAULT_P0_CORE_TOKENS


def p0_low_value_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_low_value_tokens")) or _DEFAULT_P0_LOW_VALUE_TOKENS


def p0_essay_domain_positive_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_domain_positive_tokens")) or _DEFAULT_P0_ESSAY_DOMAIN_POSITIVE_TOKENS


def p0_essay_domain_negative_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_domain_negative_tokens")) or _DEFAULT_P0_ESSAY_DOMAIN_NEGATIVE_TOKENS


def p0_essay_exclusion_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_exclusion_tokens")) or _DEFAULT_P0_ESSAY_EXCLUSION_TOKENS


def p0_essay_domain_primary_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_domain_primary_tokens")) or _DEFAULT_P0_ESSAY_DOMAIN_PRIMARY_TOKENS


# -- Priority semantics (result_postprocess_priority_semantics.py) --

def uncertain_requirement_signals() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("uncertain_requirement_signals")) or _DEFAULT_UNCERTAIN_REQUIREMENT_SIGNALS


def p0_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_keywords")) or _DEFAULT_P0_KEYWORDS


def p1_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p1_keywords")) or _DEFAULT_P1_KEYWORDS


def p2_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p2_keywords")) or _DEFAULT_P2_KEYWORDS


def strong_p0_payment_gate_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_payment_gate_tokens")) or _DEFAULT_STRONG_P0_PAYMENT_GATE_TOKENS


def strong_p0_ai_scoring_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_ai_scoring_tokens")) or _DEFAULT_STRONG_P0_AI_SCORING_TOKENS


def strong_p0_wrong_collection_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_wrong_collection_tokens")) or _DEFAULT_STRONG_P0_WRONG_COLLECTION_TOKENS


def strong_p0_week_boundary_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_week_boundary_tokens")) or _DEFAULT_STRONG_P0_WEEK_BOUNDARY_TOKENS


def strong_p0_submit_report_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_submit_report_tokens")) or _DEFAULT_STRONG_P0_SUBMIT_REPORT_TOKENS


# -- Priority scoring helpers (result_postprocess_priority_semantics_split_helpers.py) --

def ui_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("ui_keywords")) or _DEFAULT_UI_KEYWORDS


def ui_risk_words() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("ui_risk_words")) or _DEFAULT_UI_RISK_WORDS


def main_workflow_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("main_workflow_tokens")) or _DEFAULT_MAIN_WORKFLOW_TOKENS


def cross_page_flow_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("cross_page_flow_tokens")) or _DEFAULT_CROSS_PAGE_FLOW_TOKENS


def state_transition_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("state_transition_tokens")) or _DEFAULT_STATE_TRANSITION_TOKENS


def preferred_pattern_text_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("preferred_pattern_text_tokens")) or _DEFAULT_PREFERRED_PATTERN_TEXT_TOKENS


def preferred_pattern_categories() -> set[str]:
    raw = _PAYLOAD.get("preferred_pattern_categories", [])
    if not isinstance(raw, list):
        return set(_DEFAULT_PREFERRED_PATTERN_CATEGORIES)
    configured = {str(v).strip().lower() for v in raw if str(v or "").strip()}
    return configured or set(_DEFAULT_PREFERRED_PATTERN_CATEGORIES)


def reuse_risk_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("reuse_risk_tokens")) or _DEFAULT_REUSE_RISK_TOKENS


def blocking_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("blocking_tokens")) or _DEFAULT_BLOCKING_TOKENS


def severe_data_risk_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("severe_data_risk_tokens")) or _DEFAULT_SEVERE_DATA_RISK_TOKENS


def severe_security_risk_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("severe_security_risk_tokens")) or _DEFAULT_SEVERE_SECURITY_RISK_TOKENS


def behavior_depth_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("behavior_depth_tokens")) or _DEFAULT_BEHAVIOR_DEPTH_TOKENS


def state_guard_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("state_guard_tokens")) or _DEFAULT_STATE_GUARD_TOKENS


def important_non_blocking_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_non_blocking_tokens")) or _DEFAULT_IMPORTANT_NON_BLOCKING_TOKENS


def high_frequency_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("high_frequency_tokens")) or _DEFAULT_HIGH_FREQUENCY_TOKENS


def important_detail_navigation_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_detail_navigation_tokens")) or _DEFAULT_IMPORTANT_DETAIL_NAVIGATION_TOKENS


def important_content_limit_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_content_limit_tokens")) or _DEFAULT_IMPORTANT_CONTENT_LIMIT_TOKENS


def usability_degraded_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("usability_degraded_tokens")) or _DEFAULT_USABILITY_DEGRADED_TOKENS


def important_regression_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_regression_tokens")) or _DEFAULT_IMPORTANT_REGRESSION_TOKENS


def focus_boundary_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("focus_boundary_tokens")) or _DEFAULT_FOCUS_BOUNDARY_TOKENS


def focus_exception_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("focus_exception_tokens")) or _DEFAULT_FOCUS_EXCEPTION_TOKENS


def focus_state_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("focus_state_tokens")) or _DEFAULT_FOCUS_STATE_TOKENS


# -- Expected-result quality rules --

def reasoning_leakage_signals() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("reasoning_leakage_signals")) or _DEFAULT_REASONING_LEAKAGE_SIGNALS


def invalid_case_quality_markers() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("invalid_case_quality_markers")) or _DEFAULT_INVALID_CASE_QUALITY_MARKERS


def quality_check_fields() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("quality_check_fields")) or _DEFAULT_QUALITY_CHECK_FIELDS


# -- Scoring deltas --

def scoring_deltas() -> dict[str, int]:
    return {**_DEFAULT_SCORING_DELTAS, **_int_dict(_PAYLOAD.get("scoring_deltas"))}
