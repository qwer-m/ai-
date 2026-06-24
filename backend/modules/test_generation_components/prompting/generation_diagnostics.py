"""生成链路诊断工具（阶段2.5）。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from modules.domain.stage25_switches import STAGE25_SWITCHES


_STOPWORDS = {
    "以及",
    "或者",
    "并且",
    "如果",
    "那么",
    "这个",
    "那个",
    "需要",
    "可以",
    "功能",
    "模块",
    "系统",
    "页面",
    "用户",
    "数据",
}

from .generation_diagnostics_split_helpers import (
    _extract_keywords,
    _flatten_case_text,
    _priority_distribution,
    _steps_count,
    _classify_case_type,
)


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
        "\u5f00\u53d1\u9002\u914d\u70b9",
        "\u6280\u672f\u65b9\u6848",
        "\u63a5\u53e3\u8bf4\u660e",
        "\u6570\u636e\u5e93\u8868",
    )
    ui_keywords = (
        "\u754c\u9762",
        "\u5c55\u793a",
        "\u6837\u5f0f",
        "\u5e03\u5c40",
        "\u989c\u8272",
        "\u6587\u6848",
    )
    workflow_keywords = (
        "\u70b9\u51fb",
        "\u8df3\u8f6c",
        "\u8fdb\u5165",
        "\u63d0\u4ea4",
        "\u4fdd\u5b58",
        "\u5b8c\u6210",
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


def _extract_requirement_constraints(requirement: str) -> list[str]:
    text = str(requirement or "")
    constraints: set[str] = set()
    constraints.update(re.findall(r"\d+\s*[-~至到]\s*\d+", text))
    constraints.update(re.findall(r"[<>]=?\s*\d+(?:\.\d+)?", text))
    constraints.update(
        re.findall(r"\d+\s*(?:天|日|小时|分钟|秒|周|月|years?|days?|hours?|minutes?|seconds?)", text, flags=re.IGNORECASE)
    )
    constraints.update(re.findall(r"(仅一次|只能一次|不可重复|禁止重复|唯一|必须|不得|禁止|非空|不能为空)", text))
    return sorted([x for x in constraints if str(x).strip()])[:40]


def build_coverage_diagnostics(
    *,
    requirement: str,
    generated_cases: list[dict[str, Any]],
    kb_context: str,
    fusion_debug: dict[str, Any] | None = None,
    expected_count: int | None = None,
) -> dict[str, Any]:
    """
    生成覆盖度诊断。

    该诊断仅使用轻量规则，不改变生成主结果。
    """
    fusion_debug = fusion_debug or {}
    cases = generated_cases if isinstance(generated_cases, list) else []
    keywords = _extract_keywords(requirement, limit=40)
    case_text = "\n".join(_flatten_case_text(case) for case in cases).lower()
    hit_keywords: list[str] = []
    miss_keywords: list[str] = []
    for keyword in keywords:
        if keyword.lower() in case_text:
            hit_keywords.append(keyword)
        else:
            miss_keywords.append(keyword)

    module_set = {
        str(case.get("test_module") or "").strip()
        for case in cases
        if str(case.get("test_module") or "").strip()
    }

    final_chunks = fusion_debug.get("final_chunks") or []
    anchor_terms: set[str] = set()
    for chunk in final_chunks[:6]:
        anchor_terms.update(_extract_keywords(str(chunk.get("chunk_text") or ""), limit=8))
    anchor_hits = [term for term in anchor_terms if term.lower() in case_text]

    kb_keywords = _extract_keywords(kb_context, limit=30)
    kb_hits = [kw for kw in kb_keywords if kw.lower() in case_text]
    expected = max(0, int(expected_count or 0))
    generated = len(cases)
    missing_count = max(0, expected - generated)

    positive_count = 0
    negative_count = 0
    edge_count = 0
    total_steps = 0
    for case in cases:
        case_type = _classify_case_type(case)
        if case_type == "edge":
            edge_count += 1
        elif case_type == "negative":
            negative_count += 1
        else:
            positive_count += 1
        total_steps += _steps_count(case)
    avg_steps = round((total_steps / generated), 4) if generated else 0.0

    missing_boundary_class = edge_count == 0
    missing_exception_class = negative_count == 0

    possible_gap_reasons: list[str] = []
    if expected and missing_count > 0:
        possible_gap_reasons.append("under_generated_vs_expected_count")
    count_ratio = (generated / expected) if expected > 0 else 1.0
    if expected > 0 and count_ratio < float(STAGE25_SWITCHES.coverage_min_count_ratio or 0.8):
        possible_gap_reasons.append("generation_count_ratio_low")
    req_coverage = (len(hit_keywords) / len(keywords)) if keywords else 1.0
    if req_coverage < float(STAGE25_SWITCHES.coverage_keyword_warn_threshold or 0.45):
        possible_gap_reasons.append("low_requirement_keyword_coverage")
    if missing_boundary_class:
        possible_gap_reasons.append("missing_boundary_cases")
    if missing_exception_class:
        possible_gap_reasons.append("missing_exception_cases")

    requirement_constraints = _extract_requirement_constraints(requirement)
    case_text_norm = case_text.lower()
    covered_constraints = [x for x in requirement_constraints if str(x).lower() in case_text_norm]

    return {
        "diag_version": "2.5",
        "expected_count": expected,
        "generated_count": generated,
        "missing_count": missing_count,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "edge_count": edge_count,
        "missing_boundary_class": bool(missing_boundary_class),
        "missing_exception_class": bool(missing_exception_class),
        "avg_steps": avg_steps,
        "possible_gap_reasons": possible_gap_reasons[:12],
        "generated_case_count": len(cases),
        "module_count": len(module_set),
        "modules_preview": sorted(list(module_set))[:12],
        "priority_distribution": _priority_distribution(cases),
        "requirement_keyword_count": len(keywords),
        "requirement_keyword_hit_count": len(hit_keywords),
        "requirement_keyword_coverage": round(
            (len(hit_keywords) / len(keywords)) if keywords else 0.0, 4
        ),
        "missing_requirement_keywords": miss_keywords[:15],
        "kb_keyword_count": len(kb_keywords),
        "kb_keyword_hit_count": len(kb_hits),
        "kb_keyword_coverage": round(
            (len(kb_hits) / len(kb_keywords)) if kb_keywords else 0.0, 4
        ),
        "context_anchor_term_count": len(anchor_terms),
        "context_anchor_term_hit_count": len(anchor_hits),
        "context_anchor_coverage": round(
            (len(anchor_hits) / len(anchor_terms)) if anchor_terms else 0.0, 4
        ),
        "requirement_constraints_count": len(requirement_constraints),
        "covered_constraints_count": len(covered_constraints),
        "missing_constraints_preview": [x for x in requirement_constraints if x not in covered_constraints][:12],
    }


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _chunk_relevance_distribution(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    scores: list[float] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        score = None
        for key in ("final_score", "fusion_score", "rerank_score", "score", "vector_score"):
            score = _safe_float(chunk.get(key))
            if score is not None:
                break
        if score is None:
            continue
        scores.append(score)

    if not scores:
        return {
            "sample_size": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "high_ratio": 0.0,
            "medium_ratio": 0.0,
            "low_ratio": 0.0,
            "mean_score": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
        }

    total = len(scores)
    high_count = sum(1 for score in scores if score >= 0.75)
    medium_count = sum(1 for score in scores if 0.5 <= score < 0.75)
    low_count = total - high_count - medium_count

    mean_score = sum(scores) / total
    return {
        "sample_size": int(total),
        "high_count": int(high_count),
        "medium_count": int(medium_count),
        "low_count": int(low_count),
        "high_ratio": round(high_count / total, 4),
        "medium_ratio": round(medium_count / total, 4),
        "low_ratio": round(low_count / total, 4),
        "mean_score": round(mean_score, 4),
        "min_score": round(min(scores), 4),
        "max_score": round(max(scores), 4),
    }


def build_context_compression_diagnostics(
    *,
    context_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build compression diagnostics from retrieval debug payload."""
    context_result = context_result or {}
    rag_result = context_result.get("rag_result")
    rag_debug = {}
    if isinstance(rag_result, dict):
        rag_debug = dict(rag_result.get("debug") or {})
    snapshot_result = dict(context_result.get("snapshot_result") or {})
    fusion_debug = dict(context_result.get("fusion_debug") or {})

    compressor_stats = dict(rag_debug.get("compressor_stats") or {})
    final_chunks = [item for item in (rag_debug.get("final_chunks") or []) if isinstance(item, dict)]
    rerank_top = [item for item in (rag_debug.get("rerank_top") or []) if isinstance(item, dict)]
    retrieval_profile = dict(rag_debug.get("retrieval_profile") or {})

    input_chars = int(compressor_stats.get("input_chars") or 0)
    output_chars = int(compressor_stats.get("output_chars") or 0)

    input_chunk_count = int(
        compressor_stats.get("deduped_count")
        or compressor_stats.get("input_count")
        or len(rag_debug.get("dedup_chunks") or [])
        or 0
    )
    retained_chunk_count = int(rag_debug.get("compressed_count") or len(final_chunks) or 0)

    compression_ratio = 1.0
    compression_rate = 0.0
    if input_chars > 0:
        compression_ratio = round(output_chars / input_chars, 4)
        compression_rate = round(1.0 - compression_ratio, 4)

    chunk_retention_ratio = 1.0
    if input_chunk_count > 0:
        chunk_retention_ratio = round(retained_chunk_count / input_chunk_count, 4)

    relevance_distribution = _chunk_relevance_distribution(final_chunks or rerank_top)

    snapshot_id = snapshot_result.get("snapshot_id")
    if snapshot_id in (None, ""):
        snapshot_id = fusion_debug.get("snapshot_id")
    if snapshot_id in (None, ""):
        snapshot_id = snapshot_result.get("snapshot_version")
    if snapshot_id in (None, ""):
        snapshot_id = fusion_debug.get("snapshot_version")

    corpus_hash = (
        snapshot_result.get("corpus_hash")
        or snapshot_result.get("current_corpus_hash")
        or snapshot_result.get("snapshot_corpus_hash")
        or retrieval_profile.get("corpus_hash")
        or ""
    )
    retrieval_hash = (
        rag_debug.get("retrieval_hash")
        or retrieval_profile.get("retrieval_hash")
        or ""
    )
    if not retrieval_hash:
        retrieval_basis = {
            "query": str(retrieval_profile.get("query") or ""),
            "strategy": str(retrieval_profile.get("strategy") or ""),
            "top_k": int(retrieval_profile.get("top_k") or len(rerank_top) or len(final_chunks) or 0),
            "chunk_ids": [
                str(item.get("id") or item.get("chunk_id") or item.get("doc_id") or "")
                for item in (rerank_top or final_chunks)[:30]
                if isinstance(item, dict)
            ],
        }
        basis_text = json.dumps(retrieval_basis, ensure_ascii=False, sort_keys=True)
        retrieval_hash = hashlib.sha256(basis_text.encode("utf-8")).hexdigest()[:16] if basis_text else ""

    return {
        "context_source": str(context_result.get("context_source") or "none"),
        "input_chars": int(max(0, input_chars)),
        "output_chars": int(max(0, output_chars)),
        "compression_ratio": compression_ratio,
        "compression_rate": compression_rate,
        "input_chunk_count": int(max(0, input_chunk_count)),
        "retained_chunk_count": int(max(0, retained_chunk_count)),
        "chunk_retention_ratio": chunk_retention_ratio,
        "relevance_distribution": relevance_distribution,
        "snapshot_id": str(snapshot_id or ""),
        "corpus_hash": str(corpus_hash or ""),
        "retrieval_hash": str(retrieval_hash or ""),
    }


