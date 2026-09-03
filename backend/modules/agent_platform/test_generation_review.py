from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, TYPE_CHECKING

from .test_generation_facts import (
    binding_index,
    materialize_inline_grounding,
    validate_case_fact_bindings,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


REVIEW_MAX_CASES_PER_BATCH = 10
REVIEW_MAX_JSON_CHARS_PER_BATCH = 70000
REPAIR_MAX_CASES_PER_BATCH = 1
STATE_COHERENCE_REPAIR_CATEGORIES = {"state_coherence"}
GLOBAL_REVIEW_VISIBLE_FIELD_PATHS = {
    "title",
    "module",
    "priority",
    "first_action",
    "last_expected",
}


def _required_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是数组")
    return value


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _difference_identity(difference: dict[str, Any]) -> tuple[str, str, str]:
    """终审分层合并时，以问题落点识别同一条差异。"""

    return (
        str(difference.get("case_id") or ""),
        str(difference.get("category") or ""),
        str(difference.get("field_path") or ""),
    )


def _case_business_signature(case: dict[str, Any]) -> str:
    """生成不含标题和标签的业务步骤签名，用于阻止重复修复结果。"""

    payload = {
        "preconditions": [
            " ".join(str(value or "").split())
            for value in list(case.get("preconditions") or [])
        ],
        "steps": [
            {
                "action": " ".join(str(dict(step).get("action") or "").split()),
                "expected": " ".join(str(dict(step).get("expected") or "").split()),
            }
            for step in list(case.get("steps") or [])
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _binding_fact_ids(binding: dict[str, Any]) -> set[str]:
    fact_ids: set[str] = set()
    for item in list(binding.get("precondition_bindings") or []):
        fact_ids.update(str(value) for value in list(dict(item).get("fact_ids") or []))
    for item in list(binding.get("step_bindings") or []):
        detail = dict(item)
        fact_ids.update(str(value) for value in list(detail.get("action_fact_ids") or []))
        fact_ids.update(str(value) for value in list(detail.get("expected_fact_ids") or []))
    return fact_ids


def _difference_fact_ids(
    *,
    source_input: dict[str, Any],
    case_id: str | None,
    field_path: str | None,
) -> list[str]:
    """按用例字段落点从平台绑定中派生终审关联事实。"""

    if not case_id:
        return []
    bindings = binding_index(source_input.get("case_fact_bindings"))
    binding = bindings.get(case_id)
    if binding is None:
        raise ValueError(f"终审批次缺少用例事实绑定: case_id={case_id}")
    path = str(field_path or "").strip()
    precondition_match = re.fullmatch(r"preconditions\[(\d+)](?:\..+)?", path)
    if precondition_match:
        index = int(precondition_match.group(1))
        indexed = {
            int(dict(item).get("precondition_index", -1)): dict(item)
            for item in list(binding.get("precondition_bindings") or [])
        }
        return list(indexed.get(index, {}).get("fact_ids") or [])
    step_match = re.fullmatch(r"steps\[(\d+)](?:\.(action|expected))?", path)
    if step_match:
        index = int(step_match.group(1))
        indexed = {
            int(dict(item).get("step_index", -1)): dict(item)
            for item in list(binding.get("step_bindings") or [])
        }
        step_binding = indexed.get(index, {})
        field = step_match.group(2)
        if field == "action":
            return list(step_binding.get("action_fact_ids") or [])
        if field == "expected":
            return list(step_binding.get("expected_fact_ids") or [])
        return sorted(
            {
                *list(step_binding.get("action_fact_ids") or []),
                *list(step_binding.get("expected_fact_ids") or []),
            }
        )
    return sorted(_binding_fact_ids(binding))


def _has_repeated_fact_obligation(
    *,
    source_input: dict[str, Any],
    related_fact_ids: list[str],
) -> bool:
    """重复问题必须能落到至少两个字段槽位中的同一事实语义。"""

    assertions_by_id = {
        _required_text(dict(fact).get("fact_id"), "fact_id"): _required_text(
            dict(fact).get("assertion"),
            "assertion",
        )
        for fact in list(source_input.get("review_facts") or [])
    }
    target_assertions = {
        assertions_by_id[fact_id]
        for fact_id in related_fact_ids
        if fact_id in assertions_by_id
    }
    if not target_assertions:
        return False
    occurrence_counts = {assertion: 0 for assertion in target_assertions}
    for raw_binding in list(source_input.get("case_fact_bindings") or []):
        binding = dict(raw_binding)
        slots = [
            list(dict(item).get("fact_ids") or [])
            for item in list(binding.get("precondition_bindings") or [])
        ]
        slots.extend(
            [
                *list(dict(item).get("action_fact_ids") or []),
                *list(dict(item).get("expected_fact_ids") or []),
            ]
            for item in list(binding.get("step_bindings") or [])
        )
        for slot_fact_ids in slots:
            slot_assertions = {
                assertions_by_id[fact_id]
                for fact_id in slot_fact_ids
                if fact_id in assertions_by_id
            }
            for assertion in target_assertions & slot_assertions:
                occurrence_counts[assertion] += 1
    return any(count >= 2 for count in occurrence_counts.values())


def _mentioned_fact_ids(*, text: str, known_fact_ids: set[str]) -> set[str]:
    """只识别完整事实编号，避免短编号命中长编号的子串。"""

    return {
        fact_id
        for fact_id in known_fact_ids
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(fact_id)}(?![A-Za-z0-9_-])",
            text,
        )
    }


def _fact_coverage_key(fact: dict[str, Any]) -> tuple[Any, ...]:
    """把同页 OCR 分块产生的同义重复事实归为一个覆盖义务。"""

    anchor = dict(fact.get("source_anchor") or {})
    source_kind = str(anchor.get("source_kind") or "")
    if source_kind == "document":
        source_identity = (
            source_kind,
            int(anchor.get("document_id") or 0),
            int(anchor.get("page_number") or 0),
        )
    else:
        source_identity = (
            source_kind,
            str(anchor.get("requirement_sha256") or ""),
        )
    governance = tuple(
        sorted(
            (
                str(dict(item).get("relation") or ""),
                str(dict(item).get("directive_fact_id") or ""),
            )
            for item in list(fact.get("governed_by") or [])
        )
    )
    return (
        source_identity,
        str(fact.get("assertion") or "").strip(),
        str(fact.get("status") or ""),
        str(fact.get("value_policy") or ""),
        tuple(str(value) for value in list(fact.get("governed_values") or [])),
        governance,
    )


def _facts_by_coverage_key(
    facts: list[dict[str, Any]],
) -> dict[tuple[Any, ...], list[str]]:
    groups: dict[tuple[Any, ...], list[str]] = {}
    for fact in facts:
        if str(fact.get("status") or "") != "effective":
            continue
        fact_id = str(fact.get("fact_id") or "")
        groups.setdefault(_fact_coverage_key(fact), []).append(fact_id)
    return groups


def _deduplicate_facts(raw_facts: list[Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_fact in raw_facts:
        if not isinstance(raw_fact, dict):
            raise ValueError("authoritative_facts每项必须是对象")
        fact = dict(raw_fact)
        fact_id = _required_text(fact.get("fact_id"), "fact_id")
        if fact_id in seen:
            continue
        seen.add(fact_id)
        facts.append(fact)
    return facts


def _review_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """终审只读取标准化业务事实，不暴露 quote、页码和坐标。"""

    return [
        {
            "fact_id": _required_text(fact.get("fact_id"), "fact_id"),
            "assertion": _required_text(fact.get("assertion"), "assertion"),
            "value_policy": _required_text(fact.get("value_policy"), "value_policy"),
            "governed_values": [
                _required_text(value, "governed_value")
                for value in list(fact.get("governed_values") or [])
            ],
        }
        for fact in facts
    ]


def _repair_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """修复模型只读取业务事实，来源锚点保留平台校验需要的最小身份。"""

    compact: list[dict[str, Any]] = []
    for fact in facts:
        anchor = dict(fact.get("source_anchor") or {})
        source_kind = str(anchor.get("source_kind") or "")
        compact_anchor: dict[str, Any] = {"source_kind": source_kind}
        if source_kind == "document":
            compact_anchor.update(
                {
                    "document_id": int(anchor.get("document_id") or 0),
                    "page_number": int(anchor.get("page_number") or 0),
                }
            )
        else:
            compact_anchor["requirement_sha256"] = str(
                anchor.get("requirement_sha256") or ""
            )
        compact.append(
            {
                "fact_id": _required_text(fact.get("fact_id"), "fact_id"),
                "scope_id": str(fact.get("scope_id") or ""),
                "assertion": _required_text(fact.get("assertion"), "assertion"),
                "status": _required_text(fact.get("status"), "status"),
                "value_policy": _required_text(
                    fact.get("value_policy"), "value_policy"
                ),
                "governed_values": list(fact.get("governed_values") or []),
                "governed_by": deepcopy(fact.get("governed_by") or []),
                "source_anchor": compact_anchor,
            }
        )
    return compact


def _repair_fact_obligation_text(
    *,
    required_fact_ids: list[str],
    facts_by_id: dict[str, dict[str, Any]],
) -> str:
    """从当前批次的真实事实生成逐条保留清单，避免修复模型只看到编号而漏迁移。"""

    obligations: list[str] = []
    for fact_id in required_fact_ids:
        fact = facts_by_id.get(fact_id)
        assertion = str(dict(fact or {}).get("assertion") or "").strip()
        obligations.append(f"{fact_id}={assertion}" if assertion else fact_id)
    return (
        "事实保留清单（每条都必须在修复后继续绑定，不能删除或用其他事实替代）："
        + "；".join(obligations)
    )


def _deduplicate_design_items(raw_items: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("test_design_items每项必须是对象")
        item = dict(raw_item)
        item_id = _required_text(item.get("test_design_item_id"), "test_design_item_id")
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append(item)
    return items


def _batch_audit(
    *,
    test_cases: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    test_design_items: list[dict[str, Any]],
) -> dict[str, Any]:
    fact_groups = _facts_by_coverage_key(facts)
    effective_fact_ids = {fact_id for ids in fact_groups.values() for fact_id in ids}
    covered_fact_ids = {
        fact_id
        for binding in bindings
        for fact_id in _binding_fact_ids(binding)
    }
    covered_group_count = sum(
        bool(set(fact_ids) & covered_fact_ids) for fact_ids in fact_groups.values()
    )
    uncovered = sorted(
        fact_ids[0]
        for fact_ids in fact_groups.values()
        if not set(fact_ids) & covered_fact_ids
    )
    invalid = sorted(covered_fact_ids - effective_fact_ids)
    required_design_item_ids = {
        _required_text(item.get("test_design_item_id"), "test_design_item_id")
        for item in test_design_items
    }
    covered_design_item_ids = {
        str(design_item_id)
        for test_case in test_cases
        for design_item_id in list(test_case.get("test_design_item_ids") or [])
    }
    uncovered_design_item_ids = sorted(
        required_design_item_ids - covered_design_item_ids
    )
    invalid_design_item_ids = sorted(
        covered_design_item_ids - required_design_item_ids
    )
    approved = (
        not uncovered
        and not invalid
        and not uncovered_design_item_ids
        and not invalid_design_item_ids
    )
    differences: list[str] = []
    if uncovered:
        differences.append(f"当前批次存在未覆盖事实: {uncovered}")
    if invalid:
        differences.append(f"当前批次存在无效事实引用: {invalid}")
    if uncovered_design_item_ids:
        differences.append(f"当前批次存在未覆盖测试设计项: {uncovered_design_item_ids}")
    if invalid_design_item_ids:
        differences.append(f"当前批次存在无效测试设计项引用: {invalid_design_item_ids}")
    return {
        "approved": approved,
        "case_count": len(test_cases),
        "effective_fact_count": len(fact_groups),
        "covered_fact_count": covered_group_count,
        "uncovered_fact_ids": uncovered,
        "invalid_fact_ids": invalid,
        "duplicate_case_ids": [],
        "test_design_item_count": len(required_design_item_ids),
        "covered_test_design_item_count": len(
            covered_design_item_ids & required_design_item_ids
        ),
        "uncovered_test_design_item_ids": uncovered_design_item_ids,
        "invalid_test_design_item_ids": invalid_design_item_ids,
        "summary": (
            f"批次确定性审计{'通过' if approved else '未通过'}："
            f"覆盖 {covered_group_count}/{len(fact_groups)} 个有效事实组，"
            f"覆盖 {len(covered_design_item_ids & required_design_item_ids)}/"
            f"{len(required_design_item_ids)} 个测试设计项。"
        ),
        "differences": differences,
    }


def _review_item_chars(item: dict[str, Any]) -> int:
    payload = dict(item)
    authoritative_facts = [
        dict(fact) for fact in list(payload.pop("authoritative_facts", []))
    ]
    payload["review_facts"] = _review_facts(authoritative_facts)
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _finalize_review_items(items: list[dict[str, Any]]) -> None:
    total = len(items)
    for index, item in enumerate(items, start=1):
        review_batch = dict(item["review_batch"])
        review_batch["batch_number"] = index
        review_batch["batch_count"] = total
        review_batch["case_ids"] = [
            str(case.get("case_id") or "") for case in list(item["test_cases"])
        ]
        item["review_batch"] = review_batch
        item["audit_summary"] = _batch_audit(
            test_cases=[dict(case) for case in list(item["test_cases"])],
            bindings=[dict(binding) for binding in list(item["case_fact_bindings"])],
            facts=[dict(fact) for fact in list(item["authoritative_facts"])],
            test_design_items=[
                dict(design_item) for design_item in list(item["test_design_items"])
            ],
        )
        item["review_facts"] = _review_facts(
            [dict(fact) for fact in list(item.pop("authoritative_facts"))]
        )


def prepare_final_review_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """复用生成包边界，按模块和负载合并为可恢复的终审批次。"""

    generation_inputs = _required_list(arguments.get("generation_inputs"), "generation_inputs")
    generation = dict(arguments.get("generation") or {})
    test_cases = [dict(item) for item in _required_list(generation.get("test_cases"), "test_cases")]
    bindings = [
        dict(item)
        for item in _required_list(generation.get("case_fact_bindings"), "case_fact_bindings")
    ]
    if len(test_cases) != len(bindings):
        raise ValueError("终审准备阶段的用例与事实绑定数量不一致")
    bindings_by_case_id = binding_index(bindings)
    batch_case_limit = int(arguments.get("batch_case_limit") or 0)
    if batch_case_limit < 1:
        raise ValueError("batch_case_limit必须大于0")
    review_case_limit = min(
        REVIEW_MAX_CASES_PER_BATCH,
        max(batch_case_limit, batch_case_limit * 2),
    )

    source_items: list[dict[str, Any]] = []
    offset = 0
    for source_index, raw_input in enumerate(generation_inputs):
        if not isinstance(raw_input, dict):
            raise ValueError("generation_inputs每项必须是对象")
        source_input = dict(raw_input)
        case_count = int(source_input.get("case_budget") or 0)
        if case_count < 1:
            raise ValueError(f"生成包缺少有效case_budget: index={source_index}")
        source_cases = test_cases[offset : offset + case_count]
        if len(source_cases) != case_count:
            raise ValueError("生成包预算与合并用例顺序不一致")
        source_bindings = [
            deepcopy(bindings_by_case_id[_required_text(case.get("case_id"), "case_id")])
            for case in source_cases
        ]
        batch = dict(source_input.get("batch") or {})
        module_name = _required_text(batch.get("module_name"), "batch.module_name")
        test_design_items = _deduplicate_design_items(
            list(dict(source_input.get("plan") or {}).get("test_design_items") or [])
        )
        source_items.append(
            {
                "review_batch": {
                    "batch_id": f"R-{len(source_items) + 1:03d}",
                    "batch_number": 0,
                    "batch_count": 0,
                    "module_name": module_name,
                    "generation_batch_ids": [str(batch.get("batch_id") or source_index)],
                    "case_ids": [],
                },
                "test_cases": deepcopy(source_cases),
                "case_fact_bindings": source_bindings,
                "authoritative_facts": _deduplicate_facts(
                    list(source_input.get("authoritative_facts") or [])
                ),
                "test_design_items": test_design_items,
                "audit_summary": {},
            }
        )
        offset += case_count
    if offset != len(test_cases):
        raise ValueError("存在未映射到生成包的测试用例")

    items: list[dict[str, Any]] = []
    for source_item in source_items:
        if not items:
            items.append(source_item)
            continue
        current = items[-1]
        same_module = (
            current["review_batch"]["module_name"]
            == source_item["review_batch"]["module_name"]
        )
        merged = {
            "review_batch": {
                **dict(current["review_batch"]),
                "generation_batch_ids": [
                    *list(current["review_batch"]["generation_batch_ids"]),
                    *list(source_item["review_batch"]["generation_batch_ids"]),
                ],
            },
            "test_cases": [*list(current["test_cases"]), *list(source_item["test_cases"])],
            "case_fact_bindings": [
                *list(current["case_fact_bindings"]),
                *list(source_item["case_fact_bindings"]),
            ],
            "authoritative_facts": _deduplicate_facts(
                [
                    *list(current["authoritative_facts"]),
                    *list(source_item["authoritative_facts"]),
                ]
            ),
            "test_design_items": _deduplicate_design_items(
                [
                    *list(current["test_design_items"]),
                    *list(source_item["test_design_items"]),
                ]
            ),
            "audit_summary": {},
        }
        if (
            same_module
            and len(merged["test_cases"]) <= review_case_limit
            and _review_item_chars(merged) <= REVIEW_MAX_JSON_CHARS_PER_BATCH
        ):
            items[-1] = merged
        else:
            source_item["review_batch"]["batch_id"] = f"R-{len(items) + 1:03d}"
            items.append(source_item)

    _finalize_review_items(items)
    context.artifacts["final_review_batch_plan"] = {
        "batch_count": len(items),
        "case_count": len(test_cases),
        "review_case_limit": review_case_limit,
        "max_json_chars_per_batch": REVIEW_MAX_JSON_CHARS_PER_BATCH,
    }
    return {"items": items, "batch_count": len(items), "case_count": len(test_cases)}


def postprocess_final_review_batch_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    source_input = dict(arguments.get("item_input") or {})
    output = dict(arguments.get("item_output") or {})
    if output.get("phase") not in {None, "final_review"}:
        raise ValueError("终审批次phase必须为final_review")
    approved = output.get("approved")
    if not isinstance(approved, bool):
        raise ValueError("终审批次approved必须为布尔值")
    differences = _required_list(output.get("differences"), "differences")
    case_ids = {
        _required_text(dict(case).get("case_id"), "case_id")
        for case in list(source_input.get("test_cases") or [])
    }
    fact_ids = {
        _required_text(dict(fact).get("fact_id"), "fact_id")
        for fact in list(source_input.get("review_facts") or [])
    }
    normalized_differences: list[dict[str, Any]] = []
    discarded_duplicate_count = 0
    for raw_difference in differences:
        if not isinstance(raw_difference, dict):
            raise ValueError("终审difference必须是对象")
        difference = dict(raw_difference)
        case_id = difference.get("case_id")
        if case_id is not None and str(case_id) not in case_ids:
            raise ValueError(f"终审difference引用批次外用例: case_id={case_id}")
        field_path = (
            None
            if difference.get("field_path") is None
            else _required_text(difference.get("field_path"), "difference.field_path")
        )
        related = _difference_fact_ids(
            source_input=source_input,
            case_id=None if case_id is None else str(case_id),
            field_path=field_path,
        )
        unknown = set(related) - fact_ids
        if unknown:
            raise ValueError(f"终审字段绑定引用批次外事实: {sorted(unknown)}")
        difference_text = " ".join(
            str(difference.get(field) or "")
            for field in ("detail", "repair_instruction")
        )
        mentioned_fact_ids = _mentioned_fact_ids(
            text=difference_text,
            known_fact_ids=fact_ids,
        )
        # 终审建议可以引用本批次内尚未绑定到当前字段的事实；
        # 修复阶段会依据 related_fact_ids 补齐字段内容和绑定。
        related = sorted({*related, *mentioned_fact_ids})
        if (
            difference.get("category") == "semantic_duplicate"
            and not _has_repeated_fact_obligation(
                source_input=source_input,
                related_fact_ids=related,
            )
        ):
            discarded_duplicate_count += 1
            continue
        difference["field_path"] = field_path
        difference["related_fact_ids"] = related
        difference["repair_instruction"] = _required_text(
            difference.get("repair_instruction"),
            "difference.repair_instruction",
        )
        normalized_differences.append(difference)
    if approved and normalized_differences:
        raise ValueError("终审通过时不得返回差异")
    if not normalized_differences:
        approved = True
    summary = str(output.get("summary") or "").strip()
    if discarded_duplicate_count and approved:
        summary = (
            "批次终审通过：平台已剔除 "
            f"{discarded_duplicate_count} 项没有重复事实依据的语义重复误报。"
        )
    elif not summary:
        summary = (
            "批次终审通过。"
            if approved
            else f"批次终审未通过：发现 {len(normalized_differences)} 项差异。"
        )
    return {
        "phase": "final_review",
        "approved": approved,
        "summary": summary,
        "differences": normalized_differences,
    }


def _map_output(
    *,
    item_index: int,
    source_input: dict[str, Any],
    records: list[Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if item_index >= len(records):
        raise ValueError(f"映射结果缺少输入项: item_index={item_index}")
    record = records[item_index]
    if not isinstance(record, dict) or int(record.get("item_index", -1)) != item_index:
        raise ValueError(f"映射结果顺序错误: item_index={item_index}")
    output = record.get("output")
    if not isinstance(output, dict):
        raise ValueError(f"映射结果缺少output: item_index={item_index}")
    return postprocess_final_review_batch_item(
        context,
        {"item_input": source_input, "item_output": output},
    )


def _index_generation_inputs(
    generation_inputs: list[Any],
) -> dict[str, dict[str, Any]]:
    generation_by_batch_id: dict[str, dict[str, Any]] = {}
    for raw_generation_input in generation_inputs:
        if not isinstance(raw_generation_input, dict):
            raise ValueError("generation_inputs每项必须是对象")
        generation_input = dict(raw_generation_input)
        batch_id = _required_text(
            dict(generation_input.get("batch") or {}).get("batch_id"),
            "generation_batch_id",
        )
        if batch_id in generation_by_batch_id:
            raise ValueError(f"generation_inputs包含重复batch_id: {batch_id}")
        generation_by_batch_id[batch_id] = generation_input
    return generation_by_batch_id


def _chunk_case_ids(case_ids: list[str], *, chunk_size: int) -> list[list[str]]:
    """将待修用例拆成稳定的小批次，避免模型输出因批次过大被截断。"""

    if chunk_size < 1:
        raise ValueError("修复批次大小必须大于0")
    return [
        case_ids[offset : offset + chunk_size]
        for offset in range(0, len(case_ids), chunk_size)
    ]


def _mentioned_batch_case_ids(
    *,
    text: str,
    batch_case_ids: list[str],
    source_case_id: str,
) -> list[str]:
    """提取修复指令明确引用的同批目标用例，供事实跨用例迁移。"""

    mentioned: list[str] = []
    for case_id in batch_case_ids:
        if case_id == source_case_id:
            continue
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(case_id)}(?![A-Za-z0-9_-])",
            text,
        ):
            mentioned.append(case_id)
    return mentioned


def _inline_repair_cases(
    *,
    test_cases: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把平台持久化绑定投影为修复 Agent 唯一输出结构。"""

    bindings_by_id = binding_index(bindings)
    inline_cases: list[dict[str, Any]] = []
    for raw_case in test_cases:
        case = deepcopy(raw_case)
        case_id = _required_text(case.get("case_id"), "case_id")
        binding = bindings_by_id.get(case_id)
        if binding is None:
            raise ValueError(f"修复输入缺少用例事实绑定: case_id={case_id}")
        preconditions = list(case.get("preconditions") or [])
        steps = list(case.get("steps") or [])
        precondition_bindings = {
            int(dict(item).get("precondition_index")): dict(item)
            for item in list(binding.get("precondition_bindings") or [])
        }
        step_bindings = {
            int(dict(item).get("step_index")): dict(item)
            for item in list(binding.get("step_bindings") or [])
        }
        if set(precondition_bindings) != set(range(len(preconditions))):
            raise ValueError(f"修复输入前置条件绑定不完整: case_id={case_id}")
        if set(step_bindings) != set(range(len(steps))):
            raise ValueError(f"修复输入步骤绑定不完整: case_id={case_id}")
        case["preconditions"] = [
            {
                "text": _required_text(value, f"{case_id}.preconditions[{index}]"),
                "fact_ids": list(precondition_bindings[index].get("fact_ids") or []),
            }
            for index, value in enumerate(preconditions)
        ]
        case["steps"] = [
            {
                "action": _required_text(
                    dict(value).get("action"),
                    f"{case_id}.steps[{index}].action",
                ),
                "expected": _required_text(
                    dict(value).get("expected"),
                    f"{case_id}.steps[{index}].expected",
                ),
                "fact_bindings": {
                    "action": list(
                        step_bindings[index].get("action_fact_ids") or []
                    ),
                    "expected": list(
                        step_bindings[index].get("expected_fact_ids") or []
                    ),
                },
            }
            for index, value in enumerate(steps)
        ]
        inline_cases.append(case)
    return inline_cases


def prepare_final_review_repairs(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    review_inputs = _required_list(arguments.get("review_inputs"), "review_inputs")
    review_records = _required_list(arguments.get("review_records"), "review_records")
    generation_inputs = _required_list(
        arguments.get("generation_inputs"),
        "generation_inputs",
    )
    if len(review_inputs) != len(review_records):
        raise ValueError("终审输入与结果数量不一致")
    cycles = list(context.artifacts.get("final_review_repair_cycles") or [])
    repair_cycle = len(cycles) + 1
    generation_by_batch_id = _index_generation_inputs(generation_inputs)
    items: list[dict[str, Any]] = []
    source_repair_batch_count = 0
    for item_index, raw_input in enumerate(review_inputs):
        source_input = dict(raw_input)
        review = _map_output(
            item_index=item_index,
            source_input=source_input,
            records=review_records,
            context=context,
        )
        batch_audit = dict(source_input.get("audit_summary") or {})
        uncovered_fact_ids = [
            str(value) for value in list(batch_audit.get("uncovered_fact_ids") or [])
        ]
        if review.get("approved") is True and not uncovered_fact_ids:
            continue
        source_repair_batch_count += 1
        differences = [dict(item) for item in list(review.get("differences") or [])]
        batch_case_ids = [
            _required_text(case.get("case_id"), "case_id")
            for case in list(source_input.get("test_cases") or [])
        ]
        bindings_by_id = binding_index(source_input.get("case_fact_bindings"))
        difference_case_ids = {
            _required_text(difference.get("case_id"), "difference.case_id")
            for difference in differences
        }
        unknown_case_ids = difference_case_ids - set(batch_case_ids)
        if unknown_case_ids:
            raise ValueError(
                "终审差异引用未知用例: "
                f"case_ids={sorted(unknown_case_ids)}"
            )
        needs_cohort_repair = any(
            str(difference.get("category") or "")
            in STATE_COHERENCE_REPAIR_CATEGORIES
            or str(difference.get("repair_scope") or "") == "cohort"
            for difference in differences
        )
        destination_case_ids = {
            destination_case_id
            for difference in differences
            for destination_case_id in _mentioned_batch_case_ids(
                text=str(difference.get("repair_instruction") or ""),
                batch_case_ids=batch_case_ids,
                source_case_id=str(difference.get("case_id") or ""),
            )
        }
        needs_case_reassignment = bool(destination_case_ids)
        # 只把审查明确定位的用例放进修复集合；审核批次只是负载边界，不能被当成业务关联范围。
        target_case_ids = (
            batch_case_ids
            if uncovered_fact_ids and not difference_case_ids
            else [
                case_id
                for case_id in batch_case_ids
                if case_id in difference_case_ids or case_id in destination_case_ids
            ]
        )
        if not target_case_ids:
            raise ValueError("终审未通过但没有可定位的修复用例")
        cases_by_id = {
            _required_text(dict(case).get("case_id"), "case_id"): dict(case)
            for case in list(source_input.get("test_cases") or [])
        }
        generation_batch_ids = [
            _required_text(value, "review_batch.generation_batch_id")
            for value in list(
                dict(source_input.get("review_batch") or {}).get("generation_batch_ids")
                or []
            )
        ]
        unknown_generation_batch_ids = set(generation_batch_ids) - set(
            generation_by_batch_id
        )
        if unknown_generation_batch_ids:
            raise ValueError(
                "终审批次引用未知生成包: "
                f"{sorted(unknown_generation_batch_ids)}"
            )
        authoritative_facts = _deduplicate_facts(
            [
                fact
                for batch_id in generation_batch_ids
                for fact in list(
                    generation_by_batch_id[batch_id].get("authoritative_facts") or []
                )
            ]
        )
        facts_by_id = {
            str(fact.get("fact_id") or ""): fact for fact in authoritative_facts
        }
        batch_test_design_items = [
            dict(item) for item in list(source_input.get("test_design_items") or [])
        ]
        # 纯覆盖缺口需要跨用例重新分配事实，保持原批次原子性；可定位差异则按用例拆分。
        repair_case_groups = (
            [target_case_ids]
            if uncovered_fact_ids or needs_cohort_repair or needs_case_reassignment
            else _chunk_case_ids(
                target_case_ids,
                chunk_size=REPAIR_MAX_CASES_PER_BATCH,
            )
        )
        for group_index, target_group in enumerate(repair_case_groups, start=1):
            target_case_set = set(target_group)
            target_cases = [cases_by_id[case_id] for case_id in target_group]
            target_bindings = [bindings_by_id[case_id] for case_id in target_group]
            target_differences = [
                difference
                for difference in differences
                if str(difference.get("case_id") or "") in target_case_set
            ]
            required_fact_ids = sorted(
                {
                    *uncovered_fact_ids,
                    *(
                        fact_id
                        for binding in target_bindings
                        for fact_id in _binding_fact_ids(binding)
                    ),
                }
            )
            requirements = [
                _required_text(
                    difference.get("repair_instruction"),
                    "difference.repair_instruction",
                )
                for difference in target_differences
            ]
            if required_fact_ids:
                requirements.insert(
                    0,
                    _repair_fact_obligation_text(
                        required_fact_ids=required_fact_ids,
                        facts_by_id=facts_by_id,
                    ),
                )
            if uncovered_fact_ids:
                requirements.append(
                    "在保持批次用例数量不变的前提下覆盖这些有效事实："
                    + "、".join(uncovered_fact_ids)
                )
            if needs_cohort_repair:
                requirements.append(
                    "本批存在角色、权限或生命周期状态冲突；请在目标用例集合内重新分配事实，"
                    "确保每条用例的前置角色与步骤、预期及事实绑定保持一致。"
                )
            if needs_case_reassignment:
                requirements.append(
                    "终审指令明确要求在 target_case_ids 内迁移内容；请同时修改源用例和目标用例，"
                    "将相关事实放入业务语义匹配的用例，并保持目标集合的既有事实覆盖。"
                )
            difference_fact_ids = {
                str(fact_id)
                for difference in target_differences
                for fact_id in list(difference.get("related_fact_ids") or [])
            }
            relevant_fact_ids = {*required_fact_ids, *difference_fact_ids}
            relevant_coverage_keys = {
                _fact_coverage_key(facts_by_id[fact_id])
                for fact_id in relevant_fact_ids
                if fact_id in facts_by_id
            }
            target_authoritative_facts = [
                fact
                for fact in authoritative_facts
                if _fact_coverage_key(fact) in relevant_coverage_keys
            ]
            target_design_item_ids = {
                str(design_item_id)
                for case in target_cases
                for design_item_id in list(case.get("test_design_item_ids") or [])
            }
            target_test_design_items = [
                item
                for item in batch_test_design_items
                if str(item.get("test_design_item_id") or "")
                in target_design_item_ids
            ]
            requirements.append(
                "修复后必须继续覆盖 required_fact_ids 中的全部有效事实；"
                "删除或合并步骤时，应将其承载的事实改写到语义匹配的步骤，"
                "不得降低修复前已经通过的确定性事实覆盖。"
            )
            repair_batch = deepcopy(source_input.get("review_batch") or {})
            if len(repair_case_groups) > 1:
                original_batch_id = _required_text(
                    repair_batch.get("batch_id"),
                    "review_batch.batch_id",
                )
                repair_batch["batch_id"] = (
                    f"{original_batch_id}-C{group_index:03d}"
                )
            repair_batch["case_ids"] = list(target_group)
            repair_review = deepcopy(review)
            repair_review["differences"] = target_differences
            items.append(
                {
                    "review_batch": repair_batch,
                    "test_cases": _inline_repair_cases(
                        test_cases=target_cases,
                        bindings=target_bindings,
                    ),
                    "authoritative_facts": _repair_facts(
                        target_authoritative_facts
                    ),
                    "test_design_items": deepcopy(target_test_design_items),
                    "review_result": repair_review,
                    "repair_requirements": requirements,
                    "required_fact_ids": required_fact_ids,
                    "target_case_ids": list(target_group),
                    "target_case_count": len(target_group),
                    "repair_cycle": repair_cycle,
                }
            )
    plan = {
        "repair_batch_count": len(items),
        "source_repair_batch_count": source_repair_batch_count,
        "preserved_batch_count": len(review_inputs) - source_repair_batch_count,
        "max_cases_per_repair_batch": REPAIR_MAX_CASES_PER_BATCH,
    }
    cycles.append({"cycle_number": repair_cycle, **plan})
    context.artifacts["final_review_repair_cycles"] = cycles
    context.artifacts["final_review_repair_plan"] = plan
    return {"items": items, "repair_batch_count": len(items)}


def postprocess_final_review_repair_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    source_input = dict(arguments.get("item_input") or {})
    raw_output = dict(arguments.get("item_output") or {})
    original_cases = [dict(item) for item in list(source_input.get("test_cases") or [])]
    expected_case_ids = [_required_text(case.get("case_id"), "case_id") for case in original_cases]
    target_case_ids = [
        _required_text(value, "target_case_ids")
        for value in _required_list(source_input.get("target_case_ids"), "target_case_ids")
    ]
    target_case_count = int(source_input.get("target_case_count") or 0)
    if target_case_ids != expected_case_ids or target_case_count != len(expected_case_ids):
        raise ValueError("修复输入的目标用例契约与原批次不一致")
    module_names = {_required_text(case.get("module"), "module") for case in original_cases}
    if len(module_names) != 1:
        raise ValueError("修复批次必须只包含一个业务模块")
    original_raw_cases = source_input.get("test_cases")
    original_bindings = source_input.get("case_fact_bindings")
    first_case = dict(original_cases[0]) if original_cases else {}
    first_precondition = next(iter(list(first_case.get("preconditions") or [])), None)
    if not isinstance(first_precondition, dict) and isinstance(original_bindings, list):
        original_raw_cases = _inline_repair_cases(
            test_cases=original_cases,
            bindings=[dict(binding) for binding in original_bindings],
        )
    original_output = materialize_inline_grounding(
        raw_cases=original_raw_cases,
        case_ids=expected_case_ids,
        module_name=next(iter(module_names)),
        fallback_tags_by_case_id={
            case_id: list(case.get("tags") or [])
            for case_id, case in zip(expected_case_ids, original_cases, strict=True)
        },
    )
    original_inline_cases = _inline_repair_cases(
        test_cases=[dict(case) for case in original_output["test_cases"]],
        bindings=[dict(binding) for binding in original_output["case_fact_bindings"]],
    )
    patches = _required_list(raw_output.get("case_patches"), "case_patches")
    if len(patches) > len(expected_case_ids):
        raise ValueError("修复补丁数量不能超过目标用例数量")
    patched_by_id = {
        case_id: deepcopy(case)
        for case_id, case in zip(expected_case_ids, original_inline_cases, strict=True)
    }
    allowed_fields = {
        "case_id",
        "title",
        "priority",
        "preconditions",
        "steps",
        "tags",
        "test_design_item_ids",
    }
    patched_ids: set[str] = set()
    for raw_patch in patches:
        if not isinstance(raw_patch, dict):
            raise ValueError("case_patches 每项必须是对象")
        patch = deepcopy(raw_patch)
        unknown_fields = set(patch) - allowed_fields
        if unknown_fields:
            raise ValueError(f"修复补丁包含不允许字段: {sorted(unknown_fields)}")
        case_id = _required_text(patch.pop("case_id", None), "case_patch.case_id")
        if case_id not in patched_by_id or case_id in patched_ids:
            raise ValueError(f"修复补丁引用未知或重复用例: case_id={case_id}")
        if not patch:
            raise ValueError(f"修复补丁没有任何字段变化: case_id={case_id}")
        patched_by_id[case_id].update(patch)
        patched_ids.add(case_id)
    output = materialize_inline_grounding(
        raw_cases=[patched_by_id[case_id] for case_id in expected_case_ids],
        case_ids=expected_case_ids,
        module_name=next(iter(module_names)),
        fallback_tags_by_case_id={
            case_id: list(case.get("tags") or [])
            for case_id, case in zip(expected_case_ids, original_cases, strict=True)
        },
    )
    if any(
        str(dict(difference).get("category") or "") == "semantic_duplicate"
        for difference in list(
            dict(source_input.get("review_result") or {}).get("differences") or []
        )
    ):
        signatures: dict[str, str] = {}
        duplicate_pairs: list[str] = []
        for case in list(output.get("test_cases") or []):
            case_id = _required_text(dict(case).get("case_id"), "case_id")
            signature = _case_business_signature(dict(case))
            previous_case_id = signatures.get(signature)
            if previous_case_id is not None:
                duplicate_pairs.append(f"{previous_case_id}/{case_id}")
            else:
                signatures[signature] = case_id
        if duplicate_pairs:
            raise ValueError(
                "语义重复修复后仍存在相同前置条件和步骤: "
                + "、".join(duplicate_pairs)
            )
    normalized = _validate_final_review_repair_output(
        source_input=source_input,
        output=output,
    )
    if output == original_output:
        # 评审要求可能已经由当前用例满足；保留原结果并交给后续独立复审裁决。
        normalized["review_noop"] = True
    return normalized


def _validate_final_review_repair_output(
    *,
    source_input: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """校验已经由平台拆分完成的终审修复结果。"""

    original_cases = [dict(item) for item in list(source_input.get("test_cases") or [])]
    repaired_cases = [dict(item) for item in _required_list(output.get("test_cases"), "test_cases")]
    expected_case_ids = [_required_text(case.get("case_id"), "case_id") for case in original_cases]
    actual_case_ids = [_required_text(case.get("case_id"), "case_id") for case in repaired_cases]
    if actual_case_ids != expected_case_ids:
        raise ValueError(
            "修复批次必须保持原case_id及顺序: "
            f"expected={expected_case_ids}, actual={actual_case_ids}"
        )
    for original, repaired in zip(original_cases, repaired_cases, strict=True):
        repaired.setdefault("tags", list(original.get("tags") or []))
        design_item_ids = repaired.get("test_design_item_ids")
        if not isinstance(design_item_ids, list):
            raise ValueError("修复用例的 test_design_item_ids 必须是数组")
    module_names = {_required_text(case.get("module"), "module") for case in original_cases}
    if len(module_names) != 1:
        raise ValueError("修复批次必须只包含一个业务模块")
    bindings = validate_case_fact_bindings(
        test_cases=repaired_cases,
        raw_bindings=output.get("case_fact_bindings"),
        authoritative_facts=source_input.get("authoritative_facts"),
        expected_module_name=next(iter(module_names)),
    )
    covered = {fact_id for binding in bindings for fact_id in _binding_fact_ids(binding)}
    required = {str(value) for value in list(source_input.get("required_fact_ids") or [])}
    facts_by_id = {
        str(fact.get("fact_id") or ""): dict(fact)
        for fact in list(source_input.get("authoritative_facts") or [])
    }
    covered_keys = {
        _fact_coverage_key(facts_by_id[fact_id])
        for fact_id in covered
        if fact_id in facts_by_id
    }
    semantic_duplicate_groups: list[set[str]] = []
    for difference in list(
        dict(source_input.get("review_result") or {}).get("differences") or []
    ):
        if not isinstance(difference, dict):
            continue
        if str(difference.get("category") or "") != "semantic_duplicate":
            continue
        group = {
            str(fact_id)
            for fact_id in list(difference.get("related_fact_ids") or [])
            if str(fact_id) in required and str(fact_id) in facts_by_id
        }
        scope_ids = {
            str(facts_by_id[fact_id].get("scope_id") or "")
            for fact_id in group
        }
        if len(group) >= 2 and len(scope_ids) == 1 and "" not in scope_ids:
            semantic_duplicate_groups.append(group)

    def is_required_fact_covered(fact_id: str) -> bool:
        fact = facts_by_id.get(fact_id)
        if fact is None:
            return False
        if _fact_coverage_key(fact) in covered_keys:
            return True
        return any(
            fact_id in group
            and any(
                _fact_coverage_key(facts_by_id[peer_id]) in covered_keys
                for peer_id in group
            )
            for group in semantic_duplicate_groups
        )

    missing = sorted(
        fact_id
        for fact_id in required
        if not is_required_fact_covered(fact_id)
    )
    if missing:
        missing_details = "；".join(
            f"{fact_id}={str(facts_by_id[fact_id].get('assertion') or '').strip()}"
            for fact_id in missing
            if fact_id in facts_by_id
        )
        detail_suffix = f"；事实断言: {missing_details}" if missing_details else ""
        raise ValueError(f"修复批次仍未覆盖要求事实: {missing}{detail_suffix}")
    required_design_item_ids = {
        _required_text(item.get("test_design_item_id"), "test_design_item_id")
        for item in list(source_input.get("test_design_items") or [])
    }
    covered_design_item_ids = {
        str(design_item_id)
        for test_case in repaired_cases
        for design_item_id in list(test_case.get("test_design_item_ids") or [])
    }
    missing_design_item_ids = sorted(
        required_design_item_ids - covered_design_item_ids
    )
    invalid_design_item_ids = sorted(
        covered_design_item_ids - required_design_item_ids
    )
    if missing_design_item_ids or invalid_design_item_ids:
        raise ValueError(
            "修复批次测试设计覆盖不符合平台契约: "
            f"missing={missing_design_item_ids}, invalid={invalid_design_item_ids}"
        )
    return {"test_cases": repaired_cases, "case_fact_bindings": bindings}


def merge_final_review_repairs(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    generation = dict(arguments.get("generation") or {})
    test_cases = [dict(item) for item in list(generation.get("test_cases") or [])]
    bindings = [dict(item) for item in list(generation.get("case_fact_bindings") or [])]
    repair_inputs = _required_list(arguments.get("repair_inputs"), "repair_inputs")
    repair_records = _required_list(arguments.get("repair_records"), "repair_records")
    if len(repair_inputs) != len(repair_records):
        raise ValueError("修复输入与结果数量不一致")
    cases_by_id = {_required_text(case.get("case_id"), "case_id"): case for case in test_cases}
    bindings_by_id = binding_index(bindings)
    repaired_ids: set[str] = set()
    noop_case_ids: set[str] = set()
    for item_index, raw_input in enumerate(repair_inputs):
        source_input = dict(raw_input)
        record = repair_records[item_index]
        if not isinstance(record, dict) or int(record.get("item_index", -1)) != item_index:
            raise ValueError(f"修复结果顺序错误: item_index={item_index}")
        raw_record_output = dict(record.get("output") or {})
        normalized = _validate_final_review_repair_output(
            source_input=source_input,
            output=raw_record_output,
        )
        if raw_record_output.get("review_noop") is True:
            noop_case_ids.update(
                str(value) for value in list(source_input.get("target_case_ids") or [])
            )
        normalized_bindings = binding_index(normalized["case_fact_bindings"])
        for case in normalized["test_cases"]:
            case_id = _required_text(case.get("case_id"), "case_id")
            if case_id not in cases_by_id or case_id in repaired_ids:
                raise ValueError(f"修复结果引用未知或重复用例: case_id={case_id}")
            cases_by_id[case_id] = dict(case)
            bindings_by_id[case_id] = dict(normalized_bindings[case_id])
            repaired_ids.add(case_id)
    ordered_ids = [_required_text(case.get("case_id"), "case_id") for case in test_cases]
    result = {
        "test_cases": [cases_by_id[case_id] for case_id in ordered_ids],
        "case_fact_bindings": [bindings_by_id[case_id] for case_id in ordered_ids],
        "batch_count": int(generation.get("batch_count") or 0),
        "case_count": len(ordered_ids),
    }
    merge_summary = {
        "repaired_case_ids": sorted(repaired_ids),
        "noop_case_ids": sorted(noop_case_ids),
        "preserved_case_count": len(ordered_ids) - len(repaired_ids),
    }
    cycles = list(context.artifacts.get("final_review_repair_merge_cycles") or [])
    cycles.append({"cycle_number": len(cycles) + 1, **merge_summary})
    context.artifacts["final_review_repair_merge_cycles"] = cycles
    context.artifacts["final_review_repair_merge"] = merge_summary
    return result


def prepare_final_review_rechecks(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    repair_inputs = _required_list(arguments.get("repair_inputs"), "repair_inputs")
    generation = dict(arguments.get("generation") or {})
    cases_by_id = {
        _required_text(dict(case).get("case_id"), "case_id"): dict(case)
        for case in list(generation.get("test_cases") or [])
    }
    bindings_by_id = binding_index(generation.get("case_fact_bindings"))
    items: list[dict[str, Any]] = []
    for raw_input in repair_inputs:
        repair_input = dict(raw_input)
        batch = dict(repair_input.get("review_batch") or {})
        case_ids = [str(value) for value in list(batch.get("case_ids") or [])]
        # 增量复审必须沿用当前修复任务的事实边界，不能重新引入原始大批次。
        authoritative_facts = _deduplicate_facts(
            list(repair_input.get("authoritative_facts") or [])
        )
        test_design_items = _deduplicate_design_items(
            list(repair_input.get("test_design_items") or [])
        )
        item = {
            "review_batch": batch,
            "test_cases": [deepcopy(cases_by_id[case_id]) for case_id in case_ids],
            "case_fact_bindings": [deepcopy(bindings_by_id[case_id]) for case_id in case_ids],
            "authoritative_facts": authoritative_facts,
            "test_design_items": test_design_items,
            "audit_summary": {},
        }
        item["audit_summary"] = _batch_audit(
            test_cases=item["test_cases"],
            bindings=item["case_fact_bindings"],
            facts=item["authoritative_facts"],
            test_design_items=item["test_design_items"],
        )
        item["review_facts"] = _review_facts(
            [dict(fact) for fact in list(item.pop("authoritative_facts"))]
        )
        items.append(item)
    return {"items": items, "recheck_batch_count": len(items)}


def merge_final_review_recheck_records(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """按批次覆盖复审结果，使后续修复只替换发生变化的批次。"""

    baseline_inputs = _required_list(arguments.get("baseline_inputs"), "baseline_inputs")
    baseline_records = _required_list(arguments.get("baseline_records"), "baseline_records")
    replacement_inputs = _required_list(
        arguments.get("replacement_inputs"),
        "replacement_inputs",
    )
    replacement_records = _required_list(
        arguments.get("replacement_records"),
        "replacement_records",
    )
    if len(baseline_inputs) != len(baseline_records):
        raise ValueError("基线复审输入与结果数量不一致")
    if len(replacement_inputs) != len(replacement_records):
        raise ValueError("替换复审输入与结果数量不一致")

    baseline_batch_ids = [
        _required_text(
            dict(dict(item).get("review_batch") or {}).get("batch_id"),
            "baseline.review_batch.batch_id",
        )
        for item in baseline_inputs
    ]
    if len(set(baseline_batch_ids)) != len(baseline_batch_ids):
        raise ValueError("基线复审输入包含重复batch_id")
    baseline_batch_id_set = set(baseline_batch_ids)

    baseline_case_ids_by_batch: dict[str, set[str]] = {}
    baseline_batch_by_case_id: dict[str, str] = {}
    for batch_id, raw_input in zip(
        baseline_batch_ids,
        baseline_inputs,
        strict=True,
    ):
        case_ids = {
            _required_text(dict(case).get("case_id"), "case_id")
            for case in list(dict(raw_input).get("test_cases") or [])
        }
        baseline_case_ids_by_batch[batch_id] = case_ids
        for case_id in case_ids:
            if case_id in baseline_batch_by_case_id:
                raise ValueError(f"基线复审输入包含跨批重复用例: case_id={case_id}")
            baseline_batch_by_case_id[case_id] = batch_id

    replacement_by_batch_id: dict[str, dict[str, Any]] = {}
    replacement_children_by_batch_id: dict[
        str,
        list[tuple[set[str], dict[str, Any]]],
    ] = {}
    replaced_case_ids: set[str] = set()
    for index, raw_input in enumerate(replacement_inputs):
        source_input = dict(raw_input)
        batch_id = _required_text(
            dict(source_input.get("review_batch") or {}).get("batch_id"),
            "replacement.review_batch.batch_id",
        )
        record = replacement_records[index]
        if not isinstance(record, dict) or int(record.get("item_index", -1)) != index:
            raise ValueError(f"替换复审结果顺序错误: item_index={index}")
        if batch_id in baseline_batch_id_set:
            if batch_id in replacement_by_batch_id:
                raise ValueError(f"替换复审包含重复批次: batch_id={batch_id}")
            if batch_id in replacement_children_by_batch_id:
                raise ValueError(f"替换复审同时包含父批次和子批次: batch_id={batch_id}")
            replacement_by_batch_id[batch_id] = dict(record)
            continue

        case_ids = {
            _required_text(dict(case).get("case_id"), "case_id")
            for case in list(source_input.get("test_cases") or [])
        }
        if not case_ids:
            raise ValueError(f"替换复审子批次缺少用例: batch_id={batch_id}")
        parent_batch_ids = {
            baseline_batch_by_case_id.get(case_id) for case_id in case_ids
        }
        if None in parent_batch_ids or len(parent_batch_ids) != 1:
            raise ValueError(
                f"替换复审子批次必须完整归属一个基线批次: batch_id={batch_id}"
            )
        duplicate_case_ids = replaced_case_ids & case_ids
        if duplicate_case_ids:
            raise ValueError(
                f"替换复审子批次包含重复用例: case_ids={sorted(duplicate_case_ids)}"
            )
        replaced_case_ids.update(case_ids)
        parent_batch_id = str(next(iter(parent_batch_ids)))
        if parent_batch_id in replacement_by_batch_id:
            raise ValueError(
                f"替换复审同时包含父批次和子批次: batch_id={parent_batch_id}"
            )
        replacement_children_by_batch_id.setdefault(parent_batch_id, []).append(
            (case_ids, dict(record))
        )

    items: list[dict[str, Any]] = []
    for index, batch_id in enumerate(baseline_batch_ids):
        baseline_record = baseline_records[index]
        if not isinstance(baseline_record, dict) or int(
            baseline_record.get("item_index", -1)
        ) != index:
            raise ValueError(f"基线复审结果顺序错误: item_index={index}")
        baseline_output = baseline_record.get("output")
        if not isinstance(baseline_output, dict):
            raise ValueError(f"复审结果缺少output: batch_id={batch_id}")
        selected = replacement_by_batch_id.get(batch_id)
        child_records = replacement_children_by_batch_id.get(batch_id, [])
        if selected is not None:
            output = selected.get("output")
            if not isinstance(output, dict):
                raise ValueError(f"复审结果缺少output: batch_id={batch_id}")
        elif child_records:
            child_case_ids = {
                case_id
                for case_ids, _record in child_records
                for case_id in case_ids
            }
            baseline_differences = [
                dict(difference)
                for difference in list(baseline_output.get("differences") or [])
            ]
            remaining_differences = [
                difference
                for difference in baseline_differences
                if str(difference.get("case_id") or "") not in child_case_ids
            ]
            child_failed = False
            for _case_ids, child_record in child_records:
                child_output = child_record.get("output")
                if not isinstance(child_output, dict):
                    raise ValueError(f"复审结果缺少output: batch_id={batch_id}")
                child_failed = child_failed or child_output.get("approved") is not True
                remaining_differences.extend(
                    deepcopy(list(child_output.get("differences") or []))
                )
            unresolved_baseline_failure = (
                baseline_output.get("approved") is not True
                and (
                    not baseline_differences
                    or any(
                        not difference.get("case_id")
                        or str(difference.get("case_id")) not in child_case_ids
                        for difference in baseline_differences
                    )
                )
            )
            output = {
                "phase": "final_review",
                "approved": (
                    not unresolved_baseline_failure
                    and not child_failed
                    and not remaining_differences
                ),
                "summary": (
                    f"批次 {batch_id} 已按 {len(child_case_ids)} 条子批次用例复审结果更新。"
                ),
                "differences": remaining_differences,
            }
        else:
            selected = baseline_record
            output = baseline_output
        item = {
            "item_index": index,
            "output": deepcopy(output),
        }
        input_hash = str((selected or {}).get("input_hash") or "").strip()
        if input_hash:
            item["input_hash"] = input_hash
        items.append(item)

    result = {
        "items": items,
        "baseline_count": len(items),
        "replaced_count": (
            len(replacement_by_batch_id) + len(replacement_children_by_batch_id)
        ),
    }
    context.artifacts["final_review_recheck_merge"] = {
        "baseline_count": result["baseline_count"],
        "replaced_count": result["replaced_count"],
    }
    return result


def merge_final_review_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    review_inputs = _required_list(arguments.get("review_inputs"), "review_inputs")
    review_records = _required_list(arguments.get("review_records"), "review_records")
    repair_inputs = _required_list(arguments.get("repair_inputs"), "repair_inputs")
    recheck_inputs = _required_list(arguments.get("recheck_inputs"), "recheck_inputs")
    recheck_records = _required_list(arguments.get("recheck_records"), "recheck_records")
    if len(review_inputs) != len(review_records):
        raise ValueError("初审输入与结果数量不一致")
    if len(recheck_inputs) != len(recheck_records) or len(repair_inputs) != len(recheck_inputs):
        raise ValueError("修复批次与复审结果数量不一致")
    results_by_batch_id: dict[str, dict[str, Any]] = {}
    parent_batch_by_case_id: dict[str, str] = {}
    for index, raw_input in enumerate(review_inputs):
        source_input = dict(raw_input)
        batch_id = _required_text(dict(source_input.get("review_batch") or {}).get("batch_id"), "batch_id")
        if batch_id in results_by_batch_id:
            raise ValueError(f"初审输入包含重复batch_id: batch_id={batch_id}")
        case_ids = {
            _required_text(dict(case).get("case_id"), "case_id")
            for case in list(source_input.get("test_cases") or [])
        }
        if not case_ids:
            raise ValueError(f"初审输入缺少用例: batch_id={batch_id}")
        for case_id in case_ids:
            if case_id in parent_batch_by_case_id:
                raise ValueError(f"初审输入包含跨批重复用例: case_id={case_id}")
            parent_batch_by_case_id[case_id] = batch_id
        results_by_batch_id[batch_id] = _map_output(
            item_index=index,
            source_input=source_input,
            records=review_records,
            context=context,
        )

    rechecks_by_parent: dict[str, list[tuple[set[str], dict[str, Any]]]] = {}
    rechecked_case_ids: set[str] = set()
    for index, raw_input in enumerate(recheck_inputs):
        source_input = dict(raw_input)
        child_batch_id = _required_text(
            dict(source_input.get("review_batch") or {}).get("batch_id"),
            "batch_id",
        )
        case_ids = {
            _required_text(dict(case).get("case_id"), "case_id")
            for case in list(source_input.get("test_cases") or [])
        }
        if not case_ids:
            raise ValueError(f"复审输入缺少用例: batch_id={child_batch_id}")
        parent_batch_ids = {
            parent_batch_by_case_id.get(case_id) for case_id in case_ids
        }
        if None in parent_batch_ids or len(parent_batch_ids) != 1:
            raise ValueError(
                f"复审子批次必须完整归属一个初审批次: batch_id={child_batch_id}"
            )
        duplicate_case_ids = rechecked_case_ids & case_ids
        if duplicate_case_ids:
            raise ValueError(
                f"复审输入包含重复用例: case_ids={sorted(duplicate_case_ids)}"
            )
        rechecked_case_ids.update(case_ids)
        parent_batch_id = next(iter(parent_batch_ids))
        review = _map_output(
            item_index=index,
            source_input=source_input,
            records=recheck_records,
            context=context,
        )
        rechecks_by_parent.setdefault(str(parent_batch_id), []).append(
            (case_ids, review)
        )

    for batch_id, child_reviews in rechecks_by_parent.items():
        initial_review = results_by_batch_id[batch_id]
        replaced_case_ids = {
            case_id
            for case_ids, _review in child_reviews
            for case_id in case_ids
        }
        initial_differences = [
            dict(difference)
            for difference in list(initial_review.get("differences") or [])
        ]
        remaining_differences = [
            difference
            for difference in initial_differences
            if str(difference.get("case_id") or "") not in replaced_case_ids
        ]
        for _case_ids, child_review in child_reviews:
            remaining_differences.extend(
                deepcopy(list(child_review.get("differences") or []))
            )
        unresolved_initial_failure = initial_review.get("approved") is not True and (
            not initial_differences
            or any(
                not difference.get("case_id")
                or str(difference.get("case_id")) not in replaced_case_ids
                for difference in initial_differences
            )
        )
        child_failed = any(
            child_review.get("approved") is not True
            for _case_ids, child_review in child_reviews
        )
        results_by_batch_id[batch_id] = {
            "phase": "final_review",
            "approved": not unresolved_initial_failure
            and not child_failed
            and not remaining_differences,
            "summary": (
                f"批次 {batch_id} 已按 {len(replaced_case_ids)} 条用例复审结果更新。"
            ),
            "differences": remaining_differences,
        }
    audit_summary = dict(arguments.get("audit_summary") or {})
    differences: list[dict[str, Any]] = []
    for batch_id in sorted(results_by_batch_id):
        review = results_by_batch_id[batch_id]
        differences.extend(deepcopy(review.get("differences") or []))
    if audit_summary.get("approved") is not True:
        audit_repair = "；".join(
            str(value) for value in list(audit_summary.get("differences") or [])
        ) or "修复确定性审计指出的覆盖或引用问题"
        differences.append(
            {
                "case_id": None,
                "category": "deterministic_audit",
                "field_path": None,
                "detail": str(audit_summary.get("summary") or "确定性审计未通过"),
                "related_fact_ids": [
                    str(value) for value in list(audit_summary.get("uncovered_fact_ids") or [])[:100]
                ],
                "repair_instruction": audit_repair,
            }
        )
    approved = audit_summary.get("approved") is True and all(
        review.get("approved") is True for review in results_by_batch_id.values()
    )
    result = {
        "phase": "final_review",
        "approved": approved,
        "summary": (
            f"分批终审{'通过' if approved else '未通过'}："
            f"{len(results_by_batch_id)} 个批次，修复并复审 {len(recheck_inputs)} 个批次。"
        ),
        "differences": differences,
    }
    context.artifacts["final_review_batch_summary"] = result
    return result


def prepare_global_final_review(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    generation = dict(arguments.get("generation") or {})
    batch_review = dict(arguments.get("batch_review") or {})
    audit_summary = dict(arguments.get("audit_summary") or {})
    case_index: list[dict[str, Any]] = []
    for raw_case in list(generation.get("test_cases") or []):
        case = dict(raw_case)
        steps = [dict(step) for step in list(case.get("steps") or [])]
        case_index.append(
            {
                "case_id": _required_text(case.get("case_id"), "case_id"),
                "title": _required_text(case.get("title"), "title"),
                "module": _required_text(case.get("module"), "module"),
                "priority": _required_text(case.get("priority"), "priority"),
                "first_action": str((steps[0] if steps else {}).get("action") or ""),
                "last_expected": str((steps[-1] if steps else {}).get("expected") or ""),
            }
        )
    return {
        "case_index": case_index,
        "batch_review": batch_review,
        "audit_summary": audit_summary,
    }


def postprocess_global_final_review_output(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    input_payload = dict(arguments.get("input_payload") or {})
    output = dict(arguments.get("output") or {})
    if output.get("phase") not in {None, "final_review"} or not isinstance(
        output.get("approved"), bool
    ):
        raise ValueError("全局终审阶段或通过状态无效")
    case_ids = {
        _required_text(dict(item).get("case_id"), "case_id")
        for item in list(input_payload.get("case_index") or [])
    }
    differences = _required_list(output.get("differences"), "differences")
    batch_difference_keys = {
        _difference_identity(dict(difference))
        for difference in list(
            dict(input_payload.get("batch_review") or {}).get("differences") or []
        )
        if isinstance(difference, dict)
    }
    normalized_differences: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_difference in differences:
        difference = dict(raw_difference)
        case_id = _required_text(difference.get("case_id"), "difference.case_id")
        if case_id not in case_ids:
            raise ValueError(f"全局终审引用未知用例: case_id={case_id}")
        difference["case_id"] = case_id
        if list(difference.get("related_fact_ids") or []):
            raise ValueError("全局终审未读取事实正文，不得输出related_fact_ids")
        identity = _difference_identity(difference)
        if identity in batch_difference_keys or identity in seen:
            continue
        field_path = difference.get("field_path")
        if field_path is not None:
            field_path = _required_text(field_path, "difference.field_path")
            if field_path not in GLOBAL_REVIEW_VISIBLE_FIELD_PATHS:
                raise ValueError(
                    "全局终审引用了输入中不可见的字段: "
                    f"field_path={field_path}"
                )
        difference["field_path"] = field_path
        _required_text(
            difference.get("repair_instruction"),
            "difference.repair_instruction",
        )
        seen.add(identity)
        normalized_differences.append(difference)
    if output["approved"] is True and differences:
        raise ValueError("全局终审通过时不得返回差异")
    if output["approved"] is False and not differences:
        raise ValueError("全局终审不通过时必须返回差异")
    approved = not normalized_differences
    summary = str(output.get("summary") or "").strip()
    if not summary or approved != output["approved"] or len(normalized_differences) != len(differences):
        summary = (
            "全局终审通过：未发现新增跨批问题。"
            if approved
            else f"全局终审未通过：发现 {len(normalized_differences)} 项新增跨批差异。"
        )
    return {
        **output,
        "phase": "final_review",
        "approved": approved,
        "summary": summary,
        "differences": normalized_differences,
    }


def prepare_terminal_final_review_repairs(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把批次复审和全局审查差异统一路由到最后一轮增量修复。"""

    generation_inputs = _required_list(
        arguments.get("generation_inputs"),
        "generation_inputs",
    )
    generation = dict(arguments.get("generation") or {})
    batch_review = dict(arguments.get("batch_review") or {})
    global_review = dict(arguments.get("global_review") or {})
    for name, review in (("batch_review", batch_review), ("global_review", global_review)):
        if review.get("phase") != "final_review" or not isinstance(review.get("approved"), bool):
            raise ValueError(f"{name}终审结果无效")

    prepared = prepare_final_review_batches(
        context,
        {
            "generation_inputs": generation_inputs,
            "generation": generation,
            "batch_case_limit": arguments.get("batch_case_limit"),
        },
    )
    review_inputs = [dict(item) for item in list(prepared["items"])]
    cases_by_id = {
        _required_text(dict(case).get("case_id"), "case_id"): dict(case)
        for case in list(generation.get("test_cases") or [])
    }
    differences_by_case_id: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    layered_differences = [
        ("batch", raw_difference)
        for raw_difference in list(batch_review.get("differences") or [])
    ] + [
        ("global", raw_difference)
        for raw_difference in list(global_review.get("differences") or [])
    ]
    for origin, raw_difference in layered_differences:
        difference = dict(raw_difference)
        case_id = difference.get("case_id")
        if case_id is None:
            # 确定性覆盖缺口由当前批次审计直接产生修复任务，不重复搬运聚合层描述。
            if difference.get("category") == "deterministic_audit":
                continue
            raise ValueError("终审差异缺少可路由的case_id")
        case_id = _required_text(case_id, "difference.case_id")
        case = cases_by_id.get(case_id)
        if case is None:
            raise ValueError(f"终审差异引用未知用例: case_id={case_id}")
        identity = _difference_identity(difference)
        if identity in seen:
            continue
        seen.add(identity)
        if origin == "global":
            field_path = difference.get("field_path")
            steps = [dict(step) for step in list(case.get("steps") or [])]
            if field_path == "first_action" and steps:
                difference["field_path"] = "steps[0].action"
            elif field_path == "last_expected" and steps:
                difference["field_path"] = f"steps[{len(steps) - 1}].expected"
            if difference.get("category") == "semantic_duplicate":
                difference["repair_scope"] = "cohort"
        differences_by_case_id.setdefault(case_id, []).append(difference)

    routed_case_ids: set[str] = set()
    review_records: list[dict[str, Any]] = []
    for item_index, review_input in enumerate(review_inputs):
        item_case_ids = {
            _required_text(dict(case).get("case_id"), "case_id")
            for case in list(review_input.get("test_cases") or [])
        }
        item_differences = [
            deepcopy(difference)
            for case_id in item_case_ids
            for difference in differences_by_case_id.get(case_id, [])
        ]
        routed_case_ids.update(
            case_id for case_id in item_case_ids if case_id in differences_by_case_id
        )
        review_records.append(
            {
                "item_index": item_index,
                "output": {
                    "phase": "final_review",
                    "approved": not item_differences,
                    "summary": (
                        "统一终审通过。"
                        if not item_differences
                        else f"统一终审发现 {len(item_differences)} 项待修差异。"
                    ),
                    "differences": item_differences,
                },
            }
        )
    unrouted_case_ids = set(differences_by_case_id) - routed_case_ids
    if unrouted_case_ids:
        raise ValueError(f"终审差异无法映射到修复批次: case_ids={sorted(unrouted_case_ids)}")

    repairs = prepare_final_review_repairs(
        context,
        {
            "review_inputs": review_inputs,
            "review_records": review_records,
            "generation_inputs": generation_inputs,
        },
    )
    return {
        **repairs,
        "review_inputs": review_inputs,
        "review_records": review_records,
    }
