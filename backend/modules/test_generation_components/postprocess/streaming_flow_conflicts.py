from __future__ import annotations

import json
from typing import Any

from .case_access import case_flat_text


def filter_cases_conflicting_with_confirmed_flow_facts(
    cases: list[dict[str, Any]],
    *,
    requirement: str,
    kb_context: str,
    fact_profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """Drop legacy linear-unlock cases when confirmed context says stages are non-linear."""

    context_text = "\n".join(
        [
            str(requirement or ""),
            str(kb_context or ""),
            json.dumps(fact_profile, ensure_ascii=False, sort_keys=True),
        ]
    ).lower()
    nonlinear_fact_tokens = (
        "non-linear",
        "nonlinear",
        "no prerequisite",
        "no prerequisites",
        "any stage",
        "all stages enterable",
        "非线性",
        "任意进入",
        "随意进入",
        "均可进入",
        "均可点击",
        "无需前置",
        "不需要前置",
        "初始可",
        "未锁",
        "不锁",
    )
    stage_context_tokens = (
        "stage",
        "step",
        "phase",
        "流程阶段",
        "阶段",
        "环节",
    )
    nonlinear_case_tokens = (
        "non-linear",
        "nonlinear",
        "no prerequisite",
        "no prerequisites",
        "all stages",
        "any stage",
        "enterable",
        "非线性",
        "任意进入",
        "随意进入",
        "均可进入",
        "均可点击",
        "都可进入",
        "无需前置",
        "不需要前置",
        "初始可",
        "均未锁",
        "无锁",
        "未锁",
    )
    blocking_tokens = (
        "locked",
        "cannot enter",
        "can't enter",
        "unavailable",
        "previous step",
        "previous stage",
        "toast",
        "未解锁",
        "锁定",
        "锁住",
        "不可点击",
        "不可进入",
        "完成前",
        "完成前一节",
        "前一节",
        "前一环节",
        "上一环节",
        "才可以解锁",
        "解锁提示",
    )
    obsolete_linear_unlock_tokens = (
        "only first stage",
        "only the first stage",
        "complete previous",
        "previous phase",
        "仅第一个环节",
        "仅第一环节",
        "只有第一个环节",
        "其余为未解锁",
        "其余环节未解锁",
        "完成前一阶段才可以解锁",
        "完成前一阶段才可以",
        "完成前一环节才可",
    )
    compatibility_tokens = (
        "legacy",
        "compatibility",
        "config enabled",
        "old_config_only",
        "legacy_unlock_mode",
        "linear unlock",
        "旧版",
        "旧配置",
        "兼容",
        "配置开启",
        "线性解锁",
    )
    positive_nonlinear_assertion_tokens = (
        "no prerequisite",
        "no prerequisites",
        "any stage",
        "任意进入",
        "随意进入",
        "均可进入",
        "都可进入",
        "无需前置",
        "不需要前置",
        "不要求先完成",
        "不要求先完成前一环节",
    )
    case_text_pairs: list[tuple[dict[str, Any], str]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case_text_pairs.append(
            (
                item,
                case_flat_text(
                    item,
                    fields=("test_module", "description", "expected_result", "steps"),
                    separator=" ",
                    lower=True,
                ),
            )
        )
    has_nonlinear_context = any(token in context_text for token in nonlinear_fact_tokens)
    has_nonlinear_case = any(
        any(token in text for token in stage_context_tokens)
        and any(token in text for token in nonlinear_case_tokens)
        for _item, text in case_text_pairs
    )
    has_obsolete_linear_unlock_case = any(
        any(token in text for token in stage_context_tokens)
        and any(token in text for token in obsolete_linear_unlock_tokens)
        for _item, text in case_text_pairs
    )
    if not has_nonlinear_context and not has_nonlinear_case and not has_obsolete_linear_unlock_case:
        return [dict(item) for item in cases if isinstance(item, dict)], 0

    output: list[dict[str, Any]] = []
    dropped_count = 0
    for item, text in case_text_pairs:
        has_stage_context = any(token in text for token in stage_context_tokens)
        asserts_blocked_stage = any(token in text for token in blocking_tokens)
        asserts_known_obsolete_unlock = any(token in text for token in obsolete_linear_unlock_tokens)
        explicitly_compatible = any(token in text for token in compatibility_tokens)
        asserts_positive_nonlinear = any(token in text for token in positive_nonlinear_assertion_tokens)
        if asserts_positive_nonlinear and not asserts_known_obsolete_unlock:
            output.append(dict(item))
            continue
        if has_stage_context and (asserts_blocked_stage or asserts_known_obsolete_unlock) and not explicitly_compatible:
            dropped_count += 1
            continue
        output.append(dict(item))
    return output, dropped_count