def build_context_source_log(
    *,
    context_result: dict[str, Any] | None,
    gate_debug: dict[str, Any] | None,
    doc_type: str,
    compress: bool,
    requirement_length: int,
) -> dict[str, Any]:
    """构建最终生成上下文来源日志载荷。"""
    context_result = context_result or {}
    fusion_debug = dict(context_result.get("fusion_debug") or {})
    snapshot_result = dict(context_result.get("snapshot_result") or {})
    rag_result = dict(context_result.get("rag_result") or {})
    rag_debug = dict(rag_result.get("debug") or {})
    gate_debug = gate_debug or {}

    return {
        "kind": "gen_context_source",
        "doc_type": doc_type,
        "compress": bool(compress),
        "requirement_length": int(requirement_length),
        "context_source": context_result.get("context_source") or "none",
        "final_decision": fusion_debug.get("final_decision") or "",
        "reason_chain": list(fusion_debug.get("reason_chain") or []),
        "snapshot": {
            "status": fusion_debug.get("snapshot_status") or snapshot_result.get("snapshot_status"),
            "ready": bool(fusion_debug.get("snapshot_ready")),
            "usable_for_generation": bool(fusion_debug.get("snapshot_usable_for_generation")),
            "used": bool(fusion_debug.get("snapshot_used")),
            "fallback_reason": str(fusion_debug.get("snapshot_fallback_reason") or context_result.get("fallback_reason") or ""),
            "version": int(snapshot_result.get("snapshot_version") or 0),
            "fingerprint": str(snapshot_result.get("snapshot_fingerprint") or ""),
            "build_reason": snapshot_result.get("rebuild_reason"),
            "build_latency_ms": float(snapshot_result.get("build_latency_ms") or 0.0),
            "readiness_reason": fusion_debug.get("snapshot_readiness_reason"),
            "rebuild_triggered": bool(fusion_debug.get("snapshot_rebuild_triggered")),
            "rebuild_reason": str(fusion_debug.get("snapshot_rebuild_reason") or fusion_debug.get("snapshot_queue_reason") or ""),
        },
        "rag": {
            "used": bool(fusion_debug.get("rag_used")),
            "realtime_rag_used": bool(fusion_debug.get("realtime_rag_used")),
            "fallback_to_realtime_rag": bool(fusion_debug.get("fallback_to_realtime_rag")),
            "chunk_count": int(fusion_debug.get("rag_chunk_count") or 0),
            "attempt_count": int(rag_debug.get("attempt_count") or 0),
            "final_status": rag_debug.get("final_status"),
            "final_failure_reason": rag_debug.get("final_failure_reason"),
            "retrieval_profile": rag_debug.get("retrieval_profile") or {},
        },
        "current_document_used": bool(fusion_debug.get("current_document_used", True)),
        "gate": {
            "snapshot_wait_result": gate_debug.get("snapshot_wait_result"),
            "snapshot_wait_poll_count": gate_debug.get("snapshot_wait_poll_count"),
            "snapshot_wait_elapsed_ms": gate_debug.get("snapshot_wait_elapsed_ms"),
            "snapshot_status_before_generation": gate_debug.get("snapshot_status_before_generation"),
            "snapshot_status_after_wait": gate_debug.get("snapshot_status_after_wait"),
        },
    }


