from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from jsonschema import validate

from modules.agent_platform.test_generation_batching import (
    MAX_EVIDENCE_ACCOUNTING_BATCHES,
    MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS,
    MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH,
    TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS,
    _allocate_coverage_points,
    _allocate_risks,
    _batch_focus,
    _build_evidence_catalog_from_fragments,
    _coverage_points,
    _evidence_catalog_index,
    _raw_chunk_fragments,
    _select_module_evidence,
    build_planning_evidence_catalog,
    merge_evidence_accounting_batches,
    merge_grounded_generation_batches,
    merge_plan_evidence_routing,
    prepare_evidence_accounting_batches,
    prepare_test_case_batches,
)
from modules.agent_platform.test_generation_workflow import BATCH_PLAN_SCHEMA


def _authoritative_fact(*, fact_id: str, scope_id: str, assertion: str) -> dict:
    return {
        "fact_id": fact_id,
        "assertion": assertion,
        "scope_id": scope_id,
        "source_anchor": {
            "source_kind": "inline",
            "requirement_sha256": "a" * 64,
            "source_offset_start": 0,
            "source_offset_end": len(assertion),
            "quote": assertion,
        },
        "status": "effective",
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }


def _business_modules() -> list[dict]:
    return [
        {
            "name": "课程学习",
            "objective": "支持课程进入、学习内容展示和学习进度更新。",
            "lifecycle": "未学习->学习中->已完成",
            "evidence_ids": ["EV-0001"],
        },
        {
            "name": "作品审核",
            "objective": "支持作品投稿、审核、发布和退回。",
            "lifecycle": "未投稿->审核中->已发布/已退回",
            "evidence_ids": ["EV-0002"],
        },
    ]


def _generated_case(case_id: str, title: str, module: str) -> dict:
    return {
        "case_id": case_id,
        "title": title,
        "module": module,
        "priority": "P0",
        "preconditions": [],
        "steps": [{"action": "执行需求动作", "expected": "需求结果可验证"}],
        "tags": ["主流程"],
    }


def _generated_binding(case_id: str, fact_id: str) -> dict:
    return {
        "case_id": case_id,
        "precondition_bindings": [],
        "step_bindings": [
            {
                "step_index": 0,
                "action_fact_ids": [fact_id],
                "expected_fact_ids": [fact_id],
            }
        ],
    }


def test_merge_grounded_generation_batches_reindexes_and_keeps_fact_bindings() -> None:
    module = "课程学习"
    fact = _authoritative_fact(
        fact_id="FACT-001",
        scope_id="EV-0001",
        assertion="用户可以执行需求动作并验证结果",
    )
    inputs = [
        {
            "requirement": fact["assertion"],
            "plan": {"business_module": {"name": module}},
            "case_budget": 1,
            "batch": {"module_name": module},
            "authoritative_facts": [fact],
        },
        {
            "requirement": fact["assertion"],
            "plan": {"business_module": {"name": module}},
            "case_budget": 1,
            "batch": {"module_name": module},
            "authoritative_facts": [fact],
        },
    ]
    records = [
        {
            "item_index": 0,
            "output": {
                "test_cases": [_generated_case("TC-001", "进入课程", module)],
                "case_fact_bindings": [_generated_binding("TC-001", "FACT-001")],
            },
        },
        {
            "item_index": 1,
            "output": {
                "test_cases": [_generated_case("TC-001", "完成课程", module)],
                "case_fact_bindings": [_generated_binding("TC-001", "FACT-001")],
            },
        },
    ]
    context = SimpleNamespace(artifacts={})

    result = merge_grounded_generation_batches(
        context,
        {
            "generation_inputs": inputs,
            "generation_records": records,
            "case_budget": 2,
        },
    )

    assert [case["case_id"] for case in result["test_cases"]] == ["TC-001", "TC-002"]
    assert [item["case_id"] for item in result["case_fact_bindings"]] == [
        "TC-001",
        "TC-002",
    ]
    assert context.artifacts["grounded_generation_merge"]["case_count"] == 2


