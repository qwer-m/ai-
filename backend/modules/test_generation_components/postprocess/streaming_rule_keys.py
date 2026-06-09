from __future__ import annotations

import re
from typing import Any


def extract_rule_keys(case: dict[str, Any]) -> list[str]:
    text_parts: list[str] = [
        str(case.get("description") or ""),
        str(case.get("test_module") or ""),
        str(case.get("test_input") or ""),
        str(case.get("expected_result") or ""),
    ]
    steps = case.get("steps")
    if isinstance(steps, list):
        text_parts.extend([str(item) for item in steps])
    raw = " ".join(text_parts)
    keys = re.findall(r"\bREQ[-_ ]?\d+\b", raw, flags=re.IGNORECASE)
    normalized = [re.sub(r"[-_ ]+", "-", key.upper()) for key in keys]
    return sorted(set(normalized))
