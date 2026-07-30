from __future__ import annotations

import copy
import json

from modules.test_generation_components.legacy.stream.batch_flow_control import (
    assign_public_batch_merge_gap_repair_targets,
    build_public_batch_execution_plan,
    build_public_owned_shard_plan,
    group_shard_requests_by_public_batch,
)
from modules.test_generation_components.legacy.stream.batch_parallel_shards import (
    ParallelShardConfig,
)
from modules.test_generation_components.legacy.stream import batches as batches_module
from tests.generation.test_stream_batch_diagnostics import (
    _run_batch_stream,
    _valid_semantic_case,
)


def test_public_batch_plan_keeps_80_case_ownership_at_25_25_25_5() -> None:
    plan = build_public_batch_execution_plan(
        generation_target_count=80,
        batch_size=25,
        max_workers=2,
    )

    assert [item["target_count"] for item in plan] == [25, 25, 25, 5]
    assert [item["batch_index"] for item in plan] == [1, 2, 3, 4]
    assert [item["start_offset"] for item in plan] == [0, 25, 50, 75]
    assert [item["end_offset"] for item in plan] == [25, 50, 75, 80]
    assert [item["shard_target_counts"] for item in plan] == [
        [13, 12],
        [13, 12],
        [13, 12],
        [3, 2],
    ]


def test_every_public_batch_owns_exactly_the_sum_of_its_subshards() -> None:
    plan = build_public_batch_execution_plan(
        generation_target_count=62,
        batch_size=25,
        max_workers=3,
    )

    assert [item["target_count"] for item in plan] == [25, 25, 12]
    assert [item["shard_target_counts"] for item in plan] == [
        [9, 8, 8],
        [9, 8, 8],
        [4, 4, 4],
    ]
    assert all(
        sum(item["shard_target_counts"]) == item["target_count"]
        for item in plan
    )
    assert plan[-1]["end_offset"] == 62


def test_empty_generation_target_has_no_public_batch() -> None:
    assert build_public_batch_execution_plan(
        generation_target_count=0,
        batch_size=25,
        max_workers=2,
    ) == []


def test_frozen_public_batch_emission_uses_plan_slices_without_rebuilding() -> None:
    plan = build_public_batch_execution_plan(
        generation_target_count=5,
        batch_size=3,
        max_workers=2,
    )
    frozen_cases = []
    for index in range(1, 6):
        case = copy.deepcopy(_valid_semantic_case())
        case["id"] = f"TC-{index:03d}"
        case["description"] = f"frozen-case-{index}"
        frozen_cases.append(case)

    emitted_batches, diagnostic = (
        batches_module._build_frozen_public_batch_emission(
            accepted_public_batch_cases=frozen_cases,
            public_batch_execution_plan=plan,
            expected_count=5,
            workflow_blueprints=[],
        )
    )

    assert [
        [case["id"] for case in cases]
        for _batch, cases in emitted_batches
    ] == [["TC-001", "TC-002", "TC-003"], ["TC-004", "TC-005"]]
    assert diagnostic["passed"] is True
    assert diagnostic["frozen_case_count"] == 5
    assert diagnostic["emitted_case_count"] == 5
    assert diagnostic["deterministic_signature_match"] is True
    assert diagnostic["required_stage_coverage_match"] is True


def test_frozen_public_batch_emission_reports_plan_slice_mismatch() -> None:
    plan = build_public_batch_execution_plan(
        generation_target_count=5,
        batch_size=3,
        max_workers=2,
    )
    plan[1] = {**plan[1], "start_offset": 4}
    frozen_cases = []
    for index in range(1, 6):
        case = copy.deepcopy(_valid_semantic_case())
        case["id"] = f"TC-{index:03d}"
        case["description"] = f"frozen-case-{index}"
        frozen_cases.append(case)

    _emitted_batches, diagnostic = (
        batches_module._build_frozen_public_batch_emission(
            accepted_public_batch_cases=frozen_cases,
            public_batch_execution_plan=plan,
            expected_count=5,
            workflow_blueprints=[],
        )
    )

    assert diagnostic["passed"] is False
    assert diagnostic["emitted_case_count"] == 4
    assert diagnostic["deterministic_signature_match"] is False
    assert "emitted_case_count_mismatch" in diagnostic["failure_reasons"]
    assert "deterministic_signature_mismatch" in diagnostic["failure_reasons"]


