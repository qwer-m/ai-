"""Default priority postprocess configuration values."""

from __future__ import annotations

from .priority_behavior_semantics import GENERIC_LOW_VALUE_TOKENS

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

_DEFAULT_P0_CRITICAL_FAMILIES = (
    ("generation_result", ("upload", "generate", "result")),
    ("result_display", ("complete result",)),
    ("result_display", ("result details",)),
    ("submission", ("submit", "success")),
    ("submission", ("submit", "pending review")),
    ("approval", ("review approved",)),
    ("approval", ("approval passed",)),
    ("approval", ("approved work",)),
    ("permission", ("locked", "paywall")),
    ("permission", ("权限", "锁定")),
    ("generation_result", ("上传", "生成", "结果")),
    ("result_display", ("完整结果",)),
    ("submission", ("提交成功",)),
    ("submission", ("进入审核中",)),
    ("approval", ("审核通过",)),
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
    "generate result",
    "generated result",
    "complete result",
    "result details",
    "上传",
    "提交",
    "提交成功",
    "发布",
    "审核",
    "审核通过",
    "权限",
    "会员",
    "锁定",
    "生成结果",
    "完整结果",
)

_DEFAULT_P0_LOW_VALUE_TOKENS = GENERIC_LOW_VALUE_TOKENS

_DEFAULT_UNCERTAIN_REQUIREMENT_SIGNALS = (
    "需确认",
    "需要确认",
    "需要讨论",
    "本期可以不做",
    "本期可以不要",
    "暂不支持",
    "模型不支持",
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
    "不串数据",
    "不串上下文",
    "不丢上下文",
    "不错误推进",
    "不标记完成",
    "保持当前节点",
    "context preserved",
    "no wrong progression",
    "keep current node",
    "no cross-context leak",
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
    "详情跳转",
    "列表到详情",
    "detail navigation",
    "list to detail",
)

_DEFAULT_IMPORTANT_CONTENT_LIMIT_TOKENS = (
    "记录上限",
    "内容上限",
    "record limit",
    "content limit",
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

__all__ = [
    "_DEFAULT_REASONING_LEAKAGE_SIGNALS",
    "_DEFAULT_INVALID_CASE_QUALITY_MARKERS",
    "_DEFAULT_QUALITY_CHECK_FIELDS",
    "_DEFAULT_P1_KEYWORDS",
    "_DEFAULT_P0_CRITICAL_FAMILIES",
    "_DEFAULT_P0_CORE_TOKENS",
    "_DEFAULT_P0_LOW_VALUE_TOKENS",
    "_DEFAULT_UNCERTAIN_REQUIREMENT_SIGNALS",
    "_DEFAULT_P0_KEYWORDS",
    "_DEFAULT_P2_KEYWORDS",
    "_DEFAULT_UI_KEYWORDS",
    "_DEFAULT_UI_RISK_WORDS",
    "_DEFAULT_MAIN_WORKFLOW_TOKENS",
    "_DEFAULT_CROSS_PAGE_FLOW_TOKENS",
    "_DEFAULT_STATE_TRANSITION_TOKENS",
    "_DEFAULT_PREFERRED_PATTERN_TEXT_TOKENS",
    "_DEFAULT_PREFERRED_PATTERN_CATEGORIES",
    "_DEFAULT_REUSE_RISK_TOKENS",
    "_DEFAULT_BLOCKING_TOKENS",
    "_DEFAULT_SEVERE_DATA_RISK_TOKENS",
    "_DEFAULT_SEVERE_SECURITY_RISK_TOKENS",
    "_DEFAULT_BEHAVIOR_DEPTH_TOKENS",
    "_DEFAULT_STATE_GUARD_TOKENS",
    "_DEFAULT_IMPORTANT_NON_BLOCKING_TOKENS",
    "_DEFAULT_HIGH_FREQUENCY_TOKENS",
    "_DEFAULT_IMPORTANT_DETAIL_NAVIGATION_TOKENS",
    "_DEFAULT_IMPORTANT_CONTENT_LIMIT_TOKENS",
    "_DEFAULT_USABILITY_DEGRADED_TOKENS",
    "_DEFAULT_IMPORTANT_REGRESSION_TOKENS",
    "_DEFAULT_FOCUS_BOUNDARY_TOKENS",
    "_DEFAULT_FOCUS_EXCEPTION_TOKENS",
    "_DEFAULT_FOCUS_STATE_TOKENS",
    "_DEFAULT_SCORING_DELTAS",
]
