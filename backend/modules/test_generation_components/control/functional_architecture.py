from __future__ import annotations

import re
from typing import Any

from core.processing.document_structure import extract_document_structure, normalize_document_text


_FUNCTION_ACTIONS = (
    "open", "enter", "access", "click", "select", "choose", "create", "edit", "delete", "submit",
    "publish", "upload", "download", "save", "confirm", "review", "approve", "search", "filter", "sort",
    "switch", "sync", "notify", "purchase", "unlock", "display", "view", "return", "redirect",
    "打开", "进入", "访问", "点击", "选择", "创建", "新增", "编辑", "删除", "提交", "发布", "上传",
    "下载", "保存", "确认", "审核", "搜索", "筛选", "排序", "切换", "同步", "通知", "购买", "开通",
    "解锁", "展示", "显示", "查看", "返回", "跳转", "回复", "评论", "点赞", "获得", "完成",
)
_FUNCTION_STATES = (
    "state", "status", "success", "failure", "permission", "role", "entry", "button", "page", "list",
    "dialog", "toast", "状态", "成功", "失败", "权限", "角色", "入口", "按钮", "页面", "列表", "弹窗",
    "提示", "已完成", "未完成", "已发布", "未发布", "可用", "不可用", "锁定", "次数", "上限",
)
_OUT_OF_SCOPE_MARKERS = (
    "本期不做", "本期不实现", "不在本期", "后续版本", "后续迭代", "未来版本", "暂不实现", "暂不开发",
    "二期", "三期", "phase 2", "phase2", "out of scope", "future release", "not implemented",
)
_CONSTRAINT_MARKERS = (
    "必须", "严禁", "不得", "禁止", "仅限", "限制", "不允许", "不可", "需要符合",
    "must", "must not", "forbidden", "prohibited", "only", "restricted",
)
_MODULE_AUTONOMY_MARKERS = (
    "进入", "跳转", "返回", "回到", "发布", "发表", "提交", "创建", "新增", "上传", "下载",
    "购买", "开通", "解锁", "权限", "角色", "账户", "账号", "同步", "通知", "流转",
    "enter", "navigate", "redirect", "return", "publish", "submit", "create", "upload", "download",
    "purchase", "unlock", "permission", "role", "account", "sync", "notify",
)
_COMPONENT_TITLE_MARKERS = (
    "按钮", "图标", "图片", "头像", "输入框", "弹窗", "对话框", "提示框", "tab", "toast",
    "button", "icon", "image", "avatar", "input", "dialog", "popup", "tooltip",
)


def _token_hit(text: str, token: str) -> bool:
    lowered = str(text or "").lower()
    candidate = str(token or "").lower()
    if candidate.isascii() and re.search(r"[a-z0-9]", candidate):
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(candidate)}(?![a-z0-9_])", lowered))
    return candidate in lowered