def test_parallel_shards_are_owned_by_one_public_batch() -> None:
    public_plan = build_public_batch_execution_plan(
        generation_target_count=80,
        batch_size=25,
        max_workers=2,
    )
    coverage_units = [
        {
            "rule_id": f"RULE-{index:02d}",
            "rule_text": f"可验证业务规则 {index}",
            "facts": [
                {
                    "fact_id": f"FACT-{index:02d}",
                    "statement": f"业务事实 {index}",
                }
            ],
        }
        for index in range(1, 17)
    ]

    shards = build_public_owned_shard_plan(
        coverage_units,
        public_batch_plan=public_plan,
        max_workers=2,
        main_chain_target=0,
    )

    assert len(shards) == 8
    assert [shard["target_count"] for shard in shards] == [
        13,
        12,
        13,
        12,
        13,
        12,
        3,
        2,
    ]
    for public_batch in public_plan:
        batch_index = public_batch["batch_index"]
        owned_shards = [
            shard
            for shard in shards
            if shard["public_batch_index"] == batch_index
        ]
        assert sum(shard["target_count"] for shard in owned_shards) == (
            public_batch["target_count"]
        )
        assert all(
            shard["public_batch_target_count"] == public_batch["target_count"]
            for shard in owned_shards
        )

    independent_rule_ids = [
        rule_id
        for shard in shards
        for rule_id in shard.get("rule_ids") or []
    ]
    assert len(independent_rule_ids) == len(set(independent_rule_ids))


def test_main_chain_shard_stays_inside_first_public_batch() -> None:
    public_plan = build_public_batch_execution_plan(
        generation_target_count=80,
        batch_size=25,
        max_workers=2,
    )
    coverage_units = [
        {"rule_id": f"R-{index}", "rule_text": f"规则 {index}"}
        for index in range(1, 17)
    ]

    shards = build_public_owned_shard_plan(
        coverage_units,
        public_batch_plan=public_plan,
        max_workers=2,
        main_chain_target=6,
    )

    first_batch_shards = [
        shard for shard in shards if shard["public_batch_index"] == 1
    ]
    assert [shard["target_count"] for shard in first_batch_shards] == [6, 10, 9]
    assert first_batch_shards[0]["shard_kind"] == "main_chain"
    assert sum(shard["target_count"] for shard in first_batch_shards) == 25
    assert all(
        shard.get("shard_kind") != "main_chain"
        for shard in shards
        if shard["public_batch_index"] != 1
    )


def test_public_shard_plan_assigns_each_fact_to_one_owner_and_reserves_main_chain() -> None:
    public_plan = build_public_batch_execution_plan(
        generation_target_count=10,
        batch_size=10,
        max_workers=2,
    )
    coverage_units = [
        {
            "rule_id": "MODULE::A",
            "rule_text": "module A",
            "facts": [
                {"fact_id": "FACT-MAIN", "statement": "primary workflow"},
                {"fact_id": "FACT-SHARED", "statement": "shared behavior"},
                {"fact_id": "FACT-A", "statement": "behavior A"},
            ],
        },
        {
            "rule_id": "MODULE::B",
            "rule_text": "module B",
            "facts": [
                {"fact_id": "FACT-SHARED", "statement": "shared behavior"},
                {"fact_id": "FACT-B", "statement": "behavior B"},
            ],
        },
    ]

    shards = build_public_owned_shard_plan(
        coverage_units,
        public_batch_plan=public_plan,
        max_workers=2,
        main_chain_target=0,
        reserved_fact_ids={"FACT-MAIN"},
    )

    owned_fact_ids = [
        str(fact.get("fact_id") or "")
        for shard in shards
        for fact in (shard.get("facts") or [])
    ]
    assert "FACT-MAIN" not in owned_fact_ids
    assert owned_fact_ids.count("FACT-SHARED") == 1
    assert set(owned_fact_ids) == {"FACT-SHARED", "FACT-A", "FACT-B"}
    assert sum(
        int(shard.get("shared_fact_excluded_count") or 0) for shard in shards
    ) == 1
    assert sum(
        int(shard.get("reserved_fact_excluded_count") or 0) for shard in shards
    ) == 1


