from __future__ import annotations

from typing import Any, Iterable


def format_context_from_debug_chunks(chunks: Iterable[dict[str, Any]]) -> str:
    """Build a readable fallback context from debug chunks for UI diagnostics."""
    blocks: list[str] = []
    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        filename = str(chunk.get("filename") or "Unknown")
        doc_type = str(chunk.get("doc_type") or "Unknown")
        blocks.append(f"--- Relevant Knowledge: {filename} ({doc_type}) ---\n{text}")
    return "\n\n".join(blocks).strip() + ("\n\n" if blocks else "")


def context_blocked_reason(debug: dict[str, Any]) -> str:
    final_reason = str(debug.get("final_failure_reason") or "").strip()
    if final_reason:
        return final_reason
    if bool(debug.get("low_relevance_filtered")):
        return str(debug.get("low_relevance_reason") or "low_relevance_filtered").strip()
    return ""


def resolve_debug_display_fields(
    context_text: str,
    answer: str,
    debug: dict[str, Any],
) -> tuple[str, str, str]:
    """
    为当前 RAG 调试页补齐可展示字段，避免空白结果。

    - final_context: canonical retrieval context first; fallback to debug chunk preview.
    - llm_output: real model output first; fallback to a structured skip reason.
    """
    clean_context = str(context_text or "")
    clean_answer = str(answer or "")
    reason = context_blocked_reason(debug)

    fallback_context = ""
    if not clean_context:
        # Prefer final_chunks, then diverse_chunks, then rerank top.
        fallback_chunks = debug.get("final_chunks") or debug.get("diverse_chunks") or debug.get("rerank_top") or []
        if isinstance(fallback_chunks, list) and fallback_chunks:
            fallback_context = format_context_from_debug_chunks(fallback_chunks)

    display_context = clean_context or fallback_context
    display_answer = clean_answer
    if not display_answer and reason:
        display_answer = f"[LLM skipped] {reason}"

    return display_context, display_answer, reason
