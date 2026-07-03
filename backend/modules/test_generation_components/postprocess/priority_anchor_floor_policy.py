from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

_STRONG_TOKENS = (
        "submit",
        "publish",
        "upload",
        "generate",
        "approve",
        "review pass",
        "review approved",
        "approval passed",
        "permission",
        "member",
        "vip",
        "locked",
        "paywall",
        "result",
        "first lesson",
        "all courses",
        "successfully generated",
        "generated result",
        "correction result",
        "review result",
        "four modules",
        "feedback modules",
        "result details",
        "submit success",
        "approval state",
        "detail page",
        "提交",
        "投稿",
        "发布",
        "上传",
        "生成",
        "批改",
        "审核通过",
        "审核中",
        "权限",
        "会员",
        "锁定",
        "结果",
        "第一课",
        "试学",
        "普通用户",
        "非会员",
        "全部课程",
        "成功生成",
        "生成批改结果",
        "批改结果展示",
        "四大模块",
        "提交成功",
        "进入审核中",
        "审核通过后",
        "作品详情",
    )

_LOW_VALUE_TOKENS = (
        "copy",
        "toast",
        "tooltip",
        "popup",
        "modal",
        "dialog",
        "badge",
        "status badge",
        "record limit",
        "max records",
        "maximum records",
        "format",
        "layout",
        "sort",
        "sorting",
        "rank",
        "ranking",
        "share",
        "h5",
        "category",
        "tab",
        "pdf",
        "download",
        "image preview",
        "large image",
        "photo preview",
        "drag",
        "drag sort",
        "reorder",
        "delete image",
        "remove image",
        "force close",
        "kill app",
        "48h",
        "48 hours",
        "zero images",
        "0 images",
        "no images",
        "disabled button",
        "button disabled",
        "remaining count",
        "quota decrement",
        "star rating",
        "countdown",
        "title body",
        "editable title",
        "my list",
        "复制",
        "提示",
        "弹窗",
        "弹层",
        "规则弹窗",
        "状态标识",
        "标识",
        "最多20条",
        "上限",
        "文案",
        "样式",
        "格式",
        "入口",
        "空状态",
        "排序",
        "置顶",
        "分类",
        "分享",
        "下载",
        "拖动",
        "拖拽",
        "排序",
        "删除图片",
        "删除缩略图",
        "强杀",
        "强制退出",
        "48小时",
        "大图",
        "照片大图",
        "预览",
        "序号",
        "榜单",
        "0张",
        "无图片",
        "按钮不可点",
        "按钮不可用",
        "剩余次数",
        "次数递减",
        "星星评分",
        "倒计时",
        "标题正文",
        "可编辑",
        "我的列表",
        "分句点评",
        "划线句子",
        "点评跳转",
        "sentence comment",
        "underlined sentence",
        "comment jump",
    )

_ANCHOR_FAMILIES = (
        ("submission", ("submit", "publish", "提交", "投稿", "发布")),
        ("result_display", ("four modules", "feedback modules", "result details", "四大模块", "四部分", "完整展示")),
        ("generation_result", ("generate", "result", "upload", "生成", "结果", "批改", "上传")),
        ("approval", ("approve", "review approved", "approval passed", "审核通过", "审核中")),
        ("permission", ("permission", "member", "vip", "locked", "paywall", "first lesson", "权限", "会员", "锁定", "第一课", "试学")),
        ("community_detail", ("detail page", "review approved", "作品详情", "审核通过后")),
    )

_CRITICAL_ANCHOR_FAMILIES = (
        ("generation_result", ("上传", "去批改", "生成", "批改结果")),
        ("result_display", ("批改反馈", "四部分", "完整展示", "综合点评", "全文润色", "优化建议")),
        ("submission", ("投稿", "提交成功", "审核中")),
        ("submission", ("投稿成功", "审核中")),
        ("cross_module_state", ("批改", "投稿", "已发布", "作文圈")),
        ("approval", ("审核通过", "已发布", "作文圈", "可见")),
        ("approval", ("审核通过", "作文圈")),
        ("approval", ("已发布", "作文圈")),
        ("free_first_lesson", ("普通用户", "第一课", "试学")),
        ("free_first_lesson", ("普通用户", "第一课", "免费")),
        ("locked_member_courses", ("普通用户", "非第一课", "会员中心")),
        ("locked_member_courses", ("其余课程", "会员中心")),
        ("member_all_courses", ("会员", "全部课程", "可学")),
        ("member_all_courses", ("会员", "全部课程")),
        ("delete_restore", ("删除", "已发布", "恢复未投稿")),
        ("delete_restore", ("删除作品", "未投稿")),
    )

_DETAIL_TOKENS = (
            "分句点评",
            "划线句子",
            "点评跳转",
            "最多20条",
            "0张",
            "无图片",
            "按钮不可点",
            "按钮不可用",
            "剩余次数",
            "次数递减",
            "星星评分",
            "倒计时",
            "标题正文",
            "可编辑",
            "我的列表",
            "sentence comment",
            "underlined sentence",
            "comment jump",
            "max 20",
        )

