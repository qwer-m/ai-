from __future__ import annotations

import re
from typing import Any

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
    "正常显示",
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

_REDEEMABLE_NON_ASSERTABLE_EXPECTED_PHRASES = {
    "对应内容",
    "正常展示",
    "正常显示",
    "正常跳转",
    "正常更新",
}

TRUNCATED_TEXT_ENDINGS = (
    "或显",
    "对应内",
    "可校",
    "正常展",
    "跳转至",
    "显示为",
)

_BUSINESS_ASSERTION_PREDICATES = (
    "支持",
    "默认",
    "可选",
    "可选择",
    "自动",
    "生成",
    "完成",
    "展示",
    "显示",
    "渲染",
    "出现",
    "保留",
    "更新",
    "同步",
    "生效",
    "保存",
    "创建",
    "新增",
    "增加",
    "减少",
    "删除",
    "替换",
    "添加",
    "跳转",
    "进入",
    "enter",
    "返回",
    "打开",
    "关闭",
    "弹出",
    "退出",
    "切换",
    "复制",
    "点亮",
    "选中",
    "放大",
    "拖动",
    "播放",
    "加载",
    "通过",
    "下架",
    "置灰",
    "禁用",
    "隐藏",
    "锁定",
    "解锁",
    "排序",
    "过滤",
    "匹配",
    "等于",
    "变为",
    "变成",
    "保持",
    "处于",
    "包含",
    "存在",
    "可见",
    "一致",
    "符合",
    "决定",
    "读取",
    "标记",
    "提示",
    "拦截",
    "阻止",
    "拒绝",
    "写入",
    "调整",
    "解决",
    "顺延",
    "最多",
    "最少",
    "只能",
    "无法",
    "不可",
    "需",
    "contains",
    "shows",
    "displays",
    "generates",
    "updates",
    "syncs",
    "defaults",
    "supports",
    "retains",
    "matches",
    "keeps",
    "opens",
    "navigates",
    "redirects",
    "exposes",
    "enterable",
    "shown",
    "written",
    "blocks",
    "prevents",
    "rejects",
    "equals",
    "becomes",
    "remains",
    "is set to",
    "is absent",
    "is present",
)

_CONCRETE_VALUE_RE = re.compile(
    r"(?:\d+\s*[:：]\s*\d+(?:\s*[-~至到]\s*\d+\s*[:：]\s*\d+)?)"
    r"|(?:\d+\s*/\s*\d+)"
    r"|(?:第\s*\d+\s*(?:节|次|条|个|页|名))"
    r"|(?:\d+(?:\.\d+)?\s*(?:%|px|ms|s|秒|分钟|小时|天|周|月|年|节|次|条|个|页|张|人|份))"
    r"|(?:[A-Z]{1,5}-\d{2,})"
)

_BUSINESS_STATE_ANCHOR_TOKENS = (
    "当前",
    "对应",
    "最新",
    "最近",
    "自动",
    "默认",
    "最多",
    "最少",
    "第",
    "完成后",
    "更新",
    "同步",
    "标记",
    "冲突",
    "限制",
    "顺延",
    "保留",
    "保存",
    "生成",
    "正常",
    "完整",
    "未编辑",
    "未改动",
    "有改动",
    "被删除",
    "仅由",
    "仅",
    "不",
    "无法",
    "不可",
    "需",
    "top",
    "latest",
    "current",
    "default",
    "limit",
    "conflict",
    "sync",
    "updated",
)

_ASSERTION_RELATION_RE = re.compile(
    r"(?:等于|变为|变成|保持|处于|包含|不包含|存在|不存在|可见|不可见|可编辑|只读|一致|相同|"
    r"位于|介于|匹配|均围绕|直接相关|在[^，,；;]{1,40}之间|equals?|matches?|becomes?|remains?|between|"
    r"editable|read[ -]?only|is\s+(?:set\s+to|absent|present)|={1,3})",
    flags=re.IGNORECASE,
)

_COPULA_ASSERTION_RE = re.compile(
    r"^[^，,；;]{2,}(?:为|是)[^，,；;]{1,}$",
    flags=re.IGNORECASE,
)

_ENGLISH_COPULA_ASSERTION_RE = re.compile(
    r"^[a-z0-9][a-z0-9 _./:'-]{1,}\s+(?:is|are)\s+(?:not\s+)?[a-z0-9][a-z0-9 _./:'-]*$",
    flags=re.IGNORECASE,
)

_NEGATIVE_STATE_RE = re.compile(
    r"(?:无|未|没有|不得|不能|不可|不会|不予|"
    r"不(?=显示|展示|渲染|出现|包含|保留|通过|允许|可见|存在|增加|更新)|"
    r"\bno\b|\bnot\b|\bwithout\b)",
    flags=re.IGNORECASE,
)

_TRANSITION_ASSERTION_PREDICATES = (
    "跳转",
    "进入",
    "返回",
    "打开",
    "关闭",
    "弹出",
    "退出",
    "切换",
    "变为",
    "变成",
    "navigates",
    "redirects",
    "opens",
    "becomes",
)