def test_request_groups_preserve_public_batch_sequence() -> None:
    public_plan = build_public_batch_execution_plan(
        generation_target_count=80,
        batch_size=25,
        max_workers=2,
    )
    requests = [
        {
            "request_id": f"REQ-{batch_index}-{shard_index}",
            "shard": {
                "public_batch_index": batch_index,
                "target_count": target,
            },
        }
        for batch_index, targets in enumerate(
            ([13, 12], [13, 12], [13, 12], [3, 2]),
            start=1,
        )
        for shard_index, target in enumerate(targets, start=1)
    ]

    groups = group_shard_requests_by_public_batch(
        list(reversed(requests)),
        public_batch_plan=public_plan,
    )

    assert [batch["batch_index"] for batch, _ in groups] == [1, 2, 3, 4]
    assert [
        sum(int(request["shard"]["target_count"]) for request in batch_requests)
        for _, batch_requests in groups
    ] == [25, 25, 25, 5]


def test_merge_gap_is_returned_to_the_losing_shard_with_batch_history() -> None:
    shard_results = [
        {
            "shard": {"shard_id": "B01-SHARD-01"},
            "repair_target_count": 9,
            "cases": [],
        },
        {
            "shard": {
                "shard_id": "B01-SHARD-02",
                "facts": [
                    {"fact_id": "FACT-X", "statement": "behavior X"},
                    {"fact_id": "FACT-Y", "statement": "behavior Y"},
                ],
            },
            "repair_target_count": 0,
            "cases": [],
        },
    ]
    merge_result = {
        "per_shard_counts": [
            {
                "shard_id": "B01-SHARD-01",
                "input_case_count": 13,
                "unique_case_count": 13,
            },
            {
                "shard_id": "B01-SHARD-02",
                "input_case_count": 12,
                "unique_case_count": 10,
            },
        ]
    }

    assigned = assign_public_batch_merge_gap_repair_targets(
        shard_results,
        merge_result=merge_result,
        gap_count=2,
        accepted_batch_cases=[
            {"id": "TC-001", "description": "已验收批内用例"}
        ],
        history_summaries=["TC-000: 前一批已验收用例"],
    )

    assert assigned[0]["repair_target_count"] == 0
    assert assigned[1]["repair_target_count"] == 2
    assert assigned[1]["public_batch_merge_gap_target"] == 2
    assert "已验收批内用例" in assigned[1]["repair_instruction"]
    assert "前一批已验收用例" in assigned[1]["repair_instruction"]


def test_merge_gap_stops_when_every_assigned_fact_is_already_covered() -> None:
    shard_results = [
        {
            "shard": {
                "shard_id": "B03-SHARD-01",
                "facts": [
                    {"fact_id": "FACT-A", "statement": "behavior A"},
                ],
            },
            "cases": [],
        }
    ]
    merge_result = {
        "per_shard_counts": [
            {
                "shard_id": "B03-SHARD-01",
                "input_case_count": 13,
                "unique_case_count": 12,
            }
        ],
        "semantic_relation_samples": [
            {
                "relation": "contains",
                "action": "drop_contained_case",
                "dropped_shard_id": "B03-SHARD-01",
                "dropped_fact_ids": ["FACT-A"],
                "retained_fact_ids": ["FACT-A", "FACT-B"],
            }
        ],
    }

    assigned = assign_public_batch_merge_gap_repair_targets(
        shard_results,
        merge_result=merge_result,
        gap_count=1,
        accepted_batch_cases=[
            {
                "id": "TC-051",
                "description": "accepted composite behavior",
                "_semantic": {"fact_ids": ["FACT-A", "FACT-B"]},
            }
        ],
        history_summaries=[],
    )

    assert assigned[0]["repair_unused_fact_ids"] == []
    assert assigned[0]["repair_target_count"] == 0
    assert "repair_instruction" not in assigned[0]


