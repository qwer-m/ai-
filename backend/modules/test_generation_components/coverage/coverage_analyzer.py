from __future__ import annotations

import re
import unicodedata
from typing import Any


_STOPWORDS = {
    "以及",
    "或者",
    "并且",
    "如果",
    "那么",
    "需要",
    "可以",
    "必须",
    "系统",
    "模块",
    "页面",
    "用户",
    "功能",
    "流程",
    "规则",
}

_BOUNDARY_HINTS = {"边界", "上限", "下限", "最大", "最小", "临界", "范围", "boundary", "max", "min"}
_BOUNDARY_REQUIRED_HINTS = {
    "边界",
    "上限",
    "下限",
    "最大",
    "最小",
    "临界",
    "超过",
    "少于",
    "至少",
    "至多",
    "最多",
    "最少",
    "boundary",
    "max",
    "min",
}

_EXCEPTION_HINTS = {"异常", "失败", "错误", "拒绝", "超时", "fail", "error", "exception", "invalid"}
_RISK_HINTS = {"权限", "安全", "鉴权", "并发", "性能", "风控", "risk", "security", "permission", "performance"}

_RULE_ACTION_HINTS = (
    "新增",
    "调整",
    "插入",
    "后移",
    "保持",
    "保留",
    "隐藏",
    "显示",
    "展示",
    "支持",
    "点击",
    "返回",
    "切换",
    "播放",
    "打印",
    "适配",
    "不变",
    "不做改动",
    "只保留",
    "增加入口",
    "must",
    "should",
    "hide",
    "show",
    "display",
    "keep",
    "support",
)

_HEADING_PATTERNS = (
    r"^[一二三四五六七八九十]+[、.．]\s*[^：:]{1,24}$",
    r"^\d+[、.．]\s*[^：:]{1,24}$",
    r".*说明$",
    r".*调整说明$",
)

_GENERIC_NON_BLOCKING_RULES = {
    "页面布局与展示",
    "页面布局展示",
    "页面展示",
    "布局展示",
    "页面布局",
    "展示说明",
    "交互说明",
}

_OCR_CHAR_TRANSLATION = str.maketrans(
    {
        "⾼": "高",
        "⾸": "首",
        "⻚": "页",
        "⽂": "文",
        "⽣": "生",
        "⼊": "入",
        "⼝": "口",
        "⼆": "二",
        "⼀": "一",
        "⽬": "目",
        "⽤": "用",
        "⼾": "户",
        "⽀": "支",
        "⻓": "长",
        "⽅": "方",
        "⻅": "见",
        "⽇": "日",
        "⾃": "自",
        "⼒": "力",
        "⾄": "至",
        "⼼": "心",
        "⼯": "工",
        "⽆": "无",
        "⽹": "网",
    }
)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.translate(_OCR_CHAR_TRANSLATION)
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", normalized)
    normalized = normalized.replace("\u3000", " ")
    return normalized


def _looks_like_heading_or_fragment(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    if not normalized:
        return True
    if normalized.startswith("@"):
        return True
    if any(re.fullmatch(pattern, normalized) for pattern in _HEADING_PATTERNS):
        return True
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", normalized)
    if normalized.endswith(("如", "需", "核", "场", "内", "选", "按")):
        return True
    if len(tokens) <= 1 and not any(hint in normalized.lower() for hint in _RULE_ACTION_HINTS):
        return True
    return False


def _has_rule_action_signal(line: str) -> bool:
    normalized = _normalize_text(line).lower()
    return any(hint.lower() in normalized for hint in _RULE_ACTION_HINTS)


def _is_low_confidence_requirement_discussion(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    explicit_tokens = (
        "必须",
        "禁止",
        "不可",
        "不能",
        "应",
        "需要",
        "需",
        "固定",
        "只显示",
        "不显示",
        "隐藏",
        "展示",
        "显示",
        "支持",
        "保留",
        "不保留",
        "must",
        "should",
        "required",
        "forbid",
        "hide",
        "show",
        "display",
        "support",
        "keep",
    )
    has_explicit_signal = any(token in lowered for token in explicit_tokens)
    uncertain_tokens = (
        "是否",
        "如何",
        "怎么",
        "吗",
        "？",
        "?",
        "待确认",
        "暂不确定",
        "待定",
        "本期不做",
        "这一期不做",
        "这期不做",
        "不做",
        "可能",
        "可选",
        "看情况",
        "哈",
    )
    if "是否" in normalized and "已确认" not in normalized and "确认" not in normalized:
        return True
    if ("如何" in normalized or "怎么" in normalized) and "已确认" not in normalized and "确认" not in normalized:
        return True
    if any(token in normalized for token in uncertain_tokens) and not has_explicit_signal:
        return True
    if re.match(r"^[a-zA-Z]\s*[\.\)、)]", normalized) and re.search(
        r"(没有按照|问题|需调整|需要调整|结构.*调整)", normalized
    ):
        return True
    if normalized.startswith("这是") and not has_explicit_signal:
        return True
    if re.match(r"^[a-zA-Z]\s*[\.\)、)]", normalized) and not has_explicit_signal:
        return True
    return False


def _tokenize(text: str, limit: int = 18) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", _normalize_text(text))
    output: list[str] = []
    seen: set[str] = set()
    expanded: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            for idx in range(0, len(token) - 1):
                expanded.append(token[idx : idx + 2])
        else:
            expanded.append(token)
    for token in expanded:
        key = token.lower()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        output.append(token)
        if len(output) >= max(6, int(limit)):
            break
    return output


def _extract_rule_id(text: str) -> str | None:
    match = re.search(r"\bREQ[-_\s]?\d+\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0).upper().replace(" ", "")


def _classify_requirement_rule(rule_text: str) -> dict[str, Any]:
    """Classify extracted rules so diagnostics can keep context without over-blocking."""
    normalized = _normalize_text(rule_text).strip()
    lowered = normalized.lower()
    if normalized in _GENERIC_NON_BLOCKING_RULES:
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "generic_display_heading",
            "blocking": False,
            "non_blocking_reason": "generic_display_heading",
        }
    if any(re.fullmatch(pattern, normalized) for pattern in _HEADING_PATTERNS):
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "section_heading",
            "blocking": False,
            "non_blocking_reason": "section_heading",
        }
    if normalized.endswith((":", "：")) and not _extract_rule_id(normalized):
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "label_fragment",
            "blocking": False,
            "non_blocking_reason": "label_fragment",
        }
    if len(_tokenize(normalized, limit=8)) <= 1 and not _extract_rule_id(normalized):
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "short_fragment",
            "blocking": False,
            "non_blocking_reason": "short_fragment",
        }
    if "原型" in normalized and not any(token in lowered for token in ("必须", "需要", "固定", "禁止", "支持")):
        return {
            "rule_level": "soft",
            "confidence": "medium",
            "source_type": "prototype_reference",
            "blocking": False,
            "non_blocking_reason": "prototype_reference",
        }
    return {
        "rule_level": "hard",
        "confidence": "high" if _extract_rule_id(normalized) or _has_rule_action_signal(normalized) else "medium",
        "source_type": "confirmed_requirement",
        "blocking": True,
        "non_blocking_reason": "",
    }


