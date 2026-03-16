"""
RAG 第二阶段检索治理 A/B 验证脚本（验证版）。

说明：
1. 本脚本不修改检索逻辑，只调用现有 API 做质量验证。
2. 通过 TestClient 调用 `/api/knowledge/retrieve-context`，并强制 `debug=true`。
3. 输出三类 query 的逐条结果、对照表与汇总指标。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from core.auth import get_current_user
from core.database import SessionLocal
from core.models import KnowledgeDocument, Project
from main import app


@dataclass
class QueryCase:
    """单条验证 query 定义。"""

    category: str
    query: str


def _pick_project_with_success_docs() -> tuple[int, int]:
    """选择一个包含 success 文档的项目，返回 (project_id, owner_user_id)。"""
    db = SessionLocal()
    try:
        doc = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.parse_status == "success")
            .order_by(KnowledgeDocument.id.desc())
            .first()
        )
        if not doc:
            raise RuntimeError("未找到 parse_status=success 的知识库文档，无法执行检索验证。")

        project = db.query(Project).filter(Project.id == doc.project_id).first()
        if not project:
            raise RuntimeError(f"文档关联项目不存在：project_id={doc.project_id}")

        return int(project.id), int(project.user_id)
    finally:
        db.close()


def _build_queries() -> list[QueryCase]:
    """构造三类测试 query，每类至少 2 条。"""
    return [
        # 1) 精确命中问题
        QueryCase(category="精确命中", query="创建订单和支付订单有哪些步骤"),
        QueryCase(category="精确命中", query="用户登录成功后可以做什么"),
        # 2) 口语化问题
        QueryCase(category="口语化", query="下单这块一般咋走，支付在不在流程里"),
        QueryCase(category="口语化", query="登录通过以后系统能不能看订单"),
        # 3) 知识库外问题
        QueryCase(category="知识库外", query="Redis 为什么这么快"),
        QueryCase(category="知识库外", query="Kubernetes 调度策略是什么"),
    ]


def _extract_top3(debug_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 debug 结果中提取 top3。优先使用 rerank_top，不存在则回退 final_chunks。"""
    ranked = debug_payload.get("rerank_top") or debug_payload.get("final_chunks") or []
    top3: list[dict[str, Any]] = []
    for item in ranked[:3]:
        top3.append(
            {
                "filename": item.get("filename"),
                "query_source": item.get("query_source"),
                "final_score": float(item.get("final_score") or item.get("rerank_score") or 0.0),
            }
        )
    return top3


def _estimate_context_tokens(context_text: str) -> int:
    """用与压缩模块一致的简化估算，便于横向对比。"""
    return max(1, int(len(context_text or "") / 1.6))