def test_merge_grounded_generation_batches_rejects_underfilled_batch() -> None:
    context = SimpleNamespace(artifacts={})
    with pytest.raises(ValueError, match="没有精确达到分配数量"):
        merge_grounded_generation_batches(
            context,
            {
                "generation_inputs": [
                    {
                        "plan": {"business_module": {"name": "课程学习"}},
                        "case_budget": 2,
                        "batch": {"module_name": "课程学习"},
                        "authoritative_facts": [
                            _authoritative_fact(
                                fact_id="FACT-001",
                                scope_id="EV-0001",
                                assertion="用户可以进入课程",
                            )
                        ],
                    }
                ],
                "generation_records": [
                    {
                        "item_index": 0,
                        "output": {
                            "test_cases": [
                                _generated_case("TC-001", "进入课程", "课程学习")
                            ],
                            "case_fact_bindings": [
                                _generated_binding("TC-001", "FACT-001")
                            ],
                        },
                    }
                ],
                "case_budget": 2,
            },
        )


def test_inline_requirement_builds_real_evidence_scope() -> None:
    requirement = "提交后进入审核中"
    source_hash = hashlib.sha256(requirement.encode("utf-8")).hexdigest()

    catalog = build_planning_evidence_catalog(
        source={"document_id": None, "content_hash": source_hash},
        requirement=requirement,
    )

    assert catalog == {
        "document_id": None,
        "items": [
            {
                "evidence_id": "EV-0001",
                "document_id": None,
                "chunk_index": 0,
                "biz_key": "",
                "text": requirement,
                "page_number": None,
                "block_ids": [],
                "source_offset_start": 0,
                "source_offset_end": len(requirement),
                "asset_source_sha256": source_hash,
                "continuation": None,
            }
        ],
    }


def test_coverage_focus_is_split_and_assigned_to_one_related_module() -> None:
    modules = _business_modules()
    allocated = _allocate_coverage_points(
        modules,
        [
            "主流程：课程进入与学习全流程、作品投稿至审核发布全流程",
            "异常路径：审核退回、网络异常",
        ],
    )

    assert allocated == [
        ["主流程：课程进入与学习全流程"],
        ["主流程：作品投稿至审核发布全流程", "异常路径：审核退回"],
    ]
    course_focus = _batch_focus(modules[0], allocated[0])
    review_focus = _batch_focus(modules[1], allocated[1])
    assert "作品投稿" not in course_focus
    assert "课程进入" not in review_focus
    assert "网络异常" not in course_focus
    assert "网络异常" not in review_focus


def test_coverage_focus_splits_multiple_modules_joined_by_chinese_commas() -> None:
    modules = _business_modules()

    allocated = _allocate_coverage_points(
        modules,
        "主流程：进入课程并完成学习，提交作品进入审核，审核通过后发布作品。",
    )

    assert allocated == [
        ["主流程：进入课程并完成学习"],
        ["主流程：提交作品进入审核", "主流程：审核通过后发布作品"],
    ]


def test_coverage_focus_keeps_parenthesized_enumeration_complete() -> None:
    points = _coverage_points(
        "权限约束：访客拦截入口（收藏、下载、历史记录、个人中心）、会员内容禁止访问",
    )

    assert points == [
        "权限约束：访客拦截入口（收藏、下载、历史记录、个人中心）",
        "权限约束：会员内容禁止访问",
    ]
    assert all(point.count("（") == point.count("）") for point in points)

    allocated = _allocate_coverage_points(
        [
            {
                "name": "内容记录",
                "objective": "展示历史记录与个人内容。",
                "lifecycle": None,
                "evidence_ids": ["EV-0001"],
            },
            {
                "name": "访客拦截",
                "objective": "拦截访客访问并引导登录。",
                "lifecycle": None,
                "evidence_ids": ["EV-0002"],
            },
        ],
        [points[0]],
    )
    assert allocated == [[], [points[0]]]


def test_risks_are_assigned_uniquely_and_ambiguous_risk_is_not_injected() -> None:
    modules = _business_modules()
    allocated = _allocate_risks(
        modules,
        [
            "学习进度状态更新失败会造成课程重复学习",
            "审核发布状态不一致会造成作品重复展示",
            "网络异常可能影响系统操作",
        ],
    )

    assert allocated == [
        ["学习进度状态更新失败会造成课程重复学习"],
        ["审核发布状态不一致会造成作品重复展示"],
    ]


def test_batch_plan_contract_accepts_evidence_ids_and_empty_risks() -> None:
    module = _business_modules()[0]

    validate(
        instance={
            "requirement_summary": "支持课程学习和作品审核。",
            "business_module": {**module, "actors": ["用户"]},
            "coverage_focus": _batch_focus(module, []),
            "risks": [],
        },
        schema=BATCH_PLAN_SCHEMA,
    )


