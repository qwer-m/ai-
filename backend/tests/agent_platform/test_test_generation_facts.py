from __future__ import annotations

import pytest

from modules.agent_platform.test_generation_facts import (
    index_effective_facts,
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
    ],
)
def test_validate_case_fact_bindings_rejects_fixed_runtime_value(
    field_name: str,
    field_value: str,
) -> None:
    case = _case()
    case["steps"][0][field_name] = field_value

    with pytest.raises(ValueError, match="固化了运行时配置值"):
        validate_case_fact_bindings(
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
