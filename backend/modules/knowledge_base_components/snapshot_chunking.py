"""项目级上下文快照的限长分段构建工具。"""
from __future__ import annotations

from typing import Callable


def trim_text_head(text: str, max_chars: int) -> tuple[str, bool]:
    """按头部优先裁剪文本，返回（裁剪后文本，是否发生裁剪）。"""
    content = (text or "").strip()
    if max_chars <= 0:
        return "", bool(content)
    if len(content) <= max_chars:
        return content, False
    return content[:max_chars].rstrip(), True


def _render_doc_block(item: dict, block_title: str = "Document") -> str:
    """把单条语料渲染成统一块格式，便于后续长度预算。"""
    filename = item.get("filename") or f"doc_{item.get('doc_id')}"
    text = item.get("text") or ""
    return f"--- {block_title}: {filename} ---\n{text}"


def _render_batch_context(batch_items: list[dict], block_title: str = "Document") -> str:
    """把一个批次渲染为可压缩上下文。"""
    return "\n\n".join(_render_doc_block(item, block_title=block_title) for item in batch_items)


def split_snapshot_sources_by_limit(
    sources: list[dict],
    input_soft_limit: int,
    batch_max_docs: int,
) -> tuple[list[list[dict]], int]:
    """
    按长度和文档数把语料切分为多个批次。

    返回：
    - batches: 切分后的批次列表
    - extra_truncated_count: 因“单条块超过 soft limit”而额外裁剪的文档数
    """
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    extra_truncated_count = 0

    safe_batch_max_docs = max(1, int(batch_max_docs))
    safe_soft_limit = max(2000, int(input_soft_limit))

    for source in sources:
        block = _render_doc_block(source)
        block_chars = len(block)
        if block_chars > safe_soft_limit:
            # 单条仍超限时二次裁剪，确保不会因一条文本直接把批次打爆。
            source_filename = source.get("filename") or f"doc_{source.get('doc_id')}"
            header = f"--- Document: {source_filename} ---\n"
            body_budget = max(800, safe_soft_limit - len(header) - 16)
            clipped_text, clipped = trim_text_head(source.get("text") or "", body_budget)
            patched = dict(source)
            patched["text"] = clipped_text
            source = patched
            block = _render_doc_block(source)
            block_chars = len(block)
            if clipped:
                extra_truncated_count += 1

        # 批次达到文档上限或长度上限时先落盘当前批次。
        next_over_doc_limit = len(current) >= safe_batch_max_docs
        next_over_char_limit = current and (current_chars + block_chars > safe_soft_limit)
        if next_over_doc_limit or next_over_char_limit:
            batches.append(current)
            current = []
            current_chars = 0

        current.append(source)
        current_chars += block_chars

    if current:
        batches.append(current)

    return batches, extra_truncated_count


def _trim_text_blocks_by_limit(text_blocks: list[str], limit: int) -> list[str]:
    """按顺序保留文本块直到达到总预算。"""
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
            kept.append(candidate[:remain].rstrip())
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
    在“限长 + 分段 + 两层合并”约束下构建快照文本。

    约束：
    1. 最多两层压缩：批次压缩 -> 总合并压缩
    2. 单批失败时做降级，不让整体构建直接中断
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

        ok, summary, err = compress_fn(batch_context, batch_prompt)
        if not ok:
            # 单批失败后缩小输入重试一次，避免立即丢批。
            retry_context, _ = trim_text_head(batch_context, max(1000, int(len(batch_context) * 0.6)))
            ok, summary, err = compress_fn(retry_context, batch_prompt)

        if ok and (summary or "").strip():
            batch_outputs.append(summary.strip())
            continue

        batch_fail_count += 1
        # 二次失败时降级：保留该批次裁剪原文，避免单批拖垮全局构建。
        fallback_raw, _ = trim_text_head(batch_context, max(1200, safe_soft_limit // 2))
        if fallback_raw:
            batch_outputs.append(f"[批次{idx}降级片段]\n{fallback_raw}")

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
        merge_blocks = [f"[批次{i}摘要]\n{text}" for i, text in enumerate(batch_outputs, start=1)]
        trimmed_blocks = _trim_text_blocks_by_limit(merge_blocks, safe_final_merge_limit)
        merge_input = "\n\n".join(trimmed_blocks).strip()
        ok, merged_summary, err = compress_fn(merge_input, merge_prompt)
        if ok and (merged_summary or "").strip():
            final_text = merged_summary.strip()
            final_merge_mode = "merge_compress"
        else:
            # 总合并失败时降级到裁剪后的批次摘要拼接。
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
