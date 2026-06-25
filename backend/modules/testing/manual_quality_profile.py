from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from modules.testing.sample_case_access import (
    sample_case_text as _sample_case_text,
    sample_value as _sample_value,
)


_VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
_TRUSTED_SOURCE_TYPES = {
    "manual_pool_input",
    "priority_debug_manual_add",
}
_LIFECYCLE_FIELD_KEYS = (
    "ST",
    "release",
    "test_status",
    "testStatus",
    "\u6d4b\u8bd5\u72b6\u6001",
    "jira",
    "jira_ticket",
    "jiraTicket",
    "jira\u5355",
    "\u7528\u4f8b\u6267\u884c\u4eba",
    "executor",
    "case_executor",
    "supplement",
    "supplement_note",
    "\u8865\u5145\u9879",
)
_DISPLAY_TOKENS = (
    "display",
    "ui-only",
    "static ui",
    "copy",
    "style",
    "layout",
    "analytics",
    "tracking",
    "\u5c55\u793a",
    "\u6587\u6848",
    "\u6837\u5f0f",
    "\u5e03\u5c40",
    "\u57cb\u70b9",
    "\u66dd\u5149",
)


def _text(value: Any, *, max_len: int = 240) -> str:
    if isinstance(value, list):
        value = " ".join(str(item or "") for item in value)
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_len]


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _priority(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in _VALID_PRIORITIES else ""


def _signal_type(sample: dict[str, Any]) -> str:
    raw = str(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "sample_kind",
            "sampleKind",
        )
        or ""
    ).strip().lower()
    if raw in {"positive", "pos", "good", "gold", "success", "best_practice"}:
        return "positive"
    if raw in {"negative", "neg", "bad", "avoid", "anti_pattern", "antipattern"}:
        return "negative"
    usage = str(_sample_value(sample, "pattern_usage", "patternUsage") or "").strip().lower()
    if usage == "prefer":
        return "positive"
    if usage == "avoid":
        return "negative"
    return "positive" if _priority(_sample_value(sample, "expected_priority", "expectedPriority")) else "negative"


def _source_type(sample: dict[str, Any]) -> str:
    return _text(_sample_value(sample, "source_type", "sourceType", "source"), max_len=64).lower()


def _is_active_sample(sample: dict[str, Any]) -> bool:
    status = _text(_sample_value(sample, "status", "sampleStatus"), max_len=24).lower()
    governance = _text(
        _sample_value(sample, "governance_status", "pattern_status", "patternStatus"),
        max_len=24,
    ).lower()
    return status != "deleted" and governance != "disabled"


def _is_trusted_sample(sample: dict[str, Any]) -> bool:
    if not _is_active_sample(sample):
        return False
    if _boolish(_sample_value(sample, "manual_confirmed", "manualConfirmed")):
        return True
    learning_status = _text(_sample_value(sample, "learning_status", "learningStatus"), max_len=32).lower()
    if learning_status == "user_confirmed":
        return True
    return _source_type(sample) in _TRUSTED_SOURCE_TYPES


def _is_display_sample(sample: dict[str, Any]) -> bool:
    reason = _text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=80).lower()
    category = _text(_sample_value(sample, "pattern_category", "patternCategory"), max_len=80).lower()
    merged = " ".join(
        [
            reason,
            category,
            _text(_sample_value(sample, "pattern_summary", "patternSummary"), max_len=180).lower(),
            _text(_sample_case_text(sample, "description", "source_case_title", "sourceCaseTitle"), max_len=160).lower(),
            _text(
                _sample_case_text(
                    sample,
                    "expected_result",
                    "source_case_expected_result",
                    "sourceCaseExpectedResult",
                    "business_assertion",
                    "businessAssertion",
                ),
                max_len=240,
            ).lower(),
        ]
    )
    return reason == "display_issue" or any(token in merged for token in _DISPLAY_TOKENS)


def _module_name(sample: dict[str, Any]) -> str:
    return _text(
        _sample_case_text(
            sample,
            "test_module",
            "source_case_module",
            "sourceCaseModule",
        ),
        max_len=120,
    )