def _dedupe_texts(values: list[Any], *, limit: int = 24) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        key = re.sub(r"\s+", "", text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(text)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _evidence_lines(node: dict[str, Any]) -> list[str]:
    lines = [str(node.get("title") or ""), str(node.get("inline_detail") or "")]
    lines.extend(str(item) for item in (node.get("section_lines") or []))
    return _dedupe_texts(
        [line for line in lines if any(_token_hit(line, token) for token in (*_FUNCTION_ACTIONS, *_FUNCTION_STATES))],
        limit=12,
    )


def _functional_score(node: dict[str, Any]) -> float:
    evidence = _evidence_lines(node)
    joined = "\n".join(evidence)
    action_hits = sum(1 for token in _FUNCTION_ACTIONS if _token_hit(joined, token))
    state_hits = sum(1 for token in _FUNCTION_STATES if _token_hit(joined, token))
    return float(len(evidence) * 2 + min(action_hits, 8) + min(state_hits, 4))


def _scope_marker(text: str) -> str:
    lowered = str(text or "").lower()
    for marker in _OUT_OF_SCOPE_MARKERS:
        if marker.lower() in lowered:
            return marker
    return ""


def _own_scope_marker(node: dict[str, Any]) -> str:
    title = str(node.get("title") or "")
    inline_detail = str(node.get("inline_detail") or "")
    marker = _scope_marker(f"{title} {inline_detail}")
    if marker:
        return marker

    # 正文只有明确指向整个节点时才参与范围判定，避免子功能二期误伤父模块。
    normalized_title = re.sub(r"\s+", "", title)
    for line in (node.get("direct_body_lines") or [])[:3]:
        current = str(line or "").strip()
        marker = _scope_marker(current)
        if not marker:
            continue
        compact = re.sub(r"\s+", "", current)
        refers_to_whole_node = bool(normalized_title and compact.startswith(normalized_title))
        explicitly_whole_scope = any(
            token in compact
            for token in ("本模块", "本功能", "本区域", "整个模块", "全部功能", "entiremodule")
        )
        if refers_to_whole_node or explicitly_whole_scope:
            return marker
    return ""


def _scope_status(
    node: dict[str, Any],
    *,
    nodes: list[dict[str, Any]],
    document_text: str,
) -> tuple[str, str]:
    marker = _own_scope_marker(node)
    if marker:
        return "out_of_scope", marker

    by_index = {
        int(item.get("node_index")): item
        for item in nodes
        if isinstance(item.get("node_index"), int)
    }
    parent_index = node.get("parent_index")
    visited: set[int] = set()
    while isinstance(parent_index, int) and parent_index not in visited:
        visited.add(parent_index)
        parent = by_index.get(parent_index)
        if not parent:
            break
        marker = _own_scope_marker(parent)
        if marker:
            return "out_of_scope", marker
        parent_index = parent.get("parent_index")

    name = str(node.get("title") or "").strip()
    normalized_name = re.sub(r"\s+", "", name).lower()
    if normalized_name:
        # 同名的模块级声明可以出现在范围章节中，但不将内部子功能声明上浮。
        for candidate in nodes:
            candidate_name = re.sub(r"\s+", "", str(candidate.get("title") or "")).lower()
            if candidate_name != normalized_name:
                continue
            marker = _own_scope_marker(candidate)
            if marker:
                return "out_of_scope", marker

        declaration_re = re.compile(
            rf"^\s*(?:[#*\-•]\s*)?(?:\d{{1,3}}(?:\.\d{{1,3}})*\s*[.)、]?\s*)?"
            rf"{re.escape(name)}\s*(?:[:：(（\-—]|$)",
            re.IGNORECASE,
        )
        for line in normalize_document_text(document_text).splitlines():
            if not declaration_re.search(line):
                continue
            marker = _scope_marker(line)
            if marker:
                return "out_of_scope", marker
    return "in_scope", ""


def _aliases(node: dict[str, Any]) -> list[str]:
    title = str(node.get("title") or "").strip()
    inline_detail = str(node.get("inline_detail") or "")
    bracketed = re.findall(r"[【\[]([^】\]]{1,30})[】\]]", inline_detail)
    return _dedupe_texts([title, *bracketed], limit=8)


def _is_structured_declaration_group(parent: dict[str, Any], nodes: list[dict[str, Any]]) -> bool:
    children = [nodes[index] for index in (parent.get("child_indexes") or []) if 0 <= index < len(nodes)]
    if len(children) < 3:
        return False
    declared = [item for item in children if str(item.get("inline_detail") or "").strip()]
    functional = [item for item in children if _functional_score(item) >= 4.0]
    return len(declared) >= max(2, int(len(children) * 0.6)) and len(functional) >= max(2, int(len(children) * 0.5))


def _candidate_score(
    selected: list[dict[str, Any]],
    *,
    source: str,
    parent: dict[str, Any] | None = None,
) -> float:
    if len(selected) < 2:
        return float("-inf")

    count = len(selected)
    declaration_ratio = sum(bool(str(item.get("inline_detail") or "").strip()) for item in selected) / count
    compact_title_ratio = sum(
        1
        for item in selected
        if 1 <= len(re.sub(r"\s+", "", str(item.get("title") or ""))) <= 18
    ) / count
    functional_ratio = sum(_functional_score(item) >= 4.0 for item in selected) / count

    autonomy_hits = 0
    constraint_hits = 0
    component_hits = 0
    direct_scores: list[float] = []
    for item in selected:
        direct_text = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("inline_detail") or ""),
                *(str(line) for line in (item.get("direct_body_lines") or [])[:4]),
            ]
        )
        autonomy_hits += any(_token_hit(direct_text, token) for token in _MODULE_AUTONOMY_MARKERS)
        constraint_hits += any(_token_hit(direct_text, token) for token in _CONSTRAINT_MARKERS)
        component_hits += any(
            _token_hit(str(item.get("title") or ""), token) for token in _COMPONENT_TITLE_MARKERS
        )
        direct_scores.append(
            float(
                sum(1 for token in _FUNCTION_ACTIONS if _token_hit(direct_text, token))
                + sum(1 for token in _FUNCTION_STATES if _token_hit(direct_text, token))
            )
        )

    autonomy_ratio = autonomy_hits / count
    constraint_ratio = constraint_hits / count
    component_ratio = component_hits / count
    parent_component = bool(
        parent
        and any(_token_hit(str(parent.get("title") or ""), token) for token in _COMPONENT_TITLE_MARKERS)
    )
    direct_evidence = min(8.0, sum(min(item, 8.0) for item in direct_scores) / count)
    count_score = min(count, 6) * 1.5 - max(0, count - 8) * 1.5

    if source == "document_top_level_sections":
        source_score = 32.0
        declaration_score = 0.0
        sibling_breadth_score = 0.0
        depth_score = 6.0
    else:
        source_score = 0.0
        declaration_score = 22.0 * declaration_ratio
        # 连续的多个同级声明比偶然的两个字段/状态更像业务分区。
        sibling_breadth_score = 4.0 * min(max(0, count - 2), 3)
        depth = int((parent or {}).get("level") or 0)
        depth_score = max(0.0, 5.0 - max(0, depth - 1) * 0.8)

    return float(
        source_score
        + declaration_score
        + sibling_breadth_score
        + 6.0 * compact_title_ratio
        + 14.0 * functional_ratio
        + 18.0 * autonomy_ratio
        + direct_evidence
        + count_score
        + depth_score
        - 28.0 * constraint_ratio
        - 14.0 * component_ratio
        - (8.0 if parent_component else 0.0)
    )


