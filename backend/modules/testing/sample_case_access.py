from __future__ import annotations

from typing import Any

from modules.testing.case_access import (
    case_id as case_access_id,
    case_field_aliases,
    case_steps,
    case_text_field,
    case_text_list_value,
    case_text_value,
)

_PLAIN_ID_KEYS = frozenset({"id", "ID"})


def has_sample_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def sample_value(sample: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in sample and has_sample_value(sample.get(key)):
            return sample.get(key)
    return default


def _case_id_keys(*preferred_keys: str, include_plain_id: bool = True) -> tuple[str, ...]:
    keys = tuple(preferred_keys) + case_field_aliases("id")
    if include_plain_id:
        return keys
    return tuple(key for key in keys if key not in _PLAIN_ID_KEYS)


def sample_case_id(
    sample: dict[str, Any],
    *preferred_keys: str,
    include_plain_id: bool = True,
) -> str:
    preferred = sample_value(sample, *_case_id_keys(*preferred_keys, include_plain_id=include_plain_id))
    if preferred is not None:
        return case_text_value(preferred)
    if not include_plain_id:
        return ""
    return case_access_id(sample)


def sample_case_text(sample: dict[str, Any], field: str, *preferred_keys: str) -> str:
    preferred = sample_value(sample, *preferred_keys)
    if preferred is not None:
        return case_text_value(preferred)
    if field == "steps":
        return " ".join(case_steps(sample))
    return case_text_field(sample, field)


def sample_case_steps(sample: dict[str, Any], *preferred_keys: str) -> list[str]:
    preferred = sample_value(sample, *preferred_keys)
    if preferred is not None:
        return case_text_list_value(preferred)
    return case_steps(sample)
