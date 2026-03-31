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
from core.db.models import KnowledgeDocument  # noqa: E402
from core.cache_layer.chroma_client import chroma_client  # noqa: E402
from modules.knowledge_base_components.context.context_helpers import _run_retrieval_once  # noqa: E402


DOC_TYPE_LABELS = {
    "requirement": "需求文档",
    "product_requirement": "需求文档",
    "incomplete": "需求文档",
    "test_case": "测试用例",
    "testcase": "测试用例",
    "supplement": "补充说明",
    "evaluation_report": "评估报告",
    "feedback": "反馈文档",
    "agent_learning": "补充说明",
}


@dataclass(frozen=True)
class QueryCase:
    """单条回归 query 定义。"""

    query: str
    category: str
    expected_doc_types: tuple[str, ...]
    expect_multi_doc: bool = False
    expect_no_answer: bool = False


def _normalize_doc_type(value: object) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    if key in {"testcase", "test_case"}:
        return "test_case"
    return key


def _doc_type_label(value: object) -> str:
    key = _normalize_doc_type(value)
    return DOC_TYPE_LABELS.get(key, key or "未知类型")


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return float(default)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return int(default)


def _parse_csv_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _default_query_cases() -> list[QueryCase]:
    """
    默认验证查询集（>=12）：
    A 功能类 / B 流程类 / C 规则类 / D 多跳类 / E 弱相关或无答案类。
    """
    return [
        QueryCase("销售怎么打卡", "功能类", ("requirement", "test_case")),
        QueryCase("门店员工如何补卡", "功能类", ("requirement", "test_case")),
        QueryCase("请说明考勤统计口径", "规则类", ("requirement", "supplement")),
        QueryCase("迟到和缺卡如何判定", "规则类", ("requirement", "test_case")),
        QueryCase("补卡流程怎么走", "流程类", ("requirement", "test_case")),
        QueryCase("上传需求文档后如何进入评估流程", "流程类", ("requirement", "evaluation_report")),
        QueryCase("异常打卡的审批链路是怎样的", "流程类", ("requirement", "supplement")),
        QueryCase("销售打卡和统计报表如何关联", "多跳类", ("requirement", "evaluation_report"), expect_multi_doc=True),
        QueryCase("补卡规则和考勤统计如何一起生效", "多跳类", ("requirement", "test_case"), expect_multi_doc=True),
        QueryCase("测试用例与需求条款如何一一映射", "多跳类", ("requirement", "test_case"), expect_multi_doc=True),
        QueryCase("火星门店怎么同步银河ERP", "弱相关/无答案类", ("requirement",), expect_no_answer=True),
        QueryCase("系统支持量子隧穿打卡吗", "弱相关/无答案类", ("requirement",), expect_no_answer=True),
    ]


