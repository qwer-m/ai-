from __future__ import annotations

import pytest

from modules.agent_platform.test_generation_facts import (
    derive_test_design_item_ids,
    index_effective_facts,
    materialize_inline_grounding,
    validate_case_fact_bindings,
)


def _fact(
    fact_id: str,
    *,
    status: str = "effective",
    value_policy: str = "exact",
    governed_values: list[str] | None = None,
) -> dict:
    return {
        "fact_id": fact_id,
        "assertion": f"事实{fact_id}",
        "scope_id": "S-P0001-B0001",
        "source_anchor": {
            "source_kind": "document",
            "document_id": 239,
            "page_number": 1,
            "block_id": "P0001-T0001",
            "source_span": {"start": 0, "end": 5},
            "asset_source_sha256": "a" * 64,
            "page_image_sha256": "b" * 64,
        },
        "status": status,
        "value_policy": value_policy,
        "governed_values": list(governed_values or []),
        "governed_by": [],
    }


def _case() -> dict:
    return {
        "case_id": "TC-001",
        "title": "保存内容",
        "module": "内容管理",
        "priority": "P0",
        "preconditions": ["已进入编辑状态"],
        "steps": [
            {"action": "提交内容", "expected": "内容进入已保存状态"},
        ],
        "tags": [],
    }


def _binding(*, fact_id: str = "F-001") -> dict:
    return {
        "case_id": "TC-001",
        "precondition_bindings": [
            {"precondition_index": 0, "fact_ids": [fact_id]},
        ],
        "step_bindings": [
            {
                "step_index": 0,
                "action_fact_ids": [fact_id],
                "expected_fact_ids": [fact_id],
            }
        ],
    }


def test_materialize_inline_grounding_derives_ids_modules_and_indexes() -> None:
    result = materialize_inline_grounding(
        raw_cases=[
            {
                "title": "保存内容",
                "priority": "P0",
                "preconditions": [
                    {"text": "已进入编辑状态", "fact_ids": ["F-001"]}
                ],
                "steps": [
                    {
                        "action": "填写内容",
                        "expected": "内容可编辑",
                        "fact_bindings": {
                            "action": ["F-001"],
                            "expected": ["F-001"],
                        },
                    },
                    {
                        "action": "提交内容",
                        "expected": "内容进入已保存状态",
                        "fact_bindings": {
                            "action": ["F-002"],
                            "expected": ["F-002"],
                        },
                    },
                ],
                "test_design_item_ids": [],
            }
        ],
        case_ids=["TC-004"],
        module_name="内容管理",
        fallback_tags_by_case_id={"TC-004": ["回归"]},
    )

    assert result["test_cases"] == [
        {
            "case_id": "TC-004",
            "title": "保存内容",
            "module": "内容管理",
            "priority": "P0",
            "preconditions": ["已进入编辑状态"],
            "steps": [
                {"action": "填写内容", "expected": "内容可编辑"},
                {"action": "提交内容", "expected": "内容进入已保存状态"},
            ],
            "tags": ["回归"],
            "test_design_item_ids": [],
        }
    ]
    assert result["case_fact_bindings"][0]["precondition_bindings"] == [
        {"precondition_index": 0, "fact_ids": ["F-001"]}
    ]
    assert [
        item["step_index"]
        for item in result["case_fact_bindings"][0]["step_bindings"]
    ] == [0, 1]


def test_derive_test_design_item_ids_uses_bound_fact_routes_in_contract_order() -> None:
    bindings = [
        _binding(fact_id="F-001"),
        {
            **_binding(fact_id="F-002"),
            "case_id": "TC-002",
        },
    ]

    result = derive_test_design_item_ids(
        case_fact_bindings=bindings,
        fact_design_item_ids={
            "F-001": ["TD-002", "TD-001"],
            "F-002": ["TD-003"],
        },
        required_fact_ids=["F-001", "F-002"],
        required_design_item_ids=["TD-001", "TD-002", "TD-003"],
    )

    assert result == {
        "TC-001": ["TD-001", "TD-002"],
        "TC-002": ["TD-003"],
    }


def test_derive_test_design_item_ids_rejects_route_drift() -> None:
    with pytest.raises(ValueError, match="与当前批次事实不一致"):
        derive_test_design_item_ids(
            case_fact_bindings=[_binding()],
            fact_design_item_ids={"F-001": ["TD-001"], "F-EXTRA": []},
            required_fact_ids=["F-001"],
            required_design_item_ids=["TD-001"],
        )