def _lifecycle_fields(sample: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for key in _LIFECYCLE_FIELD_KEYS:
        value = _sample_value(sample, key)
        if value in (None, "", [], {}):
            continue
        label = str(key)
        if label not in seen:
            seen.add(label)
            fields.append(label)
    return fields


def _counter_payload(counter: Counter[str], *, limit: int = 20) -> dict[str, int]:
    items = sorted(counter.items(), key=lambda item: (-int(item[1]), item[0]))
    return {key: int(value) for key, value in items[:limit] if key}


def _ratio_payload(counter: Counter[str], total: int, *, limit: int = 20) -> dict[str, float]:
    if total <= 0:
        return {}
    return {
        key: round(float(value) / float(total), 4)
        for key, value in _counter_payload(counter, limit=limit).items()
    }


def _stable_sample_identity(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_type": _signal_type(sample),
        "source_type": _source_type(sample),
        "expected_priority": _priority(_sample_value(sample, "expected_priority", "expectedPriority")),
        "module": _module_name(sample),
        "title": _text(_sample_case_text(sample, "description", "source_case_title", "sourceCaseTitle"), max_len=160),
        "assertion": _text(
            _sample_case_text(
                sample,
                "expected_result",
                "source_case_expected_result",
                "sourceCaseExpectedResult",
                "business_assertion",
                "businessAssertion",
            ),
            max_len=240,
        ),
        "pattern_summary": _text(_sample_value(sample, "pattern_summary", "patternSummary"), max_len=180),
        "pattern_canonical": _text(_sample_value(sample, "pattern_canonical", "patternCanonical"), max_len=160),
        "reason_category": _text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=64).lower(),
        "pattern_category": _text(_sample_value(sample, "pattern_category", "patternCategory"), max_len=64).lower(),
        "pattern_scope": _text(_sample_value(sample, "pattern_scope", "patternScope"), max_len=40).lower(),
        "pattern_grain": _text(_sample_value(sample, "pattern_grain", "patternGrain"), max_len=40).lower(),
        "comment": _text(_sample_value(sample, "user_comment", "userComment"), max_len=160),
        "lifecycle_fields": sorted(_lifecycle_fields(sample)),
    }


