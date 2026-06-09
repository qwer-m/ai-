from __future__ import annotations

import re

from .streaming_case_normalization import is_placeholder_expected_result


NON_ASSERTABLE_EXPECTED_PHRASES = (
    "对应状态变化",
    "关键结果可核对",
    "对应内容",
    "匹配的结果",
    "结果内容可校验",
    "后续查询可验证结果",
    "可验证结果",
    "应成功完成",
    "需求说",
    "按规则",
    "正常展示",
    "正常跳转",
    "正常更新",
    "符合预期",
    "应跳转到目标页面",
    "页面路径与标题",
    "响应状态码正确",
    "授权范围内页面或模块",
    "关键字段",
    "字段值与输入",
    "后端数据一致",
    "公式计算",
    "按公式",
    "排序与",
    "排序算法",
    "计算结果一致",
    "数据一致",
    "与预期一致",
    "应一致",
    "功能正常",
    "执行成功且结果正确",
)

TRUNCATED_TEXT_ENDINGS = (
    "或显",
    "对应内",
    "可校",
    "正常展",
    "跳转至",
    "显示为",
)


def has_concrete_expected_assertion(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if re.search(r"[\"'`“”‘’].{4,}[\"'`“”‘’]", normalized):
        return True
    if re.search(r"\d+\s*/\s*\d+", normalized) and any(
        token in normalized for token in ("显示为", "剩余批改次数", "剩余次数")
    ):
        return True
    concrete_tokens = (
        "系统提示",
        "不可点击",
        "不可操作",
        "置灰",
        "隐藏",
        "移除",
        "恢复为",
        "可重新",
        "无锁",
        "无任何锁",
        "无提示",
        "无需解锁",
        "提示阻止",
        "直接进入",
        "当前单元",
        "主题名称",
        "上册",
        "下册",
        "列表依次",
        "顺序与",
        "权重公式",
        "公式计算结果一致",
        "降序排列",
        "作文圈",
        "我的作文",
        "批改记录",
        "图片不清晰",
        "重试选项",
        "disabled",
        "hidden",
        "restore",
        "removed",
    )
    return any(token in normalized for token in concrete_tokens)


def has_weak_ambiguous_expected_result(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    weak_alternatives = (
        "或显示错误信息",
        "或提示错误",
        "或提示异常",
        "或显示异常",
        "或显示对应内容",
        "或按配置",
        "or show error",
        "or display error",
        "or prompt error",
        "or as configured",
    )
    return any(token in normalized for token in weak_alternatives)


def is_non_assertable_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    if looks_template_polluted_expected_result(normalized):
        return True
    if is_placeholder_expected_result(normalized):
        return True
    concrete_assertion = has_concrete_expected_assertion(normalized)
    if is_ambiguous_expected_result(normalized):
        if concrete_assertion and not has_weak_ambiguous_expected_result(normalized):
            return False
        return True
    if concrete_assertion:
        return False
    return any(phrase in normalized for phrase in NON_ASSERTABLE_EXPECTED_PHRASES)


def looks_template_polluted_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    pollution_pairs = (
        ("目标页面", "页面路径与标题", "显隐原图"),
        ("target page", "path", "original image"),
        ("jump to target page", "title", "image visibility"),
        ("目标页面", "页面路径与标题", "原图"),
        ("应跳转到目标页面", "页面路径与标题", "按钮功能验证"),
    )
    lowered = normalized.lower()
    if any(all(part.lower() in lowered for part in parts) for parts in pollution_pairs):
        return True
    if "页面路径与标题" in normalized and any(
        token in normalized
        for token in ("上传图片", "显隐", "原图", "缩略图", "批改", "复制", "排序", "弹窗")
    ):
        return True
    if "should navigate to target page" in lowered and any(
        token in lowered for token in ("upload", "image", "copy", "sort", "popup")
    ):
        return True
    if "应跳转到目标页面" in normalized and "页面路径与标题" in normalized:
        return True
    if normalized.startswith("执行") and any(
        phrase in normalized
        for phrase in (
            "页面路径与标题均与",
            "字段值与输入/后端数据一致",
            "授权范围内页面或模块",
            "响应状态码正确",
        )
    ):
        return True
    retry_or_failure_tokens = (
        "重试",
        "失败",
        "加载失败",
        "播放失败",
        "接口500",
        "504",
        "超时",
        "网络中断",
        "retry",
        "failed",
        "timeout",
        "network interruption",
    )
    media_or_result_tokens = (
        "视频",
        "音频",
        "资料",
        "pdf",
        "批改结果",
        "作文批改",
        "审题立意",
        "写作技法",
        "video",
        "media",
        "resource",
        "correction result",
    )
    template_delete_tokens = ("应删除", "删除", "should delete", "remove")
    if (
        normalized.startswith("执行")
        and any(token in normalized for token in template_delete_tokens)
        and any(token in normalized for token in retry_or_failure_tokens)
        and any(token in normalized for token in media_or_result_tokens)
    ):
        return True
    return False


def is_ambiguous_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    ambiguous_tokens = (
        "可能",
        "默认可能",
        "视情况",
        "可选",
        "或显示",
        "或提示",
        "或按钮",
        "或直接",
        "或弹窗",
        "或跳转",
        "或进入",
        "或置灰",
        "或隐藏",
        "？",
        "?",
        "or show",
        "or display",
        "or prompt",
        "or directly",
        "or navigate",
        "or hide",
        "or disable",
    )
    if any(token in normalized for token in ambiguous_tokens):
        return True
    return bool(re.search(r"\b[xX]{2,}\b", normalized))


def looks_truncated_text(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    trimmed = re.sub(r"[。！？.!?]+$", "", normalized).strip()
    if not trimmed:
        return False
    return any(trimmed.endswith(suffix) for suffix in TRUNCATED_TEXT_ENDINGS)
