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

from scripts.rag.rag_retrieval_validation_split_helpers2 import (
    _run_single_case,
)

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