def _extract_requirement_rules(requirement_context: str) -> list[dict[str, Any]]:
    """中文注释：解析 requirement_context，提取规则级条目（支持按 biz_key 分组文本）。"""
    text = _normalize_text(requirement_context).strip()
    if not text:
        return []

    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    current_biz_key = "unknown"

    for raw_line in text.splitlines():
        line = _normalize_text(raw_line).strip()
        if not line:
            continue
        biz_match = re.match(r"^###\s*biz_key:\s*([^\s（(]+)", line, flags=re.IGNORECASE)
        if biz_match:
            current_biz_key = biz_match.group(1).strip() or "unknown"
            continue
        if line.startswith("【"):
            continue
        normalized = re.sub(r"^\s*[-*•]\s*", "", line).strip()
        if len(normalized) < 4:
            continue
        if _looks_like_heading_or_fragment(normalized):
            continue
        if normalized.lower().startswith("biz_key:") or normalized.lower().startswith("test_module:"):
            continue
        if normalized.lower().startswith("priority:"):
            continue
        if _is_low_confidence_requirement_discussion(normalized):
            continue
        if not _has_rule_action_signal(normalized) and not _extract_rule_id(normalized):
            continue

        segments = [normalized]
        if len(re.findall(r"\bREQ[-_\s]?\d+\b", normalized, flags=re.IGNORECASE)) > 1:
            segments = [seg.strip() for seg in re.split(r"[。；;]+", normalized) if seg.strip()]

        for segment in segments:
            rule_id = _extract_rule_id(segment) or f"RULE-{len(rules) + 1:03d}"
            key = (rule_id, segment, current_biz_key)
            if key in seen:
                continue
            seen.add(key)
            rules.append(
                {
                    "rule_id": rule_id,
                    "rule_text": segment,
                    "biz_key": current_biz_key,
                    **_classify_requirement_rule(segment),
                }
            )

    if not rules:
        for sentence in re.split(r"[\n。；;]+", text):
            normalized = str(sentence or "").strip()
            if len(normalized) < 6:
                continue
            rule_id = _extract_rule_id(normalized) or f"RULE-{len(rules) + 1:03d}"
            key = (rule_id, normalized, "unknown")
            if key in seen:
                continue
            seen.add(key)
            rules.append(
                {
                    "rule_id": rule_id,
                    "rule_text": normalized,
                    "biz_key": "unknown",
                    **_classify_requirement_rule(normalized),
                }
            )
            if len(rules) >= 120:
                break

    return rules[:120]


def _flatten_case_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("id", "description", "test_module", "test_input", "expected_result"):
        value = case.get(key)
        if value:
            parts.append(str(value))
    for key in ("steps", "preconditions"):
        value = case.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif isinstance(value, str):
            parts.append(value)
    return _normalize_text("\n".join(parts))


def _flatten_case_intent_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("description", "test_module", "test_input", "expected_result"):
        value = case.get(key)
        if value:
            parts.append(str(value))
    steps = case.get("steps")
    if isinstance(steps, list):
        parts.extend(str(item) for item in steps if item)
    elif isinstance(steps, str):
        parts.append(steps)
    return _normalize_text("\n".join(parts))


_FLOW_STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = ()
_FLOW_STAGE_ORDER = [str(item.get("key") or "") for item in _FLOW_STAGE_DEFINITIONS]

_CROSS_CUTTING_DEFINITIONS: tuple[dict[str, Any], ...] = ()
_CROSS_CUTTING_ORDER = [str(item.get("key") or "") for item in _CROSS_CUTTING_DEFINITIONS]

_SCENARIO_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title_format", ("标题", "格式", "title")),
    ("statistics", ("统计", "总数", "数量", "正确数", "错误数", "count", "statistics")),
    ("filter_toggle", ("筛选", "过滤", "开关", "只看", "filter", "toggle")),
    ("empty_state", ("空状态", "暂无", "无记录", "无数据", "empty", "no data")),
    ("media_preview", ("图片", "照片", "预览", "缩略图", "滑动", "image", "preview")),
    ("load_failure", ("加载失败", "无法展示", "404", "超时", "load failed", "timeout")),
    ("source_consistency", ("来源", "标签", "配置", "source", "tag")),
    ("network_error", ("网络", "断网", "弱网", "network")),
    ("permission", ("权限", "授权", "permission")),
    ("save_delete", ("保存", "删除", "清空", "save", "delete", "clear")),
    ("manual_correction", ("手动", "判定", "修正", "更正", "manual", "correct")),
    ("feedback", ("反馈", "处理", "feedback")),
    ("workflow_navigation", ("第一步", "第二步", "第三步", "第四步", "上一步", "下一步", "开始", "完成", "step")),
    ("sorting_limit", ("排序", "降序", "升序", "最多", "前", "sort", "limit")),
    ("print_export", ("打印", "导出", "print", "export")),
    ("generation_trigger", ("生成", "自动生成", "触发", "generate", "trigger")),
    ("share", ("分享", "链接", "二维码", "H5", "share", "link")),
    ("readonly", ("只读", "隐藏编辑", "不可编辑", "readonly")),
    ("quota_limit", ("额度", "次数", "限制", "耗尽", "quota", "limit")),
    ("history_makeup", ("历史", "补做", "补学", "history", "makeup")),
)

_SCENARIO_PATTERNS += (
    ("critique_limit", ("\u540c\u4e00\u4e3b\u9898", "5\u6b21", "\u6279\u6539\u4e0a\u9650", "\u6b21\u6570", "critique limit")),
    ("star_rating", ("\u7efc\u5408\u70b9\u8bc4", "\u661f\u661f", "6\u5206\u5236", "star rating")),
    ("sentence_comment_jump", ("\u5206\u53e5\u70b9\u8bc4", "\u5212\u7ebf", "\u8df3\u8f6c", "\u5207\u6362", "sentence comment")),
    ("hot_recommend_entry", ("\u70ed\u95e8\u63a8\u8350", "\u5165\u53e3", "\u5c55\u793a", "hot recommendation")),
    ("secret_entry_list", ("\u5199\u4f5c\u79d8\u7c4d", "\u79d8\u7c4d", "\u5165\u53e3", "\u5217\u8868", "secret list")),
    ("pdf_download_content", ("pdf", "\u4e0b\u8f7d", "\u5185\u5bb9", "\u8d44\u6599", "download content")),
    ("essay_sample_numbering", ("\u4f18\u79c0\u8303\u6587", "\u591a\u7bc7", "\u5355\u7bc7", "\u5e8f\u53f7", "sample numbering")),
    ("essay_empty_state", ("\u6211\u7684\u4f5c\u6587\u7a7a\u72b6\u6001", "\u6682\u65e0\u4f5c\u6587", "\u7a7a\u72b6\u6001", "my essays empty")),
    ("submission_success_state", ("\u6295\u7a3f\u6210\u529f", "\u5ba1\u6838\u4e2d", "\u5f39\u7a97", "submission success")),
    ("delete_restore_unsubmitted", ("\u5220\u9664", "\u5df2\u53d1\u5e03", "\u672a\u6295\u7a3f", "\u6062\u590d", "delete restore")),
    ("featured_sorting", ("\u4f5c\u6587\u5708", "\u7cbe\u9009", "\u6392\u5e8f", "\u6743\u91cd", "featured sorting")),
    ("original_image_toggle", ("\u539f\u56fe", "\u663e", "\u9690", "\u6309\u94ae", "original image toggle")),
    ("community_empty_state", ("\u4f5c\u6587\u5708\u7a7a\u72b6\u6001", "\u4f5c\u6587\u5708\u6682\u65e0", "\u6682\u65e0\u4f5c\u6587", "\u65e0\u6570\u636e", "community empty")),
    ("full_text_copy", ("\u590d\u5236\u5168\u6587", "\u5168\u6587\u590d\u5236", "\u590d\u5236\u6210\u529f", "copy full text")),
    ("polish_original_compare", ("\u5168\u6587\u6da6\u8272", "\u539f\u6587\u5bf9\u6bd4", "\u5bf9\u6bd4\u663e\u793a", "polish original compare")),
    ("technique_practice_answer", ("\u6280\u6cd5\u5de9\u56fa\u7b54\u9898", "\u7b54\u9898\u7ed3\u679c", "\u7b54\u6848\u72b6\u6001", "technique practice")),
    ("category_sorting", ("\u8bed\u6587\u5206\u7c7b", "\u5206\u7c7b\u6392\u5e8f", "\u7c7b\u76ee\u6392\u5e8f", "category sorting")),
    ("upload_image_management", ("\u4e0a\u4f20\u56fe\u7247", "\u5220\u9664\u56fe\u7247", "\u62d6\u52a8", "\u7f29\u7565\u56fe", "upload image management")),
    ("essay_limit_20", ("\u6211\u7684\u4f5c\u658720\u6761", "20\u6761\u4e0a\u9650", "\u6700\u591a20", "essay limit 20")),
    ("secret_overlay", ("\u83b7\u5f97\u79d8\u7c4d", "\u8499\u5c42", "\u79d8\u7c4d\u5f39\u7a97", "secret overlay")),
)

