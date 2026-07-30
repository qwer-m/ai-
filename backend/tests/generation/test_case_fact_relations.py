from modules.test_generation_components.legacy.stream.batch_diagnostics import (
    build_case_signature,
)
from modules.test_generation_components.control.semantic_contract import (
    validate_case_semantic_contract,
)
from modules.test_generation_components.coverage.coverage_analyzer import (
    analyze_case_structure,
)
from modules.test_generation_components.legacy.stream.batch_parallel_shards import (
    assign_cross_shard_duplicate_repair_targets,
    build_parallel_gap_repair_requests,
    merge_parallel_shard_cases,
    merge_public_batch_against_accepted_history,
)
from modules.test_generation_components.postprocess.case_fact_relations import (
    classify_case_fact_relation,
    deduplicate_cases_by_semantic_identity,
)


def _case(
    case_id: str,
    *,
    module: str,
    fact_ids: list[str],
    path_type: str = "happy",
    interaction_ids: list[str] | None = None,
) -> dict:
    return {
        "id": case_id,
        "test_module": module,
        "description": f"执行 {case_id} 的业务操作",
        "preconditions": ["业务数据已准备"],
        "steps": ["1. 打开业务页面", "2. 提交操作"],
        "test_input": case_id,
        "expected_result": f"{case_id} 对应状态正确更新",
        "path_type": path_type,
        "_semantic": {
            "module_candidates": [],
            "fact_ids": fact_ids,
            "interaction_ids": interaction_ids or [],
            "workflow_stage_candidates": [],
            "precondition_states": [],
            "produced_states": [],
        },
    }


def test_fact_relation_detects_cross_module_duplicate_without_text_similarity() -> None:
    left = _case("ORDER-CREATE-A", module="订单入口", fact_ids=["FACT-ORDER-CREATED"])
    right = _case("ORDER-CREATE-B", module="订单管理", fact_ids=["FACT-ORDER-CREATED"])

    assert classify_case_fact_relation(left, right) == "duplicate"


def test_case_semantic_fact_ids_must_reference_active_requirement_facts() -> None:
    requirement_contract = {
        "evidence_facts": [
            {
                "fact_id": "FACT-ORDER-CREATED",
                "statement": "提交订单后创建订单记录",
            }
        ],
        "functional_architecture": {
            "functional_modules": [
                {"module_key": "order", "module_name": "订单管理"},
            ],
            "module_interactions": [],
        },
        "workflow_blueprints": [],
    }
    semantic = {
        "module_candidates": [
            {
                "module_key": "order",
                "module_name": "订单管理",
                "role": "primary",
                "confidence": 0.9,
                "evidence": ["订单管理"],
            }
        ],
        "fact_ids": ["FACT-ORDER-CREATED"],
        "interaction_ids": [],
        "workflow_stage_candidates": [],
        "precondition_states": [],
        "produced_states": [],
    }

    accepted = validate_case_semantic_contract(
        semantic,
        case_text="订单管理 提交订单后创建订单记录",
        case_test_module="订单管理",
        requirement_contract=requirement_contract,
    )
    rejected = validate_case_semantic_contract(
        {**semantic, "fact_ids": ["FACT-NOT-ACTIVE"]},
        case_text="订单管理 提交订单后创建订单记录",
        case_test_module="订单管理",
        requirement_contract=requirement_contract,
    )

    assert accepted["valid"] is True
    assert accepted["semantic"]["fact_ids"] == ["FACT-ORDER-CREATED"]
    assert rejected["valid"] is False
    assert "fact_id:fact_reference_not_active" in rejected["rejection_reasons"]


def test_fact_relation_keeps_combined_case_as_containment() -> None:
    atomic = _case("PAY", module="支付", fact_ids=["FACT-PAY-SUCCESS"])
    combined = _case(
        "PAY-AND-NOTICE",
        module="结算",
        fact_ids=["FACT-PAY-SUCCESS", "FACT-NOTICE-CREATED"],
    )

    assert classify_case_fact_relation(combined, atomic) == "contains"
    assert classify_case_fact_relation(atomic, combined) == "contained_by"


def test_fact_relation_does_not_merge_different_paths_or_interactions() -> None:
    happy = _case(
        "HAPPY",
        module="订单",
        fact_ids=["FACT-ORDER-SUBMIT"],
        path_type="happy",
        interaction_ids=["I-ORDER-INVENTORY"],
    )
    exception = _case(
        "EXCEPTION",
        module="订单",
        fact_ids=["FACT-ORDER-SUBMIT"],
        path_type="exception",
        interaction_ids=["I-ORDER-INVENTORY"],
    )
    another_interaction = _case(
        "ANOTHER",
        module="订单",
        fact_ids=["FACT-ORDER-SUBMIT"],
        path_type="happy",
        interaction_ids=["I-ORDER-PAYMENT"],
    )

    assert classify_case_fact_relation(happy, exception) == "none"
    assert classify_case_fact_relation(happy, another_interaction) == "none"


