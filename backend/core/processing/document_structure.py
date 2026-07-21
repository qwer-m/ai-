from __future__ import annotations

import re
import unicodedata
from typing import Any


_DOTTED_HEADING_RE = re.compile(
    r"^\s*(?P<number>\d{1,3}(?:\.\d{1,3})+)\s*[.、)]?\s*(?P<title>\S.*?)\s*$"
)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?P<number>\d{1,3})\s*[.、)]\s*(?P<title>\S.*?)\s*$"
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s*(?P<marks>#{1,6})\s+(?P<title>\S.*?)\s*$")


def normalize_document_text(value: Any, *, preserve_source_form: bool = False) -> str:
    text = str(value or "")
    if not preserve_source_form:
        text = unicodedata.normalize("NFKC", text)
    for marker in ("[Requirement Understanding]", "[Parsed Requirement Evidence]", "[Multimodal Evidence Alignment]"):
        if marker in text:
            text = text.split(marker, 1)[0]
    text = text.replace("\x01", "\n").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_title_detail(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^(?P<title>[^:：]{1,80})\s*[:：]\s*(?P<detail>.+)$", text)
    if not match:
        return text[:120], ""
    return match.group("title").strip()[:120], match.group("detail").strip()


def _numeric_parent_path(path: tuple[int, ...], nodes_by_path: dict[tuple[int, ...], int]) -> tuple[int, ...]:
    candidate = path[:-1]
    while candidate:
        if candidate in nodes_by_path:
            return candidate
        candidate = candidate[:-1]
    return ()


def extract_document_structure(value: Any, *, max_nodes: int = 400) -> dict[str, Any]:
    """解析通用编号/Markdown标题层级，不判断具体业务模块名称。"""
    text = normalize_document_text(value)
    lines = [line.strip() for line in text.splitlines()]
    nodes: list[dict[str, Any]] = []
    nodes_by_path: dict[tuple[int, ...], int] = {}
    markdown_stack: list[int] = []
    current_numeric_path: tuple[int, ...] = ()
    active_list_parent: tuple[int, ...] = ()
    active_list_number = 0
    last_top_number = 0
    synthetic_number = 100000

    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or len(line) > 180:
            continue

        path: tuple[int, ...] = ()
        level = 0
        number_text = ""
        title_text = ""
        parent_index: int | None = None

        markdown_match = _MARKDOWN_HEADING_RE.match(line)
        dotted_match = _DOTTED_HEADING_RE.match(line)
        numbered_match = _NUMBERED_HEADING_RE.match(line)
        if markdown_match:
            level = len(markdown_match.group("marks"))
            title_text = markdown_match.group("title")
            synthetic_number += 1
            markdown_stack = markdown_stack[: max(0, level - 1)]
            parent_index = markdown_stack[-1] if markdown_stack else None
            parent_path = tuple(nodes[parent_index].get("path") or []) if parent_index is not None else ()
            path = (*parent_path, synthetic_number)
            markdown_stack.append(len(nodes))
            current_numeric_path = ()
            active_list_parent = ()
            active_list_number = 0
        elif dotted_match:
            number_text = dotted_match.group("number")
            path = tuple(int(item) for item in number_text.split("."))
            level = len(path)
            title_text = dotted_match.group("title")
            last_top_number = path[0]
            current_numeric_path = path
            active_list_parent = ()
            active_list_number = 0
            parent_path = _numeric_parent_path(path, nodes_by_path)
            parent_index = nodes_by_path.get(parent_path) if parent_path else None
            markdown_stack = []
        elif numbered_match:
            number = int(numbered_match.group("number"))
            number_text = str(number)
            title_text = numbered_match.group("title")
            if markdown_stack:
                parent_index = markdown_stack[-1]
                parent_path = tuple(nodes[parent_index].get("path") or [])
                path = (*parent_path, number)
                active_list_parent = parent_path
                active_list_number = number
            else:
                continues_list = bool(active_list_parent and number == active_list_number + 1)
                advances_top = bool(last_top_number and number == last_top_number + 1)
                if continues_list:
                    path = (*active_list_parent, number)
                    active_list_number = number
                elif not last_top_number or (advances_top and not active_list_parent):
                    path = (number,)
                    last_top_number = number
                    active_list_parent = ()
                    active_list_number = 0
                elif advances_top:
                    path = (number,)
                    last_top_number = number
                    active_list_parent = ()
                    active_list_number = 0
                else:
                    parent_path = current_numeric_path or ((last_top_number,) if last_top_number else ())
                    active_list_parent = parent_path
                    active_list_number = number
                    path = (*parent_path, number)
                parent_path = _numeric_parent_path(path, nodes_by_path)
                parent_index = nodes_by_path.get(parent_path) if parent_path else None
            level = len(path)
            current_numeric_path = path
        else:
            continue

        title, inline_detail = _split_title_detail(title_text)
        if not title:
            continue
        node = {
            "node_index": len(nodes),
            "number": number_text,
            "path": list(path),
            "level": int(level),
            "title": title,
            "inline_detail": inline_detail,
            "raw_heading": line[:240],
            "line_index": int(line_index),
            "parent_index": parent_index,
            "direct_body_lines": [],
            "section_lines": [],
        }
        nodes.append(node)
        if path:
            nodes_by_path[path] = int(node["node_index"])
        if len(nodes) >= max(1, int(max_nodes)):
            break

    for index, node in enumerate(nodes):
        start = int(node["line_index"]) + 1
        direct_end = int(nodes[index + 1]["line_index"]) if index + 1 < len(nodes) else len(lines)
        section_end = len(lines)
        level = int(node["level"])
        for candidate in nodes[index + 1 :]:
            if int(candidate["level"]) <= level:
                section_end = int(candidate["line_index"])
                break
        node["direct_body_lines"] = [item for item in lines[start:direct_end] if item]
        node["section_lines"] = [item for item in lines[start:section_end] if item]

    child_indexes: dict[int, list[int]] = {}
    for node in nodes:
        parent_index = node.get("parent_index")
        if isinstance(parent_index, int):
            child_indexes.setdefault(parent_index, []).append(int(node["node_index"]))
    for node in nodes:
        node["child_indexes"] = child_indexes.get(int(node["node_index"]), [])

    return {
        "version": "document-structure-v1",
        "line_count": len(lines),
        "node_count": len(nodes),
        "nodes": nodes,
    }