def build_gate_reason_chain(gate_debug: dict[str, Any] | None) -> list[str]:
    """将 snapshot gate 状态转为统一 reason_chain。"""
    debug = gate_debug or {}
    chain: list[str] = []
    if debug.get("snapshot_gate_enabled") is not None:
        chain.append(f"snapshot_gate_enabled:{bool(debug.get('snapshot_gate_enabled'))}")
    before = str(debug.get("snapshot_status_before_generation") or "").strip()
    after = str(debug.get("snapshot_status_after_wait") or "").strip()
    result = str(debug.get("snapshot_wait_result") or "").strip()
    if before:
        chain.append(f"snapshot_before:{before}")
    if after:
        chain.append(f"snapshot_after:{after}")
    if result:
        chain.append(f"snapshot_wait_result:{result}")
    queue_reason = str(debug.get("snapshot_wait_queue_reason") or "").strip()
    if queue_reason:
        chain.append(f"snapshot_queue_reason:{queue_reason}")
    return chain


def _normalize_context_source_mode(value: str) -> str:
    source = str(value or "").strip().lower()
    if source in {"snapshot+rag", "snapshot_plus_rag"}:
        return "snapshot_plus_rag"
    if source in {"snapshot_only"}:
        return "snapshot_only"
    if source in {"rag_only"}:
        return "rag_only"
    return "rag_only" if "rag" in source else "snapshot_only" if "snapshot" in source else "rag_only"