def test_raw_chunk_is_not_split_by_document_specific_numbering() -> None:
    fragments = _raw_chunk_fragments(
        document_id=41,
        text=(
            "1. 课程学习\x01进入课程并完成学习进度更新\x01"
            "2. 作品审核\x01投稿后进入审核并发布作品"
        ),
        metadata={
            "chunk_index": 9,
            "biz_key": "",
            "page_number": 6,
            "block_ids": '["P0006-T0001"]',
            "source_offset_start": 12,
            "source_offset_end": 58,
            "asset_source_sha256": "a" * 64,
        },
    )

    assert len(fragments) == 1
    assert fragments[0]["document_id"] == 41
    assert fragments[0]["chunk_index"] == 9
    assert fragments[0]["biz_key"] == ""
    assert fragments[0]["page_number"] == 6
    assert fragments[0]["block_ids"] == ["P0006-T0001"]
    assert fragments[0]["source_offset_start"] == 12
    assert fragments[0]["source_offset_end"] == 58
    assert fragments[0]["asset_source_sha256"] == "a" * 64


def _fragment(*, chunk_index: int, page_number: int, text: str) -> dict:
    return {
        "document_id": 41,
        "chunk_index": chunk_index,
        "biz_key": f"page-{page_number}@page={page_number}",
        "text": text,
        "page_number": page_number,
        "block_ids": [f"P{page_number:04d}-T0001"],
        "source_offset_start": page_number * 100,
        "source_offset_end": page_number * 100 + len(text),
        "asset_source_sha256": "a" * 64,
    }


def _catalog(*fragments: dict) -> dict:
    return {
        "document_id": 41,
        "items": _build_evidence_catalog_from_fragments(list(fragments)),
    }


def test_evidence_catalog_ids_and_fields_are_stable_for_input_order() -> None:
    earlier = _fragment(chunk_index=1, page_number=2, text="课程学习真实正文。")
    later = _fragment(chunk_index=7, page_number=8, text="作品审核真实正文。")

    first = _build_evidence_catalog_from_fragments([later, earlier])
    second = _build_evidence_catalog_from_fragments([earlier, later])

    assert first == second
    assert [item["evidence_id"] for item in first] == ["EV-0001", "EV-0002"]
    assert first[0] == {
        "evidence_id": "EV-0001",
        "document_id": 41,
        "chunk_index": 1,
        "biz_key": "",
        "text": "课程学习真实正文。",
        "page_number": 2,
        "block_ids": ["P0002-T0001"],
        "source_offset_start": 200,
        "source_offset_end": 209,
        "asset_source_sha256": "a" * 64,
        "continuation": None,
    }


def test_catalog_rejects_duplicate_ids() -> None:
    catalog = _catalog(
        _fragment(chunk_index=1, page_number=2, text="课程学习真实正文。"),
        _fragment(chunk_index=2, page_number=3, text="作品审核真实正文。"),
    )
    catalog["items"][1]["evidence_id"] = "EV-0001"

    with pytest.raises(ValueError, match="证据目录包含重复 ID"):
        _evidence_catalog_index(catalog, source={"document_id": 41})


def test_module_rejects_unknown_evidence_id() -> None:
    catalog_index = _evidence_catalog_index(
        _catalog(_fragment(chunk_index=1, page_number=2, text="课程学习真实正文。")),
        source={"document_id": 41},
    )

    with pytest.raises(ValueError, match="未知证据 ID"):
        _select_module_evidence(
            module={"name": "课程学习", "evidence_ids": ["EV-9999"]},
            catalog_index=catalog_index,
        )


def test_module_rejects_duplicate_evidence_ids() -> None:
    catalog_index = _evidence_catalog_index(
        _catalog(_fragment(chunk_index=1, page_number=2, text="课程学习真实正文。")),
        source={"document_id": 41},
    )

    with pytest.raises(ValueError, match="模块 evidence_ids 包含重复 ID"):
        _select_module_evidence(
            module={"name": "课程学习", "evidence_ids": ["EV-0001", "EV-0001"]},
            catalog_index=catalog_index,
        )


def test_modules_restore_only_the_fragments_selected_by_ids() -> None:
    course = _fragment(chunk_index=1, page_number=2, text="课程学习真实正文。")
    review = _fragment(chunk_index=7, page_number=8, text="作品审核真实正文。")
    catalog = _catalog(review, course)
    catalog_index = _evidence_catalog_index(catalog, source={"document_id": 41})

    course_text, course_chunks = _select_module_evidence(
        module={"name": "课程学习", "evidence_ids": ["EV-0001"]},
        catalog_index=catalog_index,
    )
    review_text, review_chunks = _select_module_evidence(
        module={"name": "作品审核", "evidence_ids": ["EV-0002"]},
        catalog_index=catalog_index,
    )

    assert course_text == course["text"]
    assert review_text == review["text"]
    assert [item["page_number"] for item in course_chunks] == [2]
    assert [item["page_number"] for item in review_chunks] == [8]


