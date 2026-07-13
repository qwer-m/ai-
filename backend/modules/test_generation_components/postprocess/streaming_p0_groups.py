from __future__ import annotations

from typing import Any

from .case_access import case_flat_text, case_text_field
from .streaming_case_normalization import normalize_priority_value


P0_GROUP_TOKENS: dict[str, tuple[str, ...]] = {
    "week_flow": (
        "周中",
        "周末",
        "学习报告",
        "报告",
        "完成",
        "week flow",
        "weekly flow",
    ),
    "payment_gate": (
        "付费",
        "支付",
        "购买",
        "拦截",
        "paywall",
        "payment gate",
        "subscribe",
    ),
    "ai_scoring": (
        "ai判分",
        "智能判分",
        "自动判分",
        "评分",
        "ai scoring",
        "auto score",
    ),
    "wrong_collection": (
        "错题",
        "错题归集",
        "错题本",
        "wrong question",
        "error collection",
    ),
    "week_boundary": (
        "周次切换",
        "周日24",
        "周日 24",
        "时间边界",
        "week switch",
        "time boundary",
    ),
    "makeup_rule": (
        "补做",
        "补学",
        "历史周",
        "makeup",
        "make-up",
        "history week",
    ),
}


def required_p0_groups_from_requirement(requirement_text: str) -> set[str]:
    text = str(requirement_text or "").lower()
    required_groups: set[str] = set()
    for group, tokens in P0_GROUP_TOKENS.items():
        if group == "week_flow":
            week_specific_tokens = (
                "周中",
                "周末",
                "周次",
                "周日24",
                "周日 24",
                "学习报告",
                "week flow",
                "weekly flow",
                "week switch",
            )
            if any(token in text for token in week_specific_tokens):
                required_groups.add(group)
            continue
        if group == "wrong_collection":
            wrong_collection_specific_tokens = (
                "错题归集",
                "错题本",
                "错题收集",
                "错题自动归集",
                "生成错题",
                "收录错题",
                "wrong question collection",
                "error collection",
            )
            if any(token in text for token in wrong_collection_specific_tokens):
                required_groups.add(group)
            continue
        if any(token in text for token in tokens):
            required_groups.add(group)
    return required_groups


def covered_p0_groups(cases: list[dict[str, Any]]) -> set[str]:
    covered_groups: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        decision_state = str(case.get("priority_decision_state") or "").strip().lower()
        decision_final = case_text_field(case, "priority_final").upper()
        if decision_state:
            if decision_final != "P0":
                continue
            if decision_state not in {"decided", "conflict_resolved", "overridden"}:
                continue
        elif normalize_priority_value(case_text_field(case, "priority")) != "P0":
            continue
        text = case_flat_text(
            case,
            fields=("test_module", "description", "expected_result", "test_input", "steps"),
            separator=" ",
            lower=True,
        )
        for group, tokens in P0_GROUP_TOKENS.items():
            if any(token in text for token in tokens):
                covered_groups.add(group)
    return covered_groups
