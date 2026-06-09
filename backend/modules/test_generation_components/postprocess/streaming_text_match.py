from __future__ import annotations

import re
from typing import Iterable


def normalize_match_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def normalize_match_patterns(values: Iterable[object]) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_match_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patterns.append(normalized)
    return patterns


def build_quality_hint_keywords(hints: Iterable[object], *, max_tokens_per_hint: int = 4) -> list[str]:
    keywords: list[str] = []
    for hint in hints or []:
        normalized_hint = normalize_match_text(hint)
        if not normalized_hint:
            continue
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", normalized_hint)
        for token in tokens[: max(1, int(max_tokens_per_hint))]:
            if token not in keywords:
                keywords.append(token)
    return keywords
