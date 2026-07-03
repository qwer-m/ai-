"""Prompt context intake diagnostics for generation requests."""
from __future__ import annotations

import hashlib
import re
from typing import Any


def _approx_prompt_tokens(text: str) -> int:
    value = str(text or "")
    if not value:
        return 0
    return max(1, len(value) // 4)


def _short_hash(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _compact_preview(text: str, limit: int = 160) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _text_metrics(text: str, *, preview: bool = False) -> dict[str, Any]:
    value = str(text or "")
    payload: dict[str, Any] = {
        "chars": int(len(value)),
        "approx_tokens": int(_approx_prompt_tokens(value)),
        "sha256_16": _short_hash(value),
    }
    if preview:
        payload["preview"] = _compact_preview(value)
    return payload


def _context_noise_flags(text: str) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return ["empty"]

    flags: list[str] = []
    normalized = re.sub(r"[\s:;,\.\*#\-_/\\()\[\]<>]+|[\uFF1A\uFF1B\uFF0C\u3002\u3010\u3011\u300A\u300B]", "", value)
    if len(value) <= 24:
        flags.append("short_fragment")
    if len(normalized) <= 8:
        flags.append("label_fragment")

    dev_keywords = (
        "开发适配点",
        "技术方案",
        "接口说明",
        "数据库表",
    )
    ui_keywords = (
        "界面",
        "展示",
        "样式",
        "布局",
        "颜色",
        "文案",
    )
    workflow_keywords = (
        "点击",
        "跳转",
        "进入",
        "提交",
        "保存",
        "完成",
    )
    if any(keyword in value for keyword in dev_keywords):
        flags.append("dev_adaptation_fragment")
    if any(keyword in value for keyword in ui_keywords) and not any(keyword in value for keyword in workflow_keywords):
        flags.append("ui_visual_only_fragment")
    if re.fullmatch(r"[A-Za-z0-9_\- .:/#]+", value) and len(value) <= 40:
        flags.append("generic_ascii_fragment")
    return flags[:6]


def _extract_chunk_text(chunk: dict[str, Any]) -> str:
    for key in ("chunk_text", "text", "content", "page_content", "summary"):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _score_float(value: Any) -> float:
    try:
        return round(float(value), 4)
    except Exception:
        return 0.0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    parsed = _optional_int(value)
    return int(default) if parsed is None else int(parsed)


def _collect_prompt_source_chunks(context_result: dict[str, Any], max_sources: int) -> list[dict[str, Any]]:
    rag_result = context_result.get("rag_result") if isinstance(context_result, dict) else {}
    rag_debug = dict((rag_result or {}).get("debug") or {}) if isinstance(rag_result, dict) else {}
    raw_chunks = rag_debug.get("final_chunks") or rag_debug.get("rerank_top") or []
    chunks = [item for item in raw_chunks if isinstance(item, dict)]
    sources: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks[: max(0, int(max_sources or 0))], start=1):
        text = _extract_chunk_text(chunk)
        sources.append(
            {
                "rank": int(index),
                "doc_id": str(chunk.get("doc_id") or chunk.get("document_id") or ""),
                "chunk_id": str(chunk.get("chunk_id") or chunk.get("id") or ""),
                "filename": str(chunk.get("filename") or chunk.get("source") or chunk.get("title") or ""),
                "score": _score_float(chunk.get("final_score") or chunk.get("rerank_score") or chunk.get("score")),
                "chars": int(len(text)),
                "approx_tokens": int(_approx_prompt_tokens(text)),
                "sha256_16": _short_hash(text),
                "preview": _compact_preview(text),
                "noise_flags": _context_noise_flags(text),
            }
        )
    return sources


def _count_flags(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for flag in item.get("noise_flags") or []:
            key = str(flag or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def _summarize_semantics_by_biz(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for biz_key, payload in list(value.items())[:20]:
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "biz_key": str(biz_key or "unknown"),
                "confirmed_facts_count": int(len(payload.get("confirmed_facts") or [])),
                "scoped_rules_count": int(len(payload.get("scoped_rules") or [])),
                "pending_items_count": int(len(payload.get("pending_items") or [])),
                "forbidden_facts_count": int(len(payload.get("forbidden_facts") or [])),
                "reuse_risks_count": int(len(payload.get("reuse_risks") or [])),
            }
        )
    return rows


def build_prompt_context_intake_diagnostics(
    *,
    prompt_context: dict[str, Any] | None,
    context_result: dict[str, Any] | None = None,
    requirement: str = "",
    kb_context: str = "",
    base_prompt: str = "",
    system_prompt: str = "",
    mode: str = "",
    doc_type: str = "",
    compress: bool = False,
    project_id: int | None = None,
    request_id: str = "",
    batch_index: int | None = None,
    total_batches: int | None = None,
    attempt: int | None = None,
    expected_count: int | None = None,
    multi_pass: bool | None = None,
    generation_mode: str = "",
    model: str = "",
    max_output_tokens: int | None = None,
    max_sources: int = 8,
) -> dict[str, Any]:
    """Build the model-input ledger emitted immediately before generation."""
    prompt_context = prompt_context or {}
    context_result = context_result or {}
    fusion_debug = dict(context_result.get("fusion_debug") or {})
    snapshot_result = dict(context_result.get("snapshot_result") or {})
    rag_result = context_result.get("rag_result") if isinstance(context_result, dict) else {}
    rag_debug = dict((rag_result or {}).get("debug") or {}) if isinstance(rag_result, dict) else {}
    control_summary = dict(prompt_context.get("control_summary") or {})
    feedback_state = dict(prompt_context.get("feedback_control_state") or {})
    source_meta = dict(feedback_state.get("source_meta") or {})
    fact_profile = dict(prompt_context.get("fact_profile") or source_meta.get("fact_profile") or {})
    execution_suite_order = control_summary.get("generation_execution_independent_suite_order")
    if not isinstance(execution_suite_order, list):
        execution_suite_order = []
    generation_execution_plan_blueprint_count = _safe_int(
        control_summary.get("generation_execution_plan_blueprint_count") or 0
    )
    generation_execution_plan_step_count = _safe_int(
        control_summary.get("generation_execution_plan_step_count") or 0
    )

    section_texts = {
        "requirement_user": str(requirement or ""),
        "kb_context": str(kb_context or ""),
        "requirement_context": str(prompt_context.get("requirement_context") or ""),
        "requirement_semantics_context": str(prompt_context.get("requirement_semantics_context") or ""),
        "testcase_context": str(prompt_context.get("testcase_context") or ""),
        "supplement_context": str(prompt_context.get("supplement_context") or ""),
        "control_context": str(prompt_context.get("control_context") or ""),
        "base_prompt": str(base_prompt or ""),
        "system_prompt": str(system_prompt or ""),
        "full_input": f"{system_prompt or ''}{requirement or ''}",
    }
    section_sizes: dict[str, dict[str, Any]] = {}
    section_noise_rows: list[dict[str, Any]] = []
    for name, text in section_texts.items():
        metrics = _text_metrics(text, preview=False)
        flags = _context_noise_flags(text)
        metrics["noise_flags"] = flags
        section_sizes[name] = metrics
        section_noise_rows.append({"noise_flags": flags})

    rag_sources = _collect_prompt_source_chunks(context_result, max_sources=max_sources)
    workflow_blueprints = feedback_state.get("workflow_blueprints") or []
    if not isinstance(workflow_blueprints, list):
        workflow_blueprints = []
    generation_execution_plan_in_context = "### GENERATION EXECUTION PLAN" in section_texts["control_context"]

    risk_flags: list[str] = []
    if section_sizes["requirement_context"]["chars"] < 50:
        risk_flags.append("requirement_context_too_short")
    if (
        section_sizes["supplement_context"]["chars"] > 0
        and section_sizes["requirement_context"]["chars"] > 0
        and section_sizes["supplement_context"]["chars"] > section_sizes["requirement_context"]["chars"] * 2
    ):
        risk_flags.append("supplement_context_dominates_requirement")
    if bool(fusion_debug.get("rag_used")) and not rag_sources:
        risk_flags.append("rag_used_without_source_chunks")
    if len(workflow_blueprints) <= 0:
        risk_flags.append("workflow_blueprint_missing")
    elif generation_execution_plan_step_count <= 0 and not generation_execution_plan_in_context:
        risk_flags.append("generation_execution_plan_missing")
    elif generation_execution_plan_step_count > 0 and not generation_execution_plan_in_context:
        risk_flags.append("generation_execution_plan_not_in_control_context")
    if int(len(fact_profile.get("confirmed_facts") or [])) <= 0 and int(len(fact_profile.get("pending_items") or [])) <= 0:
        risk_flags.append("fact_profile_sparse")
    if section_sizes["full_input"]["approx_tokens"] > 20000:
        risk_flags.append("prompt_input_large_estimated")

    return {
        "kind": "prompt_context_intake",
        "diag_version": "1.0",
        "mode": str(mode or ""),
        "doc_type": str(doc_type or ""),
        "compress": bool(compress),
        "project_id": _optional_int(project_id),
        "request_id": str(request_id or ""),
        "batch_index": _optional_int(batch_index),
        "total_batches": _optional_int(total_batches),
        "attempt": _optional_int(attempt),
        "expected_count": _optional_int(expected_count),
        "multi_pass": bool(multi_pass) if multi_pass is not None else None,
        "generation_mode": str(generation_mode or ""),
        "model": str(model or ""),
        "max_output_tokens": _optional_int(max_output_tokens),
        "max_tokens_semantics": "output_tokens",
        "current_biz_key": str(prompt_context.get("current_biz_key") or "unknown"),
        "only_current_biz": bool(prompt_context.get("only_current_biz")),
        "section_sizes": section_sizes,
        "source_lanes": {
            "context_source": str(context_result.get("context_source") or "none"),
            "snapshot": {
                "used": bool(fusion_debug.get("snapshot_used")),
                "status": str(fusion_debug.get("snapshot_status") or snapshot_result.get("snapshot_status") or ""),
                "version": _safe_int(fusion_debug.get("snapshot_version") or snapshot_result.get("snapshot_version") or 0),
            },
            "rag": {
                "used": bool(fusion_debug.get("rag_used")),
                "chunk_count": _safe_int(fusion_debug.get("rag_chunk_count") or rag_debug.get("compressed_count") or 0),
                "final_status": str(rag_debug.get("final_status") or ""),
                "retrieval_profile": dict(rag_debug.get("retrieval_profile") or {}),
            },
        },
        "rag_sources": rag_sources,
        "control": {
            "workflow_blueprint_count": int(len(workflow_blueprints)),
            "generation_execution_plan_blueprint_count": int(generation_execution_plan_blueprint_count),
            "generation_execution_plan_step_count": int(generation_execution_plan_step_count),
            "generation_execution_plan_in_context": bool(generation_execution_plan_in_context),
            "generation_execution_independent_suite_order": [
                str(item) for item in execution_suite_order[:10] if str(item or "").strip()
            ],
            "must_cover_rules_count": _safe_int(control_summary.get("must_cover_rules_count") or 0),
            "quality_fix_hints_count": _safe_int(control_summary.get("quality_fix_hints_count") or 0),
            "fact_profile_source": str(fact_profile.get("profile_source") or ""),
            "fact_profile_confidence": _score_float(fact_profile.get("confidence") or 0.0),
            "fact_profile_confirmed_count": int(len(fact_profile.get("confirmed_facts") or [])),
            "fact_profile_pending_count": int(len(fact_profile.get("pending_items") or [])),
            "fact_profile_forbidden_count": int(len(fact_profile.get("forbidden_facts") or [])),
            "scoped_rules_count": int(len(prompt_context.get("scoped_rules") or [])),
            "hard_flow_constraints_count": int(len(prompt_context.get("hard_flow_constraints") or [])),
            "reuse_risks_count": int(len(prompt_context.get("reuse_risks") or [])),
        },
        "business_scope": {
            "biz_key_order": list(prompt_context.get("biz_key_order") or [])[:20],
            "module_order_hint": list(prompt_context.get("module_order_hint") or [])[:20],
            "module_order_source": str(prompt_context.get("module_order_source") or ""),
            "requirement_semantics_by_biz": _summarize_semantics_by_biz(
                prompt_context.get("requirement_semantics_by_biz")
            ),
        },
        "noise_summary": {
            "section_noise_flags": _count_flags(section_noise_rows),
            "rag_source_noise_flags": _count_flags(rag_sources),
        },
        "risk_flags": risk_flags,
    }
