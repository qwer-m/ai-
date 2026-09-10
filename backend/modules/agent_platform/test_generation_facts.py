from __future__ import annotations

from typing import Any


def test_input_text(value: Any) -> str:
    """测试输入必须显式提供；空字符串表示不需要额外输入。"""

    if not isinstance(value, str):
        raise ValueError("test_input必须是字符串，无需额外输入时使用空字符串")
    return value.strip()


def bound_fact_ids(binding: dict[str, Any]) -> set[str]:
    """统一读取用例各业务字段绑定的事实，供路由、覆盖审计和终审使用。"""

    groups = [binding.get("test_input_fact_ids") or []]
    groups.extend(item.get("fact_ids") or [] for item in binding.get("precondition_bindings") or [])
    for step in binding.get("step_bindings") or []:
        groups.extend([step.get("action_fact_ids") or [], step.get("expected_fact_ids") or []])
    return {str(fact_id).strip() for group in groups for fact_id in group}


def materialize_inline_grounding(
    *,
    raw_cases: Any,
    case_ids: list[str],
    module_name: str,
    fallback_tags_by_case_id: dict[str, list[str]] | None = None,
    allow_missing_design_item_ids: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """把模型内联的事实引用确定性拆分为用例与绑定数组。"""

    if not isinstance(raw_cases, list):
        raise ValueError("test_cases必须是数组")
    if len(raw_cases) != len(case_ids):
        raise ValueError(
            "模型用例数量与平台编号契约不一致: "
            f"target={len(case_ids)}, actual={len(raw_cases)}"
        )
    if not module_name.strip():
        raise ValueError("业务模块名称不能为空")
    if any(not str(case_id).strip() for case_id in case_ids) or len(case_ids) != len(
        set(case_ids)
    ):
        raise ValueError("平台用例编号包含空值或重复值")

    fallback_tags = fallback_tags_by_case_id or {}
    test_cases: list[dict[str, Any]] = []
    case_fact_bindings: list[dict[str, Any]] = []
    for case_index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError("test_cases每项必须是对象")
        case_id = str(case_ids[case_index]).strip()
        preconditions = raw_case.get("preconditions")
        if not isinstance(preconditions, list):
            raise ValueError(f"{case_id}.preconditions必须是数组")
        raw_test_input = raw_case.get("test_input")
        if not isinstance(raw_test_input, dict):
            raise ValueError(f"{case_id}.test_input必须是对象")
        test_input = test_input_text(raw_test_input.get("text"))
        steps = raw_case.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"{case_id}.steps必须是非空数组")

        normalized_preconditions: list[str] = []
        precondition_bindings: list[dict[str, Any]] = []
        for precondition_index, raw_precondition in enumerate(preconditions):
            if not isinstance(raw_precondition, dict):
                raise ValueError(f"{case_id}.preconditions每项必须是对象")
            text = str(raw_precondition.get("text") or "").strip()
            if not text:
                raise ValueError(
                    f"{case_id}.preconditions[{precondition_index}].text不能为空"
                )
            normalized_preconditions.append(text)
            precondition_bindings.append(
                {
                    "precondition_index": precondition_index,
                    "fact_ids": raw_precondition.get("fact_ids"),
                }
            )

        normalized_steps: list[dict[str, str]] = []
        step_bindings: list[dict[str, Any]] = []
        for step_index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                raise ValueError(f"{case_id}.steps每项必须是对象")
            action = str(raw_step.get("action") or "").strip()
            expected = str(raw_step.get("expected") or "").strip()
            if not action or not expected:
                raise ValueError(
                    f"{case_id}.steps[{step_index}]的action和expected不能为空"
                )
            fact_bindings = raw_step.get("fact_bindings")
            if not isinstance(fact_bindings, dict):
                raise ValueError(
                    f"{case_id}.steps[{step_index}].fact_bindings必须是对象"
                )
            normalized_steps.append({"action": action, "expected": expected})
            step_bindings.append(
                {
                    "step_index": step_index,
                    "action_fact_ids": fact_bindings.get("action"),
                    "expected_fact_ids": fact_bindings.get("expected"),
                }
            )

        raw_tags = raw_case.get("tags", fallback_tags.get(case_id, []))
        if not isinstance(raw_tags, list):
            raise ValueError(f"{case_id}.tags必须是数组")
        design_item_ids = raw_case.get("test_design_item_ids")
        if design_item_ids is None and allow_missing_design_item_ids:
            design_item_ids = []
        if not isinstance(design_item_ids, list):
            raise ValueError(f"{case_id}.test_design_item_ids必须是数组")
        test_cases.append(
            {
                "case_id": case_id,
                "title": raw_case.get("title"),
                "module": module_name,
                "priority": raw_case.get("priority"),
                "preconditions": normalized_preconditions,
                "test_input": test_input,
                "steps": normalized_steps,
                "tags": list(raw_tags),
                "test_design_item_ids": list(design_item_ids),
            }
        )
        case_fact_bindings.append(
            {
                "case_id": case_id,
                "precondition_bindings": precondition_bindings,
                "test_input_fact_ids": raw_test_input.get("fact_ids"),
                "step_bindings": step_bindings,
            }
        )
    return {
        "test_cases": test_cases,
        "case_fact_bindings": case_fact_bindings,
    }