def test_merge_gap_moves_to_same_batch_unused_facts_when_losing_shard_is_exhausted() -> None:
    shard_results = [
        {
            "shard": {
                "shard_id": "B03-SHARD-01",
                "facts": [
                    {"fact_id": "FACT-A", "statement": "已覆盖事实"},
                ],
            },
            "cases": [],
        },
        {
            "shard": {
                "shard_id": "B03-SHARD-02",
                "facts": [
                    {"fact_id": "FACT-B", "statement": "仍未覆盖事实"},
                ],
            },
            "cases": [],
        },
    ]
    merge_result = {
        "per_shard_counts": [
            {
                "shard_id": "B03-SHARD-01",
                "input_case_count": 13,
                "unique_case_count": 12,
            },
            {
                "shard_id": "B03-SHARD-02",
                "input_case_count": 12,
                "unique_case_count": 12,
            },
        ],
        "semantic_relation_samples": [
            {
                "relation": "contained_by",
                "action": "drop_contained_case",
                "reasons": ["fact_subset", "outcome_contained"],
                "dropped_case_id": "B03-SHARD-01-REPAIR-1",
                "dropped_shard_id": "B03-SHARD-01",
                "dropped_fact_ids": ["FACT-A"],
                "retained_case_id": "B03-SHARD-02-8",
                "retained_fact_ids": ["FACT-A"],
            }
        ],
    }

    assigned = assign_public_batch_merge_gap_repair_targets(
        shard_results,
        merge_result=merge_result,
        gap_count=1,
        accepted_batch_cases=[
            {
                "id": "TC-051",
                "description": "已覆盖行为",
            }
        ],
        history_summaries=[],
        accepted_history_cases=[
            {
                "id": "TC-001",
                "description": "前批已覆盖行为",
                "_semantic": {"fact_ids": ["FACT-A"]},
            }
        ],
    )

    assert assigned[0]["repair_target_count"] == 0
    assert assigned[1]["repair_target_count"] == 1
    assert assigned[1]["repair_allocation_reason"] == (
        "reassigned_to_unused_fact_capacity"
    )
    assert assigned[1]["repair_unused_fact_ids"] == ["FACT-B"]
    instruction = assigned[1]["repair_instruction"]
    assert "B03-SHARD-01-REPAIR-1" in instruction
    assert "drop_contained_case" in instruction
    assert "FACT-B: 仍未覆盖事实" in instruction


def test_real_stream_scheduler_executes_four_public_batches_in_order(
    monkeypatch,
) -> None:
    real_stream_parallel_shard_requests = (
        batches_module.stream_parallel_shard_requests
    )
    execution_groups: list[tuple[int, int]] = []
    history_sizes: list[int] = []
    real_build_recent_history_context = batches_module.build_recent_history_context

    def _record_history_context(history_summaries: list[str]) -> str:
        history_sizes.append(len(history_summaries))
        return real_build_recent_history_context(history_summaries)

    monkeypatch.setattr(
        batches_module,
        "build_recent_history_context",
        _record_history_context,
    )

    def _record_public_batch_execution(**kwargs):
        requests = list(kwargs.get("requests") or [])
        batch_indexes = {
            int((request.get("shard") or {}).get("public_batch_index") or 0)
            for request in requests
        }
        assert len(batch_indexes) == 1
        execution_groups.append(
            (
                batch_indexes.pop(),
                sum(
                    int((request.get("shard") or {}).get("target_count") or 0)
                    for request in requests
                ),
            )
        )
        return (
            yield from real_stream_parallel_shard_requests(**kwargs)
        )

    monkeypatch.setattr(
        batches_module,
        "stream_parallel_shard_requests",
        _record_public_batch_execution,
    )

    class _DeterministicShardClient:
        model = "deterministic-contract-client"
        last_response_metadata: dict = {}

        def __init__(self, shard: dict) -> None:
            self.shard = dict(shard)
            self.call_count = 0

        def generate_response_stream(self, *_args, **_kwargs):
            self.call_count += 1
            cases: list[dict] = []
            for case_index in range(int(self.shard.get("target_count") or 0)):
                case = copy.deepcopy(_valid_semantic_case())
                shard_id = str(self.shard.get("shard_id") or "SHARD")
                unique_tag = chr(
                    0x4E00
                    + int(self.shard.get("shard_index") or 0) * 100
                    + case_index
                ) * 8
                case["id"] = f"{shard_id}-{case_index + 1:03d}"
                case["description"] = f"验证独立业务行为：{unique_tag}"
                case["steps"] = [f"执行独立操作：{unique_tag}"]
                case["test_input"] = f"独立输入：{unique_tag}"
                case["expected_result"] = f"独立结果：{unique_tag}"
                cases.append(case)
            yield json.dumps(cases, ensure_ascii=False)

    coverage_rules = [
        {"rule_id": f"RULE-{index:02d}", "rule_text": f"覆盖规则 {index}"}
        for index in range(1, 17)
    ]
    def _client_factory(shard: dict) -> _DeterministicShardClient:
        return _DeterministicShardClient(shard)

    chunks, state, serial_client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        parallel_config=ParallelShardConfig(
            enabled=True,
            max_workers=2,
            min_expected_count=25,
            min_coverage_rules=8,
            duplicate_rate_abort=0.5,
            min_unique_ratio=0.5,
        ),
        coverage_rules=coverage_rules,
        expected_count=80,
        batch_size=25,
        multi_pass=True,
        parallel_shard_client_factory=_client_factory,
    )

    assert execution_groups == [(1, 25), (2, 25), (3, 25), (4, 5)]
    assert state["stream_parallel_shards_used"] is True
    assert state["stream_parallel_shard_result"]["public_batch_targets"] == [
        25,
        25,
        25,
        5,
    ]
    assert [
        metric["new_valid_cases_count"]
        for metric in state["stream_batch_quality_metrics"]
    ] == [25, 25, 25, 5]
    assert serial_client.call_count == 0
    assert history_sizes == [0, 25, 50, 75]
    output = "".join(chunks)
    assert "25/25" in output
    assert "5/5" in output


