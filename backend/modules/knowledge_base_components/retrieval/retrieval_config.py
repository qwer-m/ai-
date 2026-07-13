"""Shared retrieval tuning normalization for RAG entrypoints."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _to_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(min_value, min(max_value, parsed))


def _to_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(min_value, min(max_value, parsed))


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def normalize_doc_types(value: Any) -> list[str]:
    """Normalize doc_type filter into a unique lowercase list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            key = str(item or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(key)
        return result
    return []


@dataclass(frozen=True)
class RetrievalConfig:
    """Single normalized retrieval tuning model shared by all RAG paths."""

    retrieval_mode: str
    recall_top_k: int
    rerank_top_n: int
    max_chunks_per_doc: int
    min_docs: int
    enable_query_rewrite: bool
    enable_rerank: bool
    vector_weight: float
    keyword_weight: float
    title_weight: float
    redundancy_threshold: float
    doc_types: list[str]
    enable_biz_key_expansion: bool
    related_top_k: int

    @classmethod
    def from_raw(cls, *, limit: int, values: Mapping[str, Any] | None = None) -> "RetrievalConfig":
        opts = dict(values or {})
        mode = str(opts.get("retrieval_mode") or "hybrid").strip().lower()
        if mode not in {"vector", "keyword", "hybrid", "bm25"}:
            mode = "hybrid"

        return cls(
            retrieval_mode=mode,
            recall_top_k=_to_int(opts.get("recall_top_k"), max(limit * 5, 20), 6, 80),
            rerank_top_n=_to_int(opts.get("rerank_top_n"), max(limit * 4, 8), 4, 80),
            max_chunks_per_doc=_to_int(opts.get("max_chunks_per_doc"), 2, 1, 3),
            min_docs=_to_int(opts.get("min_docs"), 2, 1, 12),
            enable_query_rewrite=_to_bool(opts.get("enable_query_rewrite"), True),
            enable_rerank=_to_bool(opts.get("enable_rerank"), True),
            vector_weight=_to_float(opts.get("vector_weight"), 0.6, 0.0, 3.0),
            keyword_weight=_to_float(opts.get("keyword_weight"), 0.25, 0.0, 3.0),
            title_weight=_to_float(opts.get("title_weight"), 0.15, 0.0, 3.0),
            redundancy_threshold=_to_float(opts.get("redundancy_threshold"), 0.88, 0.5, 0.99),
            doc_types=normalize_doc_types(opts.get("doc_types")),
            enable_biz_key_expansion=_to_bool(opts.get("enable_biz_key_expansion"), True),
            related_top_k=_to_int(opts.get("related_top_k"), 5, 1, 20),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_retrieval_from_eval_config(config: Mapping[str, Any]) -> tuple[int, int, RetrievalConfig]:
    """
    Normalize retrieval inputs from rag-eval config.

    Supports:
    - flat single-debug payload fields
    - nested batch-eval config.retrieval/context/advanced
    """
    root = dict(config or {})
    retrieval_cfg = dict(root.get("retrieval") or {})
    context_cfg = dict(root.get("context") or {})
    advanced_cfg = dict(root.get("advanced") or {})

    merged_top_level = {**retrieval_cfg, **root}
    top_k = _to_int(merged_top_level.get("top_k") or merged_top_level.get("limit"), 5, 1, 20)
    max_tokens = _to_int(
        (context_cfg.get("max_tokens") if context_cfg.get("max_tokens") is not None else root.get("max_tokens")),
        1800,
        128,
        8000,
    )

    merged_options = {**retrieval_cfg, **advanced_cfg}
    if "retrieval_mode" in root and root.get("retrieval_mode") is not None:
        merged_options["retrieval_mode"] = root.get("retrieval_mode")
    if "recall_top_k" in root and root.get("recall_top_k") is not None:
        merged_options["recall_top_k"] = root.get("recall_top_k")
    if "rerank_top_n" in root and root.get("rerank_top_n") is not None:
        merged_options["rerank_top_n"] = root.get("rerank_top_n")
    if "max_chunks_per_doc" in root and root.get("max_chunks_per_doc") is not None:
        merged_options["max_chunks_per_doc"] = root.get("max_chunks_per_doc")
    if "min_docs" in root and root.get("min_docs") is not None:
        merged_options["min_docs"] = root.get("min_docs")
    if "vector_weight" in root and root.get("vector_weight") is not None:
        merged_options["vector_weight"] = root.get("vector_weight")
    if "keyword_weight" in root and root.get("keyword_weight") is not None:
        merged_options["keyword_weight"] = root.get("keyword_weight")
    if "title_weight" in root and root.get("title_weight") is not None:
        merged_options["title_weight"] = root.get("title_weight")
    if "redundancy_threshold" in root and root.get("redundancy_threshold") is not None:
        merged_options["redundancy_threshold"] = root.get("redundancy_threshold")
    if "enable_query_rewrite" in root and root.get("enable_query_rewrite") is not None:
        merged_options["enable_query_rewrite"] = root.get("enable_query_rewrite")
    if "enable_rerank" in root and root.get("enable_rerank") is not None:
        merged_options["enable_rerank"] = root.get("enable_rerank")

    tuning = RetrievalConfig.from_raw(limit=top_k, values=merged_options)
    return top_k, max_tokens, tuning

