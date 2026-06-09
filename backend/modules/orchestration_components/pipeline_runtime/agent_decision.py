from __future__ import annotations

import re
from typing import Any, Literal

from .schemas import StageKey


def _classify_failure_retryability(
    stage: StageKey,
    stage_message: str,
    stage_meta: dict[str, Any],
) -> dict[str, str]:
    message = (stage_message or "").lower()
    exception_type = str(stage_meta.get("exception_type") or "").lower()
    failed_count = int(stage_meta.get("failed") or 0)

    non_retryable_patterns = [
        r"missing pipeline requirement",
        r"missing .*baseline",
        r"saved ai api key cannot be decrypted",
        r"invalid token",
        r"api key",
        r"permission denied",
        r"not found",
        r"invalid parameter",
        r"validation",
        r"syntax",
    ]
    retryable_patterns = [
        r"timeout",
        r"timed out",
        r"temporarily unavailable",
        r"connection reset",
        r"connection aborted",
        r"connection refused",
        r"network",
        r"429",
        r"rate limit",
        r"too many requests",
        r"service unavailable",
        r"\b5\d\d\b",
        r"redis",
    ]

    if exception_type in {"valueerror", "keyerror", "permissionerror"}:
        return {"retryability": "non_retryable", "reason": f"exception_type:{exception_type}"}
    if stage == "api_automation" and failed_count > 0:
        return {"retryability": "non_retryable", "reason": "api_assertion_failures"}

    for pattern in non_retryable_patterns:
        if re.search(pattern, message):
            return {"retryability": "non_retryable", "reason": f"pattern:{pattern}"}
    for pattern in retryable_patterns:
        if re.search(pattern, message):
            return {"retryability": "retryable", "reason": f"pattern:{pattern}"}

    if exception_type in {"timeouterror", "connectionerror"}:
        return {"retryability": "retryable", "reason": f"exception_type:{exception_type}"}

    return {"retryability": "unknown", "reason": "no_match"}


def _aggregate_reviewer_decision(
    stage: StageKey,
    stage_status: str,
    stage_message: str,
    stage_meta: dict[str, Any],
    reviewer_result: dict[str, Any],
    *,
    attempt_index: int,
    max_auto_retries: int,
    auto_retry_enabled: bool,
    retry_policy: Literal["conservative", "balanced", "aggressive"] = "balanced",
) -> dict[str, Any]:
    """Combine stage status with reviewer output to decide whether to retry."""
    verdict = str(reviewer_result.get("verdict") or "")
    llm_review = str(reviewer_result.get("llm_review") or "").lower()
    llm_force_retry = "force retry" in llm_review
    llm_no_retry = "do not retry" in llm_review or "no retry" in llm_review
    llm_retry_hint = "retry" in llm_review
    can_retry = auto_retry_enabled and attempt_index <= max_auto_retries
    should_retry = False
    reason = "no_retry"
    classification = _classify_failure_retryability(stage, stage_message, stage_meta)

    if stage_status != "failed":
        should_retry = False
        reason = "stage_not_failed"
    elif not can_retry:
        should_retry = False
        reason = "retry_budget_exhausted_or_disabled"
    elif classification["retryability"] == "non_retryable":
        should_retry = False
        reason = "non_retryable_failure"
    else:
        if retry_policy == "conservative":
            should_retry = classification["retryability"] == "retryable" and (
                verdict == "needs_attention" or llm_retry_hint
            )
            reason = "conservative_retryable_only" if should_retry else "conservative_blocked"
        elif retry_policy == "aggressive":
            should_retry = verdict == "needs_attention" or llm_retry_hint or llm_force_retry
            reason = "aggressive_policy_retry" if should_retry else "aggressive_blocked"
        else:
            if classification["retryability"] == "retryable":
                should_retry = verdict == "needs_attention" or llm_retry_hint
                reason = "balanced_retryable" if should_retry else "balanced_retryable_but_blocked"
            else:
                should_retry = llm_force_retry or (verdict == "needs_attention" and llm_retry_hint)
                reason = "balanced_unknown_with_signal" if should_retry else "balanced_unknown_blocked"

        if llm_no_retry:
            should_retry = False
            reason = "llm_forbid_retry"

    return {
        "should_retry": should_retry,
        "reason": reason,
        "retryability": classification["retryability"],
        "retryability_reason": classification["reason"],
        "retry_policy": retry_policy,
        "attempt_index": attempt_index,
        "max_auto_retries": max_auto_retries,
    }
