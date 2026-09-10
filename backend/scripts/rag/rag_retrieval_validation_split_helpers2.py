"""
RAG 新索引回归验证脚本（仅验证，不改检索逻辑）。

目标：
1. 复用现有检索链路（hybrid + rerank + doc diversity + context compressor）；
2. 验证新索引（含 chunk 分段）在真实 query 下的稳定性；
3. 输出结构化 JSON 报告，定位召回偏移、分段霸榜、低相关误判等风险。

示例：
python scripts/rag/rag_retrieval_validation.py --project-id 9
python scripts/rag/rag_retrieval_validation.py --project-id 9 --doc-types requirement,test_case --output reports/rag_validation_p9.json
python scripts/rag/rag_retrieval_validation.py --project-id 9 --max-queries 2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 中文注释：保证脚本在任意 cwd 下执行时都能导入 backend 包。
CURRENT_FILE = Path(__file__).resolve()
BACKEND_ROOT = CURRENT_FILE.parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.db.database import SessionLocal  # noqa: E402
from core.db.model_defs import KnowledgeDocument  # noqa: E402
from core.cache_layer.chroma_client import chroma_client  # noqa: E402
from modules.knowledge_base_components.context.context_helpers import _run_retrieval_once  # noqa: E402

from scripts.rag.rag_retrieval_validation_helpers import (
    QueryCase,
    _parse_csv_list,
    _default_query_cases,
    _load_query_cases_from_file,
    _collect_doc_type_distribution,
    _group_top_docs,
    _chunk_part_analysis,
    _count_by_doc,
    _detect_doc_type_shift,
    _detect_chunk_split_ranking_anomaly,
    _detect_low_relevance_misjudge,
    _detect_doc_diversity_not_effective,
    _doc_type_label,
    _normalize_doc_type,
    _safe_float,
    _safe_int,
)

def _run_single_case(
    db,
    project_id: int,
    case: QueryCase,
    retrieval_options: dict,
    limit: int,
    max_tokens: int,
    analyze_top_k: int,
) -> dict[str, Any]:
    outcome = _run_retrieval_once(
        question=case.query,
        project_id=project_id,
        limit=limit,
        max_tokens=max_tokens,
        db=db,
        retrieval_options=retrieval_options,
    )

    recall_result = outcome.get("recall_result") or {}
    recall_debug = recall_result.get("debug") or {}
    merged_candidates = recall_result.get("chunks") or []
    reranked_chunks = outcome.get("reranked_chunks") or []
    selected_chunks = outcome.get("selected_chunks") or []
    diversity_stats = outcome.get("diversity_stats") or {}
    low_warning = bool(outcome.get("low_relevance_filtered"))
    rerank_stage = outcome.get("rerank_stage") or {}

    doc_type_distribution = _collect_doc_type_distribution(reranked_chunks[: max(1, analyze_top_k)])
    top_docs = _group_top_docs(reranked_chunks, top_n=max(10, analyze_top_k))
    chunk_part = _chunk_part_analysis(reranked_chunks, top_k=analyze_top_k)

    issue_doc_part = chunk_part["same_doc_part_dominance"]
    issue_doc_type_shift, issue_doc_type_shift_msg = _detect_doc_type_shift(case, top_docs)
    issue_split_anomaly, issue_split_anomaly_msg = _detect_chunk_split_ranking_anomaly(reranked_chunks, analyze_top_k)
    issue_low_rel_misjudge, issue_low_rel_misjudge_msg = _detect_low_relevance_misjudge(
        low_warning,
        reranked_chunks,
        selected_chunks,
    )
    issue_diversity, issue_diversity_msg = _detect_doc_diversity_not_effective(case, selected_chunks)

    issues = {
        "same_doc_part_dominance": {
            "detected": bool(issue_doc_part),
            "detail": "同一文档分段在 TopK 中占比过高" if issue_doc_part else "",
        },
        "doc_type_shift": {
            "detected": bool(issue_doc_type_shift),
            "detail": issue_doc_type_shift_msg,
        },
        "chunk_split_ranking_anomaly": {
            "detected": bool(issue_split_anomaly),
            "detail": issue_split_anomaly_msg,
        },
        "low_relevance_misjudge": {
            "detected": bool(issue_low_rel_misjudge),
            "detail": issue_low_rel_misjudge_msg,
        },
        "doc_diversity_not_effective": {
            "detected": bool(issue_diversity),
            "detail": issue_diversity_msg,
        },
    }

    query_report = {
        "query": case.query,
        "category": case.category,
        "expected_doc_types": list(case.expected_doc_types),
        "expect_multi_doc": bool(case.expect_multi_doc),
        "expect_no_answer": bool(case.expect_no_answer),
        "doc_type_distribution": doc_type_distribution,
        "top_docs": [
            {
                "doc_id": str(item.get("doc_id")),
                "doc_type": _doc_type_label(item.get("doc_type")),
                "chunk_count": int(item.get("chunk_count") or 0),
                "top_score": round(_safe_float(item.get("top_score"), 0.0), 4),
            }
            for item in top_docs[:8]
        ],
        "chunk_part_analysis": chunk_part,
        "retrieval_metrics": {
            "candidate_count": int(recall_debug.get("merged_count") or len(merged_candidates)),
            "rerank_input_count": int(len(reranked_chunks)),
            "final_context_count": int(len(selected_chunks)),
            "doc_coverage_triggered": bool(diversity_stats.get("doc_coverage_triggered")),
            "low_relevance_warning": low_warning,
        },
        "final_context_doc_distribution": _count_by_doc(selected_chunks),
        "issues": issues,
        # 中文注释：保留可复盘证据，方便定位问题但不过度膨胀输出体积。
        "raw_trace": {
            "query_embedding_status": recall_debug.get("query_embedding_status") or "",
            "query_embedding_error": recall_debug.get("query_embedding_error") or "",
            "recall_lanes": recall_debug.get("recall_lanes") or {},
            "merge_stage": recall_debug.get("merge_stage") or {},
            "rerank_stage": rerank_stage,
            "lane_counts": recall_debug.get("lane_counts") or {},
            "lane_reasons": recall_debug.get("lane_reasons") or {},
            "biz_relation_expand": recall_debug.get("biz_relation_expand") or {},
            "merged_candidates_head": [
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "doc_type": _doc_type_label(item.get("doc_type")),
                    "score": round(_safe_float(item.get("score"), 0.0), 4),
                    "chunk_source": item.get("chunk_source"),
                    "query_source": item.get("query_source"),
                    "chunk_part_index": (
                        item.get("metadata", {}).get("chunk_part_index")
                        if isinstance(item.get("metadata"), dict)
                        else None
                    ),
                    "chunk_part_total": (
                        item.get("metadata", {}).get("chunk_part_total")
                        if isinstance(item.get("metadata"), dict)
                        else None
                    ),
                }
                for item in merged_candidates[:12]
            ],
            "reranked_head": [
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "doc_type": _doc_type_label(item.get("doc_type")),
                    "final_score": round(
                        _safe_float(item.get("final_score") or item.get("score"), 0.0),
                        4,
                    ),
                    "chunk_part_index": (
                        item.get("metadata", {}).get("chunk_part_index")
                        if isinstance(item.get("metadata"), dict)
                        else None
                    ),
                    "chunk_part_total": (
                        item.get("metadata", {}).get("chunk_part_total")
                        if isinstance(item.get("metadata"), dict)
                        else None
                    ),
                }
                for item in reranked_chunks[:12]
            ],
            "doc_coverage": diversity_stats,
            "final_context_chunks": [
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "doc_type": _doc_type_label(item.get("doc_type")),
                    "selection_reason": item.get("selection_reason"),
                    "final_score": round(
                        _safe_float(item.get("final_score") or item.get("score"), 0.0),
                        4,
                    ),
                }
                for item in selected_chunks
            ],
        },
    }
    return query_report