def build_final_context_trace(
    *,
    project_id: int,
    request_id: str,
    context_result: dict[str, Any] | None,
    gate_debug: dict[str, Any] | None,
    fallback_reason: str = "",
    abort_code: str = "",
    compressed_chars: int = 0,
) -> dict[str, Any]:
    """
    在正式模型调用前，构建最终上下文来源证据链。
    """
    context_result = context_result or {}
    fusion_debug = dict(context_result.get("fusion_debug") or {})
    snapshot_result = dict(context_result.get("snapshot_result") or {})
    rag_result = dict(context_result.get("rag_result") or {})
    rag_debug = dict(rag_result.get("debug") or {})
    gate_debug = gate_debug or {}

    rerank_top = rag_debug.get("rerank_top")
    rerank_top_k = 0
    if isinstance(rerank_top, list):
        rerank_top_k = len(rerank_top)
    elif isinstance(rag_debug.get("reranked_count"), int):
        rerank_top_k = int(rag_debug.get("reranked_count") or 0)

    reason_chain = list(fusion_debug.get("reason_chain") or [])

    return {
        "kind": "final_context_trace",
        "project_id": int(project_id),
        "request_id": str(request_id or ""),
        "snapshot_status": fusion_debug.get("snapshot_status") or snapshot_result.get("snapshot_status") or "",
        "snapshot_used": bool(fusion_debug.get("snapshot_used")),
        "snapshot_version": int(
            fusion_debug.get("snapshot_version")
            or snapshot_result.get("snapshot_version")
            or 0
        ),
        "rag_used": bool(fusion_debug.get("rag_used")),
        "rag_mode": str(fusion_debug.get("rag_mode") or fusion_debug.get("fusion_mode") or ""),
        "rag_chunk_count": int(fusion_debug.get("rag_chunk_count") or 0),
        "rerank_top_k": int(rerank_top_k),
        "compressed_chars": int(max(0, int(compressed_chars or 0))),
        "context_source_mode": _normalize_context_source_mode(
            str(context_result.get("context_source") or fusion_debug.get("fusion_mode") or "")
        ),
        "gate_result": str(gate_debug.get("snapshot_wait_result") or ""),
        "fallback_reason": str(
            fallback_reason
            or context_result.get("fallback_reason")
            or fusion_debug.get("hybrid_empty_reason")
            or ""
        ),
        "abort_code": str(abort_code or ""),
        "reason_chain": reason_chain,
    }
