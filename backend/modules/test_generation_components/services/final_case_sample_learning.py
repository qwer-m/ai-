"""Build reusable sample-pool learning signals from final-case pairs."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from ..postprocess.case_access import case_priority, case_text_parts
from .final_case_parsing import _text

_MAX_DERIVED_POSITIVE_SAMPLES = 120
_MAX_DERIVED_POSITIVE_PATTERNS = 40
_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY = 2
_MAX_DERIVED_NEGATIVE_SAMPLES = 80
_SIMILARITY_MATCH_THRESHOLD = 0.62

_NON_ASSERTABLE_EXPECTED_PATTERNS = (
    "正常展示",
    "正常显示",
    "执行成功",
    "符合预期",
    "返回成功",
    "结果正确",
    "结果可核对",
    "按配置",
    "无异常",
)

_LOW_VALUE_UI_TOKENS = (
    "按钮",
    "样式",
    "布局",
    "颜色",
    "文案",
    "展示",
    "显示",
    "页面标题",
    "进度条",
    "时长",
    "打印弹窗",
    "倍速",
    "视频播放",
    "网络异常",
)

_BUSINESS_IMPACT_TOKENS = (
    "退款",
    "退费",
    "购卡",
    "开卡",
    "余额",
    "金额",
    "订单",
    "交易",
    "支付",
    "权限",
    "未开卡",
    "督导",
    "ta",
    "ops",
    "小程序",
    "学习报告",
    "课程管理",
    "学习状态",
    "状态同步",
    "跨端",
    "回滚",
    "隔离",
    "一致",
)

_CROSS_SYSTEM_TOKENS = (
    "跨端",
    "小程序",
    "ops",
    "ta",
    "督导",
    "书房",
    "后台",
    "管理端",
    "admin",
    "client",
    "backend",
    "report",
    "cross",
)
_STATE_TOKENS = (
    "状态",
    "进度",
    "同步",
    "保留",
    "未丢失",
    "一致",
    "记录",
    "state",
    "progress",
    "retain",
    "retained",
    "unchanged",
    "consistent",
    "switch",
    "switching",
)
_TRANSACTION_TOKENS = (
    "支付",
    "购卡",
    "开卡",
    "退款",
    "退费",
    "订单",
    "金额",
    "余额",
    "交易",
    "payment",
    "refund",
    "order",
    "transaction",
    "rollback",
)
_PERMISSION_TOKENS = (
    "权限",
    "未开卡",
    "不可访问",
    "隐藏",
    "绕过",
    "隔离",
    "permission",
    "unauthorized",
    "forbidden",
    "hidden",
    "access",
)


def _case_text(case: dict[str, Any]) -> str:
    return " ".join(
        case_text_parts(
            case,
            ("description", "test_module", "preconditions", "steps", "test_input", "expected_result"),
            dedupe=False,
        )
    ).strip()


def _fingerprint(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw or "").lower())[:5000]


def _case_signature(case: dict[str, Any]) -> str:
    text = _case_text(case).lower()
    text = re.sub(r"tc[-_ ]?\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:1200]


def _case_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_sig = _case_signature(left)
    right_sig = _case_signature(right)
    if not left_sig or not right_sig:
        return 0.0
    return SequenceMatcher(None, left_sig, right_sig).ratio()


def _match_generated_to_final(generated_cases: list[dict[str, Any]], final_cases: list[dict[str, Any]]) -> set[int]:
    matched: set[int] = set()
    for gen_idx, generated in enumerate(generated_cases):
        best = 0.0
        for final in final_cases:
            best = max(best, _case_similarity(generated, final))
            if best >= _SIMILARITY_MATCH_THRESHOLD:
                break
        if best >= _SIMILARITY_MATCH_THRESHOLD:
            matched.add(gen_idx)
    return matched


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _is_state_consistency_case(text: str) -> bool:
    lowered = text.lower()
    strong_phrases = (
        "状态流转",
        "状态迁移",
        "状态变化",
        "状态同步",
        "状态一致",
        "跨端同步",
        "跨端一致",
        "刷新后保持",
        "切换后保持",
        "返回后保持",
        "state transition",
        "state consistency",
        "status transition",
        "status consistency",
        "switch-back",
        "switch back",
    )
    if any(token in lowered for token in strong_phrases):
        return True
    state_terms = (
        "状态",
        "status",
        "state",
    )
    transition_terms = (
        "流转",
        "迁移",
        "切换",
        "跳转",
        "返回",
        "变更",
        "从",
        "到",
        "transition",
        "switch",
        "change",
    )
    consistency_terms = (
        "一致",
        "同步",
        "保留",
        "保持",
        "未丢失",
        "持久",
        "刷新后",
        "回到",
        "回退",
        "consistent",
        "sync",
        "retain",
        "retained",
        "unchanged",
        "persist",
        "persistence",
        "refresh",
        "rollback",
    )
    weak_progress_terms = (
        "进度",
        "记录",
        "progress",
        "record",
    )
    has_state = any(token in lowered for token in state_terms)
    has_transition = any(token in lowered for token in transition_terms)
    has_consistency = any(token in lowered for token in consistency_terms)
    has_weak_progress = any(token in lowered for token in weak_progress_terms)
    if has_state and (has_transition or has_consistency):
        return True
    if has_weak_progress and has_transition and has_consistency:
        return True
    return False


def _case_is_grounded_in_requirement(case: dict[str, Any], requirement_text: str) -> bool:
    requirement = _fingerprint(requirement_text)
    if not requirement:
        return False
    case_tokens = [
        token
        for token in re.split(r"[\s,，。；;、:：/\\|（）()\[\]【】]+", _case_text(case))
        if len(token) >= 2
    ]
    if not case_tokens:
        return False
    hits = sum(1 for token in case_tokens[:80] if token.lower() in requirement)
    return hits >= 2


def _infer_pattern_category(case: dict[str, Any]) -> str:
    text = _case_text(case)
    if _contains_any(text, _PERMISSION_TOKENS):
        return "permission_or_scope_guard"
    if _contains_any(text, _CROSS_SYSTEM_TOKENS):
        return "cross_system_business_flow"
    if _contains_any(text, _TRANSACTION_TOKENS):
        return "transaction_business_risk"
    if _is_state_consistency_case(text):
        return "state_consistency_flow"
    return "manual_final_business_coverage"


def _priority(case: dict[str, Any]) -> str:
    value = case_priority(case) or str(case.get("model_priority") or "P2").strip().upper()
    return value if value in {"P0", "P1", "P2"} else "P2"


def _aggregate_positive_pattern_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep representative final-case patterns instead of storing every final case."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        key = _positive_pattern_key(sample)
        buckets.setdefault(key, []).append(sample)

    selected: list[dict[str, Any]] = []
    # Prefer high-risk and cross-system buckets first; within a bucket keep only
    # a few representative final cases so the pool stores reusable patterns.
    for _key, bucket in sorted(buckets.items(), key=lambda item: _positive_bucket_rank(item[1])):
        selected.extend(bucket[:_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY])
        if len(selected) >= _MAX_DERIVED_POSITIVE_PATTERNS:
            break
    return selected[:_MAX_DERIVED_POSITIVE_PATTERNS]


def _positive_pattern_key(sample: dict[str, Any]) -> str:
    return "|".join(
        [
            str(sample.get("pattern_category") or ""),
            str(sample.get("expected_priority") or ""),
            "ext" if sample.get("manual_business_extension") is True else "req",
            _summarize_module_hint(str(sample.get("source_case_module") or "")).lower(),
        ]
    )


def _positive_bucket_rank(bucket: list[dict[str, Any]]) -> tuple[int, int, str]:
    first = bucket[0] if bucket else {}
    category = str(first.get("pattern_category") or "")
    priority = str(first.get("expected_priority") or "P2")
    category_rank = {
        "transaction_business_risk": 0,
        "permission_or_scope_guard": 1,
        "cross_system_business_flow": 2,
        "state_consistency_flow": 3,
        "manual_final_business_coverage": 4,
    }.get(category, 5)
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}.get(priority, 2)
    return (category_rank, priority_rank, _positive_pattern_key(first))


def _build_positive_sample(
    case: dict[str, Any],
    *,
    index: int,
    generation_id: int | None,
    linked_doc_ids: list[int],
    manual_business_extension: bool,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = _text(case.get("description")) or f"final-case-{index}"
    module = _text(case.get("test_module"))
    expected_result = _text(case.get("expected_result"))
    steps = _text(case.get("steps"))
    category = _infer_pattern_category(case)
    pattern_summary = _summarize_positive_pattern(description, module, category)
    extension_note = "manual_business_extension" if manual_business_extension else "requirement_grounded_final_case"
    return {
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": category,
        "reason_category": category,
        "expected_priority": _priority(case),
        "case_id": _text(case.get("id")) or f"final-{index}",
        "title": description[:120],
        "user_comment": _text(case.get("user_comment") or case.get("comment"))[:240],
        "pattern_summary": pattern_summary,
        "pattern_grain": "pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
        "source_case_steps": steps[:240],
        "source_case_expected_result": expected_result[:240],
        "business_assertion": expected_result[:240],
        "source": (
            "linked_final_case_business_extension"
            if manual_business_extension
            else "linked_final_case_pattern"
        ),
        "source_type": (
            "linked_final_case_business_extension"
            if manual_business_extension
            else "linked_final_case_pattern"
        ),
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(case.get("id")) or None,
        "learning_signal_source": extension_note,
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=True),
        "quality_ledger": dict(quality_ledger or {}),
        "manual_business_extension": manual_business_extension,
        "generation_id": generation_id,
        "linked_doc_ids": linked_doc_ids,
    }


def _summarize_positive_pattern(description: str, module: str, category: str) -> str:
    """Convert a human-final case into a reusable generation pattern."""
    module_hint = _summarize_module_hint(module)
    detail = _positive_pattern_detail(category)
    parts = [category, detail]
    if module_hint:
        parts.append(f"领域:{module_hint}")
    return " | ".join(part for part in parts if part)[:180]


def _summarize_module_hint(module: str) -> str:
    normalized = re.sub(r"\s+", " ", _text(module)).strip()
    if not normalized:
        return ""
    # Keep only a compact domain hint; the concrete case title is stored separately.
    normalized = re.sub(r"[-_]+", " ", normalized)
    return normalized[:40]


def _positive_pattern_detail(category: str) -> str:
    mapping = {
        "permission_or_scope_guard": (
            "覆盖未授权、未开通、隐藏或越权访问的拦截，并验证无副作用"
        ),
        "cross_system_business_flow": (
            "覆盖客户端、管理端和下游报表之间的状态与权限一致性"
        ),
        "transaction_business_risk": (
            "覆盖支付、退款、订单、权益和回滚在完整业务链路中的一致性"
        ),
        "state_consistency_flow": (
            "验证用户操作后的状态迁移、持久化、刷新、切回和进度一致性"
        ),
        "manual_final_business_coverage": (
            "优先学习带明确断言的业务流程和回归覆盖，弱化孤立静态展示检查"
        ),
    }
    return mapping.get(category) or mapping["manual_final_business_coverage"]


def _clear_negative_reason(case: dict[str, Any]) -> str:
    expected = _text(case.get("expected_result"))
    text = _case_text(case)
    expected_compact = re.sub(r"\s+", "", expected)
    if expected_compact and len(expected_compact) <= 16:
        if any(pattern in expected_compact for pattern in _NON_ASSERTABLE_EXPECTED_PATTERNS):
            return "non_assertable_expected_result"
    if any(pattern in expected for pattern in _NON_ASSERTABLE_EXPECTED_PATTERNS):
        if len(expected_compact) <= 40:
            return "non_assertable_expected_result"

    priority = _priority(case)
    if priority == "P0":
        low_value = _contains_any(text, _LOW_VALUE_UI_TOKENS)
        business_impact = _contains_any(text, _BUSINESS_IMPACT_TOKENS)
        if low_value and not business_impact:
            return "priority_overpromotion_for_low_value_ui_case"
    return ""


def _build_negative_sample(
    case: dict[str, Any],
    *,
    index: int,
    reason: str,
    generation_id: int | None,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = _text(case.get("description")) or f"generated-case-{index}"
    expected_result = _text(case.get("expected_result"))
    steps = _text(case.get("steps"))
    module = _text(case.get("test_module"))
    return {
        "signal_type": "negative",
        "pattern_usage": "avoid",
        "pattern_category": reason,
        "reason_category": reason,
        "expected_priority": "P2",
        "case_id": _text(case.get("id")) or f"generated-{index}",
        "title": description[:120],
        "user_comment": _text(case.get("user_comment") or case.get("comment"))[:240],
        "pattern_summary": f"{reason} | {description}"[:180],
        "pattern_grain": "anti_pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
        "source_case_steps": steps[:240],
        "source_case_expected_result": expected_result[:240],
        "business_assertion": expected_result[:240],
        "source": "quality_evaluation_defect",
        "source_type": "quality_evaluation_defect",
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(case.get("id")) or None,
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=False),
        "quality_ledger": dict(quality_ledger or {}),
        "generation_id": generation_id,
    }


def _compact_quality_ledger(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    judge = payload.get("judge") if isinstance(payload.get("judge"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    remediation = (
        payload.get("quality_remediation")
        if isinstance(payload.get("quality_remediation"), dict)
        else {}
    )
    remediation_actions = [
        str(item.get("action_id") or "")
        for item in (remediation.get("actions") or [])
        if isinstance(item, dict) and str(item.get("action_id") or "").strip()
    ]
    return {
        "generation_id": int(payload.get("generation_id") or 0),
        "quality_assessment": str(payload.get("quality_assessment") or ""),
        "initial_quality_score": int(payload.get("initial_quality_score") or payload.get("quality_score") or 0),
        "quality_score_grade": str(payload.get("quality_score_grade") or ""),
        "final_count": int(payload.get("final_count") or 0),
        "coverage_rate": float(coverage.get("coverage_rate") or 0.0),
        "missing_rules_count": int(coverage.get("missing_rules_count") or 0),
        "non_blocking_rules_count": int(coverage.get("non_blocking_rules_count") or 0),
        "review_candidate_total": int(review.get("candidate_total") or funnel.get("candidate_count_before_review") or 0),
        "review_retained_total": int(review.get("retained_total") or funnel.get("review_selected_count") or 0),
        "judge_rejected_out_count": int(judge.get("rejected_out_count") or 0),
        "judge_pending_out_count": int(judge.get("pending_out_count") or 0),
        "snapshot_used": bool(context.get("snapshot_used")),
        "fusion_mode": str(context.get("fusion_mode") or ""),
        "quality_primary_action": str(remediation.get("primary_action") or ""),
        "quality_action_ids": remediation_actions[:8],
    }


def _pattern_confidence_from_ledger(payload: dict[str, Any] | None, *, positive: bool) -> float:
    if not isinstance(payload, dict) or not payload:
        return 0.72 if positive else 0.65
    coverage_rate = float(payload.get("coverage_rate") or 0.0)
    missing_rules = int(payload.get("missing_rules_count") or 0)
    rejected = int(payload.get("judge_rejected_out_count") or 0) + int(payload.get("judge_pending_out_count") or 0)
    confidence = 0.68
    if coverage_rate >= 0.9:
        confidence += 0.08
    if missing_rules <= 2:
        confidence += 0.06
    if rejected <= 0:
        confidence += 0.04
    if positive:
        confidence += 0.06
    else:
        confidence -= 0.02
    return round(max(0.35, min(0.92, confidence)), 4)