def test_prepare_batches_routes_cross_module_evidence_only_by_ids() -> None:
    course = _fragment(chunk_index=1, page_number=2, text="课程学习真实正文。")
    review = _fragment(chunk_index=7, page_number=8, text="作品审核真实正文。")

    result = prepare_test_case_batches(
        SimpleNamespace(artifacts={}),
        {
            "plan": {
                "requirement_summary": "课程学习与作品审核。",
                "business_modules": [
                    {
                        "name": "课程学习",
                        "objective": "完成课程学习",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "evidence_ids": ["EV-0001"],
                    },
                    {
                        "name": "作品审核",
                        "objective": "完成作品审核",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "evidence_ids": ["EV-0002"],
                    },
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": [
                _authoritative_fact(
                    fact_id="F-001",
                    scope_id="EV-0001",
                    assertion=course["text"],
                ),
                _authoritative_fact(
                    fact_id="F-002",
                    scope_id="EV-0002",
                    assertion=review["text"],
                ),
            ],
            "case_budget": 2,
            "batch_case_limit": 1,
        },
    )

    assert [item["requirement"] for item in result["items"]] == [
        course["text"],
        review["text"],
    ]
    assert [item["authoritative_facts"][0]["fact_id"] for item in result["items"]] == [
        "F-001",
        "F-002",
    ]


def test_same_evidence_id_can_be_reused_across_batches() -> None:
    shared = _fragment(chunk_index=3, page_number=4, text="共享真实正文。")
    catalog_index = _evidence_catalog_index(
        _catalog(shared),
        source={"document_id": 41},
    )
    module = {"name": "共享模块", "evidence_ids": ["EV-0001"]}

    assert _select_module_evidence(
        module=module,
        catalog_index=catalog_index,
    ) == _select_module_evidence(
        module=module,
        catalog_index=catalog_index,
    )


def _draft_plan() -> dict:
    return {
        "requirement_summary": "课程学习与作品审核。",
        "business_modules": [
            {
                "name": "课程学习",
                "objective": "完成课程学习",
                "actors": ["用户"],
                "lifecycle": None,
            },
            {
                "name": "作品审核",
                "objective": "完成作品审核",
                "actors": ["用户"],
                "lifecycle": "待审核->已发布",
            },
        ],
        "coverage_focus": ["覆盖课程学习", "覆盖作品审核"],
        "risks": ["审核状态不一致"],
    }


def _routing_catalog() -> dict:
    return _catalog(
        _fragment(chunk_index=1, page_number=2, text="课程学习真实正文。"),
        _fragment(chunk_index=7, page_number=8, text="作品审核真实正文。"),
        _fragment(chunk_index=9, page_number=10, text="需求文档通用背景。"),
    )


def _reviewed_routing(
    routes: list[dict],
    *,
    accounting: list[dict] | None = None,
) -> dict:
    if accounting is None:
        module_indexes_by_id = {
            "EV-0001": [],
            "EV-0002": [],
            "EV-0003": [],
        }
        for route in routes:
            module_index = route.get("module_index")
            for evidence_id in route.get("evidence_ids") or []:
                if evidence_id in module_indexes_by_id:
                    module_indexes_by_id[evidence_id].append(module_index)
        accounting = [
            {
                "evidence_id": evidence_id,
                "module_indexes": module_indexes,
                "disposition": "assigned" if module_indexes else "context_only",
                "reason": (
                    "直接支持已列出的业务模块"
                    if module_indexes
                    else "仅提供需求整体背景，不直接支持任一模块"
                ),
            }
            for evidence_id, module_indexes in module_indexes_by_id.items()
        ]
    return {"evidence_accounting": accounting}


def _map_record(item_index: int, prepared: dict, output: dict) -> dict:
    encoded = json.dumps(
        prepared,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "item_index": item_index,
        "input_hash": hashlib.sha256(encoded).hexdigest(),
        "output": output,
    }


def test_prepare_evidence_accounting_batches_obeys_text_and_item_budgets() -> None:
    catalog = _catalog(
        *[
            _fragment(
                chunk_index=index,
                page_number=index,
                text=str(index) * 1800,
            )
            for index in range(1, 10)
        ]
    )

    result = prepare_evidence_accounting_batches(
        SimpleNamespace(artifacts={}),
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": catalog,
        },
    )

    assert result["batch_count"] == 3
    assert result["evidence_count"] == 9
    assert [
        [item["evidence_id"] for item in batch["target_evidence_items"]]
        for batch in result["items"]
    ] == [
        ["EV-0001", "EV-0002", "EV-0003"],
        ["EV-0004", "EV-0005", "EV-0006"],
        ["EV-0007", "EV-0008", "EV-0009"],
    ]
    for batch in result["items"]:
        assert len(batch["target_evidence_items"]) <= MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH
        assert sum(
            len(item["text"]) for item in batch["target_evidence_items"]
        ) <= TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS
        assert batch["draft_plan"] == _draft_plan()
        assert set(batch) == {
            "draft_plan",
            "target_evidence_items",
            "neighbor_context",
        }

    middle_neighbors = result["items"][1]["neighbor_context"]
    assert [item["relative_position"] for item in middle_neighbors] == [
        "previous",
        "next",
    ]
    assert [item["evidence_id"] for item in middle_neighbors] == [
        "EV-0003",
        "EV-0007",
    ]
    assert [item["page_number"] for item in middle_neighbors] == [3, 7]
    assert [item["chunk_index"] for item in middle_neighbors] == [3, 7]
    assert middle_neighbors[0]["text"] == "3" * 600
    assert middle_neighbors[1]["text"] == "7" * 600
    assert all(item["text_truncated"] is True for item in middle_neighbors)
    assert sum(len(item["text"]) for item in middle_neighbors) == (
        MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS
    )
    assert result["items"][0]["target_evidence_items"] == catalog["items"][:3]


