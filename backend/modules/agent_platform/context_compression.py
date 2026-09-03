"""测试生成上下文压缩适配层。

通用 ``context_compressor`` 只认识 ``chunk_text``，而测试生成链使用带有
证据锚点的 ``evidence_catalog``。本模块负责两者之间的结构化转换，并且
保证压缩只改变模型视图，不改变平台用于锚点校验的原始证据。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from modules.knowledge_base_components.context.context_compressor import (
    compress_context,
)


DEFAULT_CONTEXT_COMPRESSION_MAX_TOKENS = 1800
MIN_CONTEXT_COMPRESSION_MAX_TOKENS = 128
MAX_CONTEXT_COMPRESSION_MAX_TOKENS = 32768


def evidence_catalog_fingerprint(evidence_catalog: dict[str, Any]) -> str:
    """为证据目录生成稳定指纹，避免恢复时复用过期的压缩选择。"""

    items = evidence_catalog.get("items")
    canonical = {
        "document_id": evidence_catalog.get("document_id"),
        "items": [dict(item) for item in items] if isinstance(items, list) else [],
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def context_compression_enabled(
    payload: dict[str, Any] | None,
    *,
    default: bool = True,
) -> bool:
    """读取压缩开关，兼容历史 ``compress`` 参数。"""

    values = dict(payload or {})
    canonical = values.get("enable_context_compression")
    if canonical is None:
        canonical = values.get("compress")
    if canonical is None:
        return bool(default)
    if isinstance(canonical, str):
        return canonical.strip().lower() in {"1", "true", "yes", "on"}
    return bool(canonical)


def context_compression_max_tokens(payload: dict[str, Any] | None) -> int:
    """读取并限制压缩预算，避免错误输入把运行链推向无限负载。"""

    raw = dict(payload or {}).get("context_compression_max_tokens")
    try:
        value = int(raw) if raw not in (None, "") else DEFAULT_CONTEXT_COMPRESSION_MAX_TOKENS
    except (TypeError, ValueError):
        value = DEFAULT_CONTEXT_COMPRESSION_MAX_TOKENS
    return max(
        MIN_CONTEXT_COMPRESSION_MAX_TOKENS,
        min(MAX_CONTEXT_COMPRESSION_MAX_TOKENS, value),
    )


def _authority_token_budget(items: list[dict[str, Any]]) -> int:
    """估算保留全部唯一证据正文所需的最低预算。

    证据目录中的每一项都可能包含唯一事实，不能把通用压缩器的全局预算
    当作硬截断线。估算算法与通用压缩器保持同一字符/token 比例，但不把
    短文本或标点项当作可丢弃噪音，确保权威事实始终可回填。
    """

    seen: set[str] = set()
    total = 0
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        total += max(1, int(len(text) / 1.6))
    return max(MIN_CONTEXT_COMPRESSION_MAX_TOKENS, total)


def compress_evidence_catalog(
    evidence_catalog: dict[str, Any],
    *,
    enabled: bool,
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把证据目录转换为模型压缩视图。

    通用压缩器只负责给出候选选择。证据目录项是权威事实的唯一来源，
    因此实际模型目录会保留全部原始 ID；候选被省略的 ID 单独记录，不能
    让 source semantics 或覆盖审计把“未送入候选排序”误判成“已检查”。
    """

    if not isinstance(evidence_catalog, dict):
        raise ValueError("evidence_catalog 必须是对象")
    raw_items = evidence_catalog.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("evidence_catalog.items 不能为空")
    items = [dict(item) for item in raw_items]
    evidence_ids = [str(item.get("evidence_id") or "").strip() for item in items]
    if any(not value for value in evidence_ids):
        raise ValueError("evidence_catalog.items 缺少 evidence_id")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_catalog.items 存在重复 evidence_id")
    catalog_fingerprint = evidence_catalog_fingerprint(
        {
            "document_id": evidence_catalog.get("document_id"),
            "items": items,
        }
    )
    try:
        requested_max_tokens = int(max_tokens or DEFAULT_CONTEXT_COMPRESSION_MAX_TOKENS)
    except (TypeError, ValueError):
        requested_max_tokens = DEFAULT_CONTEXT_COMPRESSION_MAX_TOKENS
    requested_max_tokens = max(
        MIN_CONTEXT_COMPRESSION_MAX_TOKENS,
        min(MAX_CONTEXT_COMPRESSION_MAX_TOKENS, requested_max_tokens),
    )

    if not enabled:
        selected_ids = set(evidence_ids)
        compressor_stats: dict[str, Any] = {
            "input_count": len(items),
            "deduped_count": len(items),
            "dropped_noisy": 0,
            "dropped_over_budget": 0,
            "dropped_over_budget_chunks": [],
            "kept_by_original_priority": 0,
            "input_chars": sum(len(str(item.get("text") or "")) for item in items),
            "output_chars": sum(len(str(item.get("text") or "")) for item in items),
        }
        selected_items = deepcopy(items)
        return (
            {
                "document_id": evidence_catalog.get("document_id"),
                "items": selected_items,
            },
            _compression_stats(
                enabled=False,
                max_tokens=requested_max_tokens,
                effective_max_tokens=requested_max_tokens,
                raw_items=items,
                selected_items=selected_items,
                selected_ids=selected_ids,
                forced_ids=[],
                candidate_omitted_ids=[],
                compressor_stats=compressor_stats,
                catalog_fingerprint=catalog_fingerprint,
            ),
        )

    # 中文注释：压缩器必须严格遵守调用方预算；权威证据即使超预算也通过
    # 独立的全量目录保留，不能用动态扩预算掩盖上下文超限。
    authority_token_estimate = _authority_token_budget(items)
    effective_max_tokens = requested_max_tokens
    chunks = [
        {
            "chunk_text": str(item.get("text") or ""),
            # 证据目录本身已经按真实来源排序，分数只用于稳定处理重复项。
            "score": float(len(items) - index),
            "query_source": "original",
            "evidence_id": evidence_ids[index],
        }
        for index, item in enumerate(items)
    ]
    compressed = compress_context(
        chunks=chunks,
        # 中文注释：严格按调用方预算生成候选，权威项由外层单独强制保留并记录溢出。
        max_tokens=effective_max_tokens,
        keep_original_top_n=2,
    )
    candidate_selected_ids = {
        str(chunk.get("evidence_id") or "").strip()
        for chunk in list(compressed.get("selected_chunks") or [])
        if str(chunk.get("evidence_id") or "").strip()
    }

    candidate_omitted_ids = [
        evidence_id for evidence_id in evidence_ids if evidence_id not in candidate_selected_ids
    ]
    # 中文注释：每个 evidence_id 都是可追踪事实源，不能因通用排序结果丢失。
    selected_ids = set(evidence_ids)
    forced_ids = list(candidate_omitted_ids)

    selected_items = [
        deepcopy(item)
        for item, evidence_id in zip(items, evidence_ids)
        if evidence_id in selected_ids
    ]
    candidate_items = [
        deepcopy(item)
        for item, evidence_id in zip(items, evidence_ids)
        if evidence_id in candidate_selected_ids
    ]
    return (
        {
            "document_id": evidence_catalog.get("document_id"),
            "items": selected_items,
            # 候选目录只用于观测和后续可证明安全的模型投影，不能替代权威目录。
            "candidate_items": candidate_items,
        },
        _compression_stats(
            enabled=True,
            max_tokens=requested_max_tokens,
            effective_max_tokens=effective_max_tokens,
            authority_token_estimate=authority_token_estimate,
            raw_items=items,
            selected_items=selected_items,
            selected_ids=selected_ids,
            forced_ids=forced_ids,
            candidate_omitted_ids=candidate_omitted_ids,
            compressor_stats=dict(compressed.get("stats") or {}),
            candidate_selected_ids=candidate_selected_ids,
            catalog_fingerprint=catalog_fingerprint,
        ),
    )