def _sample_set_hash(samples: list[dict[str, Any]]) -> str:
    identities = [_stable_sample_identity(sample) for sample in samples if isinstance(sample, dict)]
    encoded = json.dumps(
        sorted(identities, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valid_existing_profile(existing_profile: Any) -> dict[str, Any]:
    if not isinstance(existing_profile, dict):
        return {}
    if existing_profile.get("kind") != "manual_quality_profile":
        return {}
    if not existing_profile.get("sample_set_hash"):
        return {}
    return dict(existing_profile)


def build_manual_quality_profile(
    samples: list[dict[str, Any]] | None,
    *,
    project_id: int | None = None,
    user_id: int | None = None,
    existing_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_samples = [sample for sample in (samples or []) if isinstance(sample, dict)]
    active_samples = [sample for sample in raw_samples if _is_active_sample(sample)]
    trusted_samples = [sample for sample in active_samples if _is_trusted_sample(sample)]
    fallback = _valid_existing_profile(existing_profile)
    if not trusted_samples:
        return fallback

    sample_hash = _sample_set_hash(trusted_samples)
    if fallback and str(fallback.get("sample_set_hash") or "") == sample_hash:
        return fallback

    positive_samples = [sample for sample in trusted_samples if _signal_type(sample) == "positive"]
    negative_samples = [sample for sample in trusted_samples if _signal_type(sample) != "positive"]
    profile_cases = positive_samples or trusted_samples

    priority_counter: Counter[str] = Counter()
    module_counter: Counter[str] = Counter()
    pattern_category_counter: Counter[str] = Counter()
    reason_category_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    lifecycle_counter: Counter[str] = Counter()
    status_counters: dict[str, Counter[str]] = {"ST": Counter(), "release": Counter()}

    display_count = 0
    for sample in trusted_samples:
        source = _source_type(sample) or "unknown"
        source_counter[source] += 1
        if _is_display_sample(sample):
            display_count += 1
        reason = _text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=64).lower()
        if reason:
            reason_category_counter[reason] += 1
        category = _text(_sample_value(sample, "pattern_category", "patternCategory"), max_len=64).lower()
        if category:
            pattern_category_counter[category] += 1
        for field in _lifecycle_fields(sample):
            lifecycle_counter[field] += 1
        for status_field in ("ST", "release"):
            status_value = _text(_sample_value(sample, status_field), max_len=32).upper()
            if status_value:
                status_counters[status_field][status_value] += 1

    for sample in profile_cases:
        priority = _priority(_sample_value(sample, "expected_priority", "expectedPriority"))
        if priority:
            priority_counter[priority] += 1
        module = _module_name(sample)
        if module:
            module_counter[module] += 1

    profile_case_count = len(profile_cases)
    high_priority_count = int(priority_counter.get("P0", 0) + priority_counter.get("P1", 0))
    display_ratio = round(float(display_count) / float(len(trusted_samples) or 1), 4)
    display_ratio_cap = round(max(0.15, min(0.45, display_ratio + 0.10)), 4)

    return {
        "kind": "manual_quality_profile",
        "profile_source": "priority_sample_pool_manual_verified",
        "profile_version": sample_hash[:16],
        "sample_set_hash": sample_hash,
        "locked": True,
        "project_id": int(project_id or 0),
        "user_id": int(user_id or 0),
        "active_sample_count": int(len(active_samples)),
        "trusted_sample_count": int(len(trusted_samples)),
        "profile_case_count": int(profile_case_count),
        "positive_sample_count": int(len(positive_samples)),
        "negative_sample_count": int(len(negative_samples)),
        "priority_distribution": _counter_payload(priority_counter, limit=8),
        "priority_ratios": _ratio_payload(priority_counter, profile_case_count, limit=8),
        "high_priority_count": high_priority_count,
        "high_priority_ratio": round(float(high_priority_count) / float(profile_case_count or 1), 4),
        "module_distribution_top": _counter_payload(module_counter, limit=20),
        "module_ratios_top": _ratio_payload(module_counter, profile_case_count, limit=20),
        "pattern_category_distribution": _counter_payload(pattern_category_counter, limit=20),
        "reason_category_distribution": _counter_payload(reason_category_counter, limit=20),
        "source_type_distribution": _counter_payload(source_counter, limit=12),
        "execution_lifecycle_fields": list(_counter_payload(lifecycle_counter, limit=20).keys()),
        "execution_lifecycle_field_count": int(len(lifecycle_counter)),
        "execution_status_distribution": {
            key: _counter_payload(counter, limit=8)
            for key, counter in status_counters.items()
            if counter
        },
        "display_signal_count": int(display_count),
        "display_signal_ratio": display_ratio,
        "display_ratio_cap": display_ratio_cap,
    }


def manual_quality_profile_hints(profile: dict[str, Any] | None) -> list[str]:
    if not isinstance(profile, dict) or profile.get("kind") != "manual_quality_profile":
        return []
    hints: list[str] = []
    high_ratio = float(profile.get("high_priority_ratio") or 0.0)
    display_cap = float(profile.get("display_ratio_cap") or 0.0)
    if high_ratio > 0:
        hints.append(f"Manual quality profile: keep P0/P1 ratio near {int(round(high_ratio * 100))}%.")
    if display_cap > 0:
        hints.append(f"Manual quality profile: keep display-only cases <= {int(round(display_cap * 100))}%.")
    modules = list((profile.get("module_distribution_top") or {}).keys())
    if modules:
        hints.append(f"Manual quality profile: cover top modules: {', '.join(str(item) for item in modules[:6])}.")
    fields = [str(item) for item in (profile.get("execution_lifecycle_fields") or []) if str(item).strip()]
    if fields:
        hints.append(f"Manual quality profile: preserve execution lifecycle fields: {', '.join(fields[:8])}.")
    return hints[:4]