def _module_nodes(structure: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    nodes = [dict(item) for item in (structure.get("nodes") or []) if isinstance(item, dict)]
    top_level = [item for item in nodes if int(item.get("level") or 0) == 1]
    candidates: list[tuple[float, list[dict[str, Any]], str]] = []

    selected_top_level = [item for item in top_level if _functional_score(item) >= 6.0]
    if len(selected_top_level) >= 2:
        candidates.append(
            (
                _candidate_score(selected_top_level, source="document_top_level_sections"),
                selected_top_level,
                "document_top_level_sections",
            )
        )

    for parent in nodes:
        if not _is_structured_declaration_group(parent, nodes):
            continue
        selected = [
            nodes[index]
            for index in (parent.get("child_indexes") or [])
            if 0 <= index < len(nodes) and str(nodes[index].get("inline_detail") or "").strip()
        ]
        if len(selected) >= 2:
            candidates.append(
                (
                    _candidate_score(selected, source="structured_declaration_group", parent=parent),
                    selected,
                    "structured_declaration_group",
                )
            )

    if candidates:
        _, selected, source = max(candidates, key=lambda item: item[0])
        return selected, source
    return [], "none"


def _extract_interactions(text: str, modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留原文中明确共现的两个已识别模块，不推断隐式业务因果。"""
    interactions: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_text = normalize_document_text(text, preserve_source_form=True)
    for line in source_text.splitlines():
        source_line = line.strip()
        if not source_line:
            continue
        current = normalize_document_text(source_line)
        hit_rows: list[tuple[int, str]] = []
        for module in modules:
            aliases = [str(item) for item in (module.get("aliases") or []) if str(item).strip()]
            positions = [current.find(alias) for alias in aliases if alias in current]
            if positions:
                hit_rows.append((min(positions), str(module.get("module_name") or "")))
        hits = _dedupe_texts([name for _position, name in sorted(hit_rows)], limit=8)
        if len(hits) != 2:
            continue
        source, target = hits
        if source == target:
            continue
        key = f"{source}|{target}|{current}".lower()
        if key in seen:
            continue
        seen.add(key)
        interactions.append(
            {
                "source_module": source,
                "target_module": target,
                "trigger": source_line[:220],
                "evidence": [source_line[:280]],
                "relation_source": "explicit_module_cooccurrence",
            }
        )
        if len(interactions) >= 24:
            break
    return interactions


def extract_functional_architecture(requirement_text: str) -> dict[str, Any]:
    """从通用文档层级和功能证据派生一级模块，不依赖模块名称或文档模板。"""
    structure = extract_document_structure(requirement_text)
    selected_nodes, source = _module_nodes(structure)
    structure_nodes = [dict(item) for item in (structure.get("nodes") or []) if isinstance(item, dict)]
    if not selected_nodes:
        return {
            "version": "functional-architecture-v2",
            "source": "none",
            "confidence": 0.0,
            "functional_modules": [],
            "excluded_modules": [],
            "module_interactions": [],
            "shared_capabilities": [],
            "document_structure": {
                "version": structure.get("version"),
                "node_count": int(structure.get("node_count") or 0),
            },
        }

    modules: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for node in selected_nodes:
        name = str(node.get("title") or "").strip()
        if not name:
            continue
        evidence = _evidence_lines(node)
        status, marker = _scope_status(node, nodes=structure_nodes, document_text=requirement_text)
        module = {
            "module_key": f"module_{len(modules) + len(excluded) + 1:03d}",
            "module_name": name,
            "aliases": _aliases(node),
            "features": evidence[:8],
            "evidence": [str(node.get("raw_heading") or ""), *evidence[:4]],
            "scope_status": status,
            "structure_path": list(node.get("path") or []),
        }
        if marker:
            module["scope_reason"] = marker
        if status == "in_scope":
            modules.append(module)
        else:
            excluded.append(module)

    confidence = min(0.95, 0.72 + 0.04 * len(modules)) if len(modules) >= 2 else 0.58
    return {
        "version": "functional-architecture-v2",
        "source": source,
        "confidence": round(confidence, 2),
        "functional_modules": modules,
        "excluded_modules": excluded,
        "module_interactions": _extract_interactions(requirement_text, modules),
        "shared_capabilities": [],
        "document_structure": {
            "version": structure.get("version"),
            "node_count": int(structure.get("node_count") or 0),
        },
    }


def functional_module_names(project_profile: Any) -> list[str]:
    profile = dict(project_profile or {}) if isinstance(project_profile, dict) else {}
    architecture = profile.get("functional_architecture")
    if not isinstance(architecture, dict):
        return []
    return _dedupe_texts(
        [
            str(item.get("module_name") or "")
            for item in (architecture.get("functional_modules") or [])
            if isinstance(item, dict) and str(item.get("scope_status") or "in_scope") == "in_scope"
        ],
        limit=40,
    )
