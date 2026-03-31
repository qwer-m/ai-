from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


_VALID_PRIORITIES = {"P0", "P1", "P2"}
_PRIORITY_ORDER = ("P0", "P1", "P2")

_MAX_BIZ_GROUPS = 8
_MAX_CASES_PER_BUCKET = 5
_MAX_REQUIREMENTS_PER_BIZ = 8
_MAX_SUPPLEMENTS_PER_BIZ = 6
_MAX_SUPPLEMENT_CHARS = 220


def _clip_text(text: str, max_chars: int) -> str:
    """中文注释：统一裁剪文本长度，防止提示词无限膨胀。"""
    value = str(text or "").strip()
    if not value:
        return ""
    return value if len(value) <= max_chars else value[:max_chars]


def _safe_str(value: Any, default: str) -> str:
    """中文注释：空值回退为默认值。"""
    text = str(value or "").strip()
    return text or default


def _normalize_priority(value: Any) -> str:
    """中文注释：优先级统一为 P0/P1/P2，非法值回退 P2。"""
    priority = _safe_str(value, "P2").upper()
    return priority if priority in _VALID_PRIORITIES else "P2"


def _biz_tag(biz_key: str, current_biz_key: str) -> str:
    return "当前业务" if biz_key == current_biz_key else "参考"


def _ordered_biz_keys(counts: dict[str, int], current_biz_key: str) -> list[str]:
    return sorted(
        counts.keys(),
        key=lambda key: (0 if key == current_biz_key else 1, -int(counts.get(key) or 0), key),
    )[:_MAX_BIZ_GROUPS]


