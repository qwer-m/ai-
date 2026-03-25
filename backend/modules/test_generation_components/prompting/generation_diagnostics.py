"""生成链路诊断工具（阶段2.5）。"""
from __future__ import annotations

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


def _extract_keywords(text: str, limit: int = 30) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", text or "")
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        keywords.append(token)
        if len(keywords) >= max(5, int(limit)):
            break
    return keywords


def _flatten_case_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("description", "test_module", "test_input", "expected_result"):
        value = case.get(key)
        if value:
            parts.append(str(value))
    for key in ("preconditions", "steps"):
        value = case.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value if x)
        elif isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _priority_distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    dist = {"P0": 0, "P1": 0, "P2": 0, "other": 0}
    for case in cases:
        priority = str(case.get("priority") or "").strip().upper()
        if priority in dist:
            dist[priority] += 1
        else:
            dist["other"] += 1
    return dist


def _steps_count(case: dict[str, Any]) -> int:
    steps = case.get("steps")
    if isinstance(steps, list):
        return len([x for x in steps if str(x).strip()])
    if isinstance(steps, str):
        return len([x for x in re.split(r"[\n;；。]", steps) if str(x).strip()])
    return 0


def _classify_case_type(case: dict[str, Any]) -> str:
    text = _flatten_case_text(case).lower()
    edge_tokens = [
        "边界",
        "上限",
        "下限",
        "最大",
        "最小",
        "临界",
        "threshold",
        "boundary",
        "max",
        "min",
        "越界",
    ]
    negative_tokens = [
        "失败",
        "异常",
        "错误",
        "拒绝",
        "无效",
        "非法",
        "超时",
        "not",
        "fail",
        "error",
        "exception",
    ]
    if any(token in text for token in edge_tokens):
        return "edge"
    if any(token in text for token in negative_tokens):
        return "negative"
    return "positive"


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
            "version": int(snapshot_result.get("snapshot_version") or 0),
            "fingerprint": str(snapshot_result.get("snapshot_fingerprint") or ""),
            "build_reason": snapshot_result.get("rebuild_reason"),
            "build_latency_ms": float(snapshot_result.get("build_latency_ms") or 0.0),
            "readiness_reason": fusion_debug.get("snapshot_readiness_reason"),
        },
        "rag": {
            "used": bool(fusion_debug.get("rag_used")),
            "chunk_count": int(fusion_debug.get("rag_chunk_count") or 0),
            "attempt_count": int(rag_debug.get("attempt_count") or 0),
            "final_status": rag_debug.get("final_status"),
            "final_failure_reason": rag_debug.get("final_failure_reason"),
            "retrieval_profile": rag_debug.get("retrieval_profile") or {},
        },
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