def _load_query_cases_from_file(path: str) -> list[QueryCase]:
    """支持从 JSON 文件加载 query 用例。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("queries-file 必须是 JSON 数组。")

    result: list[QueryCase] = []
    for item in raw:
        if isinstance(item, str):
            result.append(QueryCase(item.strip(), "自定义", tuple()))
            continue
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        category = str(item.get("category") or "自定义").strip()
        expected_doc_types = tuple(_parse_csv_list(item.get("expected_doc_types") or ""))
        expect_multi_doc = bool(item.get("expect_multi_doc", False))
        expect_no_answer = bool(item.get("expect_no_answer", False))
        result.append(
            QueryCase(
                query=query,
                category=category,
                expected_doc_types=expected_doc_types,
                expect_multi_doc=expect_multi_doc,
                expect_no_answer=expect_no_answer,
            )
        )
    return result


def _collect_doc_type_distribution(chunks: list[dict]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in chunks:
        counter[_doc_type_label(item.get("doc_type"))] += 1
    return dict(counter)


def _group_top_docs(chunks: list[dict], top_n: int = 12) -> list[dict]:
    """
    基于 rerank 后结果做文档聚合，观察是否被单文档压制。
    """
    grouped: dict[str, dict] = {}
    for item in (chunks or [])[: max(1, int(top_n))]:
        doc_id = str(item.get("doc_id") or "unknown")
        score = _safe_float(item.get("final_score") or item.get("rerank_score") or item.get("score"), 0.0)
        doc_type = _normalize_doc_type(item.get("doc_type"))
        if doc_id not in grouped:
            grouped[doc_id] = {
                "doc_id": doc_id,
                "doc_type": doc_type,
                "chunk_count": 0,
                "top_score": score,
            }
        grouped[doc_id]["chunk_count"] += 1
        grouped[doc_id]["top_score"] = max(grouped[doc_id]["top_score"], score)

    rows = list(grouped.values())
    rows.sort(key=lambda x: (float(x.get("top_score") or 0.0), int(x.get("chunk_count") or 0)), reverse=True)
    return rows


def _chunk_part_analysis(chunks: list[dict], top_k: int) -> dict[str, Any]:
    """
    分析 chunk 分段是否导致排序异常/霸榜。
    """
    top_chunks = (chunks or [])[: max(1, int(top_k))]
    per_doc_split_count: defaultdict[str, int] = defaultdict(int)
    per_doc_max_parts: defaultdict[str, int] = defaultdict(int)

    has_split = False
    for item in top_chunks:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        part_idx = metadata.get("chunk_part_index")
        part_total = metadata.get("chunk_part_total")
        if part_idx is None and part_total is None:
            continue
        has_split = True
        doc_id = str(item.get("doc_id") or "unknown")
        per_doc_split_count[doc_id] += 1
        per_doc_max_parts[doc_id] = max(per_doc_max_parts[doc_id], _safe_int(part_total, 1))

    max_parts_in_doc = max(per_doc_max_parts.values()) if per_doc_max_parts else 0
    same_doc_part_dominance = False
    if per_doc_split_count:
        top_doc_id, top_split_count = max(per_doc_split_count.items(), key=lambda x: x[1])
        top_ratio = top_split_count / max(1, len(top_chunks))
        # 中文注释：同一文档分段占据 TopK 60% 以上或>=3 条，视为潜在霸榜。
        same_doc_part_dominance = bool(top_split_count >= 3 or top_ratio >= 0.6)
        _ = top_doc_id  # 仅用于可读性，实际结果由布尔值输出

    return {
        "has_split_chunks": has_split,
        "max_parts_in_doc": int(max_parts_in_doc),
        "same_doc_part_dominance": same_doc_part_dominance,
    }


def _count_by_doc(chunks: list[dict]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in chunks or []:
        key = str(item.get("doc_id") or item.get("filename") or "unknown")
        counter[key] += 1
    return dict(counter)


def _detect_doc_type_shift(case: QueryCase, top_docs: list[dict]) -> tuple[bool, str]:
    if not case.expected_doc_types:
        return False, ""
    expected = {_normalize_doc_type(x) for x in case.expected_doc_types}
    observed = {_normalize_doc_type(item.get("doc_type")) for item in top_docs[:5]}
    if not observed:
        return False, "无可用 top docs"
    if observed.intersection(expected):
        return False, ""
    return True, f"期望类型={sorted(expected)}，Top5 实际类型={sorted(observed)}"


def _detect_chunk_split_ranking_anomaly(reranked_chunks: list[dict], top_k: int) -> tuple[bool, str]:
    top_chunks = (reranked_chunks or [])[: max(1, int(top_k))]
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for item in top_chunks:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if metadata.get("chunk_part_index") is None and metadata.get("chunk_part_total") is None:
            continue
        grouped[str(item.get("doc_id") or "unknown")].append(item)

    for doc_id, rows in grouped.items():
        if len(rows) < 2:
            continue
        scores = [_safe_float(r.get("final_score") or r.get("rerank_score") or r.get("score"), 0.0) for r in rows]
        if max(scores) - min(scores) <= 0.06:
            return True, f"doc_id={doc_id} 在 Top{top_k} 内出现 {len(rows)} 个分段，且分数接近。"
    return False, ""


def _detect_low_relevance_misjudge(low_warning: bool, reranked_chunks: list[dict], selected_chunks: list[dict]) -> tuple[bool, str]:
    if not low_warning:
        return False, ""
    top1 = _safe_float((reranked_chunks or [{}])[0].get("final_score") if reranked_chunks else 0.0, 0.0)
    if top1 >= 0.75 and len(selected_chunks or []) >= 2:
        return True, f"low_relevance_warning=true，但 top1={top1:.3f} 且 final_context_count={len(selected_chunks)}。"
    return False, ""


def _detect_doc_diversity_not_effective(case: QueryCase, selected_chunks: list[dict]) -> tuple[bool, str]:
    if not case.expect_multi_doc:
        return False, ""
    doc_count = len({str(item.get("doc_id") or "") for item in (selected_chunks or []) if item.get("doc_id")})
    if doc_count < 2:
        return True, f"多跳 query 仅命中 {doc_count} 个文档。"
    return False, ""


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
                        _safe_float(item.get("final_score") or item.get("rerank_score") or item.get("score"), 0.0),
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
                        _safe_float(item.get("final_score") or item.get("rerank_score") or item.get("score"), 0.0),
                        4,
                    ),
                }
                for item in selected_chunks
            ],
        },
    }
    return query_report


def _build_global_summary(query_reports: list[dict]) -> dict[str, Any]:
    issue_counter: Counter[str] = Counter()
    low_rel_count = 0
    coverage_trigger_count = 0
    split_query_count = 0
    doc_type_mix = Counter()

    for row in query_reports:
        metrics = row.get("retrieval_metrics") or {}
        if metrics.get("low_relevance_warning"):
            low_rel_count += 1
        if metrics.get("doc_coverage_triggered"):
            coverage_trigger_count += 1
        if (row.get("chunk_part_analysis") or {}).get("has_split_chunks"):
            split_query_count += 1
        for key, value in (row.get("doc_type_distribution") or {}).items():
            doc_type_mix[key] += int(value or 0)
        for issue_name, issue_data in (row.get("issues") or {}).items():
            if bool((issue_data or {}).get("detected")):
                issue_counter[issue_name] += 1

    return {
        "query_count": len(query_reports),
        "split_chunk_query_count": int(split_query_count),
        "low_relevance_warning_count": int(low_rel_count),
        "doc_coverage_trigger_count": int(coverage_trigger_count),
        "doc_type_distribution_total": dict(doc_type_mix),
        "issue_counts": dict(issue_counter),
    }


def _build_project_doc_overview(db, project_id: int) -> dict[str, Any]:
    rows = (
        db.query(KnowledgeDocument.doc_type, KnowledgeDocument.id)
        .filter(KnowledgeDocument.project_id == project_id)
        .all()
    )
    counter: Counter[str] = Counter()
    for doc_type, _doc_id in rows:
        counter[_doc_type_label(doc_type)] += 1
    return {
        "project_id": int(project_id),
        "total_docs": int(len(rows)),
        "doc_type_counts": dict(counter),
    }


def _chroma_health_check(project_id: int) -> dict[str, Any]:
    """
    对当前 collection 做最小可用性自检。

    检查项：
    - collection 名称与总数；
    - project / raw / summary 三种 where 的 sample query 可用性。
    """
    result: dict[str, Any] = {
        "collection_name": "",
        "collection_count": None,
        "collection_count_error": "",
        "sample_queries": [],
    }
    collection = getattr(chroma_client, "collection", None)
    result["collection_name"] = str(getattr(collection, "name", "") or "")

    try:
        result["collection_count"] = int(collection.count() if collection else 0)
    except Exception as e:
        result["collection_count_error"] = str(e)

    checks = [
        ("project_only", {"project_id": int(project_id)}),
        ("project_raw", {"$and": [{"project_id": int(project_id)}, {"is_summary": False}]}),
        ("project_summary", {"$and": [{"project_id": int(project_id)}, {"is_summary": True}]}),
    ]
    for label, where in checks:
        item = {"label": label, "where": where, "ok": False, "rows": 0, "error": ""}
        try:
            payload = chroma_client.search(
                query="打卡规则",
                n_results=3,
                where=where,
                raise_on_error=True,
            )
            docs = (payload.get("documents") or [[]])[0]
            item["ok"] = True
            item["rows"] = len(docs)
        except Exception as e:
            item["error"] = str(e)
        result["sample_queries"].append(item)

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 新索引回归验证脚本")
    parser.add_argument("--project-id", type=int, required=True, help="项目 ID")
    parser.add_argument("--output", type=str, default="", help="报告输出路径（JSON）")
    parser.add_argument("--queries-file", type=str, default="", help="自定义查询集 JSON 文件")
    parser.add_argument("--max-queries", type=int, default=0, help="仅执行前 N 条 query（调试用）")
    parser.add_argument("--limit", type=int, default=6, help="最终上下文 chunk 限制")
    parser.add_argument("--max-tokens", type=int, default=1800, help="上下文压缩 token 预算")
    parser.add_argument("--analyze-top-k", type=int, default=10, help="排序诊断时分析的 TopK")
    parser.add_argument("--doc-types", type=str, default="", help="可选：按 doc_type 过滤，逗号分隔")
    parser.add_argument("--retrieval-mode", type=str, default="hybrid", help="检索模式：vector/keyword/hybrid/bm25")
    parser.add_argument("--sample-print", type=int, default=2, help="控制台打印前 N 条示例结果")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        query_cases = (
            _load_query_cases_from_file(args.queries_file)
            if str(args.queries_file or "").strip()
            else _default_query_cases()
        )
        if args.max_queries and int(args.max_queries) > 0:
            query_cases = query_cases[: int(args.max_queries)]

        if len(query_cases) < 1:
            raise RuntimeError("query 集为空，无法执行回归验证。")

        retrieval_options = {
            "retrieval_mode": str(args.retrieval_mode or "hybrid").strip().lower(),
            "doc_types": _parse_csv_list(args.doc_types),
            "enable_query_rewrite": True,
            "enable_rerank": True,
            "enable_biz_key_expansion": True,
        }

        project_overview = _build_project_doc_overview(db, args.project_id)
        chroma_health = _chroma_health_check(args.project_id)
        print(f"[INFO] project_id={args.project_id} total_docs={project_overview['total_docs']}")
        print(f"[INFO] query_count={len(query_cases)} retrieval_mode={retrieval_options['retrieval_mode']}")
        print(f"[INFO] chroma_collection={chroma_health.get('collection_name')} count={chroma_health.get('collection_count')}")

        query_reports: list[dict] = []
        for idx, case in enumerate(query_cases, start=1):
            report = _run_single_case(
                db=db,
                project_id=args.project_id,
                case=case,
                retrieval_options=retrieval_options,
                limit=int(args.limit),
                max_tokens=int(args.max_tokens),
                analyze_top_k=int(args.analyze_top_k),
            )
            query_reports.append(report)
            print(
                f"[RUN] {idx:02d}/{len(query_cases)} category={case.category} "
                f"query={case.query} final_context={report['retrieval_metrics']['final_context_count']}"
            )

        summary = _build_global_summary(query_reports)
        payload = {
            "report_name": "RAG 新索引回归验证",
            "generated_at": datetime.now().isoformat(),
            "project_overview": project_overview,
            "chroma_health": chroma_health,
            "retrieval_options": retrieval_options,
            "summary": summary,
            "query_reports": query_reports,
        }

        output_path = str(args.output or "").strip()
        if not output_path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(BACKEND_ROOT / "scripts" / "rag" / "reports" / f"rag_retrieval_validation_p{args.project_id}_{ts}.json")
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"[DONE] report={output_file}")
        print("[SUMMARY]", json.dumps(summary, ensure_ascii=False))

        sample_n = max(0, int(args.sample_print))
        if sample_n:
            print("[SAMPLE]")
            for item in query_reports[:sample_n]:
                print(
                    json.dumps(
                        {
                            "query": item["query"],
                            "doc_type_distribution": item["doc_type_distribution"],
                            "top_docs": item["top_docs"][:3],
                            "chunk_part_analysis": item["chunk_part_analysis"],
                            "retrieval_metrics": item["retrieval_metrics"],
                            "final_context_doc_distribution": item["final_context_doc_distribution"],
                            "issues": item["issues"],
                        },
                        ensure_ascii=False,
                    )
                )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