def test_prepare_evidence_accounting_batches_keeps_four_short_targets_per_batch() -> None:
    catalog = _catalog(
        *[
            _fragment(chunk_index=index, page_number=index, text=f"证据{index}")
            for index in range(1, 6)
        ]
    )

    result = prepare_evidence_accounting_batches(
        SimpleNamespace(artifacts={}),
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": catalog,
        },
    )

    assert [len(item["target_evidence_items"]) for item in result["items"]] == [4, 1]


def test_prepare_evidence_accounting_batches_rejects_upstream_forwarding() -> None:
    catalog = _routing_catalog()

    with pytest.raises(ValueError, match="只允许 draft_plan 和 evidence_catalog"):
        prepare_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": catalog,
                "upstream_assignments": [],
            },
        )


def test_prepare_evidence_accounting_batches_rejects_oversized_single_item() -> None:
    catalog = _catalog(
        _fragment(
            chunk_index=1,
            page_number=1,
            text="x" * (TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS + 1),
        ),
        _fragment(chunk_index=2, page_number=2, text="第二个模块证据"),
    )

    with pytest.raises(ValueError, match="单条证据正文超过"):
        prepare_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": catalog,
            },
        )


def test_prepare_evidence_accounting_batches_rejects_agent_map_overflow() -> None:
    catalog = _catalog(
        *[
            _fragment(chunk_index=index, page_number=index, text=f"证据{index}")
            for index in range(
                1,
                MAX_EVIDENCE_ACCOUNTING_BATCHES
                * MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH
                + 2,
            )
        ]
    )

    with pytest.raises(ValueError, match="分片数超过 agent_map 上限"):
        prepare_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": catalog,
            },
        )


def _prepared_accounting_fixture() -> dict:
    catalog = _routing_catalog()
    return prepare_evidence_accounting_batches(
        SimpleNamespace(artifacts={}),
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": catalog,
        },
    )


def _accounting_output(prepared: dict) -> dict:
    return {
        "evidence_accounting": [
            {
                "evidence_id": target["evidence_id"],
                "module_indexes": (
                    []
                    if target["evidence_id"] == "EV-0003"
                    else [(int(target["evidence_id"].split("-")[1]) - 1) % 2]
                ),
                "disposition": (
                    "context_only"
                    if target["evidence_id"] == "EV-0003"
                    else "assigned"
                ),
                "reason": (
                    "仅作为全局背景"
                    if target["evidence_id"] == "EV-0003"
                    else "Reviewer 直接判断支持已有模块"
                ),
            }
            for target in prepared["target_evidence_items"]
        ]
    }


