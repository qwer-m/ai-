from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