def test_batch_merge_duplicate_is_repaired_before_next_public_batch(
    monkeypatch,
) -> None:
    real_stream_parallel_shard_requests = (
        batches_module.stream_parallel_shard_requests
    )
    execution_groups: list[tuple[int, int, int]] = []

    def _record_execution(**kwargs):
        requests = list(kwargs.get("requests") or [])
        batch_indexes = {
            int((request.get("shard") or {}).get("public_batch_index") or 0)
            for request in requests
        }
        assert len(batch_indexes) == 1
        execution_groups.append(
            (
                batch_indexes.pop(),
                sum(
                    int((request.get("shard") or {}).get("target_count") or 0)
                    for request in requests
                ),
                max(int(request.get("repair_attempt") or 0) for request in requests),
            )
        )
        return (yield from real_stream_parallel_shard_requests(**kwargs))

    monkeypatch.setattr(
        batches_module,
        "stream_parallel_shard_requests",
        _record_execution,
    )

    class _DuplicateThenRepairClient:
        model = "deterministic-merge-gap-client"
        last_response_metadata: dict = {}

        def __init__(self, shard: dict) -> None:
            self.shard = dict(shard)
            self.call_count = 0

        def _case(self, tag: str, case_index: int) -> dict:
            case = copy.deepcopy(_valid_semantic_case())
            case["id"] = f"{self.shard.get('shard_id')}-{case_index:03d}"
            case["description"] = f"验证独立业务行为：{tag}"
            case["steps"] = [f"执行独立操作：{tag}"]
            case["test_input"] = f"独立输入：{tag}"
            case["expected_result"] = f"独立结果：{tag}"
            return case

        def generate_response_stream(self, *_args, **_kwargs):
            self.call_count += 1
            if self.call_count > 1:
                yield json.dumps(
                    [self._case("补齐批内合并缺口" * 4, 999)],
                    ensure_ascii=False,
                )
                return
            target_count = int(self.shard.get("target_count") or 0)
            shard_index = int(self.shard.get("shard_index") or 0)
            cases: list[dict] = []
            for case_index in range(target_count):
                if (
                    int(self.shard.get("public_batch_index") or 0) == 1
                    and case_index == 0
                    and int(self.shard.get("public_batch_shard_index") or 0)
                    in {1, 2}
                ):
                    tag = "批内跨分片重复行为" * 4
                else:
                    tag = chr(0x4E00 + shard_index * 100 + case_index) * 8
                cases.append(self._case(tag, case_index + 1))
            yield json.dumps(cases, ensure_ascii=False)

    coverage_rules = [
        {"rule_id": f"RULE-{index:02d}", "rule_text": f"覆盖规则 {index}"}
        for index in range(1, 17)
    ]
    chunks, state, serial_client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        parallel_config=ParallelShardConfig(
            enabled=True,
            max_workers=2,
            min_expected_count=25,
            min_coverage_rules=8,
            duplicate_rate_abort=0.5,
            min_unique_ratio=0.5,
        ),
        coverage_rules=coverage_rules,
        expected_count=50,
        batch_size=25,
        multi_pass=True,
        parallel_shard_client_factory=lambda shard: _DuplicateThenRepairClient(
            shard
        ),
    )

    assert execution_groups == [
        (1, 25, 0),
        (1, 1, 2),
        (2, 25, 0),
    ]
    assert [
        metric["new_valid_cases_count"]
        for metric in state["stream_batch_quality_metrics"]
    ] == [25, 25]
    assert serial_client.call_count == 0
    output = "".join(chunks)
    assert "第 1/2 批次合并去重后缺口 1 条" in output
    assert output.index("批次合并去重后缺口") < output.index(
        "正在生成第 2/2 批次"
    )