def test_merge_evidence_accounting_batches_binds_records_and_catalog_order() -> None:
    catalog = _catalog(
        *[
            _fragment(chunk_index=index, page_number=index, text=f"证据{index}")
            for index in range(1, 6)
        ]
    )
    prepared = prepare_evidence_accounting_batches(
        SimpleNamespace(artifacts={}),
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": catalog,
        },
    )["items"]
    records = [
        _map_record(index, item, _accounting_output(item))
        for index, item in enumerate(prepared)
    ]

    merged = merge_evidence_accounting_batches(
        SimpleNamespace(artifacts={}),
        {
            "prepared_items": prepared,
            "routing_records": list(reversed(records)),
        },
    )

    assert [item["evidence_id"] for item in merged["evidence_accounting"]] == [
        "EV-0001",
        "EV-0002",
        "EV-0003",
        "EV-0004",
        "EV-0005",
    ]


def test_merge_evidence_accounting_batches_rejects_input_hash_mismatch() -> None:
    prepared = _prepared_accounting_fixture()["items"]
    record = _map_record(0, prepared[0], _accounting_output(prepared[0]))
    record["input_hash"] = "0" * 64

    with pytest.raises(ValueError, match="输入指纹不一致"):
        merge_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {"prepared_items": prepared, "routing_records": [record]},
        )


def test_merge_evidence_accounting_batches_rejects_forwarded_fields() -> None:
    prepared = _prepared_accounting_fixture()["items"]
    prepared[0]["upstream_assignments"] = []
    record = _map_record(0, prepared[0], _accounting_output(prepared[0]))

    with pytest.raises(ValueError, match="包含未允许字段"):
        merge_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {"prepared_items": prepared, "routing_records": [record]},
        )


def test_merge_evidence_accounting_batches_rejects_neighbor_output() -> None:
    catalog = _catalog(
        *[
            _fragment(chunk_index=index, page_number=index, text=f"证据{index}")
            for index in range(1, 6)
        ]
    )
    prepared = prepare_evidence_accounting_batches(
        SimpleNamespace(artifacts={}),
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": catalog,
        },
    )["items"]
    output = _accounting_output(prepared[0])
    output["evidence_accounting"].append(
        {
            "evidence_id": prepared[0]["neighbor_context"][0]["evidence_id"],
            "module_indexes": [],
            "disposition": "context_only",
            "reason": "错误输出了只读邻接项",
        }
    )
    records = [
        _map_record(0, prepared[0], output),
        _map_record(1, prepared[1], _accounting_output(prepared[1])),
    ]

    with pytest.raises(ValueError, match="不得记账 neighbor_context"):
        merge_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {"prepared_items": prepared, "routing_records": records},
        )


def test_merge_evidence_accounting_batches_requires_exact_target_set() -> None:
    prepared = _prepared_accounting_fixture()["items"]
    output = _accounting_output(prepared[0])
    output["evidence_accounting"].pop()

    with pytest.raises(ValueError, match="未严格覆盖目标 ID 全集"):
        merge_evidence_accounting_batches(
            SimpleNamespace(artifacts={}),
            {
                "prepared_items": prepared,
                "routing_records": [_map_record(0, prepared[0], output)],
            },
        )


def test_merge_plan_evidence_routing_attaches_ids_by_module_index() -> None:
    context = SimpleNamespace(artifacts={})

    merged = merge_plan_evidence_routing(
        context,
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": _routing_catalog(),
            "routing": _reviewed_routing(
                [
                    {"module_index": 1, "evidence_ids": ["EV-0002"]},
                    {"module_index": 0, "evidence_ids": ["EV-0001"]},
                ]
            ),
        },
    )

    assert merged["business_modules"][0]["evidence_ids"] == ["EV-0001"]
    assert merged["business_modules"][1]["evidence_ids"] == ["EV-0002"]
    assert merged["requirement_summary"] == "课程学习与作品审核。"
    assert merged["coverage_focus"] == ["覆盖课程学习", "覆盖作品审核"]
    assert merged["risks"] == ["审核状态不一致"]
    assert context.artifacts["evidence_routing"] == {
        "document_id": 41,
        "module_count": 2,
        "evidence_dispositions": {
            "assigned": {
                "count": 2,
                "evidence_ids": ["EV-0001", "EV-0002"],
            },
            "context_only": {
                "count": 1,
                "evidence_ids": ["EV-0003"],
            },
        },
        "module_routes": [
            {
                "module_index": 0,
                "module_name": "课程学习",
                "evidence_ids": ["EV-0001"],
            },
            {
                "module_index": 1,
                "module_name": "作品审核",
                "evidence_ids": ["EV-0002"],
            },
        ],
    }


