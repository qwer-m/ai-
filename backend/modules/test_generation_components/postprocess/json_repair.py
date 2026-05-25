"""JSON repair helpers for test generation postprocessing."""

from __future__ import annotations

import re
from typing import Any

_SEMANTIC_STOP_TOKENS = {
    "case",
    "default",
    "input",
    "module",
    "none",
    "null",
    "ok",
    "output",
    "step",
    "test",
    "测试",
    "验证",
    "用例",
}


def _normalize_for_dedup(text: Any) -> str:
    """Normalize text for duplicate detection."""
    return str(text or "").strip().lower().replace("\r", "").replace("\n", " ")


def _case_dedup_key(case: dict[str, Any]) -> str:
    """Build a deduplication key that does not depend on ``id``."""
    module = _normalize_for_dedup(case.get("test_module"))
    desc = _normalize_for_dedup(case.get("description"))
    test_input = _normalize_for_dedup(case.get("test_input"))
    expected = _normalize_for_dedup(case.get("expected_result"))
    steps = case.get("steps") or []
    if isinstance(steps, list):
        steps_text = " | ".join(_normalize_for_dedup(s) for s in steps)
    else:
        steps_text = _normalize_for_dedup(steps)
    return f"{module}||{desc}||{test_input}||{expected}||{steps_text}"


def _compact_text(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)


def _module_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", text):
        return _compact_text(text)
    root = re.split(r"[-_/(（]", text, maxsplit=1)[0]
    return _compact_text(root or text)


def _semantic_similarity_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("description", "test_module", "test_input", "expected_result"):
        value = case.get(field)
        if value is not None:
            parts.append(str(value))
    steps = case.get("steps")
    if isinstance(steps, list):
        parts.extend(str(item) for item in steps[:3] if str(item).strip())
    elif steps is not None:
        parts.append(str(steps))
    return " ".join(parts)


def _semantic_tokens(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    text = re.sub(r"tc-\d+", " ", text)
    tokens: set[str] = set()
    for segment in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        if not segment:
            continue
        if re.fullmatch(r"[a-z0-9]+", segment):
            if len(segment) > 1 and not segment.isdigit() and segment not in _SEMANTIC_STOP_TOKENS:
                tokens.add(segment)
            continue
        if len(segment) == 1:
            tokens.add(segment)
            continue
        for width in (2, 3):
            if len(segment) < width:
                continue
            for index in range(0, len(segment) - width + 1):
                token = segment[index : index + width]
                if token not in _SEMANTIC_STOP_TOKENS:
                    tokens.add(token)
    return tokens


def _semantic_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tokens = _semantic_tokens(_semantic_similarity_text(left))
    right_tokens = _semantic_tokens(_semantic_similarity_text(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = float(intersection) / float(union) if union else 0.0
    containment = float(intersection) / float(min(len(left_tokens), len(right_tokens)))
    return max(jaccard, containment)


def _semantic_overlap_size(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_tokens = _semantic_tokens(_semantic_similarity_text(left))
    right_tokens = _semantic_tokens(_semantic_similarity_text(right))
    return len(left_tokens & right_tokens)


def _same_module_family(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_module = _module_family(left.get("test_module"))
    right_module = _module_family(right.get("test_module"))
    if not left_module or not right_module:
        return True
    return left_module == right_module or left_module in right_module or right_module in left_module


def _is_semantic_duplicate(candidate: dict[str, Any], existed: dict[str, Any]) -> bool:
    if not _same_module_family(candidate, existed):
        return False
    candidate_desc = _compact_text(candidate.get("description"))
    existed_desc = _compact_text(existed.get("description"))
    if candidate_desc and existed_desc and candidate_desc == existed_desc:
        return True
    return _semantic_similarity(candidate, existed) >= 0.58 and _semantic_overlap_size(candidate, existed) >= 8


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first case for each exact structural signature.

    Near-semantic overlap is intentionally left to Review/Judge diagnostics so
    the generator does not collapse a candidate pool before coverage scoring.
    """
    if not isinstance(cases, list):
        return []
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        key = _case_dedup_key(case)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    """Count unique test cases using the deduplication key."""
    return len(deduplicate_test_cases(cases))
