from __future__ import annotations

import re
from typing import Any

from .coverage_strategy import (
    intent_action_keywords,
    intent_outcome_keywords,
    intent_stopwords,
)
from .flow_outline import (
    _CROSS_CUTTING_DEFINITIONS,
    _FLOW_STAGE_DEFINITIONS,
    _canonical_stage_label,
)
from .rule_coverage import _normalize_text, _tokenize
from .scenario_registry import (
    iter_scenario_family_policies,
    scenario_pattern_entries,
    specific_scenario_kinds,
    specific_scenario_precedence,
)
from ..postprocess.case_access import case_flat_text, case_steps, case_text_field


def _flatten_case_text(case: dict[str, Any]) -> str:
    return _normalize_text(
        case_flat_text(
            case,
            fields=("id", "description", "test_module", "test_input", "expected_result", "steps", "preconditions"),
        )
    )


def _flatten_case_intent_text(case: dict[str, Any]) -> str:
    return _normalize_text(
        case_flat_text(case, fields=("description", "test_module", "test_input", "expected_result", "steps"))
    )


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
    ("submission_success_state", ("投稿成功", "审核中", "弹窗", "submission success")),
    ("community_empty_state", ("作文圈空状态", "作文圈暂无", "暂无作文", "无数据", "community empty")),
    ("full_text_copy", ("复制全文", "全文复制", "复制成功", "copy full text")),
    ("polish_original_compare", ("全文润色", "原文对比", "对比显示", "polish original compare")),
    ("technique_practice_answer", ("技法巩固答题", "答题结果", "答案状态", "technique practice")),
    ("category_sorting", ("语文分类", "分类排序", "类目排序", "category sorting")),
    ("upload_image_management", ("上传图片", "删除图片", "拖动", "缩略图", "upload image management")),
    ("essay_limit_20", ("我的作文20条", "20条上限", "最多20", "essay limit 20")),
)
_SCENARIO_PATTERNS += scenario_pattern_entries()
_SCENARIO_POLICY_BY_KEY = {policy.key: policy for policy in iter_scenario_family_policies()}

_SPECIFIC_SCENARIO_KINDS = {
    "submission_success_state",
    "community_empty_state",
    "full_text_copy",
    "polish_original_compare",
    "technique_practice_answer",
    "category_sorting",
    "upload_image_management",
    "essay_limit_20",
}
_SPECIFIC_SCENARIO_KINDS.update(specific_scenario_kinds())

_SPECIFIC_SCENARIO_PRECEDENCE = {
    "submission_success_state": 0,
    "upload_image_management": 0,
    "essay_limit_20": 0,
    "community_empty_state": 0,
    "full_text_copy": 0,
    "polish_original_compare": 0,
    "technique_practice_answer": 0,
    "category_sorting": 0,
}
_SPECIFIC_SCENARIO_PRECEDENCE.update(specific_scenario_precedence())


_INTENT_ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = intent_action_keywords()
_INTENT_OUTCOME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = intent_outcome_keywords()
_INTENT_STOPWORDS = intent_stopwords()


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


def classify_case_flow_stage(case: dict[str, Any], flow_outline: dict[str, Any] | None = None) -> str:
    text = _flatten_case_text(case)
    case_module_stage = _canonical_stage_label(case_text_field(case, "test_module"))
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
    description = case_text_field(case, "description")
    module = _canonical_stage_label(case_text_field(case, "test_module"))
    expected = case_text_field(case, "expected_result")
    steps = " ".join(case_steps(case))
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


def _scenario_policy_allowed_for_domain(scenario_key: str, primary_domain: str) -> bool:
    if not primary_domain:
        return True
    policy = _SCENARIO_POLICY_BY_KEY.get(str(scenario_key or ""))
    if policy is None:
        return True
    return str(policy.domain or "general") in {"general", str(primary_domain)}


def _scenario_patterns_for_domain(primary_domain: str = "") -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not primary_domain:
        return _SCENARIO_PATTERNS
    return tuple(
        (scenario_key, keywords)
        for scenario_key, keywords in _SCENARIO_PATTERNS
        if _scenario_policy_allowed_for_domain(scenario_key, primary_domain)
    )


def classify_case_scenario_key(
    case: dict[str, Any],
    flow_stage: str | None = None,
    *,
    primary_domain: str = "",
) -> str:
    text = _flatten_case_text(case)
    intent_text = _flatten_case_intent_text(case)
    stage = str(flow_stage or "unknown")
    action, compact_object, outcome = _case_intent_parts(case)
    scenario_patterns = _scenario_patterns_for_domain(primary_domain)
    specific_patterns = [
        (scenario_key, keywords)
        for scenario_key, keywords in scenario_patterns
        if scenario_key in _SPECIFIC_SCENARIO_KINDS
    ]
    specific_patterns.sort(key=lambda item: (_SPECIFIC_SCENARIO_PRECEDENCE.get(item[0], 10), item[0]))
    generic_patterns = [
        (scenario_key, keywords)
        for scenario_key, keywords in scenario_patterns
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
    tokens = _tokenize(case_flat_text(case, fields=("test_module", "description", "expected_result")), limit=8)
    token_key = "_".join(token.lower() for token in tokens[:6])
    return f"{stage}:semantic:{token_key or 'unknown'}"


def classify_case_intent_signature(case: dict[str, Any], flow_stage: str | None = None) -> str:
    """Build a coarse, product-agnostic action/object/outcome signature for duplicate detection."""
    stage = str(flow_stage or "unknown")
    action, compact_object, outcome = _case_intent_parts(case)
    return f"{stage}:intent:{action}:{compact_object}:{outcome}"
