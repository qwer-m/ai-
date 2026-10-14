"""生成专家的范围问题报告：校验来源引用，不把调整建议当成已通过的用例。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, TYPE_CHECKING

from jsonschema import ValidationError, validate

from .action_journal import action_state_hash
from .output_repair import OutputValidationError
from .test_generation_facts import derive_test_design_item_ids, fact_source_texts, index_effective_facts
from .test_generation_schemas import GENERATION_BATCH_ITEM_SCHEMA

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


_IDENTIFIERS_SCHEMA = {
    "type": "array", "minItems": 1, "uniqueItems": True,
    "items": {"type": "string", "minLength": 1},
}

GENERATION_SCOPE_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "mutually_exclusive_preconditions", "lifecycle_discontinuity",
                            "batch_capacity_pressure", "insufficient_design_support",
                        ],
                    },
                    "explanation": {"type": "string", "minLength": 1},
                    "scenario_groups": {
                        "type": "array", "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "fact_ids": deepcopy(_IDENTIFIERS_SCHEMA),
                                "test_design_item_ids": deepcopy(_IDENTIFIERS_SCHEMA),
                                "condition": {
                                    "type": "object",
                                    "properties": {
                                        "fact_id": {"type": "string", "minLength": 1},
                                        "quote": {"type": "string", "minLength": 1},
                                    },
                                    "required": ["fact_id", "quote"],
                                    "additionalProperties": False,
                                },
                            },
                            "required": ["fact_ids", "test_design_item_ids", "condition"],
                            "additionalProperties": False,
                        },
                    },
                    "requested_change": {
                        "type": "string", "enum": ["repartition", "reallocate", "replan"],
                    },
                },
                "required": ["kind", "explanation", "scenario_groups", "requested_change"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["issues"],
    "additionalProperties": False,
}


GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "result_type": {"type": "string", "const": "planning_feedback"},
        "planning_feedback": deepcopy(GENERATION_SCOPE_FEEDBACK_SCHEMA),
    },
    "required": ["result_type", "planning_feedback"],
    "additionalProperties": False,
}
# 身份字段由平台从本次原始输入派生。允许重复验收，但禁止模型修改这些字段。
GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA["properties"]["planning_feedback"]["properties"].update({
    "batch_id": {"type": "string", "minLength": 1},
    "case_budget": {"type": "integer", "minimum": 1},
    "source_input_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "assessment_status": {"type": "string", "const": "unreviewed"},
})


def report_generation_scope_problem(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """返回独立报告信封；只有调用方授予的备用验收契约可以接纳它。"""

    return {"result_type": "planning_feedback", "planning_feedback": deepcopy(arguments)}


def validate_generation_scope_feedback(
    input_payload: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """校验当前批次的事实、设计关联及原文引用，保留父协调者的判断责任。

    报告只证明引用属于真实批次；互斥、阶段不连续或数量压力仍是待评估的
    模型主张。此函数不分配预算、不增加事实，也不生成可交付用例。
    """

    # 原始来源损坏属于平台输入问题，不能包装成模型可修复的候选差异。
    try:
        validate(input_payload, GENERATION_BATCH_ITEM_SCHEMA)
    except ValidationError as error:
        raise ValueError(f"范围问题报告缺少完整生成批次输入: {error.message}") from error
    facts = index_effective_facts(input_payload["authoritative_facts"])
    contract = input_payload["case_fact_contract"]
    design_ids = [item["test_design_item_id"] for item in input_payload["plan"]["test_design_items"]]
    if (contract["available_fact_ids"] != list(facts)
            or not set(contract["required_fact_ids"]) <= set(facts)
            or contract["required_test_design_item_ids"] != design_ids
            or input_payload["batch"]["required_test_design_item_ids"] != design_ids):
        raise ValueError("范围问题报告的事实或设计契约与当前批次输入不一致")
    derive_test_design_item_ids(
        case_fact_bindings=[], fact_design_item_ids=contract["fact_design_item_ids"],
        required_fact_ids=contract["required_fact_ids"], available_fact_ids=list(facts), required_design_item_ids=design_ids,
    )
    expected_ids = [f"TC-{index + 1:03d}" for index in range(input_payload["case_budget"])]
    if contract["target_case_ids"] != expected_ids:
        raise ValueError("范围问题报告的原始用例数量契约不一致")

    try:
        validate(output, GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA)
    except ValidationError as error:
        raise OutputValidationError(
            f"范围问题报告结构无效: {error.message}",
            details={"field_path": ".".join(str(part) for part in error.absolute_path)},
        ) from error
    report = deepcopy(output["planning_feedback"])
    identity = {
        "batch_id": input_payload["batch"]["batch_id"],
        "case_budget": input_payload["case_budget"],
        "source_input_hash": action_state_hash(input_payload),
        "assessment_status": "unreviewed",
    }
    for name, value in identity.items():
        if name in report and report[name] != value:
            raise OutputValidationError("范围问题报告不能更改批次身份或预算", details={"field": name})

    routes = contract["fact_design_item_ids"]
    for issue_index, issue in enumerate(report["issues"]):
        if issue["kind"] != "insufficient_design_support" and len(issue["scenario_groups"]) < 2:
            raise OutputValidationError("场景冲突或容量压力需要至少两个不同的真实场景组")
        if not issue["explanation"].strip():
            raise OutputValidationError("范围问题必须说明场景之间的具体矛盾")
        group_identities: set[tuple[str, ...]] = set()
        for group_index, group in enumerate(issue["scenario_groups"]):
            position = {"issue_index": issue_index, "group_index": group_index}
            unknown_facts = sorted(set(group["fact_ids"]) - set(facts))
            unknown_designs = sorted(set(group["test_design_item_ids"]) - set(design_ids))
            if unknown_facts or unknown_designs:
                raise OutputValidationError(
                    "范围问题报告只能引用当前批次事实和设计项",
                    details={**position, "invalid_fact_ids": unknown_facts,
                             "invalid_test_design_item_ids": unknown_designs},
                )
            cited_designs = set(group["test_design_item_ids"])
            linked_designs = {identifier for fact_id in group["fact_ids"] for identifier in routes[fact_id]}
            unrelated_facts = [fact_id for fact_id in group["fact_ids"]
                               if not cited_designs.intersection(routes[fact_id])]
            if not cited_designs.issubset(linked_designs) or unrelated_facts:
                raise OutputValidationError(
                    "范围问题中的事实与设计项必须沿当前批次已有路由关联",
                    details={**position, "unrelated_fact_ids": unrelated_facts,
                             "unlinked_test_design_item_ids": sorted(cited_designs - linked_designs)},
                )
            condition = group["condition"]
            fact_id, quote = condition["fact_id"], condition["quote"]
            if (fact_id not in group["fact_ids"] or not quote.strip()
                    or not any(quote in text for text in fact_source_texts(facts[fact_id]))):
                raise OutputValidationError(
                    "场景条件必须逐字引用本组生效事实的 assertion 或同一真实表格单元格，不能跨单元格拼接",
                    details={**position, "fact_id": fact_id},
                )
            group_identity = tuple(sorted(group["fact_ids"]))
            if group_identity in group_identities:
                raise OutputValidationError("不能把相同事实组重复申报为多个冲突场景", details=position)
            group_identities.add(group_identity)
    report.update(identity)
    return {"result_type": "planning_feedback", "planning_feedback": report}