_SPECIFIC_SCENARIO_KINDS = {
    "critique_limit",
    "star_rating",
    "sentence_comment_jump",
    "hot_recommend_entry",
    "secret_entry_list",
    "pdf_download_content",
    "essay_sample_numbering",
    "essay_empty_state",
    "submission_success_state",
    "delete_restore_unsubmitted",
    "featured_sorting",
    "original_image_toggle",
    "community_empty_state",
    "full_text_copy",
    "polish_original_compare",
    "technique_practice_answer",
    "category_sorting",
    "upload_image_management",
    "essay_limit_20",
    "secret_overlay",
}

_SPECIFIC_SCENARIO_PRECEDENCE = {
    "delete_restore_unsubmitted": 0,
    "submission_success_state": 0,
    "upload_image_management": 0,
    "essay_limit_20": 0,
    "community_empty_state": 0,
    "full_text_copy": 0,
    "polish_original_compare": 0,
    "technique_practice_answer": 0,
    "category_sorting": 0,
    "secret_overlay": 0,
}

_DEFAULT_SCENARIO_CAPS: dict[str, int] = {
    "title_format": 1,
    "statistics": 2,
    "filter_toggle": 1,
    "empty_state": 1,
    "media_preview": 1,
    "load_failure": 1,
    "source_consistency": 1,
    "network_error": 1,
    "permission": 1,
    "save_delete": 1,
    "feedback": 1,
    "sorting_limit": 1,
    "print_export": 1,
    "generation_trigger": 1,
    "share": 1,
    "readonly": 1,
    "quota_limit": 1,
    "history_makeup": 1,
    "manual_correction": 2,
    "workflow_navigation": 2,
    "critique_limit": 1,
    "star_rating": 1,
    "sentence_comment_jump": 1,
    "hot_recommend_entry": 1,
    "secret_entry_list": 1,
    "pdf_download_content": 1,
    "essay_sample_numbering": 1,
    "essay_empty_state": 1,
    "submission_success_state": 1,
    "delete_restore_unsubmitted": 1,
    "featured_sorting": 1,
    "original_image_toggle": 1,
    "community_empty_state": 1,
    "full_text_copy": 1,
    "polish_original_compare": 1,
    "technique_practice_answer": 1,
    "category_sorting": 1,
    "upload_image_management": 1,
    "essay_limit_20": 1,
    "secret_overlay": 1,
}

_SCENARIO_CAPS_BY_MODE: dict[str, dict[str, int]] = {
    "core_smoke": {
        "default": 1,
        "intent": 2,
        "toast": 2,
        "list": 2,
        "navigate": 2,
    },
    "standard_regression": {
        "default": 2,
        "workflow_navigation": 3,
        "media_preview": 2,
        "save_delete": 2,
        "sorting_limit": 2,
        "intent": 2,
    },
    "expanded_regression": {
        "default": 3,
        "workflow_navigation": 4,
        "media_preview": 3,
        "save_delete": 3,
        "filter_toggle": 3,
        "sorting_limit": 3,
        "statistics": 3,
        "feedback": 3,
        "intent": 3,
    },
    "full_functional_regression": {
        "default": 5,
        "workflow_navigation": 8,
        "media_preview": 5,
        "save_delete": 5,
        "sorting_limit": 4,
        "filter_toggle": 4,
        "statistics": 4,
        "feedback": 4,
        "critique_limit": 1,
        "star_rating": 1,
        "sentence_comment_jump": 1,
        "hot_recommend_entry": 1,
        "secret_entry_list": 1,
        "pdf_download_content": 1,
        "essay_sample_numbering": 1,
        "essay_empty_state": 1,
        "submission_success_state": 1,
        "delete_restore_unsubmitted": 1,
        "featured_sorting": 1,
        "original_image_toggle": 1,
        "community_empty_state": 1,
        "full_text_copy": 1,
        "polish_original_compare": 1,
        "technique_practice_answer": 1,
        "category_sorting": 1,
        "upload_image_management": 1,
        "essay_limit_20": 1,
        "secret_overlay": 1,
        "toast": 8,
        "list": 8,
        "navigate": 8,
        "intent": 5,
    },
}

_STAGE_SPLIT_RE = re.compile(r"\s*(?:->|=>|[\\/\|>:_\-—–／：])\s*")
_STAGE_TRAILING_NOISE_RE = re.compile(
    r"\s*(?:page|screen|view|module|panel|tab|section|list|detail|flow|workflow|"
    r"页面|页|模块|面板|区域|列表|详情|流程|验证)\s*$",
    re.IGNORECASE,
)
_INTENT_ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("submit", ("submit", "commit", "publish", "post", "提交", "投稿", "发布")),
    ("open", ("open", "enter", "view", "click", "tap", "打开", "进入", "查看", "点击")),
    ("switch", ("switch", "toggle", "filter", "select", "切换", "筛选", "选择")),
    ("delete", ("delete", "remove", "clear", "删除", "移除", "清空")),
    ("copy", ("copy", "复制")),
    ("upload", ("upload", "attach", "上传", "选择图片", "选择文件")),
    ("share", ("share", "link", "分享", "链接")),
    ("download", ("download", "export", "pdf", "下载", "导出")),
    ("sort", ("sort", "order", "rank", "排序")),
    ("edit", ("edit", "input", "modify", "填写", "编辑", "修改", "输入")),
)
_INTENT_OUTCOME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("toast", ("toast", "提示", "复制成功", "成功", "失败原因")),
    ("navigate", ("navigate", "redirect", "jump", "跳转", "返回", "进入")),
    ("dialog", ("dialog", "popup", "modal", "tips", "弹窗", "浮层", "蒙层")),
    ("status", ("status", "badge", "state", "状态", "标识", "红点", "置灰", "锁")),
    ("list", ("list", "empty", "列表", "缺省", "空状态")),
    ("content", ("display", "show", "render", "显示", "展示", "内容")),
    ("permission", ("permission", "auth", "member", "权限", "会员", "不可点击")),
    ("count", ("count", "quota", "limit", "数量", "次数", "上限")),
    ("sort", ("sort", "rank", "order", "排序", "权重")),
)
_INTENT_STOPWORDS = {
    "test",
    "case",
    "verify",
    "validation",
    "page",
    "module",
    "status",
    "button",
    "user",
    "system",
    "click",
    "view",
    "validate",
    "validation",
    "shown",
    "correctly",
    "correct",
    "recalculate",
    "recalculated",
    "after",
    "before",
    "current",
    "follows",
    "follow",
    "display",
    "displays",
    "shown",
    "show",
    "opens",
    "open",
    "page",
    "a",
    "an",
    "the",
    "are",
    "is",
    "visible",
    "filtering",
}
_COMPLEXITY_HINTS = (
    "同时",
    "分别",
    "全部",
    "所有",
    "以及",
    "并且",
    "且",
    "包含",
    "both",
    "all",
    "respectively",
    "and",
    "as well as",
)


def _keyword_position(text: str, keywords: tuple[str, ...]) -> int | None:
    positions = [text.find(keyword) for keyword in keywords if keyword and text.find(keyword) >= 0]
    return min(positions) if positions else None


def _keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    score = 0
    normalized = str(text or "")
    for keyword in keywords:
        if not keyword:
            continue
        if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_\s-]*", str(keyword)):
            hit = bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(keyword))}(?![A-Za-z0-9_])", normalized, flags=re.IGNORECASE))
        else:
            hit = str(keyword) in normalized
        if hit:
            score += max(1, min(4, len(keyword) // 2))
    return score


def _keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    normalized = str(text or "")
    count = 0
    for keyword in keywords:
        if not keyword:
            continue
        if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_\s-]*", str(keyword)):
            hit = bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(keyword))}(?![A-Za-z0-9_])", normalized, flags=re.IGNORECASE))
        else:
            hit = str(keyword) in normalized
        if hit:
            count += 1
    return count


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    normalized = str(text or "")
    return any(keyword and str(keyword) in normalized for keyword in keywords)


