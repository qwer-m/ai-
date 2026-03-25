from __future__ import annotations

import json
import re
from statistics import mean
from typing import Any


def _safe_text(v: Any) -> str:
    """统一文本。"""
    return str(v or "").strip()


def _contains_ignore_case(haystack: str, needle: str) -> bool:
    """不区分大小写包含判断。"""
    return needle.lower() in haystack.lower()


def evaluate_answer_by_points_rule(
    model_answer: str,
    answer_points: list[Any] | None,
) -> dict[str, Any]:
    """
    规则评测：
    1. 字符串点位：做包含匹配；
    2. 数值类：支持 exact_value / pattern；
    3. 枚举类：支持 values 全覆盖；
    """
    points = answer_points or []
    answer = _safe_text(model_answer)
    if not points:
        return {
            "score": 0.0,
            "is_correct": False,
            "covered_points": [],
            "missing_points": [],
            "confidence": 0.2,
            "judge_basis": "no_answer_points",
        }

    covered: list[str] = []
    missing: list[str] = []

    for p in points:
        if isinstance(p, str):
            key = p.strip()
            if not key:
                continue
            if _contains_ignore_case(answer, key):
                covered.append(key)
            else:
                missing.append(key)
            continue

        if not isinstance(p, dict):
            token = _safe_text(p)
            if token and _contains_ignore_case(answer, token):
                covered.append(token)
            elif token:
                missing.append(token)
            continue

        label = _safe_text(p.get("label") or p.get("name") or p.get("point") or p.get("value"))
        kind = _safe_text(p.get("type") or "text").lower()
        if kind in {"number", "numeric"}:
            exact_value = _safe_text(p.get("exact_value"))
            pattern = _safe_text(p.get("pattern"))
            hit = False
            if exact_value and re.search(rf"(?<!\d){re.escape(exact_value)}(?!\d)", answer):
                hit = True
            elif pattern:
                try:
                    hit = bool(re.search(pattern, answer, re.IGNORECASE))
                except re.error:
                    hit = _contains_ignore_case(answer, pattern)
            if hit:
                covered.append(label or exact_value or pattern)
            else:
                missing.append(label or exact_value or pattern or "numeric_point")
            continue

        if kind in {"enum", "set"}:
            values = p.get("values") or []
            if not isinstance(values, list):
                values = [values]
            norm_values = [_safe_text(x) for x in values if _safe_text(x)]
            hits = [v for v in norm_values if _contains_ignore_case(answer, v)]
            if norm_values and len(hits) == len(norm_values):
                covered.append(label or ",".join(norm_values))
            else:
                missing.append(label or ",".join(norm_values) or "enum_point")
            continue

        token = _safe_text(p.get("contains") or p.get("value") or label)
        if token and _contains_ignore_case(answer, token):
            covered.append(label or token)
        else:
            missing.append(label or token or "text_point")

    total = len(covered) + len(missing)
    score = (len(covered) / total) if total else 0.0
    return {
        "score": score,
        "is_correct": bool(score >= 0.8 and not missing),
        "covered_points": covered,
        "missing_points": missing,
        "confidence": 0.9 if total >= 2 else 0.6,
        "judge_basis": "rule_points",
    }


def evaluate_faithfulness_rule(model_answer: str, context: str) -> dict[str, Any]:
    """
    规则忠实性评估（轻量）：
    - 将答案切成短句，统计不在上下文中的句子比例；
    - 比例越高，faithfulness 越低。
    """
    answer = _safe_text(model_answer)
    ctx = _safe_text(context)
    if not answer:
        return {"score": 0.0, "hallucinated_claims": ["empty_answer"], "judge_basis": "rule_faithfulness"}
    if not ctx:
        return {"score": 0.0, "hallucinated_claims": ["empty_context"], "judge_basis": "rule_faithfulness"}

    segments = [s.strip() for s in re.split(r"[。\n；;.!?]", answer) if s.strip()]
    if not segments:
        segments = [answer]

    unsupported = [seg for seg in segments if len(seg) >= 6 and not _contains_ignore_case(ctx, seg[: min(16, len(seg))])]
    ratio = len(unsupported) / len(segments)
    score = max(0.0, 1.0 - ratio)
    return {"score": score, "hallucinated_claims": unsupported, "judge_basis": "rule_faithfulness"}


def aggregate_generation_metrics(results: list[dict]) -> dict[str, float]:
    """聚合生成层指标。"""
    if not results:
        return {
            "avg_answer_correctness": 0.0,
            "avg_faithfulness": 0.0,
            "avg_latency_ms": 0.0,
            "avg_retrieval_latency_ms": 0.0,
            "avg_generation_latency_ms": 0.0,
            "avg_cost": 0.0,
            "pass_rate": 0.0,
        }

    def _avg(items: list[float]) -> float:
        return float(mean(items)) if items else 0.0

    correctness = [_to_float(r.get("answer_correctness_score")) for r in results]
    faith = [_to_float(r.get("faithfulness_score")) for r in results]
    latency = [_to_float(r.get("latency_ms")) for r in results]
    r_latency = [_to_float(r.get("retrieval_latency_ms")) for r in results]
    g_latency = [_to_float(r.get("generation_latency_ms")) for r in results]
    costs = [_to_float((r.get("cost_json") or {}).get("total_cost")) for r in results]
    pass_rate = _avg([1.0 if bool(r.get("answer_correct")) else 0.0 for r in results])

    return {
        "avg_answer_correctness": _avg(correctness),
        "avg_faithfulness": _avg(faith),
        "avg_latency_ms": _avg(latency),
        "avg_retrieval_latency_ms": _avg(r_latency),
        "avg_generation_latency_ms": _avg(g_latency),
        "avg_cost": _avg(costs),
        "pass_rate": pass_rate,
    }


def build_llm_judge_prompt(
    query: str,
    gold_answer: str,
    answer_points: list[Any],
    context: str,
    model_answer: str,
) -> str:
    """构造 LLM-as-a-judge 提示词。"""
    payload = {
        "query": query,
        "gold_answer": gold_answer,
        "answer_points": answer_points,
        "context": context,
        "model_answer": model_answer,
    }
    return (
        "你是RAG评测裁判，请严格输出JSON，不要输出其它文本。"
        "字段：answer_correctness_score(0~1), faithfulness_score(0~1), missing_points(list), hallucinated_claims(list)。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0

