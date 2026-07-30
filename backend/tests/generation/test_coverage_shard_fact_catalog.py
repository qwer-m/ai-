from types import SimpleNamespace

from modules.test_generation_components.legacy.stream.batch_parallel_shards import (
    build_coverage_shard_plan,
    build_parallel_shard_instruction,
    normalize_and_accept_parallel_shard_results,
)
from modules.test_generation_components.legacy.stream.batch_prompt_runtime import (
    build_stream_batch_system_prompt,
)


def _module_fact_rules() -> list[dict]:
    return [
        {
            "rule_id": "MODULE::order_create",
            "rule_text": "订单创建模块",
            "facts": [
                {
                    "fact_id": "FACT::order_create::required_fields",
                    "statement": "创建订单时必须提交客户与商品信息",
                },
                {
                    "fact_id": "FACT::order_create::initial_status",
                    "statement": "订单创建成功后初始状态为待支付",
                },
            ],
        },
        {
            "rule_id": "MODULE::order_payment",
            "rule_text": "订单支付模块",
            "facts": [
                {
                    "fact_id": "FACT::order_payment::amount_match",
                    "statement": "支付金额必须与订单应付金额一致",
                },
                {
                    "fact_id": "FACT::order_payment::paid_status",
                    "statement": "支付成功后订单状态更新为已支付",
                },
            ],
        },
        {
            "rule_id": "MODULE::shipment",
            "rule_text": "订单发货模块",
            "facts": [
                {
                    "fact_id": "FACT::shipment::paid_only",
                    "statement": "只有已支付订单可以发货",
                },
                {
                    "fact_id": "FACT::shipment::tracking_number",
                    "statement": "发货时必须记录物流单号",
                },
            ],
        },
        {
            "rule_id": "MODULE::refund",
            "rule_text": "订单退款模块",
            "facts": [
                {
                    "fact_id": "FACT::refund::paid_order",
                    "statement": "已支付订单可申请退款",
                },
                {
                    "fact_id": "FACT::refund::audit_record",
                    "statement": "退款审批结果必须留存审计记录",
                },
            ],
        },
    ]


def test_coverage_shard_plan_preserves_assigned_active_fact_catalog() -> None:
    shards = build_coverage_shard_plan(
        _module_fact_rules(),
        expected_count=50,
        max_workers=2,
        max_cases_per_worker=25,
    )

    assert [shard["rule_ids"] for shard in shards] == [
        ["MODULE::order_create", "MODULE::order_payment"],
        ["MODULE::shipment", "MODULE::refund"],
    ]
    assert shards[0]["facts"] == [
        {
            "fact_id": "FACT::order_create::required_fields",
            "statement": "创建订单时必须提交客户与商品信息",
        },
        {
            "fact_id": "FACT::order_create::initial_status",
            "statement": "订单创建成功后初始状态为待支付",
        },
        {
            "fact_id": "FACT::order_payment::amount_match",
            "statement": "支付金额必须与订单应付金额一致",
        },
        {
            "fact_id": "FACT::order_payment::paid_status",
            "statement": "支付成功后订单状态更新为已支付",
        },
    ]


def test_coverage_shard_instruction_marks_planning_labels_as_non_fact_ids() -> None:
    shard = build_coverage_shard_plan(
        _module_fact_rules(),
        expected_count=50,
        max_workers=2,
        max_cases_per_worker=25,
    )[0]

    instruction = build_parallel_shard_instruction(shard)

    assert "MODULE::order_create" in instruction
    assert "Rule IDs such as `RULE-001` and `MODULE::...` are planning labels, never fact IDs." in instruction
    assert "Never copy a rule ID into `_semantic.fact_ids`." in instruction
    assert "the only allowed non-empty `_semantic.fact_ids` values for this shard" in instruction