def _compression_stats(
    *,
    enabled: bool,
    max_tokens: int,
    effective_max_tokens: int,
    authority_token_estimate: int = 0,
    raw_items: list[dict[str, Any]],
    selected_items: list[dict[str, Any]],
    selected_ids: set[str],
    forced_ids: list[str],
    candidate_omitted_ids: list[str],
    compressor_stats: dict[str, Any],
    candidate_selected_ids: set[str] | None = None,
    catalog_fingerprint: str = "",
) -> dict[str, Any]:
    raw_ids = [str(item.get("evidence_id") or "") for item in raw_items]
    omitted_ids = [
        evidence_id for evidence_id in raw_ids if evidence_id not in selected_ids
    ]
    raw_chars = sum(len(str(item.get("text") or "")) for item in raw_items)
    selected_chars = sum(len(str(item.get("text") or "")) for item in selected_items)
    candidate_selected_ids = set(candidate_selected_ids or selected_ids)
    candidate_selected_items = [
        item for item in raw_items if str(item.get("evidence_id") or "") in candidate_selected_ids
    ]
    candidate_chars = sum(len(str(item.get("text") or "")) for item in candidate_selected_items)
    return {
        "enabled": bool(enabled),
        # 当前来源证据采用无损权威模式；这个字段明确表示是否真的改变了模型目录。
        "compression_mode": "lossless_authoritative" if enabled else "disabled",
        "model_view_mode": (
            "full_authoritative"
            if enabled and selected_chars == raw_chars
            else "candidate"
            if enabled
            else "disabled"
        ),
        # selected_* 是实际送入来源语义阶段的权威视图；candidate_* 仅表示候选压缩结果。
        "model_reduction_applied": bool(enabled and selected_chars < raw_chars),
        "candidate_reduction_applied": bool(enabled and candidate_chars < raw_chars),
        "max_tokens": int(max_tokens),
        "effective_max_tokens": int(effective_max_tokens),
        # 保留旧字段名，但严格预算策略下不会偷偷扩大预算。
        "budget_expanded_for_authority": False,
        "authority_token_estimate": int(authority_token_estimate or 0),
        "authority_budget_overflow": bool(
            enabled and int(authority_token_estimate or 0) > int(max_tokens)
        ),
        "raw_evidence_count": len(raw_items),
        # 记录选择所依据的完整目录，恢复时必须与当前目录一致。
        "raw_evidence_ids": list(raw_ids),
        "evidence_catalog_fingerprint": str(catalog_fingerprint or ""),
        "selected_evidence_count": len(selected_items),
        "omitted_evidence_count": len(omitted_ids),
        "omitted_evidence_ids": omitted_ids,
        "candidate_selected_evidence_count": len(candidate_selected_ids),
        "candidate_omitted_evidence_count": len(candidate_omitted_ids),
        "candidate_omitted_evidence_ids": list(dict.fromkeys(candidate_omitted_ids)),
        "forced_authoritative_evidence_ids": list(dict.fromkeys(forced_ids)),
        # 保留旧统计字段名，避免现有观测端读取失败。
        "forced_page_or_continuation_ids": list(dict.fromkeys(forced_ids)),
        "raw_chars": raw_chars,
        "selected_chars": selected_chars,
        "candidate_selected_chars": candidate_chars,
        "candidate_char_reduction_ratio": round(
            (raw_chars - candidate_chars) / raw_chars, 6
        )
        if raw_chars
        else 0.0,
        "char_reduction_ratio": round(
            (raw_chars - selected_chars) / raw_chars, 6
        )
        if raw_chars
        else 0.0,
        "budget_exceeded_by_forced_items": bool(
            enabled
            and candidate_omitted_ids
            and int(compressor_stats.get("dropped_over_budget") or 0) > 0
        ),
        "compressor": {
            key: value
            for key, value in compressor_stats.items()
            if key != "dropped_over_budget_chunks"
        },
    }