def test_parallel_merge_uses_final_semantic_retention_for_containment() -> None:
    shard_results = [
        {
            "shard": {"shard_id": "MAIN", "merge_order": 1},
            "cases": [
                _case("MAIN-001", module="订单入口", fact_ids=["FACT-ORDER-CREATED"]),
            ],
        },
        {
            "shard": {"shard_id": "SIDE", "merge_order": 2},
            "cases": [
                _case("SIDE-001", module="订单管理", fact_ids=["FACT-ORDER-CREATED"]),
                _case(
                    "SIDE-002",
                    module="订单通知",
                    fact_ids=["FACT-ORDER-CREATED", "FACT-NOTICE-CREATED"],
                ),
            ],
        },
    ]

    merged = merge_parallel_shard_cases(
        shard_results,
        build_case_signature_fn=build_case_signature,
        start_id=1,
        expected_count=25,
    )

    assert [case["id"] for case in merged["cases"]] == ["TC-001"]
    assert merged["cases"][0]["test_input"] == "SIDE-002"
    assert merged["duplicate_count"] == 1
    assert merged["exact_duplicate_count"] == 0
    assert merged["semantic_duplicate_count"] == 1
    assert merged["containment_count"] == 1
    assert merged["semantic_containment_dropped_count"] == 1
    assert merged["per_shard_counts"][1]["semantic_duplicate_count"] == 1
    assert merged["per_shard_counts"][0]["containment_count"] == 1
    assert merged["per_shard_counts"][1]["containment_count"] == 0
    assert {item["relation"] for item in merged["semantic_relation_samples"]} == {
        "duplicate",
        "contained_by",
    }
    assert len(merged["cases"]) == len(
        deduplicate_cases_by_semantic_identity(
            [
                case
                for result in shard_results
                for case in result["cases"]
            ]
        ).cases
    )


def test_parallel_merge_records_structured_feedback_for_exact_signature_duplicate() -> None:
    retained = _case(
        "SOURCE-001",
        module="订单入口",
        fact_ids=["FACT-ORDER-CREATED"],
    )
    duplicate = dict(retained)
    duplicate["id"] = "REPAIR-001"

    merged = merge_parallel_shard_cases(
        [
            {
                "shard": {"shard_id": "SOURCE", "merge_order": 1},
                "cases": [retained],
            },
            {
                "shard": {"shard_id": "REPAIR", "merge_order": 2},
                "cases": [duplicate],
            },
        ],
        build_case_signature_fn=build_case_signature,
        start_id=1,
        expected_count=2,
    )

    assert merged["exact_duplicate_count"] == 1
    sample = merged["semantic_relation_samples"][0]
    assert sample["action"] == "drop_duplicate"
    assert sample["reasons"] == ["exact_public_signature_match"]
    assert sample["dropped_case_id"] == "REPAIR-001"
    assert sample["retained_case_id"] == "SOURCE-001"
    assert sample["dropped_fact_ids"] == ["FACT-ORDER-CREATED"]


def test_public_batch_merge_protects_accepted_history_and_reports_current_gap() -> None:
    accepted_history = _case(
        "TC-001",
        module="订单入口",
        fact_ids=["FACT-ORDER-CREATED"],
    )
    later_containing = _case(
        "LATER-001",
        module="订单入口",
        fact_ids=["FACT-ORDER-CREATED", "FACT-NOTICE-CREATED"],
    )
    later_unique = _case(
        "LATER-002",
        module="通知设置",
        fact_ids=["FACT-NOTICE-CONFIGURED"],
    )

    merged = merge_public_batch_against_accepted_history(
        [
            {
                "shard": {"shard_id": "B02-SHARD-01", "merge_order": 2},
                "cases": [later_containing, later_unique],
            }
        ],
        accepted_history_cases=[accepted_history],
        build_case_signature_fn=build_case_signature,
        start_id=1,
        expected_batch_count=2,
    )

    assert [case["test_input"] for case in merged["cases"]] == ["LATER-002"]
    assert merged["unique_case_count"] == 1
    assert merged["accepted_history_case_count"] == 1
    assert merged["cumulative_unique_case_count"] == 2
    assert merged["cross_batch_semantic_drop_count"] == 1
    sample = merged["semantic_relation_samples"][0]
    assert sample["action"] == "drop_protected_history_conflict"
    assert "accepted_public_batch_history_protected" in sample["reasons"]
    assert sample["dropped_shard_id"] == "B02-SHARD-01"


def test_structure_diagnostics_report_fact_duplicates_and_containment_separately() -> None:
    cases = [
        _case("ORDER-A", module="订单入口", fact_ids=["FACT-ORDER-CREATED"]),
        _case("ORDER-B", module="订单管理", fact_ids=["FACT-ORDER-CREATED"]),
        _case(
            "ORDER-COMBINED",
            module="订单通知",
            fact_ids=["FACT-ORDER-CREATED", "FACT-NOTICE-CREATED"],
        ),
    ]

    structure = analyze_case_structure("订单提交后创建订单并发送通知", cases)

    fact_clusters = [
        cluster
        for cluster in structure["duplicate_clusters"]
        if cluster["group_type"] == "fact"
    ]
    assert len(fact_clusters) == 1
    assert fact_clusters[0]["candidate_indices"] == [1, 2]
    assert structure["duplicate_case_count"] >= 1
    assert structure["containment_relation_count"] == 2
    assert {
        relation["related_case_id"]
        for relation in structure["containment_relations"]
    } == {"ORDER-COMBINED"}