def test_cross_batch_duplicate_is_repaired_inside_second_batch_and_not_replayed_globally(
    monkeypatch,
) -> None:
    real_stream_parallel_shard_requests = (
        batches_module.stream_parallel_shard_requests
    )
    execution_groups: list[tuple[int, int, int]] = []
    duplicate_marker = "cross-batch-duplicate-marker"
    repair_marker = "cross-batch-repair-marker"

    def _record_execution(**kwargs):
        requests = list(kwargs.get("requests") or [])
        batch_indexes = {
            int((request.get("shard") or {}).get("public_batch_index") or 0)
            for request in requests
        }
        assert len(batch_indexes) == 1
        execution_groups.append(
            (
                batch_indexes.pop(),
                sum(
                    int((request.get("shard") or {}).get("target_count") or 0)
                    for request in requests
                ),
                max(int(request.get("repair_attempt") or 0) for request in requests),
            )
        )
        return (yield from real_stream_parallel_shard_requests(**kwargs))

    monkeypatch.setattr(
        batches_module,
        "stream_parallel_shard_requests",
        _record_execution,
    )

    class _CrossBatchDuplicateThenRepairClient:
        model = "deterministic-cross-batch-client"
        last_response_metadata: dict = {}

        def __init__(self, shard: dict) -> None:
            self.shard = dict(shard)
            self.call_count = 0

        def _case(self, tag: str, case_index: int) -> dict:
            case = copy.deepcopy(_valid_semantic_case())
            case["id"] = f"{self.shard.get('shard_id')}-{case_index:03d}"
            case["description"] = f"验证独立业务行为：{tag}"
            case["steps"] = [f"执行独立操作：{tag}"]
            case["test_input"] = f"独立输入：{tag}"
            case["expected_result"] = f"独立结果：{tag}"
            return case

        def generate_response_stream(self, *_args, **_kwargs):
            self.call_count += 1
            if self.call_count > 1:
                yield json.dumps(
                    [self._case(repair_marker, 999)],
                    ensure_ascii=False,
                )
                return

            batch_index = int(self.shard.get("public_batch_index") or 0)
            batch_shard_index = int(
                self.shard.get("public_batch_shard_index") or 0
            )
            shard_index = int(self.shard.get("shard_index") or 0)
            cases: list[dict] = []
            for case_index in range(int(self.shard.get("target_count") or 0)):
                if (
                    batch_shard_index == 1
                    and case_index == 0
                    and batch_index in {1, 2}
                ):
                    tag = duplicate_marker
                else:
                    tag = chr(0x4E00 + shard_index * 100 + case_index) * 8
                cases.append(self._case(tag, case_index + 1))
            yield json.dumps(cases, ensure_ascii=False)

    coverage_rules = [
        {"rule_id": f"RULE-{index:02d}", "rule_text": f"覆盖规则 {index}"}
        for index in range(1, 17)
    ]
    chunks, state, serial_client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        parallel_config=ParallelShardConfig(
            enabled=True,
            max_workers=2,
            min_expected_count=25,
            min_coverage_rules=8,
            duplicate_rate_abort=0.5,
            min_unique_ratio=0.5,
        ),
        coverage_rules=coverage_rules,
        expected_count=50,
        batch_size=25,
        multi_pass=True,
        parallel_shard_client_factory=(
            lambda shard: _CrossBatchDuplicateThenRepairClient(shard)
        ),
    )

    # 第二批与第一批冲突的候选必须在第二批验收前补齐；批次冻结后不得再发全局修复请求。
    assert execution_groups == [
        (1, 25, 0),
        (2, 25, 0),
        (2, 1, 2),
    ]
    assert [
        metric["new_valid_cases_count"]
        for metric in state["stream_batch_quality_metrics"]
    ] == [25, 25]
    parallel_summary = state["stream_parallel_shard_result"]
    assert parallel_summary["selection_source"] == "accepted_public_batches"
    assert parallel_summary["accepted_case_count"] == 50
    assert parallel_summary["gap_count"] == 0
    assert parallel_summary["raw_shard_candidate_count"] > 50
    emission_consistency = state[
        "stream_public_batch_emission_consistency"
    ]
    assert emission_consistency["passed"] is True
    assert emission_consistency["frozen_case_count"] == 50
    assert emission_consistency["emitted_case_count"] == 50
    assert emission_consistency["deterministic_signature_match"] is True
    assert state["full_content"].count(duplicate_marker) == 4
    assert repair_marker in state["full_content"]
    assert serial_client.call_count == 0
    output = "".join(chunks)
    assert "跨分片重复补生成" not in output


