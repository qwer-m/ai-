from __future__ import annotations

import re
from typing import Any

from .coverage_strategy import (
    cross_cutting_definitions,
    cross_cutting_hints,
    data_flow_phase_tie_priority,
    data_flow_phases,
    flow_stage_definitions,
)
from .domain_gate import current_domain_gate
from .rule_coverage import _normalize_text
from ..postprocess.case_access import case_text_field


_FLOW_STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = flow_stage_definitions()
_FLOW_STAGE_ORDER = [str(item.get("key") or "") for item in _FLOW_STAGE_DEFINITIONS]

_CROSS_CUTTING_DEFINITIONS: tuple[dict[str, Any], ...] = cross_cutting_definitions()
_CROSS_CUTTING_ORDER = [str(item.get("key") or "") for item in _CROSS_CUTTING_DEFINITIONS]

_STAGE_SPLIT_RE = re.compile(r"\s*(?:->|=>|[\\/\|>:_\-\u2014\uff1a\uff1b])\s*")
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*|[A-Za-z])(?:[.)\]\uff09\uff0e\u3001-]|\s+)|"
    r"[\u4e00-\u9fff]{1,4}[.)\]\uff09\uff0e\u3001-])"
)
_STAGE_TRAILING_NOISE_RE = re.compile(
    r"\s*(?:page|screen|view|module|panel|tab|section|list|detail|flow|workflow|"
    r"页面|页|模块|面板|区域|列表|详情|流程|验证)\s*$",
    re.IGNORECASE,
)
_CROSS_CUTTING_HINTS = cross_cutting_hints()

_DATA_FLOW_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = data_flow_phases()
_DATA_FLOW_PHASE_RANK = {phase: index for index, (phase, _tokens) in enumerate(_DATA_FLOW_PHASES)}
_DATA_FLOW_PHASE_TIE_PRIORITY = data_flow_phase_tie_priority()
_DATA_FLOW_CROSS_CUTTING_PHASES = {"access_limit", "global_exception", "history_makeup"}

_NUMBERED_CJK_PREFIX_RE = re.compile(r"^\s*\d{1,2}(?=[\u4e00-\u9fff])")

_EXECUTION_ACTION_HINTS = (
    "open",
    "enter",
    "access",
    "select",
    "choose",
    "edit",
    "input",
    "submit",
    "publish",
    "save",
    "confirm",
    "review",
    "approve",
    "sync",
    "complete",
    "reply",
    "comment",
    "message",
    "notify",
    "打开",
    "进入",
    "访问",
    "选择",
    "配置",
    "编辑",
    "填写",
    "输入",
    "提交",
    "发布",
    "保存",
    "确认",
    "审核",
    "复核",
    "同步",
    "完成",
    "回复",
    "评论",
    "消息",
    "通知",
)

_META_SECTION_HINTS = (
    "background",
    "overview",
    "scope",
    "requirement",
    "requirements",
    "description",
    "change summary",
    "overall",
    "general",
    "introduction",
    "other features",
    "other function",
    "functional modules",
    "data requirement",
    "technical solution",
    "technical research",
    "implementation detail",
    "system structure",
    "out of scope",
    "not in scope",
    "需求背景",
    "背景",
    "概述",
    "说明",
    "整体",
    "总体",
    "范围",
    "其他功能",
    "功能模块",
    "模块划分",
    "数据需求",
    "技术方案",
    "技术调研",
    "实现细节",
    "系统结构",
    "本期不做",
)

_NON_EXECUTABLE_SECTION_HINTS = (
    "technical solution",
    "technical research",
    "implementation detail",
    "system structure",
    "data requirement",
    "out of scope",
    "not in scope",
    "技术方案",
    "技术调研",
    "实现细节",
    "系统结构",
    "数据需求",
    "本期不做",
)

_STRUCTURE_SECTION_SUFFIXES = ("structure", "architecture", "结构", "架构")

_UI_ATTRIBUTE_HINTS = (
    "icon",
    "title",
    "time",
    "avatar",
    "nickname",
    "badge",
    "label",
    "tag",
    "level",
    "member",
    "field",
    "info",
    "content",
    "button",
    "count",
    "number",
    "amount",
    "date",
    "mode",
    "method",
    "rule",
    "image",
    "picture",
    "photo",
    "prompt",
    "placeholder",
    "top",
    "selected",
    "unselected",
    "tab",
    "sidebar",
    "side bar",
    "illustration",
    "original text",
    "common logic",
    "view count",
    "like count",
    "reply count",
    "search",
    "report list",
    "original page",
    "adjusted page",
    "logo",
    "图标",
    "标题",
    "时间",
    "头像",
    "昵称",
    "标识",
    "标签",
    "等级",
    "会员",
    "字段",
    "信息",
    "内容",
    "按钮",
    "数量",
    "页数",
    "日期",
    "方式",
    "规则",
    "图片",
    "提示词",
    "顶部",
    "新增标",
    "可选中",
    "可不选",
    "用户可",
    "用戶可",
    "赞了你的",
    "支持搜索",
    "搜索功能",
    "报告列表",
    "仅包含",
    "侧边栏",
    "右侧",
    "示意",
    "原文",
    "波浪",
    "通用逻辑",
    "tab",
    "浏览量",
    "点赞量",
    "回复量",
    "点赞数",
    "回复数",
    "排序方式",
    "顺序",
    "原页面",
    "调整后",
)