def _has_all(text: str, keywords: tuple[str, ...]) -> bool:
    normalized = str(text or "")
    return all(keyword and str(keyword) in normalized for keyword in keywords)


def _specific_scenario_matches(scenario_key: str, text: str, keywords: tuple[str, ...]) -> bool:
    normalized = str(text or "")
    if scenario_key == "original_image_toggle":
        return _has_any(normalized, ("原图", "original image"))
    if scenario_key == "featured_sorting":
        return _has_any(normalized, ("精选", "featured")) and _has_any(
            normalized,
            ("排序", "按权重", "权重S", "公式", "sort", "rank"),
        )
    if scenario_key == "upload_image_management":
        if _has_any(normalized, ("权限", "授权", "相机权限", "存储权限", "permission")):
            return False
        return _has_any(normalized, ("上传图片", "缩略图", "upload image")) and _has_any(
            normalized,
            ("删除图片", "拖动", "顺序", "数量", "上限", "保留", "thumbnail", "limit"),
        )
    if scenario_key == "submission_success_state":
        if _has_any(normalized, ("不进入成功", "不生成批改结果", "失败")):
            return False
        if _has_any(normalized, ("审核通过", "过审")) and not _has_any(normalized, ("投稿成功", "提交投稿", "submission success")):
            return False
        return (
            _has_any(normalized, ("投稿成功", "submission success"))
            or (_has_all(normalized, ("投稿", "成功")) and _has_any(normalized, ("弹窗", "审核中", "返回")))
            or (_has_all(normalized, ("提交", "成功")) and _has_any(normalized, ("投稿", "审核中", "弹窗")))
        )
    if scenario_key == "delete_restore_unsubmitted":
        return _has_any(normalized, ("删除", "delete")) and (
            _has_any(normalized, ("未投稿", "delete restore"))
            or (_has_any(normalized, ("已发布", "restore")) and _has_any(normalized, ("作品", "作文")))
        )
    if scenario_key == "hot_recommend_entry":
        return _has_any(normalized, ("热门推荐", "hot recommendation"))
    if scenario_key == "secret_entry_list":
        return _has_any(normalized, ("写作秘籍", "秘籍", "secret")) and _has_any(
            normalized,
            ("入口", "列表", "TAB", "分类", "list"),
        )
    if scenario_key == "pdf_download_content":
        return _has_any(normalized.lower(), ("pdf",)) and _has_any(normalized, ("下载", "资料", "download"))
    if scenario_key == "essay_sample_numbering":
        if _has_any(normalized.lower(), ("pdf",)):
            return False
        return _has_any(normalized, ("优秀范文", "sample")) and _has_any(normalized, ("多篇", "单篇", "序号", "numbering"))
    if scenario_key == "community_empty_state":
        return _has_any(normalized, ("作文圈", "community")) and _has_any(normalized, ("空状态", "缺省", "暂无", "无数据", "empty"))
    if scenario_key == "full_text_copy":
        return _has_any(normalized, ("复制全文", "全文复制", "copy full text"))
    if scenario_key == "secret_overlay":
        return _has_any(normalized, ("秘籍", "secret")) and _has_any(normalized, ("蒙层", "弹窗", "overlay"))
    if scenario_key == "sentence_comment_jump":
        return _has_any(normalized, ("分句点评", "sentence comment")) and _has_any(normalized, ("划线", "跳转", "jump"))
    if scenario_key == "critique_limit":
        return _has_any(normalized, ("批改上限", "最多批改", "已批改5", "次数已用完", "达到5次", "critique limit"))
    return _keyword_score(normalized, keywords) >= 4 or _keyword_hit_count(normalized, keywords) >= 2


_CROSS_CUTTING_HINTS = (
    "异常",
    "例外",
    "权限",
    "额度",
    "限制",
    "历史",
    "补做",
    "补学",
    "全局",
    "规则",
    "风险",
    "exception",
    "permission",
    "quota",
    "limit",
    "history",
    "global",
    "rule",
    "risk",
)

_DATA_FLOW_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entry_capture", ("拍照", "拍摄", "上传", "采集", "识别", "批改", "入口", "capture", "upload", "import", "entry")),
    ("review_confirm", ("复核", "审核", "审批", "确认", "修正", "review", "approve", "confirm", "correct")),
    ("artifact_list", ("习题本", "错题本", "题本", "列表", "workbook", "notebook", "dashboard", "list")),
    ("artifact_detail", ("详情", "解析", "答案", "detail", "answer", "analysis")),
    ("learning_plan", ("提升计划", "学习计划", "方案", "课程", "看视频", "切片", "plan", "course", "lesson", "slice")),
    ("completion_summary", ("完成", "复盘", "成果", "汇总", "summary", "complete", "completion")),
    ("report", ("报告", "成长报告", "周报", "分享", "report", "share")),
    ("access_limit", ("额度", "权限", "拦截", "次数", "quota", "permission", "limit", "gate")),
    ("global_exception", ("全局", "异常", "空状态", "无数据", "exception", "global", "empty")),
    ("history_makeup", ("历史", "补做", "补学", "history", "makeup")),
)

_DATA_FLOW_PHASE_RANK = {phase: index for index, (phase, _tokens) in enumerate(_DATA_FLOW_PHASES)}
_DATA_FLOW_PHASE_TIE_PRIORITY = {
    "review_confirm": 0,
    "entry_capture": 1,
    "artifact_list": 2,
    "artifact_detail": 3,
    "learning_plan": 4,
    "completion_summary": 5,
    "report": 6,
    "access_limit": 7,
    "global_exception": 8,
    "history_makeup": 9,
}
_DATA_FLOW_CROSS_CUTTING_PHASES = {"access_limit", "global_exception", "history_makeup"}


def _dedupe_stage_keys(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _data_flow_phase_for_label(label: str) -> str:
    lowered = str(label or "").strip().lower()
    if not lowered:
        return ""
    matches: list[tuple[int, int, str]] = []
    for phase, tokens in _DATA_FLOW_PHASES:
        score = sum(len(str(token)) for token in tokens if str(token).lower() in lowered)
        if score > 0:
            matches.append((int(score), -int(_DATA_FLOW_PHASE_TIE_PRIORITY.get(phase, 99)), phase))
    if not matches:
        return ""
    matches.sort(reverse=True)
    return matches[0][2]


def _apply_data_flow_order_to_outline(outline: dict[str, Any]) -> dict[str, Any]:
    flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = [str(item) for item in (outline.get("cross_cutting") or []) if str(item).strip()]
    flow_labels = dict(outline.get("flow_labels") or {})
    cross_labels = dict(outline.get("cross_cutting_labels") or {})
    stage_phase: dict[str, str] = {}
    retained_flow: list[str] = []
    moved_to_cross: list[str] = []
    for key in flow_order:
        label = str(flow_labels.get(key) or key)
        phase = _data_flow_phase_for_label(label)
        if phase:
            stage_phase[key] = phase
        if phase in _DATA_FLOW_CROSS_CUTTING_PHASES:
            moved_to_cross.append(key)
            cross_labels.setdefault(key, label)
            continue
        retained_flow.append(key)
    matched = [key for key in retained_flow if stage_phase.get(key)]
    if len(matched) >= 2:
        ordered = sorted(
            enumerate(retained_flow),
            key=lambda item: (
                _DATA_FLOW_PHASE_RANK.get(stage_phase.get(item[1]) or "", 10_000),
                item[0],
            ),
        )
        flow_order = [key for _index, key in ordered]
    else:
        flow_order = retained_flow
    cross_cutting = _dedupe_stage_keys([*cross_cutting, *moved_to_cross])
    edges = [
        {
            "from": left,
            "to": right,
            "from_label": str(flow_labels.get(left) or left),
            "to_label": str(flow_labels.get(right) or right),
        }
        for left, right in zip(flow_order, flow_order[1:])
    ]
    return {
        **outline,
        "flow_order": flow_order,
        "cross_cutting": cross_cutting,
        "cross_cutting_labels": cross_labels,
        "data_flow_edges": edges,
        "data_flow_phase_rank": stage_phase,
        "data_flow_order_applied": bool(flow_order != [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()] or moved_to_cross),
    }


def _compact_stage_label(label: str) -> str:
    cleaned = _normalize_text(label).strip()
    cleaned = re.sub(r"^\s*(?:#{1,6}\s*)?", "", cleaned)
    cleaned = re.sub(r"^\s*(?:[一二三四五六七八九十百]+|\d+|[A-Za-z])[\.\、\)\）\-\s]+", "", cleaned)
    cleaned = re.sub(r"[:：]\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:40]


def _canonical_stage_label(label: str) -> str:
    cleaned = _compact_stage_label(label)
    cleaned = re.sub(r"[\(\（\[].*?[\)\）\]]", "", cleaned).strip()
    parts = [part.strip() for part in _STAGE_SPLIT_RE.split(cleaned) if part and part.strip()]
    if len(parts) > 1 and len(parts[0]) >= 2:
        cleaned = parts[0]
    cleaned = _STAGE_TRAILING_NOISE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:40] or _compact_stage_label(label)


def _stage_key_from_label(label: str, index: int) -> str:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "_", _normalize_text(_canonical_stage_label(label)).strip().lower()).strip("_")
    if not compact:
        compact = f"stage_{index:03d}"
    return f"stage:{compact[:48]}"