def test_coverage_shard_fact_catalogs_do_not_cross_module_boundaries() -> None:
    shards = build_coverage_shard_plan(
        _module_fact_rules(),
        expected_count=50,
        max_workers=2,
        max_cases_per_worker=25,
    )

    first_fact_ids = {fact["fact_id"] for fact in shards[0]["facts"]}
    second_fact_ids = {fact["fact_id"] for fact in shards[1]["facts"]}

    assert first_fact_ids == {
        "FACT::order_create::required_fields",
        "FACT::order_create::initial_status",
        "FACT::order_payment::amount_match",
        "FACT::order_payment::paid_status",
    }
    assert second_fact_ids == {
        "FACT::shipment::paid_only",
        "FACT::shipment::tracking_number",
        "FACT::refund::paid_order",
        "FACT::refund::audit_record",
    }
    assert first_fact_ids.isdisjoint(second_fact_ids)

    first_instruction = build_parallel_shard_instruction(shards[0])
    second_instruction = build_parallel_shard_instruction(shards[1])
    assert all(fact_id in first_instruction for fact_id in first_fact_ids)
    assert all(fact_id not in first_instruction for fact_id in second_fact_ids)
    assert all(fact_id in second_instruction for fact_id in second_fact_ids)
    assert all(fact_id not in second_instruction for fact_id in first_fact_ids)


def test_active_fact_catalog_survives_regular_system_prompt_construction() -> None:
    shard = build_coverage_shard_plan(
        _module_fact_rules(),
        expected_count=50,
        max_workers=2,
        max_cases_per_worker=25,
    )[0]
    shard_instruction = build_parallel_shard_instruction(shard)

    prompt = build_stream_batch_system_prompt(
        base_prompt="BASE",
        coverage_instruction="COVERAGE",
        history_context="HISTORY",
        coverage_plan_lite="PLAN",
        side_suite_order="main, visual",
        batch_index=0,
        total_batches=2,
        current_id=1,
        generated_in_batch=0,
        need=25,
        shard_instruction=shard_instruction,
        architecture_instruction="ARCHITECTURE",
    )

    for fact in shard["facts"]:
        assert fact["fact_id"] in prompt
        assert fact["statement"] in prompt
    assert prompt.count("Assigned active fact catalog") == 1
    assert prompt.rfind("Assigned active fact catalog") < prompt.rfind(
        "FINAL PER-CASE STRUCTURE CHECK"
    )


def test_parallel_shard_acceptance_closes_fact_ownership_boundary() -> None:
    semantic_rejections: list[dict] = []

    def _accept(cases: list[dict], *, limit: int, start_id: int):
        return SimpleNamespace(
            cases=cases[:limit],
            incomplete_rows=[],
            module_contract_summary={},
        )

    results = normalize_and_accept_parallel_shard_results(
        [
            {
                "status": "parsed",
                "shard": {
                    "shard_id": "SHARD-01",
                    "shard_kind": "independent",
                    "target_count": 3,
                    "facts": [
                        {
                            "fact_id": "FACT::order_create::required_fields",
                            "statement": "创建订单需要完整字段",
                        }
                    ],
                },
                "cases": [
                    {"id": "EMPTY", "_semantic": {"fact_ids": []}},
                    {
                        "id": "CROSS",
                        "_semantic": {"fact_ids": ["FACT::order_payment::paid_status"]},
                    },
                    {
                        "id": "OWNED",
                        "_semantic": {
                            "fact_ids": ["FACT::order_create::required_fields"]
                        },
                    },
                ],
            }
        ],
        normalize_json_structure_fn=lambda cases: cases,
        accept_candidates_fn=_accept,
        semantic_rejections=semantic_rejections,
        start_id=1,
    )

    assert [case["id"] for case in results[0]["cases"]] == ["OWNED"]
    assert results[0]["semantic_rejection_count"] == 2
    assert results[0]["gap_count"] == 2
    assert {
        reason
        for rejection in semantic_rejections
        for reason in rejection["rejection_reasons"]
    } == {
        "fact_ids:assigned_fact_required",
        "fact_ids:outside_shard_catalog",
    }
    assert all(
        rejection["source_shard_id"] == "SHARD-01"
        for rejection in semantic_rejections
    )
