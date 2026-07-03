from __future__ import annotations

from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _manual_quality_profile_from_control(payload: dict[str, Any]) -> dict[str, Any]:
    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
    profile = source_meta.get("manual_quality_profile") if isinstance(source_meta, dict) else None
    if isinstance(profile, dict) and profile.get("kind") == "manual_quality_profile":
        return dict(profile)
    return {}


def _ratio_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    values: dict[str, float] = {}
    total = 0.0
    for key, value in raw.items():
        text = str(key or "").strip()
        if not text:
            continue
        amount = _to_float(value)
        if amount <= 0:
            continue
        values[text] = amount
        total += amount
    if total <= 0:
        return {}
    return {key: round(float(value) / total, 6) for key, value in values.items()}


def _distribution_drift(target: Any, actual: Any) -> float:
    target_ratios = _ratio_map(target)
    actual_ratios = _ratio_map(actual)
    if not target_ratios or not actual_ratios:
        return 0.0
    keys = set(target_ratios) | set(actual_ratios)
    return round(sum(abs(target_ratios.get(key, 0.0) - actual_ratios.get(key, 0.0)) for key in keys) / 2.0, 4)


def _calibrated_high_priority_target(
    profile: dict[str, Any],
    generation_summary_payload: dict[str, Any],
) -> tuple[float, bool, str]:
    raw_target = max(0.0, min(1.0, _to_float(profile.get("high_priority_ratio"))))
    coverage_mode = str(generation_summary_payload.get("generation_coverage_mode") or "").strip().lower()
    if coverage_mode != "full_functional_regression":
        return raw_target, False, ""

    profile_case_count = _to_int(profile.get("profile_case_count"))
    final_count = _to_int(generation_summary_payload.get("final_count"))
    if profile_case_count > 0 and final_count <= int(round(float(profile_case_count) * 1.25)):
        return raw_target, False, ""

    # Human sample pools are often curated high-value examples. Full regression suites
    # must also carry P2 breadth, so cap the target used for scoring while keeping the
    # raw profile target visible in diagnostics.
    full_regression_cap = 0.60
    calibrated = min(raw_target, full_regression_cap)
    return calibrated, bool(calibrated < raw_target), "full_functional_regression_suite_mix_cap"


def _calibrated_priority_distribution_target(
    profile: dict[str, Any],
    *,
    effective_high_priority_ratio: float,
) -> Any:
    original = _ratio_map(profile.get("priority_distribution"))
    if not original:
        return profile.get("priority_distribution")
    original_high = max(0.0, min(1.0, original.get("P0", 0.0) + original.get("P1", 0.0)))
    target_high = max(0.0, min(1.0, float(effective_high_priority_ratio or 0.0)))
    if original_high <= 0 or target_high >= original_high:
        return profile.get("priority_distribution")

    scale = target_high / original_high
    calibrated: dict[str, float] = {}
    calibrated["P0"] = round(float(original.get("P0", 0.0)) * scale, 6)
    calibrated["P1"] = round(float(original.get("P1", 0.0)) * scale, 6)
    remaining = max(0.0, 1.0 - calibrated["P0"] - calibrated["P1"])
    low_keys = [key for key in original.keys() if key not in {"P0", "P1"}]
    low_total = sum(float(original.get(key, 0.0)) for key in low_keys)
    if low_keys and low_total > 0:
        for key in low_keys:
            calibrated[key] = round(remaining * (float(original.get(key, 0.0)) / low_total), 6)
    else:
        calibrated["P2"] = round(remaining, 6)
    return {key: value for key, value in calibrated.items() if value > 0}


def build_manual_delivery_metrics(
    *,
    feedback_control_debug_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
) -> dict[str, Any]:
    profile = _manual_quality_profile_from_control(feedback_control_debug_payload)
    if not profile:
        return {"applied": False}
    final_count = _to_int(generation_summary_payload.get("final_count"))
    has_final_distribution = bool(
        generation_summary_payload.get("final_priority_breakdown")
        or generation_summary_payload.get("final_module_breakdown_top")
        or ("final_display_ratio" in generation_summary_payload)
    )
    if final_count <= 0 or not has_final_distribution:
        return {
            "applied": False,
            "profile_version": str(profile.get("profile_version") or ""),
            "profile_trusted_sample_count": int(profile.get("trusted_sample_count") or 0),
            "reason": "missing_final_distribution",
        }
    raw_target_high_priority_ratio = max(0.0, min(1.0, _to_float(profile.get("high_priority_ratio"))))
    target_high_priority_ratio, high_target_calibrated, high_target_calibration_reason = (
        _calibrated_high_priority_target(profile, generation_summary_payload)
    )
    final_high_priority_ratio = max(
        0.0,
        min(1.0, _to_float(generation_summary_payload.get("final_high_priority_ratio"))),
    )
    target_display_cap = max(0.0, min(1.0, _to_float(profile.get("display_ratio_cap"))))
    final_display_ratio = max(
        0.0,
        min(1.0, _to_float(generation_summary_payload.get("final_display_ratio"))),
    )
    effective_priority_distribution_target = _calibrated_priority_distribution_target(
        profile,
        effective_high_priority_ratio=target_high_priority_ratio,
    )
    priority_drift = _distribution_drift(
        effective_priority_distribution_target,
        generation_summary_payload.get("final_priority_breakdown"),
    )
    module_drift = _distribution_drift(
        profile.get("module_distribution_top"),
        generation_summary_payload.get("final_module_breakdown_top"),
    )
    return {
        "applied": True,
        "profile_source": str(profile.get("profile_source") or ""),
        "profile_version": str(profile.get("profile_version") or ""),
        "profile_trusted_sample_count": int(profile.get("trusted_sample_count") or 0),
        "raw_target_high_priority_ratio": round(raw_target_high_priority_ratio, 4),
        "target_high_priority_ratio": round(target_high_priority_ratio, 4),
        "high_priority_target_calibrated": bool(high_target_calibrated),
        "high_priority_target_calibration_reason": str(high_target_calibration_reason),
        "final_high_priority_ratio": round(final_high_priority_ratio, 4),
        "high_priority_ratio_shortfall": round(max(0.0, target_high_priority_ratio - final_high_priority_ratio), 4),
        "effective_priority_distribution_target": effective_priority_distribution_target,
        "target_display_ratio_cap": round(target_display_cap, 4),
        "final_display_ratio": round(final_display_ratio, 4),
        "display_ratio_excess": round(max(0.0, final_display_ratio - target_display_cap), 4),
        "priority_distribution_drift": priority_drift,
        "module_distribution_drift": module_drift,
    }


__all__ = [
    "build_manual_delivery_metrics",
]