_CONTAINER_SECTION_HINTS = (
    "area",
    "areas",
    "zone",
    "zones",
    "section",
    "sections",
    "category",
    "categories",
    "partition",
    "partitions",
    "block",
    "blocks",
    "区域",
    "分区",
    "版块",
    "分类",
    "栏目",
)

_OPTION_LABEL_HINTS = (
    "featured",
    "hot",
    "latest",
    "recommend",
    "recommended",
    "精选",
    "热门",
    "最新",
    "推荐",
    "正序",
    "审核通过",
    "审核不通过",
)

_UI_CONTROL_HEADING_HINTS = (
    "click",
    "tap",
    "switch",
    "toggle",
    "return",
    "back",
    "delete",
    "download",
    "drag",
    "slide",
    "swipe",
    "zoom",
    "default",
    "点击",
    "切换",
    "返回",
    "删除",
    "下载",
    "拖动",
    "滑动",
    "放大",
    "全屏",
    "单击",
    "双击",
    "左右滑动",
    "上拉",
    "下拉",
    "默认",
    "小于",
    "大于",
    "第一种情况",
    "第二种情况",
)

_GENERIC_SURFACE_LABELS = {
    "list",
    "detail",
    "content",
    "info",
    "message",
    "列表",
    "详情",
    "内容",
    "信息",
    "消息",
}


def _keyword_position(text: str, keywords: tuple[str, ...]) -> int | None:
    positions = [text.find(keyword) for keyword in keywords if keyword and text.find(keyword) >= 0]
    return min(positions) if positions else None


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


def _apply_data_flow_order_to_outline(
    outline: dict[str, Any],
    *,
    enable_data_flow_order: bool = True,
) -> dict[str, Any]:
    flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = [str(item) for item in (outline.get("cross_cutting") or []) if str(item).strip()]
    flow_labels = dict(outline.get("flow_labels") or {})
    cross_labels = dict(outline.get("cross_cutting_labels") or {})
    if not enable_data_flow_order:
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
            "data_flow_phase_rank": {},
            "data_flow_order_applied": False,
        }
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
    original_flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
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
        "data_flow_order_applied": bool(flow_order != original_flow_order or moved_to_cross),
    }


def _compact_stage_label(label: str) -> str:
    cleaned = _normalize_text(label).strip()
    cleaned = re.sub(r"^\s*(?:#{1,6}\s*)?", "", cleaned)
    cleaned = _NUMBERED_HEADING_RE.sub("", cleaned, count=1)
    cleaned = _NUMBERED_CJK_PREFIX_RE.sub("", cleaned, count=1)
    cleaned = re.sub(r"[:\uff1a]\s*$", "", cleaned).strip()
    parts = [part.strip() for part in re.split(r"[:\uff1a]", cleaned, maxsplit=1) if part.strip()]
    if len(parts) > 1 and 2 <= len(parts[0]) <= 40:
        cleaned = parts[0]
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:40]


def _canonical_stage_label(label: str) -> str:
    cleaned = _compact_stage_label(label)
    cleaned = re.sub(r"[\(\[\uff08].*?[\)\]\uff09]", "", cleaned).strip()
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


def _label_token_hit(label: str, tokens: tuple[str, ...]) -> bool:
    lowered = _normalize_text(label).strip().lower()
    lowered = lowered.replace("⻔", "门")
    if not lowered:
        return False
    for raw_token in tokens:
        token = str(raw_token or "").strip().lower()
        if not token:
            continue
        if token.isascii() and re.search(r"[a-z0-9]", token):
            if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", lowered):
                return True
            continue
        if token in lowered:
            return True
    return False


def _has_execution_signal(label: str) -> bool:
    if _data_flow_phase_for_label(label):
        return True
    return _label_token_hit(label, _EXECUTION_ACTION_HINTS)