def derive_test_design_item_ids(
    *,
    case_fact_bindings: Any,
    fact_design_item_ids: Any,
    required_fact_ids: list[str] | None = None,
    required_design_item_ids: list[str] | None = None,
) -> dict[str, list[str]]:
    """依据每条用例实际绑定的事实确定性派生测试设计项编号。

    设计项编号属于平台路由数据，不应由模型凭空生成。该函数只沿事实到
    设计项的已持久化映射取交集，并按契约顺序输出；映射缺失时直接报错，
    不按用例位置或全量编号猜测。
    """

    if not isinstance(case_fact_bindings, list):
        raise ValueError("事实绑定必须是数组，无法派生测试设计项")
    if not isinstance(fact_design_item_ids, dict):
        raise ValueError("生成批次缺少事实到测试设计项路由")

    normalized_routes: dict[str, list[str]] = {}
    for raw_fact_id, raw_design_ids in fact_design_item_ids.items():
        fact_id = str(raw_fact_id or "").strip()
        if not fact_id:
            raise ValueError("事实到测试设计项路由包含空 fact_id")
        if not isinstance(raw_design_ids, list):
            raise ValueError(f"事实到测试设计项路由必须是数组: fact_id={fact_id}")
        design_ids: list[str] = []
        for raw_design_id in raw_design_ids:
            design_id = str(raw_design_id or "").strip()
            if not design_id:
                raise ValueError(f"事实到测试设计项路由包含空设计项: fact_id={fact_id}")
            if design_id not in design_ids:
                design_ids.append(design_id)
        normalized_routes[fact_id] = design_ids

    normalized_required_fact_ids: list[str] = []
    for raw_fact_id in list(required_fact_ids or []):
        fact_id = str(raw_fact_id or "").strip()
        if not fact_id or fact_id in normalized_required_fact_ids:
            raise ValueError("生成批次 required_fact_ids 包含空值或重复值")
        normalized_required_fact_ids.append(fact_id)
    if normalized_required_fact_ids:
        missing_route_fact_ids = sorted(
            set(normalized_required_fact_ids) - set(normalized_routes)
        )
        invalid_route_fact_ids = sorted(
            set(normalized_routes) - set(normalized_required_fact_ids)
        )
        if missing_route_fact_ids or invalid_route_fact_ids:
            raise ValueError(
                "事实到测试设计项路由与当前批次事实不一致: "
                f"missing={missing_route_fact_ids}, invalid={invalid_route_fact_ids}"
            )

    design_order: list[str] = []
    for raw_design_id in list(required_design_item_ids or []):
        design_id = str(raw_design_id or "").strip()
        if not design_id or design_id in design_order:
            raise ValueError("生成批次 required_test_design_item_ids 包含空值或重复值")
        design_order.append(design_id)
    if not design_order:
        for design_ids in normalized_routes.values():
            for design_id in design_ids:
                if design_id not in design_order:
                    design_order.append(design_id)
    else:
        invalid_route_design_ids = sorted(
            {
                design_id
                for design_ids in normalized_routes.values()
                for design_id in design_ids
                if design_id not in design_order
            }
        )
        if invalid_route_design_ids:
            raise ValueError(
                "事实到测试设计项路由包含当前批次外编号: "
                f"invalid={invalid_route_design_ids}"
            )

    derived: dict[str, list[str]] = {}
    for raw_binding in case_fact_bindings:
        if not isinstance(raw_binding, dict):
            raise ValueError("事实绑定每项必须是对象")
        binding = dict(raw_binding)
        case_id = str(binding.get("case_id") or "").strip()
        if not case_id or case_id in derived:
            raise ValueError(f"事实绑定包含空或重复 case_id: {case_id}")
        fact_ids = bound_fact_ids(binding)
        unknown_fact_ids = sorted(
            fact_id for fact_id in fact_ids if fact_id not in normalized_routes
        )
        if unknown_fact_ids:
            raise ValueError(
                f"{case_id} 绑定了事实路由表外编号: invalid={unknown_fact_ids}"
            )
        fact_set = set(fact_ids)
        derived[case_id] = [
            design_id
            for design_id in design_order
            if any(design_id in normalized_routes.get(fact_id, []) for fact_id in fact_set)
        ]
    return derived


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
    """要求需求业务字段绑定事实，同时保留中性测试动作的可执行性。"""

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
    validation_errors: list[str] = []
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
            validation_errors.append(
                "事实绑定用例跨越当前模块: "
                f"case_id={case_id}, module={module_name}, expected={expected_module_name}"
            )
        expected_case_ids.add(case_id)
        binding = bindings_by_case_id.get(case_id)
        if binding is None:
            validation_errors.append(f"用例缺少事实绑定: case_id={case_id}")
            continue

        case_errors: list[str] = []
        try:
            input_text = test_input_text(case.get("test_input"))
            test_input_fact_ids = _validate_fact_ids(
                binding.get("test_input_fact_ids"),
                facts_by_id=facts_by_id,
                field_name=f"{case_id}.test_input",
                allow_empty=not input_text,
            )
            if not input_text and test_input_fact_ids:
                raise ValueError(f"{case_id}.test_input为空时不得绑定事实")
        except ValueError as exc:
            case_errors.append(str(exc))
            test_input_fact_ids = []
        preconditions = list(case.get("preconditions") or [])
        normalized_preconditions: list[dict[str, Any]] = []
        try:
            precondition_bindings = _indexed_bindings(
                binding.get("precondition_bindings"),
                index_field="precondition_index",
                expected_count=len(preconditions),
                field_name=f"{case_id}.precondition_bindings",
            )
        except ValueError as exc:
            case_errors.append(str(exc))
        else:
            for index in range(len(preconditions)):
                try:
                    fact_ids = _validate_fact_ids(
                        precondition_bindings[index].get("fact_ids"),
                        facts_by_id=facts_by_id,
                        field_name=f"{case_id}.preconditions[{index}]",
                    )
                except ValueError as exc:
                    case_errors.append(str(exc))
                    continue
                normalized_preconditions.append(
                    {
                        "precondition_index": index,
                        "fact_ids": fact_ids,
                    }
                )

        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            validation_errors.extend(case_errors)
            validation_errors.append(f"用例步骤必须是非空数组: case_id={case_id}")
            continue
        normalized_steps: list[dict[str, Any]] = []
        try:
            step_bindings = _indexed_bindings(
                binding.get("step_bindings"),
                index_field="step_index",
                expected_count=len(steps),
                field_name=f"{case_id}.step_bindings",
            )
        except ValueError as exc:
            case_errors.append(str(exc))
        else:
            for step_index, raw_step in enumerate(steps):
                if not isinstance(raw_step, dict):
                    case_errors.append(
                        f"用例步骤必须是对象: case_id={case_id}, step={step_index}"
                    )
                    continue
                step_binding = step_bindings[step_index]
                try:
                    action_fact_ids = _validate_fact_ids(
                        step_binding.get("action_fact_ids"),
                        facts_by_id=facts_by_id,
                        field_name=f"{case_id}.steps[{step_index}].action",
                        allow_empty=True,
                    )
                except ValueError as exc:
                    case_errors.append(str(exc))
                    action_fact_ids = []
                try:
                    expected_fact_ids = _validate_fact_ids(
                        step_binding.get("expected_fact_ids"),
                        facts_by_id=facts_by_id,
                        field_name=f"{case_id}.steps[{step_index}].expected",
                    )
                except ValueError as exc:
                    case_errors.append(str(exc))
                    expected_fact_ids = []
                if expected_fact_ids:
                    normalized_steps.append(
                        {
                            "step_index": step_index,
                            "action_fact_ids": action_fact_ids,
                            "expected_fact_ids": expected_fact_ids,
                        }
                    )
        if case_errors:
            validation_errors.extend(case_errors)
            continue
        normalized.append(
            {
                "case_id": case_id,
                "precondition_bindings": normalized_preconditions,
                "test_input_fact_ids": test_input_fact_ids,
                "step_bindings": normalized_steps,
            }
        )

    extra_case_ids = set(bindings_by_case_id) - expected_case_ids
    if extra_case_ids:
        validation_errors.append(f"事实绑定引用未知用例: {sorted(extra_case_ids)}")
    if validation_errors:
        raise ValueError(
            f"事实绑定校验失败（{len(validation_errors)}项）: "
            + "；".join(validation_errors)
        )
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
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(raw_fact_ids, list):
        raise ValueError(f"{field_name}的事实绑定必须是数组")
    if not raw_fact_ids and not allow_empty:
        raise ValueError(f"{field_name}必须绑定至少一个生效事实")
    fact_ids = [str(value or "").strip() for value in raw_fact_ids]
    if any(not fact_id for fact_id in fact_ids) or len(fact_ids) != len(set(fact_ids)):
        raise ValueError(f"{field_name}包含空或重复 fact_id")
    unknown = [fact_id for fact_id in fact_ids if fact_id not in facts_by_id]
    if unknown:
        raise ValueError(f"{field_name}引用非当前模块生效事实: {unknown}")
    # 动态值按来源事实原样进入用例；平台只校验事实引用，不判断文本是否“固化”。
    return fact_ids