def test_public_batch_emission_mismatch_fails_closed_before_streaming_cases(
    monkeypatch,
) -> None:
    real_build_emission = batches_module._build_frozen_public_batch_emission

    def _force_emission_mismatch(**kwargs):
        emitted_batches, diagnostic = real_build_emission(**kwargs)
        diagnostic = {
            **diagnostic,
            "passed": False,
            "failure_reasons": ["deterministic_signature_mismatch"],
            "deterministic_signature_match": False,
        }
        return emitted_batches, diagnostic

    monkeypatch.setattr(
        batches_module,
        "_build_frozen_public_batch_emission",
        _force_emission_mismatch,
    )

    class _UniqueParallelClient:
        model = "deterministic-emission-mismatch-client"
        last_response_metadata: dict = {}

        def __init__(self, shard: dict) -> None:
            self.shard = dict(shard)

        def generate_response_stream(self, *_args, **_kwargs):
            cases: list[dict] = []
            shard_index = int(self.shard.get("shard_index") or 0)
            for case_index in range(int(self.shard.get("target_count") or 0)):
                case = copy.deepcopy(_valid_semantic_case())
                marker = f"emission-{shard_index}-{case_index}"
                case["id"] = marker
                case["description"] = f"verify {marker}"
                case["steps"] = [f"execute {marker}"]
                case["test_input"] = f"input {marker}"
                case["expected_result"] = f"result {marker}"
                cases.append(case)
            yield json.dumps(cases, ensure_ascii=False)

    coverage_rules = [
        {"rule_id": f"RULE-{index:02d}", "rule_text": f"rule {index}"}
        for index in range(1, 17)
    ]
    chunks, state, serial_client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        parallel_config=ParallelShardConfig(
            enabled=True,
            max_workers=2,
            min_expected_count=25,
            min_coverage_rules=8,
            duplicate_rate_abort=0.5,
            min_unique_ratio=0.5,
        ),
        coverage_rules=coverage_rules,
        expected_count=25,
        batch_size=25,
        multi_pass=True,
        parallel_shard_client_factory=lambda shard: _UniqueParallelClient(
            shard
        ),
    )

    assert state["primary_generation_failed"] is True
    assert state["primary_generation_failure"]["abort_code"] == (
        "PUBLIC_BATCH_EMISSION_MISMATCH_ABORT"
    )
    assert state["stream_public_batch_emission_consistency"]["passed"] is False
    assert state["full_content"] == "[]\n"
    assert state["stream_batch_quality_metrics"] == []
    assert serial_client.call_count == 0
    assert "PUBLIC_BATCH_EMISSION_MISMATCH_ABORT" in "".join(chunks)


