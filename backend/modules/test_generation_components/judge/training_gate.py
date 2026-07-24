from __future__ import annotations

from typing import Any

from ..postprocess.json_repair import deterministic_case_dedup_key
from .judge_types import JudgeBatchResult, JudgeStatus


def _case_signature(case: dict[str, Any]) -> str:
    return deterministic_case_dedup_key(case)


def _dedupe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cases:
        if not isinstance(item, dict):
            continue
        signature = _case_signature(item)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(dict(item))
    return output


def training_gate(
    judged: JudgeBatchResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    confirmed_pass_cases: list[dict[str, Any]] = []
    repaired_pass_cases: list[dict[str, Any]] = []
    rejected_cases: list[dict[str, Any]] = []
    pending_cases: list[dict[str, Any]] = []

    for item in judged.cases:
        payload_before = item.before_case if isinstance(item.before_case, dict) else {}
        payload_after = item.after_case if isinstance(item.after_case, dict) else {}

        if item.status == JudgeStatus.PASS:
            case = payload_after if payload_after else payload_before
            if case:
                confirmed_pass_cases.append(case)
            continue

        if item.status == JudgeStatus.REPAIRABLE and bool(item.repaired_pass):
            if payload_after:
                repaired_pass_cases.append(payload_after)
            continue

        if item.status == JudgeStatus.REJECT:
            case = payload_before if payload_before else payload_after
            if case:
                rejected_cases.append(case)
            continue

        if item.status == JudgeStatus.PENDING:
            case = payload_before if payload_before else payload_after
            if case:
                pending_cases.append(case)

    return (
        _dedupe_cases(confirmed_pass_cases),
        _dedupe_cases(repaired_pass_cases),
        _dedupe_cases(rejected_cases),
        _dedupe_cases(pending_cases),
    )
