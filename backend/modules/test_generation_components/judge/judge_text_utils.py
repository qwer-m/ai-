from __future__ import annotations

import re
from typing import Any


def _normalize_text(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)


def _dedupe_texts(values: list[Any] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        key = _normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output