def test_cross_shard_duplicate_gap_is_assigned_only_to_duplicate_source_shard() -> None:
    main_case = _case(
        "MAIN-001",
        module="订单入口",
        fact_ids=["FACT-ORDER-CREATED"],
    )
    duplicate_case = _case(
        "SIDE-001",
        module="订单管理",
        fact_ids=["FACT-ORDER-CREATED"],
    )
    shard_results = [
        {
            "shard": {"shard_id": "MAIN", "merge_order": 1, "target_count": 1},
            "cases": [main_case],
        },
        {
            "shard": {"shard_id": "SIDE", "merge_order": 2, "target_count": 1},
            "cases": [duplicate_case],
        },
    ]
    merge_result = merge_parallel_shard_cases(
        shard_results,
        build_case_signature_fn=build_case_signature,
        start_id=1,
        expected_count=2,
    )

    targeted = assign_cross_shard_duplicate_repair_targets(
        shard_results,
        merge_result,
    )
    requests = build_parallel_gap_repair_requests(
        requests=[
            {"shard": item["shard"], "system_prompt": "base"}
            for item in shard_results
        ],
        accepted_results=targeted,
        repair_attempt=2,
    )

    assert [request["shard"]["shard_id"] for request in requests] == ["SIDE"]
    assert requests[0]["shard"]["target_count"] == 1
    assert "FACT-ORDER-CREATED" in requests[0]["system_prompt"]


def test_cross_shard_repair_clears_stale_targets_and_caps_requests_to_real_gap() -> None:
    shard_results = [
        {
            "shard": {"shard_id": "A", "target_count": 2},
            "repair_target_count": 3,
            "cases": [
                {"id": "A-1", "description": "已接受行为 A1"},
                {"id": "A-2", "description": "已接受行为 A2"},
            ],
        },
        {
            "shard": {"shard_id": "B", "target_count": 2},
            "repair_target_count": 2,
            "cases": [
                {"id": "B-1", "description": "已接受行为 B1"},
                {"id": "B-2", "description": "已接受行为 B2"},
            ],
        },
    ]
    merge_result = {
        "semantic_relation_samples": [
            {
                "action": "drop_duplicate",
                "dropped_shard_id": "A",
                "dropped_fact_ids": ["FACT-A-1"],
            },
            {
                "action": "drop_contained_case",
                "dropped_shard_id": "A",
                "dropped_fact_ids": ["FACT-A-2"],
            },
            {
                "action": "drop_duplicate",
                "dropped_shard_id": "B",
                "dropped_fact_ids": ["FACT-B-1"],
            },
        ]
    }

    targeted = assign_cross_shard_duplicate_repair_targets(
        shard_results,
        merge_result,
        gap_count=1,
    )
    requests = build_parallel_gap_repair_requests(
        requests=[
            {"shard": item["shard"], "system_prompt": "base"}
            for item in shard_results
        ],
        accepted_results=targeted,
        repair_attempt=2,
    )

    assert sum(item["repair_target_count"] for item in targeted) == 1
    assert len(requests) == 1
    assert requests[0]["shard"]["target_count"] == 1


def test_cross_shard_containment_gap_is_assigned_to_dropped_source_shard() -> None:
    atomic_case = _case(
        "MAIN-001",
        module="订单入口",
        fact_ids=["FACT-ORDER-CREATED"],
    )
    combined_case = _case(
        "SIDE-001",
        module="订单通知",
        fact_ids=["FACT-ORDER-CREATED", "FACT-NOTICE-CREATED"],
    )
    shard_results = [
        {
            "shard": {"shard_id": "MAIN", "merge_order": 1, "target_count": 1},
            "cases": [atomic_case],
        },
        {
            "shard": {"shard_id": "SIDE", "merge_order": 2, "target_count": 1},
            "cases": [combined_case],
        },
    ]
    merge_result = merge_parallel_shard_cases(
        shard_results,
        build_case_signature_fn=build_case_signature,
        start_id=1,
        expected_count=2,
    )

    targeted = assign_cross_shard_duplicate_repair_targets(
        shard_results,
        merge_result,
    )
    requests = build_parallel_gap_repair_requests(
        requests=[
            {"shard": item["shard"], "system_prompt": "base"}
            for item in shard_results
        ],
        accepted_results=targeted,
        repair_attempt=2,
    )

    assert merge_result["unique_case_count"] == 1
    assert merge_result["semantic_containment_dropped_count"] == 1
    assert merge_result["semantic_relation_samples"][0]["action"] == (
        "replace_with_containing_case"
    )
    assert [request["shard"]["shard_id"] for request in requests] == ["MAIN"]
    assert requests[0]["shard"]["target_count"] == 1
    assert "broaden, or narrow" in requests[0]["system_prompt"]
