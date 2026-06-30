from __future__ import annotations

from typing import Any, Callable

from .case_access import case_flat_text, case_priority
from .postprocess_priority_config import (
    p0_core_tokens,
    p0_critical_families,
    p0_essay_domain_negative_tokens,
    p0_essay_domain_positive_tokens,
    p0_essay_domain_primary_tokens,
    p0_essay_exclusion_tokens,
    p0_low_value_tokens,
)
from .streaming_case_normalization import normalize_priority_value
from .streaming_postprocess_utils import _dict_case_copies

_COURSE_PERMISSION_FAMILIES = {
    "free_first_lesson",
    "locked_member_courses",
    "member_all_courses",
    "permission",
}


def p0_main_path_target_count(case_count: int, *, coverage_mode: str = "") -> int:
    count = max(0, int(case_count or 0))
    if count <= 0:
        return 0
    mode = str(coverage_mode or "")
    if mode == "full_functional_regression":
        if count >= 80:
            target_count = min(12, max(8, int((count + 9) // 10)))
        elif count >= 40:
            target_count = min(10, max(9, int(round(count * 0.12))))
        else:
            target_count = min(6, max(3, int(round(count * 0.14))))
    elif mode == "expanded_regression":
        target_count = (
            min(4, max(3, int(round(count * 0.06))))
            if count >= 50
            else min(3, max(1, int(round(count * 0.08))))
        )
    else:
        target_count = 0
    return min(max(0, int(target_count)), count)


def apply_priority_override(
    case: dict[str, Any],
    *,
    priority: str,
    source: str,
    state: str = "overridden",
) -> None:
    normalized_priority = str(priority or "").strip().upper()
    if normalized_priority not in {"P0", "P1", "P2"}:
        return
    case["priority"] = normalized_priority
    case["priority_final"] = normalized_priority
    case["priority_decision_state"] = str(state or "overridden")
    case["priority_decision_source"] = str(source or "").strip()


def p0_case_anchor_text(case: dict[str, Any] | Any) -> str:
    if not isinstance(case, dict):
        return str(case or "").lower()
    return case_flat_text(
        case,
        fields=("test_module", "description", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )


def p0_essay_domain_active(requirement_text: str = "") -> bool:
    requirement_text_lower = str(requirement_text or "").lower()
    return any(
        token.lower() in requirement_text_lower
        for token in p0_essay_domain_primary_tokens()
    ) or (
        any(token.lower() in requirement_text_lower for token in p0_essay_domain_positive_tokens())
        and not any(token.lower() in requirement_text_lower for token in p0_essay_domain_negative_tokens())
    )


def p0_cross_domain_essay_case(case: dict[str, Any], *, requirement_text: str = "") -> bool:
    if p0_essay_domain_active(requirement_text):
        return False
    text = p0_case_anchor_text(case)
    return any(token.lower() in text for token in p0_essay_exclusion_tokens())


def p0_has_low_value_signal(case_or_text: dict[str, Any] | str) -> bool:
    text = p0_case_anchor_text(case_or_text)
    return any(token.lower() in text for token in p0_low_value_tokens())


def p0_has_core_signal(case_or_text: dict[str, Any] | str) -> bool:
    text = p0_case_anchor_text(case_or_text)
    return any(token.lower() in text for token in p0_core_tokens())


def p0_configured_anchor_family(
    case_or_text: dict[str, Any] | str,
    *,
    requirement_text: str = "",
    course_only_when_non_essay: bool = True,
) -> str:
    if isinstance(case_or_text, dict) and p0_cross_domain_essay_case(
        case_or_text,
        requirement_text=requirement_text,
    ):
        return ""
    text = p0_case_anchor_text(case_or_text)
    critical_families = p0_critical_families()
    if course_only_when_non_essay and not p0_essay_domain_active(requirement_text):
        critical_families = tuple(
            item
            for item in critical_families
            if str(item[0]) in _COURSE_PERMISSION_FAMILIES
        )
    for family, tokens in critical_families:
        if all(token.lower() in text for token in tokens):
            return str(family)
    return ""


def p0_main_path_anchor(case: dict[str, Any], *, requirement_text: str = "") -> bool:
    if p0_cross_domain_essay_case(case, requirement_text=requirement_text):
        return False
    if p0_configured_anchor_family(case, requirement_text=requirement_text):
        return True
    return p0_has_core_signal(case) and not p0_has_low_value_signal(case)


def _default_case_signature(case: dict[str, Any]) -> str:
    return "\n".join(
        str(case.get(field) or "")
        for field in ("test_module", "description", "expected_result", "test_input")
    )


def enforce_main_path_p0_anchors(
    cases: list[dict[str, Any]],
    *,
    coverage_mode: str = "",
    requirement_text: str = "",
    case_signature_fn: Callable[[dict[str, Any]], str] | None = None,
    case_complexity_profile_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    candidate_cases = _dict_case_copies(cases)
    mode = str(coverage_mode or "")
    if mode not in {"expanded_regression", "full_functional_regression"}:
        return candidate_cases
    case_count = len(candidate_cases)
    if case_count <= 0:
        return candidate_cases
    target_count = p0_main_path_target_count(case_count, coverage_mode=mode)
    signature_fn = case_signature_fn or _default_case_signature
    complexity_fn = case_complexity_profile_fn
    strong_tokens = (
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
    low_value_tokens = (
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
    anchor_families = (
        ("submission", ("submit", "publish", "提交", "投稿", "发布")),
        ("result_display", ("four modules", "feedback modules", "result details", "四大模块", "四部分", "完整展示")),
        ("generation_result", ("generate", "result", "upload", "生成", "结果", "批改", "上传")),
        ("approval", ("approve", "review approved", "approval passed", "审核通过", "审核中")),
        ("permission", ("permission", "member", "vip", "locked", "paywall", "first lesson", "权限", "会员", "锁定", "第一课", "试学")),
        ("community_detail", ("detail page", "review approved", "作品详情", "审核通过后")),
    )
    critical_anchor_families = (
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

    def _anchor_family(text: str) -> str:
        for family, tokens in anchor_families:
            if any(token and token.lower() in text for token in tokens):
                return family
        return "general"

    def _case_anchor_text(item: dict[str, Any]) -> str:
        return p0_case_anchor_text(item)

    def _has_strong_anchor(text: str) -> bool:
        return any(token and token.lower() in text for token in strong_tokens) or p0_has_core_signal(text)

    def _critical_anchor_family(text: str) -> str:
        for family, tokens in critical_anchor_families:
            if all(token and token.lower() in text for token in tokens):
                return family
        return p0_configured_anchor_family(
            text,
            requirement_text=str(requirement_text or ""),
            course_only_when_non_essay=False,
        )

    def _has_critical_anchor(text: str) -> bool:
        return bool(_critical_anchor_family(text))

    def _has_low_value_anchor(text: str) -> bool:
        return any(token and token.lower() in text for token in low_value_tokens) or p0_has_low_value_signal(text)

    def _has_non_blocking_detail_anchor(text: str) -> bool:
        detail_tokens = (
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
        return any(token and token.lower() in text for token in detail_tokens)

    def _has_blocking_anchor(text: str) -> bool:
        generation_terms = (
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
        submit_terms = (
            "submit success",
            "submitted successfully",
            "enters pending review",
            "提交成功",
            "投稿成功",
            "进入审核中",
            "状态变为审核中",
        )
        approval_terms = (
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
        permission_terms = (
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
        return any(token and token.lower() in text for token in generation_terms + submit_terms + approval_terms + permission_terms)

    def _complexity_penalty(item: dict[str, Any]) -> int:
        if complexity_fn is None:
            return 0
        try:
            return 4 * int((complexity_fn(item) or {}).get("complexity_score") or 0)
        except Exception:
            return 0

    for item in candidate_cases:
        if normalize_priority_value(case_priority(item)) != "P0":
            continue
        if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
            apply_priority_override(
                item,
                priority="P1",
                source="main_path_anchor_demoted_domain_mismatch",
            )
            continue
        text = _case_anchor_text(item)
        if (not _has_critical_anchor(text)) and (_has_non_blocking_detail_anchor(text) or (
            _has_low_value_anchor(text) and not _has_blocking_anchor(text)
        )):
            apply_priority_override(
                item,
                priority="P1",
                source="main_path_anchor_demoted_non_blocking",
            )

    existing_p0_signatures = {
        signature_fn(item)
        for item in candidate_cases
        if normalize_priority_value(case_priority(item)) == "P0"
    }
    if len(existing_p0_signatures) >= target_count:
        return candidate_cases

    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for index, item in enumerate(candidate_cases):
        if signature_fn(item) in existing_p0_signatures:
            continue
        if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
            continue
        text = _case_anchor_text(item)
        score = 0
        score += 10 * sum(1 for token in strong_tokens if token and token.lower() in text)
        score -= 12 * sum(1 for token in low_value_tokens if token and token.lower() in text)
        if normalize_priority_value(case_priority(item)) == "P1":
            score += 6
        critical_family = _critical_anchor_family(text)
        if critical_family:
            score += 70
        if (not critical_family) and (_has_non_blocking_detail_anchor(text) or (
            _has_low_value_anchor(text) and not _has_blocking_anchor(text)
        )):
            score -= 40
        if str(item.get("priority_decision_state") or "").strip().lower() in {"optional", "invalid"}:
            score -= 20
        score -= _complexity_penalty(item)
        if score >= 10:
            ranked.append((score, -index, critical_family or _anchor_family(text), item))

    if len(ranked) < max(1, target_count - len(existing_p0_signatures)):
        ranked_signatures = {signature_fn(item) for _score, _neg_index, _family, item in ranked}
        for index, item in enumerate(candidate_cases):
            signature = signature_fn(item)
            if signature in existing_p0_signatures or signature in ranked_signatures:
                continue
            if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
                continue
            text = _case_anchor_text(item)
            critical_family = _critical_anchor_family(text)
            if (not critical_family) and (_has_non_blocking_detail_anchor(text) or (
                _has_low_value_anchor(text) and not _has_blocking_anchor(text)
            )):
                continue
            if mode == "full_functional_regression" and not (_has_strong_anchor(text) or critical_family):
                continue
            normalized_priority = normalize_priority_value(case_priority(item))
            priority_bonus = 8 if normalized_priority == "P1" else 3 if normalized_priority == "P2" else 0
            fallback_score = priority_bonus + (60 if critical_family else 0) - _complexity_penalty(item)
            if fallback_score >= 3:
                ranked.append((fallback_score, -index, critical_family or _anchor_family(text), item))
                ranked_signatures.add(signature)

    if not ranked:
        return candidate_cases
    ranked.sort(reverse=True)
    promoted_signatures: set[str] = set(existing_p0_signatures)
    promoted_families: set[str] = set()
    output = _dict_case_copies(candidate_cases)
    for _score, _neg_index, family, item in ranked:
        if len(promoted_signatures) >= target_count:
            break
        if family in promoted_families and family != "general":
            continue
        signature = signature_fn(item)
        if signature in promoted_signatures:
            continue
        promoted_signatures.add(signature)
        promoted_families.add(family)
        for updated in output:
            if signature_fn(updated) == signature:
                apply_priority_override(
                    updated,
                    priority="P0",
                    source="main_path_anchor_floor",
                )
                break
    if len(promoted_signatures) < target_count:
        for _score, _neg_index, _family, item in ranked:
            if len(promoted_signatures) >= target_count:
                break
            signature = signature_fn(item)
            if signature in promoted_signatures:
                continue
            promoted_signatures.add(signature)
            for updated in output:
                if signature_fn(updated) == signature:
                    apply_priority_override(
                        updated,
                        priority="P0",
                        source="main_path_anchor_floor",
                    )
                    break
    return output
