from __future__ import annotations

import re
from typing import Any

from .execution_plan_case_state import (
    _case_semantic_text,
    _state_value,
    _text,
)

_ACTION_SUPPORT_SPLIT_RE = re.compile(r"[的了着和与及并或且在从到于后前时中上下里内为把将对、，。；：:（）()\[\]\s]+")
_ACTION_SUPPORT_GENERIC_TOKENS = {
    "button",
    "click",
    "current",
    "page",
    "user",
    "view",
    "页面",
    "按钮",
    "点击",
    "操作",
    "用户",
    "当前",
    "对应",
    "进行",
    "所有",
}
_ACTION_SUPPORT_EQUIVALENTS = {
    "entry": ("入口", "进入", "首页", "导航"),
    "enter": ("进入", "入口"),
    "navigate": ("进入", "跳转", "导航"),
    "home": ("首页", "主页"),
    "configure": ("配置", "设置", "选择", "编辑", "填写"),
    "choose": ("选择",),
    "edit": ("编辑", "修改", "填写", "输入"),
    "compose": ("编辑", "填写", "输入"),
    "input": ("输入", "填写"),
    "upload": ("上传", "图片"),
    "preview": ("预览", "查看", "详情", "检查"),
    "review": ("预览", "查看", "检查", "审核"),
    "detail": ("详情", "查看"),
    "commit": ("提交", "发布", "保存", "确认"),
    "submit": ("提交", "发布"),
    "publish": ("发布",),
    "save": ("保存"),
    "display": ("显示", "展示", "可见", "出现"),
    "visible": ("可见", "显示", "展示"),
    "visibility": ("可见", "显示", "展示"),
    "show": ("显示", "展示", "出现"),
    "message": ("消息", "通知", "回复", "小红点"),
    "notification": ("通知", "消息", "小红点"),
    "reply": ("回复", "评论", "消息"),
    "sync": ("同步", "更新", "状态"),
    "complete": ("完成", "闭环", "结束"),
    "completion": ("完成", "闭环", "结束"),
    "status": ("状态", "进度", "更新"),
}

_ACTION_SUPPORT_STRONG_TOKENS = {
    "entry",
    "enter",
    "navigate",
    "open",
    "configure",
    "choose",
    "edit",
    "modify",
    "compose",
    "input",
    "upload",
    "preview",
    "review",
    "commit",
    "submit",
    "publish",
    "save",
    "saved",
    "confirm",
    "display",
    "visible",
    "visibility",
    "show",
    "sync",
    "complete",
    "completion",
    "status",
    "进入",
    "跳转",
    "导航",
    "打开",
    "配置",
    "设置",
    "选择",
    "编辑",
    "修改",
    "填写",
    "输入",
    "上传",
    "预览",
    "查看",
    "检查",
    "审核",
    "确认",
    "提交",
    "发布",
    "保存",
    "显示",
    "展示",
    "可见",
    "出现",
    "同步",
    "更新",
    "完成",
    "闭环",
}

_ACTION_SUPPORT_KNOWN_CHINESE_TOKENS = tuple(
    dict.fromkeys(
        [
            *[item for values in _ACTION_SUPPORT_EQUIVALENTS.values() for item in values],
            "修改",
            "打开",
            "文案",
            "正文",
            "图片",
            "内容",
            "标题",
            "分区",
            "分类",
            "详情",
            "列表",
            "结果",
            "计划",
        ]
    )
)

_DOWNSTREAM_STAGE_ACTION_ANCHORS = (
    "同步",
    "生效",
    "展示",
    "显示",
    "可见",
    "消息",
    "通知",
    "回复",
    "小红点",
    "最新",
    "更新",
    "状态",
    "sync",
    "visible",
    "visibility",
    "display",
    "show",
    "status",
    "message",
    "notification",
    "downstream",
    "latest",
    "updated",
)

_MESSAGE_SURFACE_TOKENS = (
    "消息",
    "通知",
    "message",
    "messages",
    "notification",
    "notifications",
)

_AUDIT_ACTION_TOKENS = (
    "审核",
    "审批",
    "处理",
    "通过",
    "驳回",
    "屏蔽",
    "删除",
    "禁止",
    "恢复",
    "audit",
    "approve",
    "approval",
    "moderate",
    "moderation",
)


def _action_support_tokens(value: Any) -> list[str]:
    text = _text(value).lower()
    if not text:
        return []
    raw_tokens: list[str] = []
    for ascii_token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text):
        if "_" in ascii_token or "-" in ascii_token:
            raw_tokens.extend(part for part in re.split(r"[_\-]+", ascii_token) if len(part) >= 2)
        else:
            raw_tokens.append(ascii_token)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for piece in _ACTION_SUPPORT_SPLIT_RE.split(sequence):
            if len(piece) < 2:
                continue
            if len(piece) <= 8:
                raw_tokens.append(piece)
            raw_tokens.extend(token for token in _ACTION_SUPPORT_KNOWN_CHINESE_TOKENS if token in piece)

    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        normalized = token.strip().lower()
        if len(normalized) < 2 or normalized in _ACTION_SUPPORT_GENERIC_TOKENS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _action_token_variants(token: str) -> set[str]:
    variants = {token}
    if token.endswith("ed") and len(token) > 4:
        variants.add(token[:-1])
        variants.add(token[:-2])
    if token.endswith("s") and len(token) > 4:
        variants.add(token[:-1])
    return variants