def _looks_like_section_heading(line: str) -> bool:
    text = _normalize_text(line).strip()
    if not text:
        return False
    if len(text) > 80:
        return False
    if re.match(r"^\s*#{1,6}\s+\S+", text):
        return True
    if re.match(r"^\s*(?:[一二三四五六七八九十百]+|\d+|[A-Za-z])[\.\、\)\）\-\s]+.{2,40}$", text):
        return True
    if text.endswith((":", "：")) and len(text) <= 50:
        return True
    return False


def _extract_requirement_sections(requirement_context: str) -> list[dict[str, Any]]:
    text = _normalize_text(requirement_context)
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw_line in enumerate(text.splitlines()):
        line = _normalize_text(raw_line).strip()
        if not _looks_like_section_heading(line):
            continue
        label = _compact_stage_label(line)
        if len(label) < 2 or label.lower() in seen:
            continue
        seen.add(label.lower())
        sections.append(
            {
                "key": _stage_key_from_label(label, len(sections) + 1),
                "label": label,
                "position": int(position),
                "source": "requirement_heading",
            }
        )
        if len(sections) >= 24:
            break
    return sections


def _extract_case_module_stages(requirement_context: str, cases: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    text = _normalize_text(requirement_context)
    modules: list[str] = []
    seen: set[str] = set()
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        module = _canonical_stage_label(str(case.get("test_module") or ""))
        if len(module) < 2:
            continue
        key = module.lower()
        if key in seen:
            continue
        seen.add(key)
        modules.append(module)
    stages: list[dict[str, Any]] = []
    for index, module in enumerate(modules, start=1):
        position = text.find(module)
        stages.append(
            {
                "key": _stage_key_from_label(module, index),
                "label": module,
                "position": int(position if position >= 0 else 100000 + index),
                "source": "case_module",
            }
        )
    stages.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("label") or "")))
    return stages