def run_ab_validation() -> dict[str, Any]:
    """
    执行 A/B 验证（A: original 主召回；B: rewrite 补充召回）。

    这里的 A/B 不切换代码分支，而是在同一 debug 输出中对 original/rewrite 两路贡献做对照。
    """
    project_id, owner_user_id = _pick_project_with_success_docs()
    queries = _build_queries()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_user_id)

    per_query_rows: list[dict[str, Any]] = []
    raw_debug_payloads: list[dict[str, Any]] = []

    zero_score_count = 0
    total_score_count = 0
    all_scores: list[float] = []

    total_original_final = 0
    total_rewrite_final = 0
    total_final_chunks = 0

    rewrite_recalled_total = 0
    rewrite_final_total = 0

    merged_total = 0
    deduped_total = 0
    final_total = 0

    try:
        with TestClient(app) as client:
            for case in queries:
                payload = {
                    "project_id": project_id,
                    "query": case.query,
                    "limit": 5,
                    "max_tokens": 1600,
                    "debug": True,
                }
                resp = client.post("/api/knowledge/retrieve-context", json=payload)
                if resp.status_code != 200:
                    raise RuntimeError(f"接口调用失败 query={case.query} status={resp.status_code} body={resp.text}")

                body = resp.json()
                debug_data = body.get("debug") or {}
                final_chunks = debug_data.get("final_chunks") or []
                lane_counts = debug_data.get("lane_counts") or {}
                lane_reasons = debug_data.get("lane_reasons") or {}
                rewrite_queries = debug_data.get("rewrite_queries") or []

                # score 健康度统计
                for chunk in final_chunks:
                    s = float(chunk.get("score") or 0.0)
                    all_scores.append(s)
                    total_score_count += 1
                    if abs(s) < 1e-12:
                        zero_score_count += 1

                # original / rewrite 命中统计
                original_final = sum(1 for c in final_chunks if c.get("query_source") == "original")
                rewrite_final = sum(1 for c in final_chunks if c.get("query_source") == "rewrite")
                total_original_final += original_final
                total_rewrite_final += rewrite_final
                total_final_chunks += len(final_chunks)

                # rewrite 噪音率统计
                rewrite_recalled = int(lane_counts.get("rewrite_raw") or 0) + int(
                    lane_counts.get("rewrite_summary") or 0
                )
                rewrite_recalled_total += rewrite_recalled
                rewrite_final_total += rewrite_final

                # 压缩效果统计
                merged_count = int(debug_data.get("merged_count") or 0)
                deduped_count = int(debug_data.get("deduped_count") or 0)
                final_count = len(final_chunks)
                merged_total += merged_count
                deduped_total += deduped_count
                final_total += final_count

                compressor_stats = debug_data.get("compressor_stats") or {}
                original_priority_kept = int(compressor_stats.get("kept_by_original_priority") or 0)

                row = {
                    "category": case.category,
                    "query": case.query,
                    "rewrite_queries": rewrite_queries,
                    "lane_counts": lane_counts,
                    "lane_reasons": lane_reasons,
                    "deduped_count": deduped_count,
                    "rerank_top3": _extract_top3(debug_data),
                    "original_priority_kept": original_priority_kept,
                    "final_chunks": final_count,
                    "context_tokens": _estimate_context_tokens(body.get("context") or ""),
                }
                per_query_rows.append(row)

                raw_debug_payloads.append(
                    {
                        "category": case.category,
                        "query": case.query,
                        "response": body,
                    }
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    score_zero_ratio = (zero_score_count / total_score_count) if total_score_count else 0.0
    score_range = {
        "min": min(all_scores) if all_scores else 0.0,
        "max": max(all_scores) if all_scores else 0.0,
    }

    original_hit_ratio = (total_original_final / total_final_chunks) if total_final_chunks else 0.0
    rewrite_hit_ratio = (total_rewrite_final / total_final_chunks) if total_final_chunks else 0.0

    rewrite_noise_ratio = (
        max(0, (rewrite_recalled_total - rewrite_final_total)) / rewrite_recalled_total
        if rewrite_recalled_total
        else 0.0
    )

    return {
        "project_id": project_id,
        "query_count": len(queries),
        "per_query_rows": per_query_rows,
        "raw_debug_payloads": raw_debug_payloads,
        "metrics": {
            "score_health": {
                "zero_ratio": round(score_zero_ratio, 4),
                "score_range": score_range,
                "sample_count": total_score_count,
            },
            "original_hit_ratio": round(original_hit_ratio, 4),
            "rewrite_hit_ratio": round(rewrite_hit_ratio, 4),
            "rewrite_noise_ratio": round(rewrite_noise_ratio, 4),
            "compression_effect": {
                "merged_total": merged_total,
                "deduped_total": deduped_total,
                "final_total": final_total,
            },
        },
    }


def _format_markdown_table(rows: list[dict[str, Any]]) -> str:
    """把每条 query 的核心字段渲染为 Markdown 对照表。"""
    header = (
        "| query | rewrite_queries | lane_counts | lane_reasons | deduped_count | "
        "rerank_top3 | original_priority_kept | final_chunks | context_tokens |"
    )
    sep = "|---|---|---|---|---:|---|---:|---:|---:|"
    lines = [header, sep]
    for row in rows:
        lines.append(
            "| "
            + row["query"].replace("|", " ")
            + " | "
            + json.dumps(row.get("rewrite_queries") or [], ensure_ascii=False)
            + " | "
            + json.dumps(row.get("lane_counts") or {}, ensure_ascii=False)
            + " | "
            + json.dumps(row.get("lane_reasons") or {}, ensure_ascii=False)
            + f" | {row.get('deduped_count', 0)} | "
            + json.dumps(row.get("rerank_top3") or [], ensure_ascii=False)
            + f" | {row.get('original_priority_kept', 0)} | {row.get('final_chunks', 0)} | "
            + f"{row.get('context_tokens', 0)} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_ab_validation()

    print("=== 测试 Query 列表 ===")
    for item in result["per_query_rows"]:
        print(f"- [{item['category']}] {item['query']}")

    print("\n=== 每条 Query Debug 结果（完整 JSON） ===")
    print(json.dumps(result["raw_debug_payloads"], ensure_ascii=False, indent=2))

    print("\n=== 检索对照表 ===")
    print(_format_markdown_table(result["per_query_rows"]))

    print("\n=== 指标统计 ===")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
