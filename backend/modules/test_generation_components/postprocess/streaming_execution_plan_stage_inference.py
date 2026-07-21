from __future__ import annotations

import re
from typing import Collection


_STAGE_KIND_COMPATIBILITY: dict[str, set[str]] = {
    "entry": {"entry", "consume", "unknown"},
    "configure": {"configure", "unknown"},
    "edit": {"configure", "unknown"},
    "preview": {"preview", "consume", "unknown"},
    "commit": {"commit"},
    "downstream_visibility": {"downstream_visibility"},
    "completion_sync": {"completion_sync", "downstream_visibility", "unknown"},
    "consume": {"consume", "preview", "entry", "unknown"},
}

_COMMIT_TOKENS = (
    "保存",
    "提交",
    "确认",
    "发布",
    "save",
    "submit",
    "commit",
    "confirm",
    "publish",
)
_DOWNSTREAM_PHASE_TOKENS = (
    "同步",
    "生效",
    "展示",
    "显示",
    "刷新",
    "sync",
    "display",
    "displayed",
    "show",
    "shows",
    "shown",
    "visible",
    "effective",
    "reflect",
    "reflects",
    "reflected",
    "downstream",
)
_DOWNSTREAM_VISIBILITY_TOKENS = (*_DOWNSTREAM_PHASE_TOKENS, "出现", "可见", "最新", "latest")
_ENTRY_ANCHOR_TOKENS = ("入口", "工作流入口", "进入入口", "entry", "workflow entry")
_CONSUME_TOKENS = (
    "点击",
    "跳转",
    "查看",
    "打开",
    "进入",
    "click",
    "navigate",
    "view",
    "open",
)
_PREVIEW_TOKENS = ("预览", "检查", "确认前", "preview", "review")
_CONFIGURE_TOKENS = (
    "新增",
    "创建",
    "添加",
    "选择",
    "设置",
    "配置",
    "编辑",
    "修改",
    "create",
    "add",
    "select",
    "set",
    "configure",
    "edit",
    "modify",
)
_ENTRY_TOKENS = ("访问", "enter", "access")
_COMPLETION_TOKENS = ("完成", "进度", "状态", "complete", "completion", "progress", "status")


def contains_any_token(text: str, tokens: Collection[str]) -> bool:
    haystack = str(text or "").lower()
    return any(token and str(token).lower() in haystack for token in tokens)


def stage_kind_compatible(expected: str, candidate: str) -> bool:
    expected_kind = str(expected or "").strip().lower()
    candidate_kind = str(candidate or "").strip().lower() or "unknown"
    if not expected_kind or expected_kind == "unknown":
        return True
    if candidate_kind == "unknown":
        return True
    return candidate_kind in _STAGE_KIND_COMPATIBILITY.get(expected_kind, {expected_kind, "unknown"})


def token_hit(text: str, tokens: tuple[str, ...]) -> bool:
    haystack = str(text or "").strip().lower()
    if not haystack:
        return False
    for token in tokens:
        needle = str(token or "").strip().lower()
        if not needle:
            continue
        if needle.isascii() and re.search(r"[a-z0-9]", needle):
            if re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack):
                return True
            continue
        if needle in haystack:
            return True
    return False


def infer_workflow_stage_kind(text: str) -> str:
    lowered = str(text or "").lower()
    if token_hit(lowered, _COMMIT_TOKENS):
        return "commit"
    if token_hit(lowered, _DOWNSTREAM_VISIBILITY_TOKENS):
        return "downstream_visibility"
    if token_hit(lowered, _ENTRY_ANCHOR_TOKENS):
        return "entry"
    if token_hit(lowered, _CONSUME_TOKENS):
        return "consume"
    if token_hit(lowered, _PREVIEW_TOKENS):
        return "preview"
    if token_hit(lowered, _CONFIGURE_TOKENS):
        return "configure"
    if token_hit(lowered, _ENTRY_TOKENS):
        return "entry"
    if token_hit(lowered, _COMPLETION_TOKENS):
        return "completion_sync"
    return "unknown"


def infer_workflow_phase(text: str) -> int:
    lowered = str(text or "").lower()
    if token_hit(lowered, (*_COMMIT_TOKENS, "下架", "删除", "delete")):
        return 60
    if token_hit(lowered, _DOWNSTREAM_PHASE_TOKENS):
        return 70
    if contains_any_token(lowered, ("打开", "进入", "访问", "入口", "open", "enter", "entry")):
        return 10
    if contains_any_token(lowered, ("新增", "创建", "添加", "选择", "选课", "设置", "配置", "准备", "create", "add", "select", "set", "prepare", "prepared", "ready")):
        return 20
    if contains_any_token(lowered, ("编辑", "修改", "调整", "update", "edit", "modify")):
        return 30
    if contains_any_token(lowered, ("预览", "检查", "确认前", "preview", "review")):
        return 50
    if contains_any_token(lowered, ("点击", "跳转", "查看", "click", "navigate", "view")):
        return 80
    return 90