def test_unresolved_public_batch_gap_aborts_before_next_batch(
    monkeypatch,
) -> None:
    real_stream_parallel_shard_requests = (
        batches_module.stream_parallel_shard_requests
    )
    real_merge_public_batch = (
        batches_module.merge_public_batch_against_accepted_history
    )
    execution_groups: list[tuple[int, int, int]] = []

    def _record_execution(**kwargs):
        requests = list(kwargs.get("requests") or [])
        batch_index = int(
            (requests[0].get("shard") or {}).get("public_batch_index") or 0
        )
        execution_groups.append(
            (
                batch_index,
                sum(
                    int((request.get("shard") or {}).get("target_count") or 0)
                    for request in requests
                ),
                max(int(request.get("repair_attempt") or 0) for request in requests),
            )
        )
        return (yield from real_stream_parallel_shard_requests(**kwargs))

    def _keep_first_public_batch_underfilled(shard_results, **kwargs):
        result = real_merge_public_batch(shard_results, **kwargs)
        batch_indexes = {
            int((item.get("shard") or {}).get("public_batch_index") or 0)
            for item in shard_results
            if isinstance(item, dict)
        }
        if batch_indexes == {1} and len(result.get("cases") or []) >= 25:
            result = dict(result)
            result["cases"] = list(result.get("cases") or [])[:-1]
            result["unique_case_count"] = len(result["cases"])
        return result

    monkeypatch.setattr(
        batches_module,
        "stream_parallel_shard_requests",
        _record_execution,
    )
    monkeypatch.setattr(
        batches_module,
        "merge_public_batch_against_accepted_history",
        _keep_first_public_batch_underfilled,
    )

    class _AlwaysUniqueClient:
        model = "deterministic-fail-close-client"
        last_response_metadata: dict = {}

        def __init__(self, shard: dict) -> None:
            self.shard = dict(shard)
            self.call_count = 0

        def generate_response_stream(self, *_args, **_kwargs):
            self.call_count += 1
            count = (
                1
                if self.call_count > 1
                else int(self.shard.get("target_count") or 0)
            )
            cases: list[dict] = []
            for case_index in range(count):
                case = copy.deepcopy(_valid_semantic_case())
                unique_offset = (
                    int(self.shard.get("shard_index") or 0) * 200
                    + self.call_count * 50
                    + case_index
                )
                tag = chr(0x4E00 + unique_offset) * 8
                case["id"] = (
                    f"{self.shard.get('shard_id')}-{self.call_count}-{case_index}"
                )
                case["description"] = f"验证独立业务行为：{tag}"
                case["steps"] = [f"执行独立操作：{tag}"]
                case["test_input"] = f"独立输入：{tag}"
                case["expected_result"] = f"独立结果：{tag}"
                cases.append(case)
            yield json.dumps(cases, ensure_ascii=False)

    coverage_rules = [
        {"rule_id": f"RULE-{index:02d}", "rule_text": f"覆盖规则 {index}"}
        for index in range(1, 17)
    ]
    chunks, state, serial_client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        parallel_config=ParallelShardConfig(
            enabled=True,
            max_workers=2,
            min_expected_count=25,
            min_coverage_rules=8,
            duplicate_rate_abort=0.5,
            min_unique_ratio=0.5,
        ),
        coverage_rules=coverage_rules,
        expected_count=50,
        batch_size=25,
        multi_pass=True,
        parallel_shard_client_factory=lambda shard: _AlwaysUniqueClient(shard),
    )

    assert execution_groups == [
        (1, 25, 0),
        (1, 1, 2),
        (1, 1, 3),
    ]
    assert state["primary_generation_failed"] is True
    assert state["primary_generation_failure"]["batch_index"] == 1
    assert state["primary_generation_failure"]["gap_count"] == 1
    assert state["stream_batch_quality_metrics"] == []
    assert serial_client.call_count == 0
    output = "".join(chunks)
    assert "PUBLIC_BATCH_UNDERFILLED_ABORT" in output
    assert "正在生成第 2/2 批次" not in output
    assert "Gap supplement" not in output
