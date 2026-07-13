from __future__ import annotations

from typing import Any, Callable


AnalyzeCoverageFn = Callable[[str, list[dict[str, Any]]], dict[str, Any]]


def extract_requirement_semantics_payload(prompt_context: dict[str, Any]) -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    for key in (
        "confirmed_facts",
        "scoped_rules",
        "pending_items",
        "reuse_declarations",
        "hard_flow_constraints",
        "reuse_risks",
    ):
        values = prompt_context.get(key)
        if isinstance(values, list):
            payload[key] = [str(item).strip() for item in values if item is not None and str(item).strip()]
        else:
            payload[key] = []
    return payload


def is_retryable_provider_error(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    fatal_markers = (
        "[额度耗尽]",
        "insufficient_quota",
        "quota exceeded",
        "billing",
        "unauthorized",
        "invalid api key",
        "permission denied",
        "content policy",
        "safety",
        "forbidden",
    )
    if any(marker in text for marker in fatal_markers):
        return False
    retryable_markers = (
        "exception occurred:",
        "incomplete chunked read",
        "peer closed connection",
        "read operation timed out",
        "read timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "remote protocol error",
        "temporarily unavailable",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "502",
        "503",
        "504",
    )
    return any(marker in text for marker in retryable_markers)


def _metadata_int(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        try:
            number = int(value)
        except Exception:
            continue
        if number >= 0:
            return number
    return -1


def build_stream_batch_token_usage(
    *,
    client: Any,
    project_id: int,
    request_id: str,
    current_biz_key: str,
    multi_pass: bool,
    generation_mode: str,
    batch_index: int,
    total_batches: int,
    attempt: int,
    need: int,
    system_prompt_text: str = "",
    requirement_text: str = "",
    output_text: str,
    duration_ms: int | None = None,
    response_chars: int | None = None,
    attempt_status: str = "",
    provider_error: str = "",
) -> dict[str, Any]:
    _ = system_prompt_text
    _ = requirement_text
    metadata = dict(getattr(client, "last_response_metadata", {}) or {})
    input_tokens = _metadata_int(metadata, "input_tokens", "prompt_tokens")
    output_tokens = _metadata_int(metadata, "output_tokens", "completion_tokens")
    estimate_method = str(metadata.get("token_estimate_method") or "").strip()
    has_provider_usage = input_tokens >= 0 and output_tokens >= 0 and not estimate_method
    token_unavailable_reason = ""
    if not has_provider_usage:
        token_unavailable_reason = "provider_usage_missing"
        if estimate_method:
            token_unavailable_reason = "provider_usage_estimated"
    return {
        "kind": "stream_batch_token_usage",
        "project_id": int(project_id),
        "request_id": str(request_id or ""),
        "current_biz_key": str(current_biz_key or "unknown"),
        "multi_pass": bool(multi_pass),
        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
        "batch_index": int(batch_index),
        "total_batches": int(total_batches),
        "attempt": int(attempt),
        "requested_count": int(need),
        "input_tokens": int(input_tokens) if has_provider_usage else None,
        "output_tokens": int(output_tokens) if has_provider_usage else None,
        "total_tokens": int(input_tokens + output_tokens) if has_provider_usage else None,
        "token_source": "provider" if has_provider_usage else "unavailable",
        "token_unavailable_reason": token_unavailable_reason,
        "estimate_method": estimate_method,
        "model": str(metadata.get("model") or getattr(client, "model", "") or ""),
        "duration_ms": int(duration_ms or 0),
        "response_chars": int(response_chars if response_chars is not None else len(output_text or "")),
        "attempt_status": str(attempt_status or ""),
        "provider_error": str(provider_error or "")[:200],
    }


def normalize_signature_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def build_case_signature(case: dict[str, Any]) -> str:
    steps = case.get("steps") if isinstance(case.get("steps"), list) else []
    return "|".join(
        [
            normalize_signature_text(case.get("test_module")),
            normalize_signature_text(case.get("description")),
            normalize_signature_text(case.get("test_input")),
            normalize_signature_text(case.get("expected_result")),
            normalize_signature_text(" ".join([str(step) for step in steps])),
        ]
    )


def is_non_assertable_expected_result(text: str) -> bool:
    normalized = normalize_signature_text(text)
    if not normalized:
        return True
    weak_tokens = (
        "正常展示",
        "符合预期",
        "执行成功",
        "返回成功",
        "结果可核对",
        "结果正确",
        "shows expected result",
        "works as expected",
        "success",
    )
    return any(normalize_signature_text(token) in normalized for token in weak_tokens)


def build_stream_coverage_plan_lite(
    requirement_text: str,
    *,
    analyze_coverage_fn: AnalyzeCoverageFn,
    max_rules: int = 16,
) -> tuple[str, list[dict[str, Any]]]:
    coverage_seed = analyze_coverage_fn(str(requirement_text or ""), [])
    diagnostics = [
        item
        for item in (coverage_seed.get("rule_diagnostics") or [])
        if isinstance(item, dict) and str(item.get("rule_text") or "").strip()
    ]
    rules = diagnostics[: max(1, int(max_rules))]
    if not rules:
        return "", []
    lines = [
        "# --- COVERAGE PLAN-LITE (internal planning, do not output this section) ---",
        "Use these confirmed requirement rules as the generation plan.",
        "Prefer one high-value case per distinct rule first; add boundary/exception/risk cases only when the rule itself supports them.",
    ]
    for index, item in enumerate(rules, start=1):
        rule_text = str(item.get("rule_text") or "").strip()
        rule_id = str(item.get("rule_id") or f"RULE-{index:03d}").strip()
        lines.append(f"{index}. {rule_id}: {rule_text[:180]}")
    lines.extend(
        [
            "Before adding a case, identify its validation goal internally.",
            "Do not generate cases for headings, notes, pending运营补充文案, or unsupported assumptions.",
        ]
    )
    return "\n".join(lines), rules
