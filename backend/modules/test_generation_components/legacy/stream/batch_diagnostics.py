from __future__ import annotations

import json
from collections import Counter
from typing import Any, Callable


AnalyzeCoverageFn = Callable[[str, list[dict[str, Any]]], dict[str, Any]]


def build_case_semantic_retry_instruction(rejections: list[dict[str, Any]]) -> str:
    """只汇总契约错误类型和字段，不把被拒用例正文重新塞回提示词。"""
    reason_counts: Counter[str] = Counter()
    item_reason_counts: Counter[str] = Counter()
    missing_field_counts: Counter[str] = Counter()
    for rejection in rejections if isinstance(rejections, list) else []:
        if not isinstance(rejection, dict):
            continue
        for reason in rejection.get("rejection_reasons") or []:
            text = str(reason or "").strip()
            if text:
                reason_counts[text] += 1
        for item in rejection.get("rejected_semantic_items") or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type") or "semantic_item").strip()
            reason = str(item.get("reason") or "invalid").strip()
            item_reason_counts[f"{item_type}:{reason}"] += 1
            for field in item.get("missing_or_invalid_fields") or []:
                field_name = str(field or "").strip()
                if field_name:
                    missing_field_counts[field_name] += 1
    summary = {
        "rejected_case_count": int(len(rejections or [])),
        "reason_counts": dict(reason_counts.most_common(16)),
        "item_reason_counts": dict(item_reason_counts.most_common(16)),
        "missing_field_counts": dict(missing_field_counts.most_common(16)),
    }
    semantic_skeleton = {
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": "<exact active module key>",
                    "module_name": "<exact active module name>",
                    "role": "<exact active role>",
                    "evidence": [
                        "<complete verbatim value of this case description, steps, or expected_result>"
                    ],
                    "confidence": 0.8,
                }
            ],
            "fact_ids": [],
            "interaction_ids": [],
            "workflow_stage_candidates": [],
            "precondition_states": [],
            "produced_states": [],
        }
    }
    return f"""
# --- CASE SEMANTIC CONTRACT RETRY ---
The previous cases were rejected by the strict case-semantic gate.
Field-level feedback: {json.dumps(summary, ensure_ascii=False, separators=(",", ":"))}
Regenerate complete case objects, not patches.
For every module candidate, workflow stage candidate, precondition state, and produced state, include every required field shown in CASE OUTPUT CONTRACT.
`evidence` must be an array containing one complete public-field value copied verbatim from that same case; prefer description, steps, or expected_result. Do not shorten, summarize, or paraphrase evidence. `confidence` must be a positive number.
Keep IDs aligned to the active requirement contract. Do not invent semantics and do not omit `_semantic`.
Every regenerated case must contain all six `_semantic` arrays. Start from this minimal shape, replace placeholders with active-contract values and current-case evidence, and add fact, workflow, or state candidates only when the active contract requires them:
{json.dumps(semantic_skeleton, ensure_ascii=False, separators=(",", ":"))}
`_semantic.module_candidates` must remain non-empty. Copy every directly verified active fact ID into `_semantic.fact_ids`; use [] only when no active fact applies. The other arrays may be empty only when the active contract does not apply.
For a required workflow stage, copy workflow_id, stage_id, and stage_kind exactly from the active workflow catalog and copy one complete value of this case's description, steps, or expected_result verbatim into workflow_stage_candidates[].evidence. Never replace stage evidence with a summary.
Before returning, validate every regenerated case separately. Never output a case with `_semantic` missing or with any of its six arrays omitted.
""".strip()


def build_required_stage_coverage_instruction(coverage: dict[str, Any] | None) -> str:
    """只传递执行计划同口径确认的精确阶段缺口，不传递用例正文。"""
    payload = dict(coverage or {})
    if (
        payload.get("active") is not True
        or payload.get("source_generation_allowed") is not True
    ):
        return ""
    missing_stages = [
        {
            "workflow_id": str(item.get("workflow_id") or "").strip(),
            "stage_id": str(item.get("stage_id") or "").strip(),
            "stage_kind": str(item.get("stage_kind") or "").strip(),
            "stage_order": int(item.get("stage_order") or 0),
        }
        for item in (payload.get("missing_required_stages") or [])
        if isinstance(item, dict)
        and str(item.get("workflow_id") or "").strip()
        and str(item.get("stage_id") or "").strip()
    ]
    if not missing_stages:
        return ""
    return f"""
# --- REQUIRED WORKFLOW STAGE COVERAGE ---
The accepted candidate set still misses these exact required workflow stages:
{json.dumps(missing_stages, ensure_ascii=False, separators=(",", ":"))}
Before generating independent cases, generate contract-valid candidates for these stages in stage_order.
Copy workflow_id, stage_id, and stage_kind exactly from the matching ACTIVE WORKFLOW SEMANTIC CATALOG entries. For module_candidates, copy the declared module_key, module_name, and role values exactly while citing evidence and confidence from the current case; copy interaction_ids exactly.
Each required stage needs its own executable candidate. Do not infer IDs from case text and do not use an empty workflow_stage_candidates array for a matching required stage.
`_semantic.precondition_states` and `_semantic.produced_states` may be empty; the execution plan inherits authoritative required_states and produced_states from the matching workflow step. Do not copy the catalog's typed-state arrays into the candidate.
Declare an additional typed state only when the current case's public fields provide exact evidence for it. Use canonical entity, state, source, scope, polarity, and temporal values, and do not conflict with the matching workflow step's authoritative states.
""".strip()


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
        "reasoning_chars": max(0, _metadata_int(metadata, "reasoning_chars")),
        "first_reasoning_ms": metadata.get("first_reasoning_ms"),
        "first_content_ms": metadata.get("first_content_ms"),
        "provider_total_duration_ms": metadata.get("total_duration_ms"),
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
) -> tuple[str, list[dict[str, Any]]]:
    coverage_seed = analyze_coverage_fn(str(requirement_text or ""), [])
    diagnostics = [
        item
        for item in (coverage_seed.get("rule_diagnostics") or [])
        if isinstance(item, dict) and str(item.get("rule_text") or "").strip()
    ]
    rules = diagnostics
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
        lines.append(f"{index}. {rule_id}: {rule_text}")
    lines.extend(
        [
            "Before adding a case, identify its validation goal internally.",
            "Do not generate cases for headings, notes, pending运营补充文案, or unsupported assumptions.",
        ]
    )
    return "\n".join(lines), rules