_GENERIC_ASSERTION_RESIDUE_TOKENS = (
    "系统",
    "页面",
    "用户",
    "操作",
    "业务",
    "处理",
    "对应",
    "相关",
    "结果",
    "内容",
    "信息",
    "正常",
    "完成",
)


def _non_assertable_phrase_hit(text: str) -> bool:
    return any(phrase in text for phrase in NON_ASSERTABLE_EXPECTED_PHRASES)


def _hard_non_assertable_phrase_hit(text: str) -> bool:
    return any(
        phrase in text
        for phrase in NON_ASSERTABLE_EXPECTED_PHRASES
        if phrase not in _REDEEMABLE_NON_ASSERTABLE_EXPECTED_PHRASES
    )


def _has_concrete_value(text: str) -> bool:
    return bool(_CONCRETE_VALUE_RE.search(text))


def _has_business_assertion_predicate(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token.lower() in lowered for token in _BUSINESS_ASSERTION_PREDICATES)


def _assertion_predicate_kind_count(text: str) -> int:
    lowered = str(text or "").lower()
    return sum(
        1
        for token in set(_BUSINESS_ASSERTION_PREDICATES)
        if token and token.lower() in lowered
    )


def _has_business_state_anchor(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token.lower() in lowered for token in _BUSINESS_STATE_ANCHOR_TOKENS)


def _has_assertion_subject(text: str) -> bool:
    residue = str(text or "").strip().lower()
    removable = sorted(
        {*_BUSINESS_ASSERTION_PREDICATES, *_BUSINESS_STATE_ANCHOR_TOKENS},
        key=len,
        reverse=True,
    )
    for token in removable:
        residue = residue.replace(token.lower(), " ")
    residue = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", residue)
    return len(residue) >= 2


def _has_assertion_relation(text: str) -> bool:
    return bool(
        _ASSERTION_RELATION_RE.search(text)
        or _COPULA_ASSERTION_RE.search(text)
        or _ENGLISH_COPULA_ASSERTION_RE.search(text)
    )


def _has_transition_assertion_predicate(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token.lower() in lowered for token in _TRANSITION_ASSERTION_PREDICATES)


def _has_capability_operation_set(text: str) -> bool:
    normalized = str(text or "")
    if not any(token in normalized for token in ("支持", "可进行", "可以")):
        return False
    return normalized.count("、") >= 2


def _has_enumerated_assertion_items(text: str) -> bool:
    normalized = str(text or "")
    if normalized.count("、") < 2 or not _has_business_assertion_predicate(normalized):
        return False
    specific_items = 0
    for item in normalized.split("、"):
        if _has_specific_assertion_subject(item):
            specific_items += 1
    return specific_items >= 2


def _has_specific_assertion_subject(text: str) -> bool:
    residue = str(text or "").strip().lower()
    removable = sorted(
        {
            *_BUSINESS_ASSERTION_PREDICATES,
            *_BUSINESS_STATE_ANCHOR_TOKENS,
            *_GENERIC_ASSERTION_RESIDUE_TOKENS,
        },
        key=len,
        reverse=True,
    )
    for token in removable:
        residue = residue.replace(token.lower(), " ")
    residue = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", residue)
    return len(residue) >= 2


def _has_parallel_specific_assertions(text: str) -> bool:
    clauses = [
        part.strip()
        for part in re.split(r"[；;。.!！,，\n]+", str(text or ""))
        if part.strip()
    ]
    if len(clauses) < 2:
        return False
    concrete_count = 0
    for clause in clauses:
        if _non_assertable_phrase_hit(clause):
            continue
        if not _has_business_assertion_predicate(clause):
            continue
        if not _has_specific_assertion_subject(clause):
            continue
        concrete_count += 1
    return concrete_count >= 2


def _verified_semantic_assertion_terms(semantic: Any) -> tuple[str, ...]:
    if not isinstance(semantic, dict):
        return ()
    terms: list[str] = []
    seen: set[str] = set()
    for collection_name, fields in (
        ("module_candidates", ("module_name", "module_key")),
        ("produced_states", ("entity", "state")),
    ):
        collection = semantic.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict) or item.get("evidence_verified") is not True:
                continue
            for field in fields:
                value = re.sub(r"\s+", "", str(item.get(field) or "").strip().lower())
                if len(value) < 2 or value in seen:
                    continue
                seen.add(value)
                terms.append(value)
    return tuple(terms)


def _has_verified_semantic_assertion(text: str, semantic: Any) -> bool:
    normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
    if not normalized or not _has_business_assertion_predicate(text):
        return False
    return any(term in normalized for term in _verified_semantic_assertion_terms(semantic))


