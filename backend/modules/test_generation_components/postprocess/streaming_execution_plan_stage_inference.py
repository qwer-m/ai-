from __future__ import annotations

import re
from typing import Collection


def contains_any_token(text: str, tokens: Collection[str]) -> bool:
    return any(token and token.lower() in text for token in tokens)


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
    if token_hit(lowered, ("保存", "提交", "确认", "发布")):
        return "commit"
    if token_hit(lowered, ("保存", "提交", "确认", "发布", "save", "submit", "commit", "confirm", "publish")):
        return "commit"
    if token_hit(
        lowered,
        (
            "触发打分",
            "开始打分",
            "自动打分",
            "评分计算",
            "生成评分",
            "给出评分",
            "trigger score",
            "score calculation",
        ),
    ):
        return "commit"
    if token_hit(
        lowered,
        (
            "同步",
            "生效",
            "展示",
            "显示",
            "出现",
            "可见",
            "最新",
            "评分结果",
            "打分结果",
            "综合评分",
            "visible",
            "display",
            "displayed",
            "show",
            "shows",
            "shown",
            "score result",
            "scoring result",
        ),
    ):
        return "downstream_visibility"
    if token_hit(lowered, ("入口", "进入入口")):
        return "entry"
    if token_hit(
        lowered,
        (
            "点击",
            "跳转",
            "学习",
            "查看",
            "打开",
            "进入",
        ),
    ):
        return "consume"
    if token_hit(lowered, ("预览", "检查", "确认前")):
        return "preview"
    if token_hit(
        lowered,
        (
            "新增",
            "创建",
            "添加",
            "选择",
            "设置",
            "配置",
            "编辑",
            "修改",
        ),
    ):
        return "configure"
    if token_hit(lowered, ("进入", "访问", "打开")):
        return "entry"
    if token_hit(lowered, ("完成", "进度", "状态")):
        return "completion_sync"
    if token_hit(lowered, ("同步", "生效", "展示", "显示", "刷新", "最新", "sync", "display", "show", "visible", "effective", "latest", "reflect", "reflects", "reflected", "downstream")):
        return "downstream_visibility"
    if token_hit(lowered, ("入口", "工作流入口", "进入入口", "entry", "workflow entry")):
        return "entry"
    if token_hit(lowered, ("点击", "跳转", "学习", "查看", "打开", "click", "navigate", "learn", "view", "open")):
        return "consume"
    if token_hit(lowered, ("预览", "检查", "确认前", "preview", "review")):
        return "preview"
    if token_hit(lowered, ("新增", "创建", "添加", "选择", "设置", "配置", "编辑", "修改", "create", "add", "select", "set", "configure", "edit", "modify")):
        return "configure"
    if token_hit(lowered, ("进入", "访问", "打开", "enter", "access", "open")):
        return "entry"
    if token_hit(lowered, ("完成", "进度", "状态", "complete", "completion", "progress", "status")):
        return "completion_sync"
    return "unknown"


def infer_workflow_phase(text: str) -> int:
    lowered = str(text or "").lower()
    if token_hit(
        lowered,
        (
            "保存",
            "提交",
            "确认",
            "发布",
            "下架",
            "删除",
            "save",
            "submit",
            "commit",
            "confirm",
            "publish",
            "delete",
            "触发打分",
            "开始打分",
            "自动打分",
            "评分计算",
            "生成评分",
            "给出评分",
            "trigger score",
            "score calculation",
        ),
    ):
        return 60
    if token_hit(
        lowered,
        (
            "同步",
            "展示",
            "显示",
            "刷新",
            "生效",
            "评分结果",
            "打分结果",
            "综合评分",
            "sync",
            "display",
            "displayed",
            "show",
            "shows",
            "shown",
            "effective",
            "visible",
            "reflect",
            "reflects",
            "reflected",
            "downstream",
            "score result",
            "scoring result",
        ),
    ):
        return 70
    if contains_any_token(lowered, ("打开", "进入", "访问", "入口", "open", "enter", "entry")):
        return 10
    if contains_any_token(lowered, ("新增", "创建", "添加", "选择", "选课", "设置", "配置", "准备", "create", "add", "select", "set", "prepare", "prepared", "ready")):
        return 20
    if contains_any_token(lowered, ("编辑", "修改", "调整", "update", "edit", "modify")):
        return 30
    if contains_any_token(lowered, ("预览", "检查", "确认前", "preview", "review")):
        return 50
    if token_hit(
        lowered,
        (
            "保存",
            "提交",
            "确认",
            "发布",
            "下架",
            "删除",
            "save",
            "submit",
            "commit",
            "confirm",
            "publish",
            "delete",
            "触发打分",
            "开始打分",
            "自动打分",
            "评分计算",
            "生成评分",
            "给出评分",
            "trigger score",
            "score calculation",
        ),
    ):
        return 60
    if token_hit(lowered, ("同步", "展示", "显示", "刷新", "生效", "sync", "display", "displayed", "show", "shows", "shown", "effective", "visible", "reflect", "reflects", "reflected", "downstream")):
        return 70
    if contains_any_token(lowered, ("点击", "跳转", "学习", "查看", "click", "navigate", "learn", "view")):
        return 80
    return 90
