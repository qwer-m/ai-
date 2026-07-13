from __future__ import annotations

from typing import Callable

from core.processing.semantic_chunking import semantic_head, split_semantic_text


def trim_text_head(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep a semantic head segment under the size budget."""
    return semantic_head(text or "", max_chars)


def _render_doc_block(item: dict, block_title: str = "Document") -> str:
    filename = item.get("filename") or f"doc_{item.get('doc_id')}"
    text = item.get("text") or ""
    return f"--- {block_title}: {filename} ---\n{text}"


def _render_batch_context(batch_items: list[dict], block_title: str = "Document") -> str:
    return "\n\n".join(_render_doc_block(item, block_title=block_title) for item in batch_items)


def split_snapshot_sources_by_limit(
    sources: list[dict],
    input_soft_limit: int,
    batch_max_docs: int,
) -> tuple[list[list[dict]], int]:
    """
    Split snapshot sources into batches by semantic slices and size budget.

    Returns:
    - batches: list of source slices grouped by budget
    - extra_truncated_count: number of extra truncations during over-limit guard
    """
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    extra_truncated_count = 0

    safe_batch_max_docs = max(1, int(batch_max_docs))
    safe_soft_limit = max(2000, int(input_soft_limit))
    semantic_slice_limit = max(700, min(4000, safe_soft_limit // 2))

    for source in sources:
        source_text = str(source.get("text") or "").strip()
        if not source_text:
            continue

        semantic_units = split_semantic_text(
            source_text,
            max_chars=semantic_slice_limit,
            min_chars=max(120, int(semantic_slice_limit * 0.25)),
        )
        if not semantic_units:
            semantic_units = [source_text]

        total_units = len(semantic_units)
        for idx, unit in enumerate(semantic_units, start=1):
            piece = dict(source)
            piece["text"] = unit
            if total_units > 1:
                base_filename = source.get("filename") or f"doc_{source.get('doc_id')}"
                piece["filename"] = f"{base_filename} [seg {idx}/{total_units}]"

            block = _render_doc_block(piece)
            block_chars = len(block)
            if block_chars > safe_soft_limit:
                # One semantic slice can still be too long because of huge sentence fragments.
                piece_filename = piece.get("filename") or f"doc_{piece.get('doc_id')}"
                header = f"--- Document: {piece_filename} ---\n"
                body_budget = max(800, safe_soft_limit - len(header) - 16)
                clipped_text, clipped = trim_text_head(piece.get("text") or "", body_budget)
                patched = dict(piece)
                patched["text"] = clipped_text
                piece = patched
                block = _render_doc_block(piece)
                block_chars = len(block)
                if clipped:
                    extra_truncated_count += 1

            next_over_doc_limit = len(current) >= safe_batch_max_docs
            next_over_char_limit = current and (current_chars + block_chars > safe_soft_limit)
            if next_over_doc_limit or next_over_char_limit:
                batches.append(current)
                current = []
                current_chars = 0

            current.append(piece)
            current_chars += block_chars

    if current:
        batches.append(current)

    return batches, extra_truncated_count


def _trim_text_blocks_by_limit(text_blocks: list[str], limit: int) -> list[str]:
    """Keep blocks in order until the total size reaches limit."""
    safe_limit = max(1000, int(limit))
    kept: list[str] = []
    used = 0
    for block in text_blocks:
        if not block:
            continue
        candidate = block.strip()
        if not candidate:
            continue
        block_len = len(candidate)
        if used + block_len <= safe_limit:
            kept.append(candidate)
            used += block_len
            continue
        remain = safe_limit - used
        if remain > 300:
            clipped, _ = trim_text_head(candidate, remain)
            if clipped:
                kept.append(clipped)
        break
    return kept


def build_snapshot_text_with_budget(
    sources: list[dict],
    compress_fn: Callable[[str, str], tuple[bool, str, str]],
    batch_prompt: str,
    merge_prompt: str,
    input_soft_limit: int,
    single_doc_limit: int,
    batch_max_docs: int,
    final_merge_limit: int,
) -> dict:
    """
    Build snapshot text under length constraints with a two-level compression strategy.
    """
    if not sources:
        return {
            "success": False,
            "text": "",
            "error": "empty_sources",
            "build_observability": {
                "source_total_chars": 0,
                "source_effective_doc_count": 0,
                "build_mode": "direct",
                "batch_count": 0,
                "truncated_doc_count": 0,
                "batch_fail_count": 0,
                "final_merge_mode": "none",
                "input_soft_limit": int(input_soft_limit),
                "single_doc_limit": int(single_doc_limit),
            },
        }

    safe_single_limit = max(800, int(single_doc_limit))
    safe_soft_limit = max(2000, int(input_soft_limit))
    safe_final_merge_limit = max(2000, int(final_merge_limit))

    truncated_doc_count = 0
    effective_sources: list[dict] = []
    raw_total_chars = 0
    for source in sources:
        text = (source.get("text") or "").strip()
        if not text:
            continue
        raw_total_chars += len(text)
        clipped_text, clipped = trim_text_head(text, safe_single_limit)
        if not clipped_text:
            continue
        if clipped:
            truncated_doc_count += 1
        patched = dict(source)
        patched["text"] = clipped_text
        effective_sources.append(patched)

    source_total_chars = sum(len(item.get("text") or "") for item in effective_sources)
    batches, extra_truncated_count = split_snapshot_sources_by_limit(
        effective_sources,
        input_soft_limit=safe_soft_limit,
        batch_max_docs=int(batch_max_docs),
    )
    truncated_doc_count += extra_truncated_count

    build_mode = "direct" if len(batches) <= 1 else "batched"
    batch_fail_count = 0
    final_merge_mode = "none"

    batch_outputs: list[str] = []
    for idx, batch in enumerate(batches, start=1):
        batch_context = _render_batch_context(batch, block_title="Document")
        if not batch_context.strip():
            continue

        ok, summary, _ = compress_fn(batch_context, batch_prompt)
        if not ok:
            retry_context, _ = trim_text_head(batch_context, max(1000, int(len(batch_context) * 0.6)))
            ok, summary, _ = compress_fn(retry_context, batch_prompt)

        if ok and (summary or "").strip():
            batch_outputs.append(summary.strip())
            continue

        batch_fail_count += 1
        fallback_raw, _ = trim_text_head(batch_context, max(1200, safe_soft_limit // 2))
        if fallback_raw:
            batch_outputs.append(f"[batch-{idx}-fallback]\n{fallback_raw}")

    if not batch_outputs:
        observability = {
            "source_total_chars": source_total_chars,
            "source_total_chars_raw": raw_total_chars,
            "source_effective_doc_count": len(effective_sources),
            "build_mode": build_mode,
            "batch_count": len(batches),
            "truncated_doc_count": truncated_doc_count,
            "batch_fail_count": batch_fail_count,
            "final_merge_mode": final_merge_mode,
            "input_soft_limit": safe_soft_limit,
            "single_doc_limit": safe_single_limit,
        }
        return {
            "success": False,
            "text": "",
            "error": "all_batches_failed",
            "build_observability": observability,
        }

    if len(batch_outputs) == 1:
        final_text = batch_outputs[0]
        final_merge_mode = "single_batch_passthrough"
    else:
        merge_blocks = [f"[batch-{i}-summary]\n{text}" for i, text in enumerate(batch_outputs, start=1)]
        trimmed_blocks = _trim_text_blocks_by_limit(merge_blocks, safe_final_merge_limit)
        merge_input = "\n\n".join(trimmed_blocks).strip()
        ok, merged_summary, _ = compress_fn(merge_input, merge_prompt)
        if ok and (merged_summary or "").strip():
            final_text = merged_summary.strip()
            final_merge_mode = "merge_compress"
        else:
            fallback_text, _ = trim_text_head(merge_input, safe_final_merge_limit)
            final_text = fallback_text
            final_merge_mode = "merge_fallback_raw"

    final_text, _ = trim_text_head(final_text, safe_final_merge_limit)
    observability = {
        "source_total_chars": source_total_chars,
        "source_total_chars_raw": raw_total_chars,
        "source_effective_doc_count": len(effective_sources),
        "build_mode": build_mode,
        "batch_count": len(batches),
        "truncated_doc_count": truncated_doc_count,
        "batch_fail_count": batch_fail_count,
        "final_merge_mode": final_merge_mode,
        "input_soft_limit": safe_soft_limit,
        "single_doc_limit": safe_single_limit,
    }
    if not final_text.strip():
        return {
            "success": False,
            "text": "",
            "error": "final_text_empty",
            "build_observability": observability,
        }

    return {
        "success": True,
        "text": final_text,
        "error": "",
        "build_observability": observability,
    }
