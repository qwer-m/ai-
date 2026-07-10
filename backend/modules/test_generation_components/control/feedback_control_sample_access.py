from __future__ import annotations

import re
from typing import Any

from modules.testing.sample_case_access import sample_case_id as _shared_sample_case_id

_RULE_PATTERN = re.compile(r"\b(?:RULE|REQ)[-_ ]?[A-Z0-9]+\b", re.IGNORECASE)
_MISSING_SAMPLE_VALUE = object()

_VALID_REASON_CATEGORY = {
    "core_flow",
    "exception_path",
    "boundary_condition",
    "state_transition",
    "redundant_case",
    "display_issue",
    "other",
}


def safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def safe_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return float(default)


def doc_value(doc: Any, key: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(key, default)
    return getattr(doc, key, default)


def normalize_rule_id(raw: str) -> str:
    return re.sub(r"[-_ ]+", "-", str(raw or "").strip().upper())


def extract_rule_ids(text: str) -> list[str]:
    return [normalize_rule_id(item) for item in _RULE_PATTERN.findall(str(text or ""))]


def sample_value(sample: Any, *keys: Any, default: Any = None) -> Any:
    if keys and (not isinstance(keys[-1], str) or keys[-1] == ""):
        default = keys[-1]
        keys = keys[:-1]
    for key in keys:
        if not isinstance(key, str) or not key:
            continue
        if isinstance(sample, dict):
            if key in sample:
                return sample.get(key)
            continue
        value = getattr(sample, key, _MISSING_SAMPLE_VALUE)
        if value is not _MISSING_SAMPLE_VALUE:
            return value
    return default


def sample_case_id(sample: dict[str, Any]) -> str:
    return _shared_sample_case_id(sample, include_plain_id=False)


def normalize_reason_category(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _VALID_REASON_CATEGORY else ""


def normalize_pattern_category(raw: Any) -> str:
    return str(raw or "").strip().lower()[:64]


def normalize_expected_priority(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    return value if value in {"P0", "P1", "P2", "P3"} else ""


def normalize_signal_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value == "positive":
        return "positive"
    if value in {"pos", "good", "gold", "success", "best_practice"}:
        return "positive"
    return "negative"


def normalize_pattern_usage(raw: Any, *, signal_type: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"prefer", "avoid"}:
        return value
    return "prefer" if signal_type == "positive" else "avoid"


def normalize_comment_hint(comment: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(comment or "").strip())
    if len(cleaned) < 6:
        return ""
    return cleaned[:140]


def extract_forbidden_pattern_from_sample(*, title: str, comment: str) -> str:
    candidate = str(title or "").strip() or str(comment or "").strip()
    candidate = re.sub(r"^[\-*\d\.\s]+", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if len(candidate) < 4:
        return ""
    return candidate[:40]