def _is_concrete_business_assertion_clause(text: str) -> bool:
    clause = str(text or "").strip()
    if not clause:
        return False
    has_value = _has_concrete_value(clause)
    has_relation = _has_assertion_relation(clause)
    has_negative_state = bool(_NEGATIVE_STATE_RE.search(clause))
    has_subject = _has_assertion_subject(clause)
    has_transition = _has_transition_assertion_predicate(clause)
    # “正常显示/符合预期”等模板话术不能仅凭业务主语或状态锚点升级为可断言结果。
    if _hard_non_assertable_phrase_hit(clause) and not (has_value or has_negative_state):
        return False
    if _non_assertable_phrase_hit(clause) and not (
        has_value or has_negative_state or has_transition
    ):
        return False
    if not (_has_business_assertion_predicate(clause) or has_relation):
        return False
    if has_value:
        return True
    if has_relation:
        return has_subject
    if has_negative_state:
        return has_subject
    if has_transition:
        return has_subject
    if _has_capability_operation_set(clause):
        return has_subject
    if _has_enumerated_assertion_items(clause):
        return has_subject
    if (
        _assertion_predicate_kind_count(clause) >= 2
        and _has_specific_assertion_subject(clause)
    ):
        return has_subject
    return False


def _business_assertion_clause_count(text: str) -> int:
    clauses = [
        part.strip()
        for part in re.split(r"[；;。.!！,\uFF0C\n]+", str(text or ""))
        if part.strip()
    ]
    if len(clauses) < 2:
        return 0
    concrete_count = 0
    for clause in clauses:
        if _is_concrete_business_assertion_clause(clause):
            concrete_count += 1
    return concrete_count


def _has_formula_or_algorithm_assertion(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    formula_signal = (
        bool(re.search(r"\b[a-z]\w*\s*=", lowered))
        or bool(re.search(r"\b(?:max|min|sum|avg)\s*\(", lowered))
        or "公式" in normalized
        or "计算结果" in normalized
    )
    if not formula_signal:
        return False
    numeric_signal = _has_concrete_value(normalized) or bool(re.search(r"=\s*-?\d", normalized))
    result_signal = any(
        token in normalized
        for token in ("符合", "一致", "决定", "权重", "排序", "排序位置", "计算结果", "公式")
    )
    return bool(numeric_signal and result_signal)


def has_concrete_expected_assertion(text: str, semantic: Any = None) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if re.search(r"[\"'`“”‘’].{4,}[\"'`“”‘’]", normalized):
        return True
    if re.search(r"\d+\s*/\s*\d+", normalized) and any(
        token in normalized for token in ("显示为", "剩余次数")
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
        "无提示",
        "提示阻止",
        "顺序与",
        "降序排列",
        "重试选项",
        "enabled",
        "disabled",
        "hidden",
        "visible",
        "restore",
        "removed",
    )
    if any(token in normalized for token in concrete_tokens):
        return True
    if _has_verified_semantic_assertion(normalized, semantic):
        return True
    if _has_formula_or_algorithm_assertion(normalized):
        return True
    if _is_concrete_business_assertion_clause(normalized):
        return True
    # 多分句结果只要包含一个独立、具体且可观察的断言即可验收；其他弱描述仍由
    # ambiguous/template 检查单独拦截，避免把“正常显示”本身当成断言。
    if _business_assertion_clause_count(normalized) >= 1:
        return True
    if _has_parallel_specific_assertions(normalized):
        return True
    return False


def has_weak_ambiguous_expected_result(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    weak_alternatives = (
        "或显示错误信息",
        "或提示错误",
        "或提示异常",
        "或显示异常",
        "或显示对应内容",
        "或按配置",
        "or show",
        "or show error",
        "or display error",
        "or prompt error",
        "or as configured",
    )
    return any(token in normalized for token in weak_alternatives)


def is_non_assertable_expected_result(text: str, semantic: Any = None) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    if looks_template_polluted_expected_result(normalized):
        return True
    if is_placeholder_expected_result(normalized):
        return True
    concrete_assertion = has_concrete_expected_assertion(normalized, semantic)
    if is_ambiguous_expected_result(normalized):
        if concrete_assertion and not has_weak_ambiguous_expected_result(normalized):
            return False
        return True
    if concrete_assertion:
        return False
    return True


def is_case_expected_result_non_assertable(case: Any) -> bool:
    if not isinstance(case, dict):
        return True
    expected_result = ""
    for key in ("expected_result", "expectedResult", "预期结果", "预期"):
        value = str(case.get(key) or "").strip()
        if value:
            expected_result = value
            break
    semantic = case.get("_semantic") if isinstance(case.get("_semantic"), dict) else None
    return is_non_assertable_expected_result(expected_result, semantic)


def looks_template_polluted_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    legacy_template_signatures = (
        "对应记录，且列表或查询中不再显示该记录",
        "对应记录，且查询结果应反映新值",
        "且后续查询可验证结果",
        "且每条记录关键字段值正确",
        "响应状态码正确，且用户仅可访问",
        "应生成可下载的",
        "导入并返回处理结果或统计信息",
        "应触发权限或付费拦截提示",
        "按钮可用状态与提示文案正确",
        "应给出明确校验提示，并拦截不符合条件的输入",
        "关键字段，且字段值与输入/后端数据一致",
    )
    if normalized.startswith("执行") and any(signature in normalized for signature in legacy_template_signatures):
        return True
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
        for token in ("上传图片", "显隐", "原图", "缩略图", "复制", "排序", "弹窗")
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
        "处理结果",
        "生成结果",
        "video",
        "media",
        "resource",
        "generated result",
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
