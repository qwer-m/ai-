from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from ..coverage.coverage_analyzer import case_complexity_profile
from .case_access import case_flat_text
from .result_postprocess_priority_semantics import score_case_priority
from .streaming_case_keys import (
    case_coverage_bucket,
    case_focus_score,
    case_priority_score,
    case_signature,
    review_case_id,
)
from .streaming_postprocess_utils import _dict_case_items
from .streaming_review_selection import is_high_signal
from .streaming_rule_keys import extract_rule_keys
from .streaming_semantic_text import jaccard_similarity, semantic_signature, semantic_tokenize
from .streaming_ui_like import is_ui_like_case


def rerank_and_cap_by_rule(
    cases: list[dict[str, Any]],
    *,
    expected_count: int = 0,
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    hits_reuse_risk_fn: Callable[[dict[str, Any], dict[str, Any] | None], bool],
    hits_soft_constraint_fn: Callable[[dict[str, Any]], bool],
    max_per_rule: int = 3,
    include_trace: bool = False,
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
    generation_profile: dict[str, Any] | None = None,
) -> Any:
    profile = dict(generation_profile or {})
    coverage_mode = str(profile.get("coverage_mode") or "").strip()
    if coverage_mode == "full_functional_regression":
        max_cases_per_rule = max(1, min(int(max_per_rule or 4), 4))
        max_ui_like_cases_per_bucket = 4
        semantic_duplicate_threshold = 0.90
    elif coverage_mode == "expanded_regression":
        max_cases_per_rule = max(1, min(int(max_per_rule or 3), 3))
        max_ui_like_cases_per_bucket = 3
        semantic_duplicate_threshold = 0.88
    elif coverage_mode == "standard_regression":
        max_cases_per_rule = max(1, min(int(max_per_rule or 3), 3))
        max_ui_like_cases_per_bucket = 3
        semantic_duplicate_threshold = 0.86
    else:
        max_cases_per_rule = max(1, min(int(max_per_rule or 1), 2))
        max_ui_like_cases_per_bucket = 2
        semantic_duplicate_threshold = 0.82

    candidate_items = _dict_case_items(cases)
    original_signatures = [case_signature(item) for item in candidate_items]
    candidate_items = deduplicate_test_cases_fn(candidate_items)
    dedup_signatures = [case_signature(item) for item in candidate_items]

    rank_entries: list[dict[str, Any]] = []
    for item in candidate_items:
        signature = case_signature(item)
        score_profile = score_case_priority(
            item,
            coverage_context=coverage_context,
            rule_diagnostics=rule_diagnostics,
        )
        missing_rule_hits = [str(x) for x in (score_profile.get("missing_rule_hits") or []) if str(x).strip()]
        core_rule_hits = [str(x) for x in (score_profile.get("core_rule_hits") or []) if str(x).strip()]
        unique_coverage_hits = [str(x) for x in (score_profile.get("unique_coverage_hits") or []) if str(x).strip()]
        has_coverage_value = bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
        focus_score = int(case_focus_score(item))
        coverage_gain_score = int(score_profile.get("coverage_gain_score") or 0)
        high_signal_seed = is_high_signal(item, score_profile)
        ui_like_case = is_ui_like_case(item, score_profile)
        reuse_risk_hit = hits_reuse_risk_fn(item, score_profile)
        complexity_score = int(case_complexity_profile(item).get("complexity_score") or 0)
        semantic_sig = semantic_signature(item, list(extract_rule_keys(item)))
        semantic_tokens = semantic_tokenize(
            case_flat_text(item, ("description", "expected_result", "test_input", "steps"), separator=" ")
        )
        entry = {
            "item": item,
            "signature": signature,
            "rule_keys": extract_rule_keys(item),
            "bucket": case_coverage_bucket(item),
            "score_profile": score_profile,
            "missing_rule_hits": missing_rule_hits,
            "core_rule_hits": core_rule_hits,
            "unique_coverage_hits": unique_coverage_hits,
            "has_coverage_value": bool(has_coverage_value),
            "focus_score": int(focus_score),
            "coverage_gain_score": int(coverage_gain_score),
            "high_signal_seed": bool(high_signal_seed),
            "ui_like_case": bool(ui_like_case),
            "reuse_risk_hit": bool(reuse_risk_hit),
            "complexity_score": int(complexity_score),
            "soft_constraint_hit": bool(hits_soft_constraint_fn(item)),
            "semantic_signature": semantic_sig,
            "semantic_tokens": semantic_tokens,
            "priority_tiebreaker": int(case_priority_score(item)),
        }
        rank_entries.append(entry)

    signal_bearing_candidate_total = int(
        sum(
            1
            for entry in rank_entries
            if bool(entry.get("rule_keys"))
            or bool(entry.get("has_coverage_value"))
            or bool(entry.get("reuse_risk_hit"))
        )
    )
    preserve_dense_quality_set = bool(
        len(rank_entries) >= 30 and signal_bearing_candidate_total == 0
    ) or bool(
        coverage_mode == "full_functional_regression" and len(rank_entries) >= 30
    )

    def _seed_sort_key(entry: dict[str, Any]) -> tuple:
        return (
            int(entry.get("has_coverage_value") or False),
            int(entry.get("high_signal_seed") or False),
            int(bool(entry.get("core_rule_hits") or [])),
            int(bool(entry.get("missing_rule_hits") or [])),
            int(bool(entry.get("unique_coverage_hits") or [])),
            int(bool(entry.get("reuse_risk_hit"))),
            int(entry.get("focus_score") or 0),
            max(0, int(entry.get("coverage_gain_score") or 0)),
            -min(8, int(entry.get("complexity_score") or 0)),
            int(not bool(entry.get("ui_like_case"))),
            int(entry.get("priority_tiebreaker") or 0),
        )

    seed_ordered_entries = sorted(
        rank_entries,
        key=lambda entry: tuple([-value for value in _seed_sort_key(entry)])
        + (
            int(bool(entry.get("soft_constraint_hit"))),
            str(entry.get("signature") or ""),
        ),
    )
    fallback_first_entry = seed_ordered_entries[0] if seed_ordered_entries else None
    ui_like_candidates_total = int(
        sum(
            1
            for entry in seed_ordered_entries
            if bool(entry.get("ui_like_case")) and not bool(entry.get("has_coverage_value"))
        )
    )
    expected_total = max(0, int(expected_count or 0))
    ui_min_keep_baseline = 2 if expected_total >= 12 else 1
    ui_min_keep_ratio_count = int(round(float(expected_total) * 0.15))
    ui_min_keep_target = min(
        ui_like_candidates_total,
        max(ui_min_keep_baseline, min(4, ui_min_keep_ratio_count)),
    )
    remaining_entries = list(seed_ordered_entries)

    selected: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    seen_buckets: set[str] = set()
    rule_counts: dict[str, int] = {}
    primary_rule_rank_counts: dict[str, int] = {}
    ui_like_bucket_counts: dict[str, int] = {}
    selected_semantic_by_group: dict[str, list[dict[str, Any]]] = {}
    trace_decisions: dict[str, dict[str, Any]] = {}
    selected_signatures: list[str] = []
    ordered_signatures: list[str] = []
    selected_ui_like_count = 0

    rank = 0
    while remaining_entries:
        for entry in remaining_entries:
            rule_keys = list(entry.get("rule_keys") or [])
            bucket = str(entry.get("bucket") or "")
            adds_rule = any(key not in seen_rules for key in rule_keys)
            adds_bucket = bucket not in seen_buckets
            has_coverage_value = bool(entry.get("has_coverage_value"))
            high_signal = is_high_signal(entry.get("item") or {}, entry.get("score_profile") or {})
            if bool(entry.get("reuse_risk_hit")):
                high_signal = True
            prefers_bucket_representative = bool(
                adds_bucket and not rule_keys and not high_signal and not has_coverage_value
            )
            dynamic_sort_key = (
                int(adds_rule),
                int(adds_bucket),
                int(prefers_bucket_representative),
                int(has_coverage_value),
                int(high_signal),
                int(bool(entry.get("core_rule_hits") or [])),
                int(bool(entry.get("missing_rule_hits") or [])),
                int(bool(entry.get("unique_coverage_hits") or [])),
                int(bool(entry.get("reuse_risk_hit"))),
                int(entry.get("focus_score") or 0),
                max(0, int(entry.get("coverage_gain_score") or 0)),
                -min(8, int(entry.get("complexity_score") or 0)),
                int(not bool(entry.get("ui_like_case"))),
                int(entry.get("priority_tiebreaker") or 0),
            )
            entry["adds_rule"] = bool(adds_rule)
            entry["adds_bucket"] = bool(adds_bucket)
            entry["high_signal_dynamic"] = bool(high_signal)
            entry["dynamic_sort_key"] = dynamic_sort_key

        remaining_entries.sort(
            key=lambda entry: tuple([-value for value in tuple(entry.get("dynamic_sort_key") or ())])
            + (
                int(bool(entry.get("soft_constraint_hit"))),
                str(entry.get("signature") or ""),
            )
        )
        current = remaining_entries.pop(0)
        rank += 1
        item = dict(current.get("item") or {})
        signature = str(current.get("signature") or "")
        rule_keys = list(current.get("rule_keys") or [])
        bucket = str(current.get("bucket") or "")
        adds_rule = bool(current.get("adds_rule"))
        adds_bucket = bool(current.get("adds_bucket"))
        has_coverage_value = bool(current.get("has_coverage_value"))
        high_signal = bool(current.get("high_signal_dynamic"))
        missing_rule_hits = [str(x) for x in (current.get("missing_rule_hits") or []) if str(x).strip()]
        core_rule_hits = [str(x) for x in (current.get("core_rule_hits") or []) if str(x).strip()]
        unique_coverage_hits = [str(x) for x in (current.get("unique_coverage_hits") or []) if str(x).strip()]
        ui_like_case = bool(current.get("ui_like_case"))
        reuse_risk_hit = bool(current.get("reuse_risk_hit"))
        soft_constraint_hit = bool(current.get("soft_constraint_hit"))
        semantic_sig = str(current.get("semantic_signature") or "")
        semantic_tokens = set(current.get("semantic_tokens") or set())
        primary_rule_key = rule_keys[0] if rule_keys else f"BUCKET::{bucket}"
        semantic_group_key = "|".join(sorted(rule_keys)) if rule_keys else f"BUCKET::{bucket}"
        gate_sort_key = list(current.get("dynamic_sort_key") or ())
        ordered_signatures.append(signature)
        drop_reason = ""
        drop_reason_detail = ""
        rule_cap_applied = False
        is_semantic_duplicate = False
        duplicate_of_case_id = ""
        retained_rank_within_rule = int(primary_rule_rank_counts.get(primary_rule_key, 0)) + 1

        if rule_keys and all(rule_counts.get(key, 0) >= max_cases_per_rule for key in rule_keys):
            rule_cap_applied = True
            drop_reason = "drop_rule_cap"
            drop_reason_detail = "drop_rule_level_cap"
        elif (
            not preserve_dense_quality_set
            and not adds_rule
            and not adds_bucket
            and not high_signal
            and not has_coverage_value
            and not reuse_risk_hit
        ):
            if not (ui_like_case and selected_ui_like_count < ui_min_keep_target):
                drop_reason = "drop_no_new_rule_no_new_bucket_no_high_signal"

        if not drop_reason and not preserve_dense_quality_set:
            existed_candidates = list(selected_semantic_by_group.get(semantic_group_key) or [])
            for existed in existed_candidates:
                existed_signature = str(existed.get("semantic_signature") or "")
                existed_tokens = set(existed.get("semantic_tokens") or set())
                if semantic_sig and semantic_sig == existed_signature:
                    is_semantic_duplicate = True
                else:
                    similarity = jaccard_similarity(semantic_tokens, existed_tokens)
                    if similarity >= semantic_duplicate_threshold:
                        is_semantic_duplicate = True
                if is_semantic_duplicate:
                    duplicate_of_case_id = str(existed.get("case_id") or "")
                    drop_reason = "drop_semantic_duplicate"
                    drop_reason_detail = "drop_semantic_duplicate"
                    break

        if not drop_reason and ui_like_case and not has_coverage_value and not reuse_risk_hit:
            ui_like_count = int(ui_like_bucket_counts.get(bucket, 0))
            if ui_like_count >= max_ui_like_cases_per_bucket and selected_ui_like_count >= ui_min_keep_target:
                drop_reason = "drop_ui_like_redundant_case"
                drop_reason_detail = "drop_ui_like_redundant_case"

        selected_flag = not bool(drop_reason)
        if selected_flag:
            selected.append(item)
            selected_signatures.append(signature)
            seen_buckets.add(bucket)
            primary_rule_rank_counts[primary_rule_key] = int(primary_rule_rank_counts.get(primary_rule_key, 0)) + 1
            retained_rank_within_rule = int(primary_rule_rank_counts.get(primary_rule_key, 0))
            if ui_like_case and not has_coverage_value and not reuse_risk_hit:
                ui_like_bucket_counts[bucket] = int(ui_like_bucket_counts.get(bucket, 0)) + 1
                selected_ui_like_count += 1
            for key in rule_keys:
                rule_counts[key] = rule_counts.get(key, 0) + 1
                seen_rules.add(key)
            selected_semantic_by_group.setdefault(semantic_group_key, []).append(
                {
                    "signature": signature,
                    "case_id": review_case_id(item),
                    "semantic_signature": semantic_sig,
                    "semantic_tokens": semantic_tokens,
                }
            )
        else:
            retained_rank_within_rule = int(primary_rule_rank_counts.get(primary_rule_key, 0))

        if include_trace:
            trace_decisions[signature] = {
                "signature": signature,
                "rank": int(rank),
                "selected": bool(selected_flag),
                "drop_reason": drop_reason or "retained",
                "drop_reason_detail": drop_reason_detail or (drop_reason or "retained"),
                "rule_keys": rule_keys,
                "bucket": bucket,
                "adds_rule": bool(adds_rule),
                "adds_bucket": bool(adds_bucket),
                "high_signal": bool(high_signal),
                "has_coverage_value": bool(has_coverage_value),
                "missing_rule_hits": missing_rule_hits,
                "core_rule_hits": core_rule_hits,
                "unique_coverage_hits": unique_coverage_hits,
                "gate_sort_key": gate_sort_key,
                "retained_reason": (
                    "retained_due_to_coverage_value"
                    if selected_flag and (not adds_rule and not adds_bucket and has_coverage_value and not reuse_risk_hit)
                    else "retained_default"
                ) if selected_flag else "",
                "priority_before_gate": str(item.get("priority") or ""),
                "priority_tiebreaker": int(current.get("priority_tiebreaker") or 0),
                "focus_score": int(case_focus_score(item)),
                "semantic_signature": semantic_sig,
                "is_semantic_duplicate": bool(is_semantic_duplicate),
                "duplicate_of_case_id": duplicate_of_case_id,
                "rule_cap_applied": bool(rule_cap_applied),
                "ui_like_case": bool(ui_like_case),
                "reuse_risk_hit": bool(reuse_risk_hit),
                "soft_constraint_hit": bool(soft_constraint_hit),
                "retained_rank_within_rule": int(retained_rank_within_rule),
            }

    if not selected and fallback_first_entry:
        fallback_min_keep = 1
        if len(seed_ordered_entries) > 1 and expected_total >= 12:
            fallback_min_keep = min(3, len(seed_ordered_entries), 2)
        fallback_entries = seed_ordered_entries[: int(max(1, fallback_min_keep))]
        selected = [dict(entry.get("item") or {}) for entry in fallback_entries if dict(entry.get("item") or {})]
        selected_signatures = [
            str(entry.get("signature") or "") for entry in fallback_entries if str(entry.get("signature") or "")
        ]
        selected_ui_like_count = int(
            sum(
                1
                for entry in fallback_entries
                if bool(entry.get("ui_like_case"))
            )
        )
        if include_trace:
            for signature in selected_signatures:
                if signature in trace_decisions:
                    trace_decisions[signature]["selected"] = True
                    trace_decisions[signature]["drop_reason"] = "retained_fallback_first"

    if selected_ui_like_count < ui_min_keep_target:
        selected_signature_set = set(selected_signatures)
        for entry in seed_ordered_entries:
            if selected_ui_like_count >= ui_min_keep_target:
                break
            signature = str(entry.get("signature") or "")
            if not signature or signature in selected_signature_set:
                continue
            if (
                not bool(entry.get("ui_like_case"))
                or bool(entry.get("has_coverage_value"))
                or bool(entry.get("reuse_risk_hit"))
            ):
                continue
            item = dict(entry.get("item") or {})
            if not item:
                continue
            selected.append(item)
            selected_signatures.append(signature)
            selected_signature_set.add(signature)
            selected_ui_like_count += 1
            if include_trace:
                trace_decisions[signature] = {
                    **dict(trace_decisions.get(signature) or {}),
                    "signature": signature,
                    "selected": True,
                    "drop_reason": "retained_min_ui_keep",
                    "drop_reason_detail": "retained_min_ui_keep",
                }

    if not include_trace:
        return selected

    original_counter = Counter(original_signatures)
    dedup_counter = Counter(dedup_signatures)
    dedup_dropped_signatures = sorted(
        {
            signature
            for signature, count in original_counter.items()
            if count > dedup_counter.get(signature, 0)
        }
    )
    trace_payload = {
        "decisions": trace_decisions,
        "selected_signatures": selected_signatures,
        "ordered_signatures": ordered_signatures,
        "dedup_dropped_signatures": dedup_dropped_signatures,
        "summary": {
            "input_count": int(len(original_signatures)),
            "dedup_input_count": int(len(dedup_signatures)),
            "selected_count": int(len(selected)),
            "dropped_count": int(max(0, len(dedup_signatures) - len(selected))),
            "drop_rule_cap_count": int(
                sum(1 for item in trace_decisions.values() if item.get("drop_reason") == "drop_rule_cap")
            ),
            "rule_cap_drop_count": int(
                sum(1 for item in trace_decisions.values() if item.get("drop_reason") == "drop_rule_cap")
            ),
            "drop_no_new_signal_count": int(
                sum(
                    1
                    for item in trace_decisions.values()
                    if item.get("drop_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal"
                )
            ),
            "ui_min_keep_target": int(ui_min_keep_target),
            "ui_selected_after_keep": int(selected_ui_like_count),
            "semantic_duplicate_drop_count": int(
                sum(1 for item in trace_decisions.values() if item.get("drop_reason") == "drop_semantic_duplicate")
            ),
            "ui_like_drop_count": int(
                sum(
                    1
                    for item in trace_decisions.values()
                    if item.get("drop_reason") == "drop_ui_like_redundant_case"
                )
            ),
            "dedup_drop_count": int(max(0, len(original_signatures) - len(dedup_signatures))),
        },
    }
    return selected, trace_payload