def _split_cross_cutting(stages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flow: list[dict[str, Any]] = []
    cross: list[dict[str, Any]] = []
    for stage in stages:
        label = str(stage.get("label") or "")
        lowered = label.lower()
        if any(hint.lower() in lowered for hint in _CROSS_CUTTING_HINTS):
            cross.append(stage)
        else:
            flow.append(stage)
    return flow, cross


def _extract_profile_flow_outline(project_profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(project_profile, dict):
        return None
    try:
        confidence = float(project_profile.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    outline = project_profile.get("flow_outline")
    if not isinstance(outline, dict):
        return None
    flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = [str(item) for item in (outline.get("cross_cutting") or []) if str(item).strip()]
    if confidence < 0.2 or (not flow_order and not cross_cutting):
        return None
    normalized = dict(outline)
    normalized["source"] = str(project_profile.get("profile_source") or outline.get("source") or "project_profile")
    normalized["flow_order"] = flow_order
    normalized["cross_cutting"] = cross_cutting
    normalized["flow_labels"] = dict(outline.get("flow_labels") or {})
    normalized["cross_cutting_labels"] = dict(outline.get("cross_cutting_labels") or {})
    normalized["profile_confidence"] = confidence
    return _apply_data_flow_order_to_outline(normalized)


def extract_flow_outline(
    requirement_context: str,
    cases: list[dict[str, Any]] | None = None,
    *,
    project_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract a coarse product flow outline from the requirement document itself."""
    profile_outline = _extract_profile_flow_outline(project_profile)
    if profile_outline is not None:
        return profile_outline
    text = _normalize_text(requirement_context)
    found: list[dict[str, Any]] = _extract_case_module_stages(text, cases)
    if not found:
        found = _extract_requirement_sections(text)
    for definition in _FLOW_STAGE_DEFINITIONS:
        position = _keyword_position(text, tuple(definition.get("keywords") or ()))
        if position is None:
            continue
        found.append(
            {
                "key": str(definition.get("key") or ""),
                "label": str(definition.get("label") or ""),
                "position": int(position),
            }
        )
    found.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("key") or "")))
    found_by_key: dict[str, dict[str, Any]] = {}
    for item in found:
        key = str(item.get("key") or "")
        if key and key not in found_by_key:
            found_by_key[key] = item
    ordered_found = list(found_by_key.values())
    if _FLOW_STAGE_ORDER:
        canonical_found = [found_by_key[key] for key in _FLOW_STAGE_ORDER if key in found_by_key]
        canonical_found.extend([item for item in ordered_found if str(item.get("key") or "") not in _FLOW_STAGE_ORDER])
    else:
        canonical_found = ordered_found

    cross_cutting: list[dict[str, Any]] = []
    for definition in _CROSS_CUTTING_DEFINITIONS:
        position = _keyword_position(text, tuple(definition.get("keywords") or ()))
        if position is None:
            continue
        cross_cutting.append(
            {
                "key": str(definition.get("key") or ""),
                "label": str(definition.get("label") or ""),
                "position": int(position),
            }
        )
    flow_stages, inferred_cross_cutting = _split_cross_cutting(canonical_found)
    cross_cutting.extend(inferred_cross_cutting)
    cross_cutting.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("key") or "")))

    return _apply_data_flow_order_to_outline({
        "source": "requirement_keyword_positions" if found else "no_flow_keywords_detected",
        "flow_order": [item["key"] for item in flow_stages],
        "document_flow_order": [item["key"] for item in found],
        "flow_labels": {item["key"]: item["label"] for item in flow_stages},
        "flow_stage_positions": {item["key"]: item["position"] for item in found},
        "cross_cutting": [item["key"] for item in cross_cutting],
        "cross_cutting_labels": {item["key"]: item["label"] for item in cross_cutting},
    })


def classify_case_flow_stage(case: dict[str, Any], flow_outline: dict[str, Any] | None = None) -> str:
    text = _flatten_case_text(case)
    case_module_stage = _canonical_stage_label(str(case.get("test_module") or ""))
    if isinstance(flow_outline, dict):
        candidates: list[tuple[int, int, str]] = []
        labels = dict(flow_outline.get("flow_labels") or {})
        for index, key in enumerate(flow_outline.get("flow_order") or []):
            label = str(labels.get(key) or "")
            aliases = tuple(
                item
                for item in {label, _canonical_stage_label(label), str(key).split(":", 1)[-1].replace("_", " ")}
                if item
            )
            score = _keyword_score(text, aliases) if aliases else 0
            if case_module_stage and case_module_stage == _canonical_stage_label(label):
                score += 8
            if score > 0:
                candidates.append((score, -index, str(key)))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][2] or "unknown"
    scored: list[tuple[int, int, str]] = []
    for index, definition in enumerate(_FLOW_STAGE_DEFINITIONS):
        score = _keyword_score(text, tuple(definition.get("keywords") or ()))
        if score > 0:
            scored.append((score, -index, str(definition.get("key") or "")))
    if not scored:
        return "unknown"
    scored.sort(reverse=True)
    return scored[0][2] or "unknown"


def classify_case_cross_cutting(case: dict[str, Any], flow_outline: dict[str, Any] | None = None) -> list[str]:
    text = _flatten_case_text(case)
    hits: list[str] = []
    if isinstance(flow_outline, dict):
        labels = dict(flow_outline.get("cross_cutting_labels") or {})
        for key in flow_outline.get("cross_cutting") or []:
            label = str(labels.get(key) or "")
            if label and _keyword_score(text, (label,)) > 0:
                hits.append(str(key))
    for definition in _CROSS_CUTTING_DEFINITIONS:
        score = _keyword_score(text, tuple(definition.get("keywords") or ()))
        if score > 0:
            hits.append(str(definition.get("key") or ""))
    return [item for item in hits if item]


def _first_keyword_label(text: str, patterns: tuple[tuple[str, tuple[str, ...]], ...], default: str) -> str:
    lowered = _normalize_text(text).lower()
    for label, keywords in patterns:
        if any(str(keyword or "").lower() in lowered for keyword in keywords if str(keyword or "").strip()):
            return label
    return default


def _case_intent_parts(case: dict[str, Any]) -> tuple[str, str, str]:
    description = str(case.get("description") or "")
    module = _canonical_stage_label(str(case.get("test_module") or ""))
    expected = str(case.get("expected_result") or "")
    steps = " ".join(str(item) for item in (case.get("steps") or []) if str(item).strip()) if isinstance(case.get("steps"), list) else ""
    intent_text = "\n".join([module, description, steps, expected])
    action = _first_keyword_label(intent_text, _INTENT_ACTION_KEYWORDS, "observe")
    outcome = _first_keyword_label(expected or intent_text, _INTENT_OUTCOME_KEYWORDS, "content")
    object_tokens = [
        token.lower()
        for token in _tokenize("\n".join([module, description, expected]), limit=12)
        if token.lower() not in _INTENT_STOPWORDS
        and token.lower() not in {action, outcome}
        and len(token.strip()) >= 2
    ]
    compact_object = "_".join(object_tokens[:3]) or "general"
    return action, compact_object, outcome


def classify_case_scenario_key(case: dict[str, Any], flow_stage: str | None = None) -> str:
    text = _flatten_case_text(case)
    intent_text = _flatten_case_intent_text(case)
    stage = str(flow_stage or "unknown")
    action, compact_object, outcome = _case_intent_parts(case)
    specific_patterns = [
        (scenario_key, keywords)
        for scenario_key, keywords in _SCENARIO_PATTERNS
        if scenario_key in _SPECIFIC_SCENARIO_KINDS
    ]
    specific_patterns.sort(key=lambda item: (_SPECIFIC_SCENARIO_PRECEDENCE.get(item[0], 10), item[0]))
    generic_patterns = [
        (scenario_key, keywords)
        for scenario_key, keywords in _SCENARIO_PATTERNS
        if scenario_key not in _SPECIFIC_SCENARIO_KINDS
    ]
    for scenario_key, keywords in [*specific_patterns, *generic_patterns]:
        if scenario_key in _SPECIFIC_SCENARIO_KINDS:
            # Specific domain clusters are intentionally global, so a single broad
            # keyword from preconditions or shared setup text is too weak to group
            # cases. Require multiple intent-text hits or one strong phrase.
            if _specific_scenario_matches(scenario_key, intent_text, keywords):
                return f"global:{scenario_key}"
            continue
        if _keyword_score(text, keywords) > 0:
            return f"{stage}:{scenario_key}:obj:{compact_object}"
    tokens = _tokenize(
        "\n".join(
            [
                str(case.get("test_module") or ""),
                str(case.get("description") or ""),
                str(case.get("expected_result") or ""),
            ]
        ),
        limit=8,
    )
    token_key = "_".join(token.lower() for token in tokens[:6])
    return f"{stage}:semantic:{token_key or 'unknown'}"


def classify_case_intent_signature(case: dict[str, Any], flow_stage: str | None = None) -> str:
    """Build a coarse, product-agnostic action/object/outcome signature for duplicate detection."""
    stage = str(flow_stage or "unknown")
    action, compact_object, outcome = _case_intent_parts(case)
    return f"{stage}:intent:{action}:{compact_object}:{outcome}"


def case_complexity_profile(case: dict[str, Any]) -> dict[str, Any]:
    steps = case.get("steps")
    step_count = len([item for item in steps if str(item).strip()]) if isinstance(steps, list) else 0
    expected = _normalize_text(str(case.get("expected_result") or ""))
    description = _normalize_text(str(case.get("description") or ""))
    punctuation_parts = len([part for part in re.split(r"[;；。.!?？]|(?:\s+and\s+)", expected) if part.strip()])
    comma_parts = len([part for part in re.split(r"[，,、]", expected) if part.strip()])
    hint_hits = [hint for hint in _COMPLEXITY_HINTS if hint and hint.lower() in f"{description}\n{expected}".lower()]
    score = 0
    reasons: list[str] = []
    if step_count > 5:
        score += step_count - 5
        reasons.append("too_many_steps")
    if punctuation_parts >= 4 or comma_parts >= 5:
        score += 2
        reasons.append("many_expected_clauses")
    if len(hint_hits) >= 2:
        score += 1
        reasons.append("multi_assertion_language")
    if len(expected) > 220:
        score += 1
        reasons.append("long_expected_result")
    return {
        "step_count": int(step_count),
        "expected_clause_count": int(max(punctuation_parts, comma_parts)),
        "complexity_score": int(score),
        "complexity_reasons": reasons,
        "is_complex_multi_assertion": bool(score >= 3),
    }


def analyze_case_structure(
    requirement_context: str,
    cases: list[dict[str, Any]],
    *,
    project_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Annotate candidate cases with flow-stage, scenario-cluster and ordering diagnostics."""
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    flow_outline = extract_flow_outline(requirement_context, normalized_cases, project_profile=project_profile)
    flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item)]
    flow_labels = dict(flow_outline.get("flow_labels") or {})
    flow_rank = {stage: index for index, stage in enumerate(flow_order)}

    rows: list[dict[str, Any]] = []
    scenario_groups: dict[str, list[int]] = {}
    intent_groups: dict[str, list[int]] = {}
    stage_breakdown: dict[str, int] = {}
    max_seen_rank = -1

    for index, case in enumerate(normalized_cases, start=1):
        stage = classify_case_flow_stage(case, flow_outline)
        cross_cutting = classify_case_cross_cutting(case, flow_outline)
        scenario_key = classify_case_scenario_key(case, stage)
        intent_signature = classify_case_intent_signature(case, stage)
        duplicate_group_key = intent_signature if ":semantic:" in scenario_key and intent_signature else scenario_key
        complexity = case_complexity_profile(case)
        rank = flow_rank.get(stage)
        has_explicit_execution_sequence = case.get("execution_sequence") not in (None, "")
        misordered = bool(
            not has_explicit_execution_sequence
            and rank is not None
            and rank < max_seen_rank
        )
        if rank is not None and not has_explicit_execution_sequence:
            max_seen_rank = max(max_seen_rank, int(rank))
        stage_breakdown[stage] = int(stage_breakdown.get(stage, 0)) + 1
        scenario_groups.setdefault(scenario_key, []).append(index)
        intent_groups.setdefault(intent_signature, []).append(index)
        rows.append(
            {
                "candidate_index": int(index),
                "case_id": str(case.get("id") or ""),
                "flow_stage": stage,
                "flow_stage_label": str(flow_labels.get(stage) or stage),
                "flow_rank": int(rank) if rank is not None else None,
                "cross_cutting": cross_cutting,
                "scenario_key": scenario_key,
                "intent_signature": intent_signature,
                "duplicate_group_key": duplicate_group_key,
                **complexity,
                "misordered_against_requirement_flow": misordered,
            }
        )

    duplicate_clusters: list[dict[str, Any]] = []
    row_by_index = {int(row["candidate_index"]): row for row in rows}
    grouped_candidates: list[tuple[str, str, list[int]]] = [
        ("scenario", key, value) for key, value in scenario_groups.items()
    ]
    grouped_candidates.extend(("intent", key, value) for key, value in intent_groups.items())
    seen_cluster_sets: set[tuple[int, ...]] = set()
    for scenario_key, group_type, indices in [
        (key, kind, value)
        for kind, key, value in sorted(
            grouped_candidates,
            key=lambda item: (item[2][0], 0 if item[0] == "scenario" else 1, item[1]),
        )
    ]:
        if len(indices) <= 1:
            continue
        index_tuple = tuple(int(item) for item in indices)
        if index_tuple in seen_cluster_sets:
            continue
        seen_cluster_sets.add(index_tuple)
        cluster_id = f"SC-{len(duplicate_clusters) + 1:03d}"
        first_index = int(indices[0])
        first_row = row_by_index.get(first_index) or {}
        duplicate_of = str(first_row.get("case_id") or f"candidate:{first_index}")
        for idx in indices:
            row = row_by_index.get(int(idx))
            if not row:
                continue
            row["duplicate_cluster_id"] = cluster_id
            row["duplicate_cluster_size"] = int(len(indices))
            if int(idx) != first_index:
                row["duplicate_of_case_id"] = duplicate_of
                row["is_scenario_duplicate"] = True
            else:
                row["duplicate_of_case_id"] = ""
                row["is_scenario_duplicate"] = False
        duplicate_clusters.append(
            {
                "cluster_id": cluster_id,
                "scenario_key": scenario_key,
                "group_type": group_type,
                "size": int(len(indices)),
                "first_case_id": duplicate_of,
                "candidate_indices": [int(item) for item in indices],
            }
        )

    for row in rows:
        row.setdefault("duplicate_cluster_id", "")
        row.setdefault("duplicate_cluster_size", 0)
        row.setdefault("duplicate_of_case_id", "")
        row.setdefault("is_scenario_duplicate", False)

    covered_flow_stages = {str(row.get("flow_stage") or "") for row in rows}
    missing_flow_stages = [stage for stage in flow_order if stage not in covered_flow_stages]
    return {
        "flow_outline": flow_outline,
        "rows": rows,
        "stage_breakdown": stage_breakdown,
        "missing_flow_stages": missing_flow_stages,
        "missing_flow_stage_count": int(len(missing_flow_stages)),
        "misordered_count": int(sum(1 for row in rows if bool(row.get("misordered_against_requirement_flow")))),
        "duplicate_clusters": duplicate_clusters[:50],
        "duplicate_cluster_count": int(len(duplicate_clusters)),
        "duplicate_case_count": int(sum(max(0, int(cluster.get("size") or 0) - 1) for cluster in duplicate_clusters)),
    }


def _priority_score(value: Any) -> int:
    priority = str(value or "").strip().upper()
    if priority == "P0":
        return 3
    if priority == "P1":
        return 2
    if priority == "P2":
        return 1
    return 0


def _case_value_score(case: dict[str, Any], original_index: int) -> tuple[int, int, int, int, int, int]:
    steps = case.get("steps")
    step_count = len([item for item in steps if str(item).strip()]) if isinstance(steps, list) else 0
    text_len = len(str(case.get("description") or "")) + len(str(case.get("expected_result") or ""))
    preconditions = case.get("preconditions")
    precondition_count = len([item for item in preconditions if str(item).strip()]) if isinstance(preconditions, list) else 0
    complexity_score = int(case_complexity_profile(case).get("complexity_score") or 0)
    return (
        _priority_score(case.get("priority_final") or case.get("priority")),
        -min(complexity_score, 8),
        min(step_count, 8),
        min(precondition_count, 6),
        min(text_len, 400),
        -int(original_index),
    )


def _scenario_kind_from_key(scenario_key: str) -> str:
    value = str(scenario_key or "")
    parts = [part for part in value.split(":") if part]
    known_kinds = set(_DEFAULT_SCENARIO_CAPS) | {"intent", "semantic", "toast", "list", "navigate"}
    for part in parts:
        if part in known_kinds:
            return part
    return parts[-1] if parts else value


def _scenario_max_keep(
    scenario_key: str,
    *,
    default_max: int,
    project_profile: dict[str, Any] | None = None,
) -> int:
    kind = _scenario_kind_from_key(scenario_key)
    policy: dict[str, Any] = {}
    if isinstance(project_profile, dict) and isinstance(project_profile.get("scenario_cluster_policy"), dict):
        policy = dict(project_profile.get("scenario_cluster_policy") or {})
    if bool(policy.get("disable_scenario_pruning")):
        return 1_000_000
    caps = policy.get("scenario_caps") if isinstance(policy.get("scenario_caps"), dict) else {}
    try:
        if kind in caps:
            return max(1, int(caps.get(kind) or 1))
    except Exception:
        pass
    mode = str(policy.get("coverage_mode") or policy.get("generation_coverage_mode") or "").strip()
    mode_caps = _SCENARIO_CAPS_BY_MODE.get(mode) or {}
    if kind in mode_caps:
        return max(1, int(mode_caps.get(kind) or 1))
    if mode_caps:
        return max(1, int(mode_caps.get("default") or default_max or 2))
    if kind in _DEFAULT_SCENARIO_CAPS:
        return max(1, int(_DEFAULT_SCENARIO_CAPS[kind]))
    return max(1, int(default_max or 2))


def _scenario_policy(project_profile: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(project_profile, dict) and isinstance(project_profile.get("scenario_cluster_policy"), dict):
        return dict(project_profile.get("scenario_cluster_policy") or {})
    return {}


def _renumber_cases(cases: list[dict[str, Any]], start_id: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    current = int(start_id or 1)
    for case in cases:
        if not isinstance(case, dict):
            continue
        item = dict(case)
        item["id"] = f"TC-{current:03d}"
        current += 1
        output.append(item)
    return output


def govern_cases_by_flow_structure(
    requirement_context: str,
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    max_per_scenario: int = 2,
    project_profile: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply light final ordering and scenario-cluster pruning from flow diagnostics."""
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    if not normalized_cases:
        return [], {
            "applied": False,
            "reason": "empty_cases",
            "scenario_duplicate_pruned_count": 0,
            "flow_reordered": False,
        }

    structure = analyze_case_structure(requirement_context, normalized_cases, project_profile=project_profile)
    flow_outline = dict(structure.get("flow_outline") or {})
    flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item)]
    cross_order = [str(item) for item in (flow_outline.get("cross_cutting") or []) if str(item)]
    if not flow_order and not cross_order:
        return (
            _renumber_cases(normalized_cases, start_id) if renumber_ids else normalized_cases,
            {
                "applied": False,
                "reason": "no_flow_outline",
                "scenario_duplicate_pruned_count": 0,
                "flow_reordered": False,
            },
        )

    rows = [dict(item) for item in (structure.get("rows") or []) if isinstance(item, dict)]
    row_by_index = {int(row.get("candidate_index") or 0): row for row in rows}
    drop_indices: set[int] = set()
    cap_policy_used: dict[str, int] = {}
    scenario_policy = _scenario_policy(project_profile)
    disable_category_pruning = bool(scenario_policy.get("disable_scenario_pruning"))
    intent_duplicate_cap = max(1, int(scenario_policy.get("intent_duplicate_cap") or 1))
    duplicate_clusters = [dict(item) for item in (structure.get("duplicate_clusters") or []) if isinstance(item, dict)]
    for cluster in duplicate_clusters:
        scenario_key = str(cluster.get("scenario_key") or "")
        if ":semantic:" in scenario_key:
            continue
        group_type = str(cluster.get("group_type") or "scenario")
        indices = [int(item) for item in (cluster.get("candidate_indices") or []) if int(item or 0) > 0]
        if disable_category_pruning and group_type != "intent":
            continue
        max_keep = intent_duplicate_cap if group_type == "intent" else _scenario_max_keep(
                scenario_key,
                default_max=max_per_scenario,
                project_profile=project_profile,
            )
        cap_policy_used[_scenario_kind_from_key(scenario_key)] = int(max_keep)
        if len(indices) <= max_keep:
            continue
        ranked = sorted(
            indices,
            key=lambda idx: _case_value_score(normalized_cases[idx - 1], idx),
            reverse=True,
        )
        drop_indices.update(ranked[max_keep:])

    kept_pairs = [
        (index, case)
        for index, case in enumerate(normalized_cases, start=1)
        if index not in drop_indices
    ]
    flow_rank = {stage: idx for idx, stage in enumerate(flow_order)}
    cross_rank = {stage: idx for idx, stage in enumerate(cross_order)}
    stage_base = len(flow_rank)

    def _sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int, str]:
        index, case = pair
        row = row_by_index.get(index) or {}
        stage = str(row.get("flow_stage") or "unknown")
        crosses = [str(item) for item in (row.get("cross_cutting") or []) if str(item)]
        module = str(case.get("test_module") or "")
        primary_cross = ""
        for cross in crosses:
            label = str((flow_outline.get("cross_cutting_labels") or {}).get(cross) or "")
            if label and label in module:
                primary_cross = cross
                break
        if primary_cross:
            group = stage_base + cross_rank.get(primary_cross, len(cross_rank))
        elif stage in flow_rank:
            group = flow_rank[stage]
        elif crosses:
            group = stage_base + min(cross_rank.get(cross, len(cross_rank)) for cross in crosses)
        else:
            group = stage_base + len(cross_rank) + 1
        return (int(group), int(row.get("flow_rank") or 9999), int(index), str(row.get("scenario_key") or ""))

    ordered_pairs = sorted(kept_pairs, key=_sort_key)
    ordered_cases = [dict(case) for _index, case in ordered_pairs]
    if renumber_ids:
        ordered_cases = _renumber_cases(ordered_cases, start_id)

    original_order = [int(index) for index, _case in kept_pairs]
    new_order = [int(index) for index, _case in ordered_pairs]
    return ordered_cases, {
        "applied": True,
        "flow_reordered": bool(original_order != new_order),
        "flow_order": flow_order,
        "cross_cutting_order": cross_order,
        "scenario_duplicate_pruned_count": int(len(drop_indices)),
        "scenario_duplicate_pruned_indices": sorted(drop_indices)[:100],
        "scenario_cap_policy": cap_policy_used,
        "scenario_duplicate_cluster_count": int(structure.get("duplicate_cluster_count") or 0),
        "flow_misordered_count_before": int(structure.get("misordered_count") or 0),
        "missing_flow_stage_count": int(structure.get("missing_flow_stage_count") or 0),
    }


def _detect_case_types(case_text: str) -> set[str]:
    lowered = _normalize_text(case_text).lower()
    types: set[str] = set()
    if any(keyword in lowered for keyword in _BOUNDARY_HINTS):
        types.add("boundary")
    if any(keyword in lowered for keyword in _EXCEPTION_HINTS):
        types.add("exception")
    if any(keyword in lowered for keyword in _RISK_HINTS):
        types.add("risk")
    if not types:
        types.add("happy")
    else:
        types.add("happy")
    return types


def _required_types_for_rule(rule_text: str) -> set[str]:
    lowered = _normalize_text(rule_text).lower()
    required = {"happy"}
    if any(keyword in lowered for keyword in _BOUNDARY_REQUIRED_HINTS):
        required.add("boundary")
    if any(keyword in lowered for keyword in _EXCEPTION_HINTS):
        required.add("exception")
    if any(keyword in lowered for keyword in _RISK_HINTS):
        required.add("risk")
    return required


def _is_rule_hit(rule: dict[str, Any], case_text: str) -> bool:
    lowered_case = _normalize_text(case_text).lower()
    rule_id = str(rule.get("rule_id") or "").strip().lower().replace(" ", "")
    rule_text = _normalize_text(str(rule.get("rule_text") or "")).strip()
    if rule_id and rule_id in lowered_case.replace(" ", ""):
        return True
    if rule_text and rule_text.lower() in lowered_case:
        return True
    tokens = _tokenize(rule_text, limit=18)
    if not tokens:
        return False
    hit_count = sum(1 for token in tokens if token.lower() in lowered_case)
    strong_hits = [
        token
        for token in tokens
        if len(token) >= 2 and token.lower() in lowered_case and token.lower() not in _STOPWORDS
    ]
    if len(strong_hits) >= 2 and any(hint.lower() in lowered_case for hint in _RULE_ACTION_HINTS):
        return True
    return (hit_count / len(tokens)) >= 0.35


def analyze_coverage(requirement_context: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """中文注释：规则级覆盖诊断（可直接驱动 gap 阶段精准补漏）。"""
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    rules = _extract_requirement_rules(requirement_context)
    blocking_rules = [rule for rule in rules if bool(rule.get("blocking", True))]
    total_rules = len(blocking_rules)
    if total_rules <= 0:
        return {
            "total_rules": 0,
            "total_extracted_rules": len(rules),
            "non_blocking_rules": [rule.get("rule_id") for rule in rules if not bool(rule.get("blocking", True))],
            "covered_rules": [],
            "missing_rules": [],
            "rule_diagnostics": [],
            "coverage_rate": 1.0,
            "missing_types": {"boundary": [], "exception": []},
        }

    case_texts = [_flatten_case_text(case) for case in normalized_cases]
    case_type_map = [_detect_case_types(text) for text in case_texts]

    covered_rules: list[str] = []
    missing_rules: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    missing_boundary: list[str] = []
    missing_exception: list[str] = []

    for rule in rules:
        required_types = _required_types_for_rule(rule.get("rule_text") or "")
        coverage_types: set[str] = set()
        for idx, case_text in enumerate(case_texts):
            if _is_rule_hit(rule, case_text):
                coverage_types.update(case_type_map[idx])
        covered = bool(coverage_types)
        blocking = bool(rule.get("blocking", True))
        if covered and blocking:
            covered_rules.append(rule["rule_id"])
            missing_types = sorted(required_types - coverage_types)
        elif not covered and blocking:
            missing_rules.append(rule["rule_id"])
            missing_types = sorted(required_types)
        else:
            missing_types = []
        if blocking and "boundary" in missing_types:
            missing_boundary.append(rule["rule_id"])
        if blocking and "exception" in missing_types:
            missing_exception.append(rule["rule_id"])
        diagnostics.append(
            {
                "rule_id": rule["rule_id"],
                "rule_text": rule["rule_text"],
                "biz_key": rule.get("biz_key") or "unknown",
                "rule_level": rule.get("rule_level") or ("hard" if blocking else "soft"),
                "confidence": rule.get("confidence") or ("high" if blocking else "low"),
                "source_type": rule.get("source_type") or "confirmed_requirement",
                "blocking": blocking,
                "non_blocking_reason": rule.get("non_blocking_reason") or "",
                "covered": covered,
                "coverage_types": sorted(coverage_types) if covered else [],
                "missing_types": missing_types,
            }
        )

    coverage_rate = round(len(covered_rules) / total_rules, 4) if total_rules else 1.0
    coverage_rate = max(0.0, min(1.0, coverage_rate))

    return {
        "total_rules": total_rules,
        "total_extracted_rules": len(rules),
        "non_blocking_rules": [rule.get("rule_id") for rule in rules if not bool(rule.get("blocking", True))],
        "covered_rules": covered_rules,
        "missing_rules": missing_rules,
        "rule_diagnostics": diagnostics,
        "coverage_rate": coverage_rate,
        "missing_types": {
            "boundary": sorted(set(missing_boundary)),
            "exception": sorted(set(missing_exception)),
        },
    }