def _is_plain_container_label(label: str) -> bool:
    lowered = _canonical_stage_label(label).strip().lower()
    if not lowered:
        return False
    if lowered in _GENERIC_SURFACE_LABELS:
        return True
    if _label_token_hit(lowered, _CONTAINER_SECTION_HINTS):
        return True
    # 中文注释：命名功能区可能就是需求模块，不能仅凭“区”后缀排除。
    return False


def _is_non_executable_document_section_label(label: str) -> bool:
    raw_label = _normalize_text(label).strip()
    canonical = _canonical_stage_label(label)
    if not canonical:
        return True
    match_text = f"{canonical} {raw_label}".strip()
    if re.match(r"^[\W_]+", canonical, flags=re.UNICODE):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{0,2}\s*[-/]\s*[\u4e00-\u9fff]{0,2}", raw_label):
        return True
    if "/" in raw_label and raw_label.endswith("时"):
        return True
    if "的消息" in raw_label:
        return True
    if _label_token_hit(match_text, _NON_EXECUTABLE_SECTION_HINTS):
        return True
    normalized_canonical = _canonical_stage_label(canonical).strip().lower()
    if any(normalized_canonical.endswith(suffix) for suffix in _STRUCTURE_SECTION_SUFFIXES):
        return True
    if normalized_canonical in _GENERIC_SURFACE_LABELS:
        return True
    has_action = _has_execution_signal(canonical)
    if _label_token_hit(match_text, _OPTION_LABEL_HINTS):
        return True
    if _label_token_hit(match_text, _UI_CONTROL_HEADING_HINTS):
        return True
    if _label_token_hit(match_text, _UI_ATTRIBUTE_HINTS):
        return True
    if _label_token_hit(match_text, _META_SECTION_HINTS) and not has_action:
        return True
    if _is_plain_container_label(canonical) and not has_action:
        return True
    return False


def _looks_like_section_heading(line: str) -> bool:
    text = _normalize_text(line).strip()
    if not text:
        return False
    if len(text) > 80:
        return False
    if re.match(r"^\s*#{1,6}\s+\S+", text):
        return True
    if _NUMBERED_HEADING_RE.match(text) and len(text) <= 80:
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
        if _is_non_executable_document_section_label(label):
            continue
        is_numbered = bool(_NUMBERED_HEADING_RE.match(line))
        seen.add(label.lower())
        sections.append(
            {
                "key": _stage_key_from_label(label, len(sections) + 1),
                "label": label,
                "position": int(position),
                "source": "requirement_heading_numbered" if is_numbered else "requirement_heading",
            }
        )
        if len(sections) >= 24:
            break
    numbered_sections = [item for item in sections if item.get("source") == "requirement_heading_numbered"]
    if len(numbered_sections) >= 2:
        return numbered_sections
    return sections


def _extract_case_module_stages(
    requirement_context: str,
    cases: list[dict[str, Any]] | None,
    *,
    require_document_match: bool = False,
) -> list[dict[str, Any]]:
    text = _normalize_text(requirement_context)
    modules: list[str] = []
    seen: set[str] = set()
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        module = _canonical_stage_label(case_text_field(case, "test_module"))
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
        if require_document_match and position < 0:
            continue
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
    text = _normalize_text(requirement_context)
    domain_gate = current_domain_gate(text)
    allows_historical_profile = bool(domain_gate.get("allows_historical_profile"))
    found: list[dict[str, Any]] = _extract_requirement_sections(text)
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
    if not found and allows_historical_profile:
        found = _extract_case_module_stages(
            text,
            cases,
            require_document_match=bool(text.strip()),
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
    if found or allows_historical_profile:
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

    if allows_historical_profile and not flow_stages and not cross_cutting:
        profile_outline = _extract_profile_flow_outline(project_profile)
        if profile_outline is not None:
            profile_outline["domain_gate_status"] = str(domain_gate.get("status") or "")
            profile_outline["domain_gate_reason"] = str(domain_gate.get("reason") or "")
            profile_outline["domain_gate_allows_historical_profile"] = allows_historical_profile
            return profile_outline

    return _apply_data_flow_order_to_outline(
        {
            "source": "requirement_keyword_positions" if found else "no_flow_keywords_detected",
            "domain_gate_status": str(domain_gate.get("status") or ""),
            "domain_gate_reason": str(domain_gate.get("reason") or ""),
            "domain_gate_allows_historical_profile": allows_historical_profile,
            "flow_order": [item["key"] for item in flow_stages],
            "document_flow_order": [item["key"] for item in found],
            "flow_labels": {item["key"]: item["label"] for item in flow_stages},
            "flow_stage_positions": {item["key"]: item["position"] for item in found},
            "cross_cutting": [item["key"] for item in cross_cutting],
            "cross_cutting_labels": {item["key"]: item["label"] for item in cross_cutting},
        },
        enable_data_flow_order=allows_historical_profile,
    )
