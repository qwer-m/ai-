from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from core.ai_client import get_client_for_user
from modules.rag_eval.metrics_generation import (
    build_llm_judge_prompt,
    evaluate_answer_by_points_rule,
    evaluate_faithfulness_rule,
)


def judge_answer(
    *,
    query: str,
    gold_answer: str,
    answer_points: list[Any],
    context: str,
    model_answer: str,
    db: Session,
    user_id: int,
    mode: str = "hybrid",
    judge_model: str | None = None,
) -> dict[str, Any]:
    """
    统一判分入口：
    - rule：只规则；
    - llm：只 LLM judge；
    - hybrid：优先规则，信心不足时再走 LLM。
    """
    mode = (mode or "hybrid").lower()

    rule_answer = evaluate_answer_by_points_rule(model_answer, answer_points)
    rule_faith = evaluate_faithfulness_rule(model_answer, context)
    rule_result = {
        "answer_correctness_score": float(rule_answer.get("score") or 0.0),
        "faithfulness_score": float(rule_faith.get("score") or 0.0),
        "missing_points": list(rule_answer.get("missing_points") or []),
        "hallucinated_claims": list(rule_faith.get("hallucinated_claims") or []),
        "source": "rule",
    }

    if mode == "rule":
        return _with_flags(rule_result)

    if mode == "llm":
        llm_result = _llm_judge(
            query=query,
            gold_answer=gold_answer,
            answer_points=answer_points,
            context=context,
            model_answer=model_answer,
            db=db,
            user_id=user_id,
            judge_model=judge_model,
        )
        return _with_flags(llm_result)

    # hybrid
    confidence = float(rule_answer.get("confidence") or 0.0)
    if confidence >= 0.85:
        return _with_flags(rule_result)

    llm_result = _llm_judge(
        query=query,
        gold_answer=gold_answer,
        answer_points=answer_points,
        context=context,
        model_answer=model_answer,
        db=db,
        user_id=user_id,
        judge_model=judge_model,
    )
    merged = {
        "answer_correctness_score": (float(rule_result["answer_correctness_score"]) + float(llm_result["answer_correctness_score"])) / 2,
        "faithfulness_score": (float(rule_result["faithfulness_score"]) + float(llm_result["faithfulness_score"])) / 2,
        "missing_points": _merge_list(rule_result.get("missing_points"), llm_result.get("missing_points")),
        "hallucinated_claims": _merge_list(rule_result.get("hallucinated_claims"), llm_result.get("hallucinated_claims")),
        "source": "hybrid",
    }
    return _with_flags(merged)


def _llm_judge(
    *,
    query: str,
    gold_answer: str,
    answer_points: list[Any],
    context: str,
    model_answer: str,
    db: Session,
    user_id: int,
    judge_model: str | None,
) -> dict[str, Any]:
    """调用 LLM 进行评测判分。"""
    prompt = build_llm_judge_prompt(query, gold_answer, answer_points, context, model_answer)
    client = get_client_for_user(user_id, db)
    raw = client.generate_response(
        user_input=prompt,
        system_prompt="你是严谨的RAG评测员。",
        db=db,
        task_type="general",
        model=judge_model or None,
    )
    payload = _safe_parse_json(raw)
    return {
        "answer_correctness_score": _to_float(payload.get("answer_correctness_score")),
        "faithfulness_score": _to_float(payload.get("faithfulness_score")),
        "missing_points": payload.get("missing_points") or [],
        "hallucinated_claims": payload.get("hallucinated_claims") or [],
        "source": "llm",
    }


def _with_flags(result: dict[str, Any]) -> dict[str, Any]:
    """补充 bool 判定字段，便于前端和归因直接消费。"""
    correctness = _to_float(result.get("answer_correctness_score"))
    faithfulness = _to_float(result.get("faithfulness_score"))
    out = dict(result)
    out["is_answer_correct"] = correctness >= 0.8
    out["is_faithful"] = faithfulness >= 0.7
    return out


def _safe_parse_json(raw: str) -> dict[str, Any]:
    """兼容代码块与纯 JSON。"""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception:
        # 尝试截取第一段对象
        l = text.find("{")
        r = text.rfind("}")
        if l != -1 and r != -1 and r > l:
            try:
                return json.loads(text[l : r + 1])
            except Exception:
                return {}
        return {}


def _merge_list(a, b) -> list[str]:
    merged = []
    for item in list(a or []) + list(b or []):
        token = str(item).strip()
        if token and token not in merged:
            merged.append(token)
    return merged


def _to_float(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0