def _extract_chunks_from_rag_result(rag_result: Any) -> list[dict[str, Any]]:
    """中文注释：优先从 RAG debug 中提取最终 chunk 列表。"""
    if not isinstance(rag_result, dict):
        return []
    debug = rag_result.get("debug") if isinstance(rag_result.get("debug"), dict) else {}
    if not isinstance(debug, dict):
        return []
    for key in ("final_chunks", "diverse_chunks", "dedup_chunks", "rerank_top"):
        value = debug.get(key)
        if isinstance(value, list) and value:
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_chunks_from_context_text(kb_context: str) -> list[dict[str, Any]]:
    """中文注释：当缺失 debug chunks 时，从文本降级切片。"""
    text = str(kb_context or "").strip()
    if not text:
        return []
    pattern = re.compile(r"--- Relevant Knowledge:\s*(.*?)\s*\((.*?)\)\s*---\s*\n", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [
            {
                "filename": "unknown",
                "doc_type": "unknown",
                "chunk_text": text,
                "biz_key": "unknown",
                "module": "unknown",
            }
        ]
    chunks: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunks.append(
            {
                "filename": _safe_str(match.group(1), "unknown"),
                "doc_type": _safe_str(match.group(2), "unknown"),
                "chunk_text": text[start:end].strip(),
                "biz_key": "unknown",
                "module": "unknown",
            }
        )
    return chunks


def _collect_biz_counts_from_chunks(chunks: list[dict[str, Any]]) -> dict[str, int]:
    """中文注释：统计补充上下文中的 biz_key 分布。"""
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        counts[biz_key] += 1
    return dict(counts)


def _resolve_current_biz_key(
    *, explicit_current_biz_key: str, testcase_counts: dict[str, int], supplement_counts: dict[str, int]
) -> str:
    """中文注释：优先显式 biz_key，缺失时按上下文频次推断。"""
    explicit = _safe_str(explicit_current_biz_key, "unknown")
    if explicit != "unknown":
        return explicit
    merged: dict[str, int] = defaultdict(int)
    for source in (testcase_counts, supplement_counts):
        for key, count in source.items():
            biz_key = _safe_str(key, "unknown")
            if biz_key == "unknown":
                continue
            merged[biz_key] += int(count or 0)
    if not merged:
        return "unknown"
    return sorted(merged.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _normalize_case(case: dict[str, Any], index: int) -> dict[str, str]:
    """中文注释：统一 testcase 字段并兜底缺省值。"""
    metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    return {
        "id": _safe_str(case.get("id"), f"TC-AUTO-{index:03d}"),
        "description": _clip_text(_safe_str(case.get("description"), ""), 160),
        "biz_key": _safe_str(case.get("biz_key") or metadata.get("biz_key"), "unknown"),
        "test_module": _safe_str(case.get("test_module"), "未分类模块"),
        "priority": _normalize_priority(case.get("priority")),
    }


def _extract_requirement_lines(text: str, limit: int) -> list[str]:
    """中文注释：抽取需求规则行（REQ-xxx / 列表项 / 句子）。"""
    src = str(text or "").strip()
    if not src:
        return []
    output: list[str] = []
    seen: set[str] = set()

    for raw in re.split(r"[\n\r]+", src):
        line = re.sub(r"^\s*[-*•]\s*", "", str(raw or "").strip())
        if not line:
            continue
        if re.search(r"\bREQ[-_\s]?\d+\b", line, flags=re.IGNORECASE):
            if line not in seen:
                output.append(line)
                seen.add(line)

    for raw in re.split(r"[。；;]+", src):
        line = re.sub(r"^\s*[-*•]\s*", "", str(raw or "").strip())
        if len(line) < 6 or line in seen:
            continue
        output.append(line)
        seen.add(line)
        if len(output) >= max(1, int(limit)):
            break

    return output[: max(1, int(limit))]


def _build_testcase_context(
    *, existing_cases: list[dict[str, Any]] | None, current_biz_key: str, only_current_biz: bool, max_chars: int = 8000
) -> tuple[str, dict[str, int], dict[str, int], str, dict[str, str]]:
    """中文注释：按 biz_key->module->priority 分组 testcase，上屏+按 biz_key 子上下文。"""
    raw_cases = [item for item in (existing_cases or []) if isinstance(item, dict)]
    if not raw_cases:
        return "(empty)", {}, {}, "empty_cases", {}

    normalized_cases = [_normalize_case(case, idx) for idx, case in enumerate(raw_cases, start=1)]
    all_counts: dict[str, int] = defaultdict(int)
    for case in normalized_cases:
        all_counts[case["biz_key"]] += 1

    mode = "reference_allowed"
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    if bool(only_current_biz) and current_biz_key == "unknown":
        mode = "reference_allowed_current_unknown"
    elif effective_only_current:
        mode = "strict_current_only"

    grouped: dict[str, dict[str, dict[str, list[dict[str, str]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    rendered_counts: dict[str, int] = defaultdict(int)
    for case in normalized_cases:
        biz_key = case["biz_key"]
        if effective_only_current and biz_key != current_biz_key:
            continue
        bucket = grouped[biz_key][case["test_module"]][case["priority"]]
        if len(bucket) < _MAX_CASES_PER_BUCKET:
            bucket.append(case)
            rendered_counts[biz_key] += 1

    if not grouped:
        return "(empty)", dict(all_counts), {}, mode, {}

    biz_order = _ordered_biz_keys(dict(rendered_counts), current_biz_key)
    full_lines: list[str] = ["【Testcases - 按业务分组】"]
    scoped_map: dict[str, str] = {}
    for biz_key in biz_order:
        lines: list[str] = [f"### biz_key: {biz_key}（{_biz_tag(biz_key, current_biz_key)}）"]
        module_map = grouped.get(biz_key, {})
        module_order = sorted(
            module_map.keys(),
            key=lambda key: (-sum(len(items) for items in module_map[key].values()), key),
        )
        for module_name in module_order:
            lines.append(f"#### test_module: {module_name}")
            for priority in _PRIORITY_ORDER:
                items = module_map[module_name].get(priority) or []
                if not items:
                    continue
                lines.append(f"##### priority: {priority}")
                for case in items:
                    lines.append(f"* {case['id']}: {case['description'] or '（无描述）'}")
        block = "\n".join(lines)
        scoped_map[biz_key] = _clip_text(f"【Testcases - 按业务分组】\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(lines)

    return _clip_text("\n".join(full_lines), max_chars) or "(empty)", dict(all_counts), dict(rendered_counts), mode, scoped_map


def _build_requirement_context(
    *,
    requirement: str,
    chunks: list[dict[str, Any]],
    current_biz_key: str,
    only_current_biz: bool,
    max_chars: int = 12000,
) -> tuple[str, dict[str, int], dict[str, str]]:
    """中文注释：需求上下文按 biz_key 聚焦分组，当前业务为主，其他业务为参考。"""
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, list[str]] = defaultdict(list)

    for item in _extract_requirement_lines(requirement, _MAX_REQUIREMENTS_PER_BIZ):
        grouped[current_biz_key].append(item)

    for chunk in chunks[:96]:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue
        doc_type = _safe_str(chunk.get("doc_type") or metadata.get("doc_type"), "unknown").lower()
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        if ("requirement" not in doc_type) and ("需求" not in doc_type) and ("规则" not in text) and ("REQ-" not in text):
            continue
        for item in _extract_requirement_lines(text, 2):
            if len(grouped[biz_key]) < _MAX_REQUIREMENTS_PER_BIZ:
                grouped[biz_key].append(item)

    deduped: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for biz_key, items in grouped.items():
        seen: set[str] = set()
        merged: list[str] = []
        for item in items:
            key = str(item).strip()
            if (not key) or key in seen:
                continue
            seen.add(key)
            merged.append(key)
            if len(merged) >= _MAX_REQUIREMENTS_PER_BIZ:
                break
        if merged:
            deduped[biz_key] = merged
            counts[biz_key] = len(merged)

    if not deduped:
        return "(empty)", {}, {}

    biz_order = _ordered_biz_keys(counts, current_biz_key)
    full_lines: list[str] = ["【Requirements - 按业务分组】"]
    scoped_map: dict[str, str] = {}
    for biz_key in biz_order:
        block_lines = [f"### biz_key: {biz_key}（{_biz_tag(biz_key, current_biz_key)}）", ""]
        block_lines.extend([f"* {item}" for item in deduped.get(biz_key, [])])
        block = "\n".join(block_lines).strip()
        scoped_map[biz_key] = _clip_text(f"【Requirements - 按业务分组】\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(block_lines)
    return _clip_text("\n".join(full_lines), max_chars) or "(empty)", counts, scoped_map


def _build_supplement_context(
    *, chunks: list[dict[str, Any]], current_biz_key: str, only_current_biz: bool, max_chars: int = 16000
) -> tuple[str, dict[str, int], dict[str, str]]:
    """中文注释：补充上下文按 biz_key 聚合，面向边界/缺陷补充。"""
    effective_only_current = bool(only_current_biz) and current_biz_key != "unknown"
    grouped: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks[:128]:
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        biz_key = _safe_str(chunk.get("biz_key") or metadata.get("biz_key"), "unknown")
        if effective_only_current and biz_key != current_biz_key:
            continue
        filename = _safe_str(chunk.get("filename") or metadata.get("filename"), "unknown")
        snippet = _clip_text(str(chunk.get("chunk_text") or "").strip(), _MAX_SUPPLEMENT_CHARS)
        if not snippet:
            continue
        prefix = "缺陷/补充"
        lowered = snippet.lower()
        if any(key in lowered for key in ("缺陷", "bug", "失败", "错误", "异常")):
            prefix = "缺陷"
        elif any(key in lowered for key in ("建议", "优化", "补充", "建议")):
            prefix = "建议"
        if len(grouped[biz_key]) < _MAX_SUPPLEMENTS_PER_BIZ:
            grouped[biz_key].append(f"* {prefix}：{snippet}（来源: {filename}）")

    counts = {key: len(items) for key, items in grouped.items() if items}
    if not counts:
        return "(empty)", {}, {}

    biz_order = _ordered_biz_keys(counts, current_biz_key)
    full_lines: list[str] = ["【Supplement - 按业务分组】"]
    scoped_map: dict[str, str] = {}
    for biz_key in biz_order:
        block_lines = [f"### biz_key: {biz_key}（{_biz_tag(biz_key, current_biz_key)}）", ""]
        block_lines.extend(grouped.get(biz_key, []))
        block = "\n".join(block_lines).strip()
        scoped_map[biz_key] = _clip_text(f"【Supplement - 按业务分组】\n\n{block}", max_chars)
        full_lines.append("")
        full_lines.extend(block_lines)
    return _clip_text("\n".join(full_lines), max_chars) or "(empty)", counts, scoped_map


def _build_biz_key_isolation_log(
    *,
    current_biz_key: str,
    requirement_biz_counts: dict[str, int],
    testcase_biz_counts: dict[str, int],
    supplement_biz_counts: dict[str, int],
    mode: str,
) -> dict[str, Any]:
    """中文注释：构建跨 biz_key 混用检测日志，供 GEN_DIAG 透传。"""
    violations: list[dict[str, Any]] = []
    for source_name, source_counts in (
        ("requirement_context", requirement_biz_counts),
        ("testcase_context", testcase_biz_counts),
        ("supplement_context", supplement_biz_counts),
    ):
        for biz_key, count in sorted(source_counts.items(), key=lambda item: (-int(item[1] or 0), item[0])):
            if biz_key == current_biz_key:
                continue
            if current_biz_key == "unknown" and biz_key == "unknown":
                continue
            violations.append({"source": source_name, "biz_key": biz_key, "count": int(count or 0), "tag": "参考"})
    return {
        "kind": "biz_key_isolation_check",
        "current_biz_key": current_biz_key,
        "requirement_biz_keys": sorted(requirement_biz_counts.keys()),
        "testcase_biz_keys": sorted(testcase_biz_counts.keys()),
        "supplement_biz_keys": sorted(supplement_biz_counts.keys()),
        "cross_biz_detected": bool(violations),
        "mode": mode,
        "violations": violations,
    }


def build_structured_prompt_context(
    *,
    requirement: str,
    kb_context: str = "",
    rag_result: Any = None,
    existing_cases: list[dict[str, Any]] | None = None,
    current_biz_key: str = "",
    only_current_biz: bool = False,
) -> dict[str, Any]:
    """中文注释：构建结构化上下文，包含三类 context 的按 biz_key 分组结果。"""
    chunks = _extract_chunks_from_rag_result(rag_result) or _extract_chunks_from_context_text(kb_context)

    tentative_current = _safe_str(current_biz_key, "unknown")
    strict_only_enabled = bool(only_current_biz) and tentative_current != "unknown"

    testcase_context, testcase_all_counts, testcase_render_counts, testcase_mode, testcase_scoped = _build_testcase_context(
        existing_cases=existing_cases,
        current_biz_key=tentative_current,
        only_current_biz=strict_only_enabled,
    )
    supplement_raw_counts = _collect_biz_counts_from_chunks(chunks)
    resolved_current_biz = _resolve_current_biz_key(
        explicit_current_biz_key=tentative_current,
        testcase_counts=testcase_all_counts,
        supplement_counts=supplement_raw_counts,
    )

    testcase_context, _, testcase_render_counts, testcase_mode, testcase_scoped = _build_testcase_context(
        existing_cases=existing_cases,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    requirement_context, requirement_counts, requirement_scoped = _build_requirement_context(
        requirement=requirement,
        chunks=chunks,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )
    supplement_context, supplement_counts, supplement_scoped = _build_supplement_context(
        chunks=chunks,
        current_biz_key=resolved_current_biz,
        only_current_biz=strict_only_enabled,
    )

    isolation_log = _build_biz_key_isolation_log(
        current_biz_key=resolved_current_biz,
        requirement_biz_counts=requirement_counts,
        testcase_biz_counts=testcase_render_counts,
        supplement_biz_counts=supplement_counts,
        mode=testcase_mode,
    )
    if bool(only_current_biz) and (not strict_only_enabled):
        isolation_log["mode"] = "reference_allowed_current_unknown"
        isolation_log["degraded_reason"] = "current_biz_key_unknown"

    biz_keys = set(requirement_scoped.keys()) | set(testcase_scoped.keys()) | set(supplement_scoped.keys())
    if not biz_keys:
        biz_keys = {resolved_current_biz}
    biz_counts_for_order: dict[str, int] = {}
    for biz_key in biz_keys:
        biz_counts_for_order[biz_key] = int(requirement_counts.get(biz_key, 0)) + int(
            testcase_render_counts.get(biz_key, 0)
        ) + int(supplement_counts.get(biz_key, 0))
    biz_key_order = _ordered_biz_keys(biz_counts_for_order, resolved_current_biz)

    context_by_biz: dict[str, dict[str, str]] = {}
    for biz_key in biz_key_order:
        context_by_biz[biz_key] = {
            "requirement_context": requirement_scoped.get(biz_key) or requirement_context,
            "testcase_context": testcase_scoped.get(biz_key) or testcase_context,
            "supplement_context": supplement_scoped.get(biz_key) or supplement_context,
        }

    return {
        "requirement_context": requirement_context or "(empty)",
        "testcase_context": testcase_context or "(empty)",
        "supplement_context": supplement_context or "(empty)",
        "current_biz_key": resolved_current_biz,
        "only_current_biz": bool(only_current_biz),
        "biz_key_isolation_log": isolation_log,
        "biz_key_order": biz_key_order,
        "context_by_biz": context_by_biz,
    }