def test_merge_plan_evidence_routing_normalizes_ids_by_catalog_order() -> None:
    context = SimpleNamespace(artifacts={})
    routes = [
        {"module_index": 0, "evidence_ids": ["EV-0002", "EV-0001"]},
        {"module_index": 1, "evidence_ids": ["EV-0002"]},
    ]

    merged = merge_plan_evidence_routing(
        context,
        {
            "draft_plan": _draft_plan(),
            "evidence_catalog": _routing_catalog(),
            "routing": _reviewed_routing(routes),
        },
    )

    assert merged["business_modules"][0]["evidence_ids"] == [
        "EV-0001",
        "EV-0002",
    ]
    assert context.artifacts["evidence_routing"]["module_routes"][0][
        "evidence_ids"
    ] == ["EV-0001", "EV-0002"]


def test_merge_plan_evidence_routing_rejects_module_without_assigned_evidence() -> None:
    with pytest.raises(ValueError, match="证据总账未完整覆盖业务模块"):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(
                    [
                        {"module_index": 0, "evidence_ids": ["EV-0001"]},
                        {"module_index": 1, "evidence_ids": []},
                    ]
                ),
            },
        )


def test_merge_plan_evidence_routing_rejects_incomplete_module_coverage() -> None:
    with pytest.raises(ValueError, match="未完整覆盖业务模块"):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(
                    [
                        {"module_index": 0, "evidence_ids": ["EV-0001"]}
                    ]
                ),
            },
        )


@pytest.mark.parametrize(
    ("accounting", "message"),
    [
        (
            [
                {
                    "evidence_id": "EV-0001",
                    "module_indexes": [0],
                    "disposition": "assigned",
                    "reason": "直接支持模块",
                },
                {
                    "evidence_id": "EV-0002",
                    "module_indexes": [1],
                    "disposition": "assigned",
                    "reason": "直接支持模块",
                },
            ],
            "未完整覆盖证据目录",
        ),
        (
            [
                {
                    "evidence_id": "EV-0001",
                    "module_indexes": [0],
                    "disposition": "assigned",
                    "reason": "直接支持模块",
                },
                {
                    "evidence_id": "EV-0001",
                    "module_indexes": [0],
                    "disposition": "assigned",
                    "reason": "重复记账",
                },
                {
                    "evidence_id": "EV-0002",
                    "module_indexes": [1],
                    "disposition": "assigned",
                    "reason": "直接支持模块",
                },
            ],
            "包含重复证据 ID",
        ),
        (
            [
                {
                    "evidence_id": "EV-0001",
                    "module_indexes": [0],
                    "disposition": "assigned",
                    "reason": "直接支持模块",
                },
                {
                    "evidence_id": "EV-0002",
                    "module_indexes": [1],
                    "disposition": "assigned",
                    "reason": "直接支持模块",
                },
                {
                    "evidence_id": "EV-9999",
                    "module_indexes": [],
                    "disposition": "context_only",
                    "reason": "目录外证据",
                },
            ],
            "包含未知证据 ID",
        ),
    ],
)
def test_merge_plan_evidence_routing_requires_exact_catalog_accounting(
    accounting: list[dict],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(
                    [
                        {"module_index": 0, "evidence_ids": ["EV-0001"]},
                        {"module_index": 1, "evidence_ids": ["EV-0002"]},
                    ],
                    accounting=accounting,
                ),
            },
        )


@pytest.mark.parametrize(
    ("module_indexes", "message"),
    [
        ([2], "包含越界 module_index"),
        ([0, 0], "包含重复下标"),
        (["0"], "只能包含整数"),
    ],
)
def test_merge_plan_evidence_routing_validates_accounted_module_indexes(
    module_indexes: list,
    message: str,
) -> None:
    accounting = _reviewed_routing(
        [
            {"module_index": 0, "evidence_ids": ["EV-0001"]},
            {"module_index": 1, "evidence_ids": ["EV-0002"]},
        ]
    )["evidence_accounting"]
    accounting[0]["module_indexes"] = module_indexes

    with pytest.raises(ValueError, match=message):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(
                    [
                        {"module_index": 0, "evidence_ids": ["EV-0001"]},
                        {"module_index": 1, "evidence_ids": ["EV-0002"]},
                    ],
                    accounting=accounting,
                ),
            },
        )


