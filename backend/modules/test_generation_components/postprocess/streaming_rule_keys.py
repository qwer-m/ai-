from __future__ import annotations

import re
from typing import Any

from .case_access import case_steps, case_text_field


def extract_rule_keys(case: dict[str, Any]) -> list[str]:
    text_parts: list[str] = [
        case_text_field(case, "description"),
        case_text_field(case, "test_module"),
        case_text_field(case, "test_input"),
        case_text_field(case, "expected_result"),
    ]
    text_parts.extend(case_steps(case))
    raw = " ".join(text_parts)
    keys = re.findall(r"\bREQ[-_ ]?\d+\b", raw, flags=re.IGNORECASE)
    normalized = [re.sub(r"[-_ ]+", "-", key.upper()) for key in keys]
    return sorted(set(normalized))
