from __future__ import annotations

import unicodedata
from typing import Any


def index_effective_facts(
    raw_facts: Any,
    *,
    field_name: str = "authoritative_facts",
) -> dict[str, dict[str, Any]]:
    """校验并索引当前模块批次可使用的生效事实。"""

    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError(f"{field_name}必须是非空数组")
    indexed: dict[str, dict[str, Any]] = {}
    for raw_fact in raw_facts:
        if not isinstance(raw_fact, dict):
            raise ValueError(f"{field_name}每项必须是对象")
        fact = dict(raw_fact)
        fact_id = str(fact.get("fact_id") or "").strip()
        assertion = str(fact.get("assertion") or "").strip()
        status = str(fact.get("status") or "").strip()
        source_anchor = fact.get("source_anchor")
        value_policy = str(fact.get("value_policy") or "").strip()
        governed_values = fact.get("governed_values")
        if not fact_id or fact_id in indexed:
            raise ValueError(f"{field_name}包含空或重复 fact_id: {fact_id}")
        if not assertion:
            raise ValueError(f"权威事实缺少 assertion: fact_id={fact_id}")
        if status != "effective":
            raise ValueError(
                f"当前模块批次包含非生效事实: fact_id={fact_id}, status={status}"
            )
        if not isinstance(source_anchor, dict) or not source_anchor:
            raise ValueError(f"权威事实缺少 source_anchor: fact_id={fact_id}")
        if value_policy not in {"exact", "runtime_configured"}:
            raise ValueError(
                f"权威事实 value_policy 无效: fact_id={fact_id}, value={value_policy}"
            )
        if not isinstance(governed_values, list) or any(
            not str(value or "").strip() for value in governed_values
        ):
            raise ValueError(f"权威事实 governed_values 必须是非空字符串数组: fact_id={fact_id}")
        normalized_values = [str(value).strip() for value in governed_values]
        if len(normalized_values) != len(set(normalized_values)):
            raise ValueError(f"权威事实 governed_values 包含重复值: fact_id={fact_id}")
        if value_policy == "exact" and normalized_values:
            raise ValueError(f"exact 事实不得声明 governed_values: fact_id={fact_id}")
        fact["governed_values"] = normalized_values
        indexed[fact_id] = fact
    return indexed


def validate_case_fact_bindings(
    *,
    test_cases: list[Any],
    raw_bindings: Any,
    authoritative_facts: Any,
    expected_module_name: str,
    field_name: str = "case_fact_bindings",
) -> list[dict[str, Any]]:
    """要求每个业务字段都显式绑定当前模块的生效事实。"""

    facts_by_id = index_effective_facts(authoritative_facts)
    if not isinstance(raw_bindings, list):
        raise ValueError(f"{field_name}必须是数组")
    bindings_by_case_id: dict[str, dict[str, Any]] = {}
    for raw_binding in raw_bindings:
        if not isinstance(raw_binding, dict):
            raise ValueError(f"{field_name}每项必须是对象")
        binding = dict(raw_binding)
        case_id = str(binding.get("case_id") or "").strip()
        if not case_id or case_id in bindings_by_case_id:
            raise ValueError(f"{field_name}包含空或重复 case_id: {case_id}")
        bindings_by_case_id[case_id] = binding

    normalized: list[dict[str, Any]] = []
    expected_case_ids: set[str] = set()
    for raw_case in test_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("test_cases 每项必须是对象")
        case = dict(raw_case)
        case_id = str(case.get("case_id") or "").strip()
        module_name = str(case.get("module") or "").strip()
        if not case_id or case_id in expected_case_ids:
            raise ValueError(f"test_cases 包含空或重复 case_id: {case_id}")
        if module_name != expected_module_name:
            raise ValueError(
                "事实绑定用例跨越当前模块: "
                f"case_id={case_id}, module={module_name}, expected={expected_module_name}"
            )
        expected_case_ids.add(case_id)
        binding = bindings_by_case_id.get(case_id)
        if binding is None:
            raise ValueError(f"用例缺少事实绑定: case_id={case_id}")

        preconditions = list(case.get("preconditions") or [])
        precondition_bindings = _indexed_bindings(
            binding.get("precondition_bindings"),
            index_field="precondition_index",
            expected_count=len(preconditions),
            field_name=f"{case_id}.precondition_bindings",
        )
        normalized_preconditions = [
            {
                "precondition_index": index,
                "fact_ids": _validate_fact_ids(
                    precondition_bindings[index].get("fact_ids"),
                    facts_by_id=facts_by_id,
                    field_name=f"{case_id}.preconditions[{index}]",
                    claim_text=preconditions[index],
                ),
            }
            for index in range(len(preconditions))
        ]

        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"用例步骤必须是非空数组: case_id={case_id}")
        step_bindings = _indexed_bindings(
            binding.get("step_bindings"),
            index_field="step_index",
            expected_count=len(steps),
            field_name=f"{case_id}.step_bindings",
        )
        normalized_steps: list[dict[str, Any]] = []
        for step_index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                raise ValueError(f"用例步骤必须是对象: case_id={case_id}, step={step_index}")
            step_binding = step_bindings[step_index]
            normalized_steps.append(
                {
                    "step_index": step_index,
                    "action_fact_ids": _validate_fact_ids(
                        step_binding.get("action_fact_ids"),
                        facts_by_id=facts_by_id,
                        field_name=f"{case_id}.steps[{step_index}].action",
                        claim_text=raw_step.get("action"),
                    ),
                    "expected_fact_ids": _validate_fact_ids(
                        step_binding.get("expected_fact_ids"),
                        facts_by_id=facts_by_id,
                        field_name=f"{case_id}.steps[{step_index}].expected",
                        claim_text=raw_step.get("expected"),
                    ),
                }
            )
        normalized.append(
            {
                "case_id": case_id,
                "precondition_bindings": normalized_preconditions,
                "step_bindings": normalized_steps,
            }
        )

    extra_case_ids = set(bindings_by_case_id) - expected_case_ids
    if extra_case_ids:
        raise ValueError(f"事实绑定引用未知用例: {sorted(extra_case_ids)}")
    return normalized