@pytest.mark.parametrize("reason", ["  ", "证" * 161])
def test_merge_plan_evidence_routing_validates_accounting_reason(reason: str) -> None:
    routes = [
        {"module_index": 0, "evidence_ids": ["EV-0001"]},
        {"module_index": 1, "evidence_ids": ["EV-0002"]},
    ]
    accounting = _reviewed_routing(routes)["evidence_accounting"]
    accounting[2]["reason"] = reason

    with pytest.raises(ValueError, match="reason 必须是 1 至 160 字的字符串"):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(routes, accounting=accounting),
            },
        )


@pytest.mark.parametrize(
    ("disposition", "module_indexes", "message"),
    [
        ("assigned", [], "assigned 证据必须包含至少一个 module_index"),
        ("context_only", [0], "context_only 证据的 module_indexes 必须为空"),
        ("plan_gap", [0], "plan_gap 证据的 module_indexes 必须为空"),
        ("ignored", [], "disposition 无效"),
    ],
)
def test_merge_plan_evidence_routing_validates_disposition_contract(
    disposition: str,
    module_indexes: list[int],
    message: str,
) -> None:
    routes = [
        {"module_index": 0, "evidence_ids": ["EV-0001"]},
        {"module_index": 1, "evidence_ids": ["EV-0002"]},
    ]
    accounting = _reviewed_routing(routes)["evidence_accounting"]
    accounting[2].update(
        {
            "disposition": disposition,
            "module_indexes": module_indexes,
        }
    )

    with pytest.raises(ValueError, match=message):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(routes, accounting=accounting),
            },
        )


def test_merge_plan_evidence_routing_blocks_plan_gap_with_reason() -> None:
    routes = [
        {"module_index": 0, "evidence_ids": ["EV-0001"]},
        {"module_index": 1, "evidence_ids": ["EV-0002"]},
    ]
    accounting = _reviewed_routing(routes)["evidence_accounting"]
    accounting[2].update(
        {
            "disposition": "plan_gap",
            "module_indexes": [],
            "reason": "该证据描述独立可测试能力，但当前规划没有对应模块",
        }
    )

    with pytest.raises(
        ValueError,
        match="业务规划缺口.*EV-0003.*当前规划没有对应模块",
    ):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _draft_plan(),
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing(routes, accounting=accounting),
            },
        )


def test_merge_plan_evidence_routing_rejects_prefilled_draft_ids() -> None:
    draft_plan = _draft_plan()
    draft_plan["business_modules"][0]["evidence_ids"] = ["EV-0001"]

    with pytest.raises(ValueError, match="不能预置 evidence_ids"):
        merge_plan_evidence_routing(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": draft_plan,
                "evidence_catalog": _routing_catalog(),
                "routing": _reviewed_routing([]),
            },
        )


def test_authoritative_facts_cannot_replace_empty_module_scope_ids() -> None:
    with pytest.raises(ValueError, match="evidence_ids 必须是非空数组"):
        prepare_test_case_batches(
            SimpleNamespace(artifacts={}),
            {
                "plan": {
                    "requirement_summary": "课程入口需求",
                    "business_modules": [
                        {
                            "name": "课程入口",
                            "objective": "进入课程并查看学习内容",
                            "actors": ["用户"],
                            "lifecycle": None,
                            "evidence_ids": [],
                        }
                    ],
                    "coverage_focus": ["进入课程"],
                    "risks": [],
                },
                "effective_facts": [
                    _authoritative_fact(
                        fact_id="F-001",
                        scope_id="EV-0001",
                        assertion="页面展示进入课程入口",
                    )
                ],
                "case_budget": 1,
                "batch_case_limit": 1,
            },
        )


def test_batch_fails_when_evidence_ids_are_empty() -> None:
    with pytest.raises(ValueError, match="evidence_ids 必须是非空数组"):
        prepare_test_case_batches(
            SimpleNamespace(artifacts={}),
            {
                "plan": {
                    "requirement_summary": "课程入口需求",
                    "business_modules": [
                        {
                            "name": "课程入口",
                            "objective": "进入课程并查看学习内容",
                            "actors": ["用户"],
                            "lifecycle": None,
                            "evidence_ids": [],
                        }
                    ],
                    "coverage_focus": [],
                    "risks": [],
                },
                "effective_facts": [
                    _authoritative_fact(
                        fact_id="F-001",
                        scope_id="EV-0001",
                        assertion="页面展示进入课程入口",
                    )
                ],
                "case_budget": 1,
                "batch_case_limit": 1,
            },
        )