_GENERATION_BLOCKING_TERMS = (
            "generate",
            "generated",
            "correction result",
            "review result",
            "four modules",
            "feedback modules",
            "result details",
            "successfully generated",
            "生成",
            "生成批改结果",
            "批改结果",
            "批改结果展示",
            "四大模块",
            "四部分",
            "完整展示",
        )

_SUBMIT_BLOCKING_TERMS = (
            "submit success",
            "submitted successfully",
            "enters pending review",
            "提交成功",
            "投稿成功",
            "进入审核中",
            "状态变为审核中",
        )

_APPROVAL_BLOCKING_TERMS = (
            "approval passed",
            "review approved",
            "approved work",
            "visible in community",
            "community detail",
            "审核通过",
            "审核通过后",
            "作文圈可见",
            "他人可见",
            "作品详情",
        )

_PERMISSION_BLOCKING_TERMS = (
            "permission",
            "member all courses",
            "all courses",
            "first lesson",
            "locked",
            "paywall",
            "vip",
            "权限",
            "普通用户",
            "第一课",
            "试学",
            "非第一课",
            "锁课",
            "锁定",
            "跳会员",
            "会员用户",
            "全部课程",
        )


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token and token.lower() in text for token in tokens)


@dataclass(frozen=True)
class MainPathAnchorPolicy:
    configured_anchor_family_fn: Callable[[str], str]
    has_core_signal_fn: Callable[[str], bool]
    has_low_value_signal_fn: Callable[[str], bool]
    complexity_profile_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None

    def anchor_family(self, text: str) -> str:
        for family, tokens in _ANCHOR_FAMILIES:
            if _contains_any(text, tokens):
                return family
        return "general"

    def has_strong_anchor(self, text: str) -> bool:
        return _contains_any(text, _STRONG_TOKENS) or self.has_core_signal_fn(text)

    def critical_anchor_family(self, text: str) -> str:
        for family, tokens in _CRITICAL_ANCHOR_FAMILIES:
            if all(token and token.lower() in text for token in tokens):
                return family
        return self.configured_anchor_family_fn(text)

    def has_critical_anchor(self, text: str) -> bool:
        return bool(self.critical_anchor_family(text))

    def has_low_value_anchor(self, text: str) -> bool:
        return _contains_any(text, _LOW_VALUE_TOKENS) or self.has_low_value_signal_fn(text)

    def has_non_blocking_detail_anchor(self, text: str) -> bool:
        return _contains_any(text, _DETAIL_TOKENS)

    def has_blocking_anchor(self, text: str) -> bool:
        blocking_terms = (
            _GENERATION_BLOCKING_TERMS
            + _SUBMIT_BLOCKING_TERMS
            + _APPROVAL_BLOCKING_TERMS
            + _PERMISSION_BLOCKING_TERMS
        )
        return _contains_any(text, blocking_terms)

    def should_demote_non_blocking(
        self,
        text: str,
        *,
        critical_family: str | None = None,
    ) -> bool:
        family = self.critical_anchor_family(text) if critical_family is None else critical_family
        return bool(
            not family
            and (
                self.has_non_blocking_detail_anchor(text)
                or (self.has_low_value_anchor(text) and not self.has_blocking_anchor(text))
            )
        )

    def complexity_penalty(self, item: dict[str, Any]) -> int:
        if self.complexity_profile_fn is None:
            return 0
        try:
            return 4 * int((self.complexity_profile_fn(item) or {}).get("complexity_score") or 0)
        except Exception:
            return 0

    def primary_rank(
        self,
        *,
        item: dict[str, Any],
        index: int,
        text: str,
        normalized_priority: str,
    ) -> tuple[int, int, str, dict[str, Any]] | None:
        score = 0
        score += 10 * sum(1 for token in _STRONG_TOKENS if token and token.lower() in text)
        score -= 12 * sum(1 for token in _LOW_VALUE_TOKENS if token and token.lower() in text)
        if normalized_priority == "P1":
            score += 6
        critical_family = self.critical_anchor_family(text)
        if critical_family:
            score += 70
        if self.should_demote_non_blocking(text, critical_family=critical_family):
            score -= 40
        if str(item.get("priority_decision_state") or "").strip().lower() in {"optional", "invalid"}:
            score -= 20
        score -= self.complexity_penalty(item)
        if score >= 10:
            return (score, -index, critical_family or self.anchor_family(text), item)
        return None

    def fallback_rank(
        self,
        *,
        item: dict[str, Any],
        index: int,
        text: str,
        normalized_priority: str,
        mode: str,
    ) -> tuple[int, int, str, dict[str, Any]] | None:
        critical_family = self.critical_anchor_family(text)
        if self.should_demote_non_blocking(text, critical_family=critical_family):
            return None
        if mode == "full_functional_regression" and not (
            self.has_strong_anchor(text) or critical_family
        ):
            return None
        priority_bonus = 8 if normalized_priority == "P1" else 3 if normalized_priority == "P2" else 0
        fallback_score = priority_bonus + (60 if critical_family else 0) - self.complexity_penalty(item)
        if fallback_score >= 3:
            return (fallback_score, -index, critical_family or self.anchor_family(text), item)
        return None


__all__ = ["MainPathAnchorPolicy"]