def binding_index(raw_bindings: Any) -> dict[str, dict[str, Any]]:
    """索引已经过平台校验的用例事实绑定。"""

    if not isinstance(raw_bindings, list):
        raise ValueError("case_fact_bindings必须是数组")
    indexed: dict[str, dict[str, Any]] = {}
    for raw_binding in raw_bindings:
        if not isinstance(raw_binding, dict):
            raise ValueError("case_fact_bindings每项必须是对象")
        binding = dict(raw_binding)
        case_id = str(binding.get("case_id") or "").strip()
        if not case_id or case_id in indexed:
            raise ValueError(f"case_fact_bindings包含空或重复 case_id: {case_id}")
        indexed[case_id] = binding
    return indexed


def replace_binding_case_id(binding: dict[str, Any], *, case_id: str) -> dict[str, Any]:
    """用例重编号时仅替换绑定主键，不改事实引用。"""

    return {**dict(binding), "case_id": case_id}


def _indexed_bindings(
    raw_bindings: Any,
    *,
    index_field: str,
    expected_count: int,
    field_name: str,
) -> dict[int, dict[str, Any]]:
    if not isinstance(raw_bindings, list):
        raise ValueError(f"{field_name}必须是数组")
    indexed: dict[int, dict[str, Any]] = {}
    for raw_binding in raw_bindings:
        if not isinstance(raw_binding, dict):
            raise ValueError(f"{field_name}每项必须是对象")
        binding = dict(raw_binding)
        try:
            item_index = int(binding.get(index_field, -1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}包含无效索引") from exc
        if item_index < 0 or item_index >= expected_count or item_index in indexed:
            raise ValueError(f"{field_name}包含越界或重复索引: {item_index}")
        indexed[item_index] = binding
    expected_indexes = set(range(expected_count))
    if set(indexed) != expected_indexes:
        raise ValueError(
            f"{field_name}未完整覆盖字段索引: "
            f"missing={sorted(expected_indexes - set(indexed))}"
        )
    return indexed


def _validate_fact_ids(
    raw_fact_ids: Any,
    *,
    facts_by_id: dict[str, dict[str, Any]],
    field_name: str,
    claim_text: Any,
) -> list[str]:
    if not isinstance(raw_fact_ids, list) or not raw_fact_ids:
        raise ValueError(f"{field_name}必须绑定至少一个生效事实")
    fact_ids = [str(value or "").strip() for value in raw_fact_ids]
    if any(not fact_id for fact_id in fact_ids) or len(fact_ids) != len(set(fact_ids)):
        raise ValueError(f"{field_name}包含空或重复 fact_id")
    unknown = [fact_id for fact_id in fact_ids if fact_id not in facts_by_id]
    if unknown:
        raise ValueError(f"{field_name}引用非当前模块生效事实: {unknown}")
    claim_identity = _claim_identity(claim_text)
    for fact_id in fact_ids:
        fact = facts_by_id[fact_id]
        if str(fact.get("value_policy") or "") != "runtime_configured":
            continue
        fixed_values = [
            str(value)
            for value in fact.get("governed_values") or []
            if _claim_identity(value) and _claim_identity(value) in claim_identity
        ]
        if fixed_values:
            raise ValueError(
                f"{field_name}固化了运行时配置值: fact_id={fact_id}, values={fixed_values}"
            )
    return fact_ids


def _claim_identity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())