def test_validate_case_fact_bindings_requires_every_business_field() -> None:
    result = validate_case_fact_bindings(
        test_cases=[_case()],
        raw_bindings=[_binding()],
        authoritative_facts=[_fact("F-001")],
        expected_module_name="内容管理",
    )

    assert result == [_binding()]


def test_validate_case_fact_bindings_rejects_cross_module_fact() -> None:
    with pytest.raises(ValueError, match="非当前模块生效事实"):
        validate_case_fact_bindings(
            test_cases=[_case()],
            raw_bindings=[_binding(fact_id="F-OTHER")],
            authoritative_facts=[_fact("F-001")],
            expected_module_name="内容管理",
        )


def test_validate_case_fact_bindings_rejects_unbound_expected() -> None:
    binding = _binding()
    binding["step_bindings"][0]["expected_fact_ids"] = []

    with pytest.raises(ValueError, match="必须绑定至少一个生效事实"):
        validate_case_fact_bindings(
            test_cases=[_case()],
            raw_bindings=[binding],
            authoritative_facts=[_fact("F-001")],
            expected_module_name="内容管理",
        )


def test_validate_case_fact_bindings_allows_neutral_action_without_fact() -> None:
    binding = _binding()
    binding["step_bindings"][0]["action_fact_ids"] = []

    result = validate_case_fact_bindings(
        test_cases=[_case()],
        raw_bindings=[binding],
        authoritative_facts=[_fact("F-001")],
        expected_module_name="内容管理",
    )

    assert result == [binding]


def test_validate_case_fact_bindings_reports_all_missing_indexes_in_batch() -> None:
    first_case = _case()
    first_case["case_id"] = "TC-003"
    first_case["preconditions"].append("当前主题有剩余批改次数")
    first_binding = _binding()
    first_binding["case_id"] = "TC-003"

    second_case = _case()
    second_case["case_id"] = "TC-005"
    second_binding = _binding()
    second_binding["case_id"] = "TC-005"
    second_binding["precondition_bindings"] = []

    with pytest.raises(ValueError) as captured:
        validate_case_fact_bindings(
            test_cases=[first_case, second_case],
            raw_bindings=[first_binding, second_binding],
            authoritative_facts=[_fact("F-001")],
            expected_module_name="内容管理",
        )

    message = str(captured.value)
    assert "事实绑定校验失败（2项）" in message
    assert "TC-003.precondition_bindings未完整覆盖字段索引: missing=[1]" in message
    assert "TC-005.precondition_bindings未完整覆盖字段索引: missing=[0]" in message


def test_index_effective_facts_rejects_inactive_fact() -> None:
    with pytest.raises(ValueError, match="包含非生效事实"):
        index_effective_facts([_fact("F-001", status="inactive")])


def test_index_effective_facts_rejects_exact_fact_with_governed_values() -> None:
    with pytest.raises(ValueError, match="exact 事实不得声明 governed_values"):
        index_effective_facts(
            [_fact("F-001", governed_values=["199元"])]
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("action", "填写价格199元后提交"),
        ("expected", "系统固定展示200张图片"),
        ("expected", "投稿按钮右侧奖励标语消失"),
        ("expected", "投稿状态显示审核文案"),
    ],
)
def test_validate_case_fact_bindings_allows_runtime_value_as_source_text(
    field_name: str,
    field_value: str,
) -> None:
    case = _case()
    case["steps"][0][field_name] = field_value

    result = validate_case_fact_bindings(
        test_cases=[case],
        raw_bindings=[_binding()],
        authoritative_facts=[
            _fact(
                "F-001",
                value_policy="runtime_configured",
                governed_values=["199元", "200张", "消失", "显示审核文案"],
            )
        ],
        expected_module_name="内容管理",
    )

    assert result == [_binding()]


def test_validate_case_fact_bindings_allows_runtime_lookup_expression() -> None:
    case = _case()
    case["steps"][0] = {
        "action": "读取后台当前配置并提交内容",
        "expected": "页面展示与后台当前配置一致",
    }

    result = validate_case_fact_bindings(
        test_cases=[case],
        raw_bindings=[_binding()],
        authoritative_facts=[
            _fact(
                "F-001",
                value_policy="runtime_configured",
                governed_values=["199元", "200张"],
            )
        ],
        expected_module_name="内容管理",
    )

    assert result == [_binding()]