def _is_action_anchor_token(token: str) -> bool:
    normalized = token.strip().lower()
    if not normalized:
        return False
    if any(variant in _ACTION_SUPPORT_STRONG_TOKENS for variant in _action_token_variants(normalized)):
        return True
    equivalents = _ACTION_SUPPORT_EQUIVALENTS.get(normalized, ())
    return any(item in _ACTION_SUPPORT_STRONG_TOKENS for item in equivalents)


def _action_token_in_text(text: str, token: str) -> bool:
    equivalents = _ACTION_SUPPORT_EQUIVALENTS.get(token, ())
    if any(item and item in text for item in equivalents):
        return True
    if token.isascii() and re.search(r"[a-z0-9]", token):
        if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", text):
            return True
        words = re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text)
        variants = _action_token_variants(token)
        for word in words:
            if word in variants:
                return True
            if len(word) >= 7 and len(token) >= 7 and (word.startswith(token[:5]) or token.startswith(word[:5])):
                return True
        return False
    return token in text


def _any_action_token_in_text(text: str, tokens: tuple[str, ...]) -> bool:
    return any(_action_token_in_text(text, token) for token in tokens if str(token or "").strip())


def _surface_token_in_text(text: str, token: str) -> bool:
    normalized_text = str(text or "").lower()
    normalized_token = str(token or "").strip().lower()
    if not normalized_text or not normalized_token:
        return False
    if normalized_token.isascii() and re.search(r"[a-z0-9]", normalized_token):
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(normalized_token)}(?![a-z0-9_])", normalized_text))
    return normalized_token in normalized_text


def _any_surface_token_in_text(text: str, tokens: tuple[str, ...]) -> bool:
    return any(_surface_token_in_text(text, token) for token in tokens if str(token or "").strip())


def _case_behavior_text(case: dict[str, Any]) -> str:
    values: list[Any] = [
        case.get("test_input"),
        case.get("expected_result"),
        case.get("preconditions"),
        case.get("steps"),
    ]
    return " ".join(_text(value) for value in values if _text(value)).lower()


def main_chain_action_support_conflict_reason(case: dict[str, Any]) -> str:
    """Return a conflict reason when workflow action metadata is not supported by public case text."""
    action = _text(_state_value(case, "action"))
    label = _text(case.get("main_chain_stage_label"))
    stage_kind = _text(_state_value(case, "stage_kind") or case.get("main_chain_stage_kind")).lower()
    stage_action_text = f"{action} {label}".lower()
    text = _case_semantic_text(case)
    stage_specific_support_satisfied = False
    if stage_kind == "downstream_visibility":
        if not any(_action_token_in_text(stage_action_text, token) for token in _DOWNSTREAM_STAGE_ACTION_ANCHORS):
            return "stage_action_not_supported_by_case_text"
        if _any_surface_token_in_text(stage_action_text, _MESSAGE_SURFACE_TOKENS) and not _any_surface_token_in_text(
            text,
            _MESSAGE_SURFACE_TOKENS,
        ):
            return "stage_action_not_supported_by_case_text"
        stage_specific_support_satisfied = True
    if stage_kind == "consume" and _any_action_token_in_text(stage_action_text, _AUDIT_ACTION_TOKENS):
        if not _any_action_token_in_text(_case_behavior_text(case), _AUDIT_ACTION_TOKENS):
            return "stage_action_not_supported_by_case_text"
        stage_specific_support_satisfied = True
    action_tokens = _action_support_tokens(action)
    label_tokens = _action_support_tokens(label)
    if len(action_tokens) < 2 and len(label_tokens) < 2:
        return ""

    expected_tokens = list(dict.fromkeys([*action_tokens, *label_tokens]))
    if len(expected_tokens) < 2:
        return ""
    if stage_specific_support_satisfied and not any(token.isascii() for token in expected_tokens):
        return ""

    source_anchors = [
        token
        for token in dict.fromkeys([*action_tokens, *label_tokens])
        if _is_action_anchor_token(token)
    ]
    if source_anchors and not any(_action_token_in_text(text, token) for token in source_anchors):
        return "stage_action_not_supported_by_case_text"

    matched = [token for token in expected_tokens if _action_token_in_text(text, token)]
    required = 1
    if len(expected_tokens) >= 5:
        required = 3
    elif len(expected_tokens) >= 3:
        required = 2
    min_match_ratio = 0.45 if len(expected_tokens) >= 4 else 0.3
    if len(matched) < required or (len(matched) / max(1, len(expected_tokens))) < min_match_ratio:
        return "stage_action_not_supported_by_case_text"
    return ""
