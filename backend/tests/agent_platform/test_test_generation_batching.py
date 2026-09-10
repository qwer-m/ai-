from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace

import pytest
from jsonschema import validate

from modules.agent_platform.test_generation_batching import (
    GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH,
    GENERATION_MAX_PAGES_PER_BATCH,
    GENERATION_MAX_REQUIRED_FACTS_PER_BATCH,
    _allocate_coverage_points,
    _allocate_risks,
    _allocate_test_design_items_to_fact_groups,
    _batch_focus,
    _build_case_fact_contract,
    _build_evidence_catalog_from_fragments,
    _coverage_points,
    _raw_chunk_fragments,
    build_planning_evidence_catalog,
    merge_grounded_generation_batches,
    postprocess_generation_batch_item,
    prepare_test_case_batches,
)
from modules.agent_platform.test_generation_workflow import (
    BATCH_PLAN_SCHEMA,
    GENERATION_BATCH_ITEM_SCHEMA,
    GROUNDING_SCHEMA,
    MODEL_GROUNDING_SCHEMA,
)


def _authoritative_fact(
    *,
    fact_id: str,
    scope_id: str,
    assertion: str,
    page_number: int | None = None,
) -> dict:
    source_anchor = {
        "source_kind": "inline",
        "requirement_sha256": "a" * 64,
        "source_offset_start": 0,
        "source_offset_end": len(assertion),
        "quote": assertion,
    }
    if page_number is not None:
        source_anchor = {
            "source_kind": "document",
            "document_id": 1,
            "page_number": page_number,
            "block_id": f"P{page_number:04d}-T0001",
            "source_span": {"start": 0, "end": len(assertion)},
            "quote": assertion,
            "asset_source_sha256": "b" * 64,
            "page_image_sha256": "c" * 64,
        }
    return {
        "fact_id": fact_id,
        "assertion": assertion,
        "scope_id": scope_id,
        "source_anchor": source_anchor,
        "status": "effective",
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }


def _test_points(name: str = "核心流程") -> list[dict]:
    return [
        {
            "name": name,
            "objective": f"验证{name}",
            "test_designs": [
                {
                    "technique": "场景法",
                    "rationale": "覆盖需求明确声明的业务流程",
                    "coverage_items": [name],
                }
            ],
        }
    ]


def _fact_design_routes(fact_ids: list[str]) -> list[dict]:
    return [
        {"fact_id": fact_id, "test_design_item_indexes": [0]}
        for fact_id in fact_ids
    ]


def _business_modules() -> list[dict]:
    return [
        {
            "name": "课程学习",
            "objective": "支持课程进入、学习内容展示和学习进度更新。",
            "actors": ["用户"],
            "lifecycle": "未学习->学习中->已完成",
            "test_points": _test_points("课程学习"),
            "evidence_ids": ["EV-0001"],
            "fact_ids": ["F-001"],
            "fact_design_routes": _fact_design_routes(["F-001"]),
        },
        {
            "name": "作品审核",
            "objective": "支持作品投稿、审核、发布和退回。",
            "actors": ["用户"],
            "lifecycle": "未投稿->审核中->已发布/已退回",
            "test_points": _test_points("作品审核"),
            "evidence_ids": ["EV-0002"],
            "fact_ids": ["F-002"],
            "fact_design_routes": _fact_design_routes(["F-002"]),
        },
    ]


def _generated_case(case_id: str, title: str, module: str) -> dict:
    return {
        "case_id": case_id,
        "title": title,
        "module": module,
        "priority": "P0",
        "preconditions": [],
        "test_input": "角色=用户",
        "steps": [{"action": "执行需求动作", "expected": "需求结果可验证"}],
        "tags": ["主流程"],
        "test_design_item_ids": [],
    }


def _generated_binding(case_id: str, fact_id: str) -> dict:
    return {
        "case_id": case_id,
        "precondition_bindings": [],
        "test_input_fact_ids": [fact_id],
        "step_bindings": [
            {
                "step_index": 0,
                "action_fact_ids": [fact_id],
                "expected_fact_ids": [fact_id],
            }
        ],
    }


def _inline_grounding_output(
    output: dict,
    *,
    include_tags: bool = True,
) -> dict:
    bindings_by_case_id = {
        binding["case_id"]: binding for binding in output["case_fact_bindings"]
    }
    inline_cases: list[dict] = []
    for case in output["test_cases"]:
        binding = bindings_by_case_id[case["case_id"]]
        preconditions_by_index = {
            item["precondition_index"]: item
            for item in binding["precondition_bindings"]
        }
        steps_by_index = {
            item["step_index"]: item for item in binding["step_bindings"]
        }
        inline_case = {
            "title": case["title"],
            "priority": case["priority"],
            "preconditions": [
                {
                    "text": text,
                    "fact_ids": preconditions_by_index[index]["fact_ids"],
                }
                for index, text in enumerate(case["preconditions"])
            ],
            "test_input": {
                "text": case["test_input"],
                "fact_ids": binding["test_input_fact_ids"],
            },
            "steps": [
                {
                    "action": step["action"],
                    "expected": step["expected"],
                    "fact_bindings": {
                        "action": steps_by_index[index]["action_fact_ids"],
                        "expected": steps_by_index[index]["expected_fact_ids"],
                    },
                }
                for index, step in enumerate(case["steps"])
            ],
            "test_design_item_ids": list(case["test_design_item_ids"]),
        }
        if include_tags:
            inline_case["tags"] = list(case.get("tags") or [])
        inline_cases.append(inline_case)
    return {"test_cases": inline_cases}


def test_design_items_follow_relevant_facts_across_batch_boundaries() -> None:
    design_items = [
        {
            "test_design_item_id": "TD-001-001-001",
            "module_index": 0,
            "module_name": "订单处理",
            "test_point": "支付与积分",
            "technique": "场景法",
            "rationale": "覆盖订单支付到积分发放的完整流程",
            "coverage_intent": "订单支付成功后发放积分",
        },
        {
            "test_design_item_id": "TD-001-002-001",
            "module_index": 0,
            "module_name": "订单处理",
            "test_point": "订单查询",
            "technique": "等价类",
            "rationale": "覆盖不同订单状态的查询结果",
            "coverage_intent": "订单列表按状态筛选",
        },
    ]
    fact_groups = [
        [
            _authoritative_fact(
                fact_id="F-001",
                scope_id="EV-0001",
                assertion="订单支持在线支付",
            )
        ],
        [
            _authoritative_fact(
                fact_id="F-002",
                scope_id="EV-0002",
                assertion="支付完成后向用户发放积分",
            )
        ],
        [
            _authoritative_fact(
                fact_id="F-003",
                scope_id="EV-0003",
                assertion="订单列表支持按订单状态筛选",
            )
        ],
        [
            _authoritative_fact(
                fact_id="F-004",
                scope_id="EV-0004",
                assertion="导出订单入口不再提供",
            )
        ],
    ]

    allocated = _allocate_test_design_items_to_fact_groups(
        design_items=design_items,
        fact_groups=fact_groups,
        fact_design_routes=[
            {"fact_id": "F-001", "test_design_item_indexes": [0]},
            {"fact_id": "F-002", "test_design_item_indexes": [0]},
            {"fact_id": "F-003", "test_design_item_indexes": [1]},
            {"fact_id": "F-004", "test_design_item_indexes": []},
        ],
    )

    allocated_ids = [
        [item["test_design_item_id"] for item in group]
        for group in allocated
    ]
    assert allocated_ids == [
        ["TD-001-001-001"],
        ["TD-001-001-001"],
        ["TD-001-002-001"],
        [],
    ]
    assert all(len(group) == len(set(group)) for group in allocated_ids)


def _case_fact_contract(case_count: int, *fact_ids: str) -> dict:
    target_case_ids = [f"TC-{index:03d}" for index in range(1, case_count + 1)]
    facts_by_case = [[] for _ in target_case_ids]
    for index, fact_id in enumerate(fact_ids):
        facts_by_case[index % case_count].append(fact_id)
    for index, assigned in enumerate(facts_by_case):
        if not assigned:
            assigned.append(fact_ids[index % len(fact_ids)])
    return {
        "target_case_ids": target_case_ids,
        "required_fact_ids": list(fact_ids),
        "required_test_design_item_ids": [],
        "coverage_slots": [
            {
                "case_id": case_id,
                "required_fact_ids": facts_by_case[index],
                "required_test_design_item_ids": [],
            }
            for index, case_id in enumerate(target_case_ids)
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
            "case_fact_contract": _case_fact_contract(1, "FACT-001"),
        },
        {
            "requirement": fact["assertion"],
            "plan": {"business_module": {"name": module}},
            "case_budget": 1,
            "batch": {"module_name": module},
            "authoritative_facts": [fact],
            "case_fact_contract": _case_fact_contract(1, "FACT-001"),
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


def test_generation_item_postprocessor_rejects_wrong_count_and_normalizes_bindings() -> None:
    module = "课程学习"
    fact = _authoritative_fact(
        fact_id="FACT-001",
        scope_id="EV-0001",
        assertion="用户可以进入课程并看到课程内容",
    )
    item_input = {
        "plan": {"business_module": {"name": module}},
        "case_budget": 1,
        "batch": {"module_name": module},
        "authoritative_facts": [fact],
        "case_fact_contract": _case_fact_contract(1, "FACT-001"),
    }
    valid_output = {
        "test_cases": [_generated_case("TC-001", "进入课程", module)],
        "case_fact_bindings": [_generated_binding("TC-001", "FACT-001")],
    }

    inline_output = _inline_grounding_output(valid_output)
    validate(instance=inline_output, schema=MODEL_GROUNDING_SCHEMA)
    normalized = postprocess_generation_batch_item(
        SimpleNamespace(artifacts={}),
        {
            "item_input": item_input,
            "item_output": inline_output,
        },
    )
    assert normalized == valid_output
    validate(instance=normalized, schema=GROUNDING_SCHEMA)

    normalized_without_tags = postprocess_generation_batch_item(
        SimpleNamespace(artifacts={}),
        {
            "item_input": item_input,
            "item_output": _inline_grounding_output(
                valid_output,
                include_tags=False,
            ),
        },
    )
    assert normalized_without_tags["test_cases"][0]["tags"] == []

    with pytest.raises(ValueError, match="模型用例数量与平台编号契约不一致"):
        postprocess_generation_batch_item(
            SimpleNamespace(artifacts={}),
            {
                "item_input": item_input,
                "item_output": {
                    "test_cases": _inline_grounding_output(valid_output)["test_cases"]
                    * 2,
                },
            },
        )


def test_generation_item_postprocessor_derives_design_ids_from_fact_bindings() -> None:
    module = "课程学习"
    design_id = "TD-001-001-001"
    fact = _authoritative_fact(
        fact_id="FACT-001",
        scope_id="EV-0001",
        assertion="用户可以进入课程并看到课程内容",
    )
    case = _generated_case("TC-001", "进入课程", module)
    case["test_design_item_ids"] = []
    inline_output = _inline_grounding_output(
        {
            "test_cases": [case],
            "case_fact_bindings": [_generated_binding("TC-001", "FACT-001")],
        }
    )
    inline_output["test_cases"][0].pop("test_design_item_ids")
    item_input = {
        "plan": {
            "business_module": {"name": module},
            "test_design_items": [{"test_design_item_id": design_id}],
        },
        "case_budget": 1,
        "batch": {
            "module_name": module,
            "required_test_design_item_ids": [design_id],
        },
        "authoritative_facts": [fact],
        "case_fact_contract": {
            "target_case_ids": ["TC-001"],
            "required_fact_ids": ["FACT-001"],
            "required_test_design_item_ids": [design_id],
            "fact_design_item_ids": {"FACT-001": [design_id]},
            "coverage_slots": [
                {
                    "case_id": "TC-001",
                    "required_fact_ids": ["FACT-001"],
                    "required_test_design_item_ids": [design_id],
                }
            ],
        },
    }

    normalized = postprocess_generation_batch_item(
        SimpleNamespace(artifacts={}),
        {"item_input": item_input, "item_output": inline_output},
    )

    assert normalized["test_cases"][0]["test_design_item_ids"] == [design_id]


def test_generation_item_postprocessor_requires_planned_test_design_coverage() -> None:
    module = "课程学习"
    fact = _authoritative_fact(
        fact_id="FACT-001",
        scope_id="EV-0001",
        assertion="用户可以进入课程并看到课程内容",
    )
    missing_fact = _authoritative_fact(
        fact_id="FACT-002",
        scope_id="EV-0001",
        assertion="课程入口支持返回课程列表",
    )
    design_item = {
        "test_design_item_id": "TD-001-001-001",
        "module_index": 0,
        "module_name": module,
        "test_point": "课程入口",
        "technique": "场景法",
        "rationale": "覆盖课程入口主流程",
        "coverage_intent": "用户进入课程并看到课程内容",
    }
    item_input = {
        "plan": {
            "business_module": {"name": module},
            "test_design_items": [design_item],
        },
        "case_budget": 1,
        "batch": {
            "module_name": module,
            "required_test_design_item_ids": ["TD-001-001-001"],
        },
        "authoritative_facts": [fact, missing_fact],
        "case_fact_contract": {
            **_case_fact_contract(1, "FACT-001", "FACT-002"),
            "required_test_design_item_ids": ["TD-001-001-001"],
            "coverage_slots": [
                {
                    "case_id": "TC-001",
                    "required_fact_ids": ["FACT-001", "FACT-002"],
                    "required_test_design_item_ids": ["TD-001-001-001"],
                }
            ],
        },
    }
    case = _generated_case("TC-001", "进入课程", module)
    output = {
        "test_cases": [case],
        "case_fact_bindings": [_generated_binding("TC-001", "FACT-001")],
    }

    with pytest.raises(ValueError) as exc_info:
        postprocess_generation_batch_item(
            SimpleNamespace(artifacts={}),
            {
                "item_input": item_input,
                "item_output": _inline_grounding_output(output),
            },
        )
    feedback = str(exc_info.value)
    assert "生成批次未完整覆盖平台要求的事实" in feedback
    assert "FACT-002" in feedback
    assert "生成批次测试设计覆盖不符合平台契约" in feedback
    assert "TD-001-001-001" in feedback

    case["test_design_item_ids"] = ["TD-001-001-001"]
    output["case_fact_bindings"][0]["step_bindings"][0]["expected_fact_ids"].append(
        "FACT-002"
    )
    normalized = postprocess_generation_batch_item(
        SimpleNamespace(artifacts={}),
        {
            "item_input": item_input,
            "item_output": _inline_grounding_output(output),
        },
    )
    assert normalized["test_cases"][0]["test_design_item_ids"] == [
        "TD-001-001-001"
    ]


def test_generation_item_postprocessor_accepts_multiple_grounded_cases() -> None:
    module = "课程学习"
    facts = [
        _authoritative_fact(
            fact_id="FACT-001",
            scope_id="EV-0001",
            assertion="用户可以进入课程",
        ),
        _authoritative_fact(
            fact_id="FACT-002",
            scope_id="EV-0001",
            assertion="课程展示学习内容",
        ),
    ]
    output = {
        "test_cases": [
            _generated_case("TC-001", "进入课程", module),
            _generated_case("TC-002", "查看课程内容", module),
        ],
        "case_fact_bindings": [
            _generated_binding("TC-001", "FACT-001"),
            _generated_binding("TC-002", "FACT-002"),
        ],
    }

    normalized = postprocess_generation_batch_item(
        SimpleNamespace(artifacts={}),
        {
            "item_input": {
                "plan": {"business_module": {"name": module}},
                "case_budget": 2,
                "batch": {"module_name": module},
                "authoritative_facts": facts,
                "case_fact_contract": _case_fact_contract(2, "FACT-001", "FACT-002"),
            },
            "item_output": _inline_grounding_output(output),
        },
    )

    assert normalized == output

    regrouped_output = {
        **output,
        "case_fact_bindings": [
            {
                **_generated_binding("TC-001", "FACT-001"),
                "step_bindings": [
                    {
                        "step_index": 0,
                        "action_fact_ids": ["FACT-001", "FACT-002"],
                        "expected_fact_ids": ["FACT-001", "FACT-002"],
                    }
                ],
            },
            _generated_binding("TC-002", "FACT-001"),
        ],
    }
    regrouped = postprocess_generation_batch_item(
        SimpleNamespace(artifacts={}),
        {
            "item_input": {
                "plan": {"business_module": {"name": module}},
                "case_budget": 2,
                "batch": {"module_name": module},
                "authoritative_facts": facts,
                "case_fact_contract": _case_fact_contract(
                    2,
                    "FACT-001",
                    "FACT-002",
                ),
            },
            "item_output": _inline_grounding_output(regrouped_output),
        },
    )
    assert regrouped["case_fact_bindings"] == regrouped_output[
        "case_fact_bindings"
    ]

    invalid_output = {
        **output,
        "case_fact_bindings": [
            _generated_binding("TC-001", "FACT-001"),
            _generated_binding("TC-002", "FACT-001"),
        ],
    }
    with pytest.raises(ValueError, match="未完整覆盖平台要求的事实"):
        postprocess_generation_batch_item(
            SimpleNamespace(artifacts={}),
            {
                "item_input": {
                    "plan": {"business_module": {"name": module}},
                    "case_budget": 2,
                    "batch": {"module_name": module},
                    "authoritative_facts": facts,
                    "case_fact_contract": _case_fact_contract(
                        2,
                        "FACT-001",
                        "FACT-002",
                    ),
                },
                "item_output": _inline_grounding_output(invalid_output),
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
                "test_points": _test_points("内容记录"),
                "evidence_ids": ["EV-0001"],
            },
            {
                "name": "访客拦截",
                "objective": "拦截访客访问并引导登录。",
                "lifecycle": None,
                "test_points": _test_points("访客拦截"),
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
    module.pop("fact_design_routes")
    module.pop("test_points")

    validate(
        instance={
            "requirement_summary": "支持课程学习和作品审核。",
            "business_module": {**module, "actors": ["用户"]},
            "coverage_focus": _batch_focus(module, []),
            "risks": [],
            "test_design_items": [],
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


def test_prepare_batches_routes_cross_module_evidence_only_by_ids() -> None:
    course = _fragment(chunk_index=1, page_number=2, text="课程学习真实正文。")
    review = _fragment(chunk_index=7, page_number=8, text="作品审核真实正文。")

    context = SimpleNamespace(artifacts={})
    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "课程学习与作品审核。",
                "business_modules": [
                    {
                        "name": "课程学习",
                        "objective": "完成课程学习",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("课程学习"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": ["F-001"],
                        "fact_design_routes": _fact_design_routes(["F-001"]),
                    },
                    {
                        "name": "作品审核",
                        "objective": "完成作品审核",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("作品审核"),
                        "evidence_ids": ["EV-0002"],
                        "fact_ids": ["F-002"],
                        "fact_design_routes": _fact_design_routes(["F-002"]),
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
    assert [
        item["plan"]["test_design_items"][0]["test_design_item_id"]
        for item in result["items"]
    ] == ["TD-001-001-001", "TD-002-001-001"]
    assert [
        item["case_fact_contract"]["required_test_design_item_ids"]
        for item in result["items"]
    ] == [["TD-001-001-001"], ["TD-002-001-001"]]


def test_prepare_batches_removes_modules_whose_routed_facts_became_inactive() -> None:
    effective_fact = _authoritative_fact(
        fact_id="F-NEW",
        scope_id="EV-0002",
        assertion="新规则替代旧规则后继续生效",
    )
    context = SimpleNamespace(artifacts={})

    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "旧规则已被新规则替代。",
                "business_modules": [
                    {
                        "name": "旧规则模块",
                        "objective": "仅承载已经失效的旧规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("旧规则"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": ["F-OLD"],
                        "fact_design_routes": _fact_design_routes(["F-OLD"]),
                    },
                    {
                        "name": "当前规则模块",
                        "objective": "覆盖当前有效规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("当前规则"),
                        "evidence_ids": ["EV-0002"],
                        "fact_ids": ["F-NEW"],
                        "fact_design_routes": _fact_design_routes(["F-NEW"]),
                    },
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": [effective_fact],
            "case_budget": 1,
            "batch_case_limit": 1,
        },
    )

    assert result["items"][0]["batch"]["module_name"] == "当前规则模块"
    assert context.artifacts["generation_batch_plan"]["planned_module_count"] == 2
    assert context.artifacts["generation_batch_plan"]["active_module_count"] == 1
    assert context.artifacts["generation_batch_plan"]["inactive_modules"] == [{
        "planned_module_index": 0,
        "module_name": "旧规则模块",
        "reason": "all_routed_facts_inactive",
    }]


def test_prepare_batches_removes_designs_only_supported_by_inactive_facts() -> None:
    effective_fact = _authoritative_fact(
        fact_id="F-NEW",
        scope_id="EV-0001",
        assertion="新规则替代旧规则后继续生效",
    )
    context = SimpleNamespace(artifacts={})

    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "同一模块内旧规则已被新规则替代。",
                "business_modules": [
                    {
                        "name": "规则管理",
                        "objective": "覆盖当前有效规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": [
                            {
                                "name": "规则切换",
                                "objective": "验证规则切换后的行为",
                                "test_designs": [
                                    {
                                        "technique": "场景法",
                                        "rationale": "分别覆盖旧规则和当前规则",
                                        "coverage_items": [
                                            "旧规则",
                                            "当前规则",
                                            "新旧规则共用校验",
                                        ],
                                    }
                                ],
                            }
                        ],
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": ["F-OLD", "F-NEW"],
                        "fact_design_routes": [
                            {
                                "fact_id": "F-OLD",
                                "test_design_item_indexes": [0, 2],
                            },
                            {
                                "fact_id": "F-NEW",
                                "test_design_item_indexes": [1, 2],
                            },
                        ],
                    }
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": [effective_fact],
            "case_budget": 1,
            "batch_case_limit": 1,
        },
    )

    item = result["items"][0]
    assert item["plan"]["business_module"]["fact_ids"] == ["F-NEW"]
    assert "test_points" not in item["plan"]["business_module"]
    assert [
        design["test_design_item_id"]
        for design in item["plan"]["test_design_items"]
    ] == ["TD-001-001-002", "TD-001-001-003"]
    assert item["case_fact_contract"]["required_test_design_item_ids"] == [
        "TD-001-001-002",
        "TD-001-001-003",
    ]
    assert context.artifacts["generation_batch_plan"][
        "inactive_test_design_item_ids"
    ] == ["TD-001-001-001"]


def test_prepare_batches_keeps_fact_without_matching_planned_design() -> None:
    facts = [
        _authoritative_fact(
            fact_id="F-SHOW",
            scope_id="EV-0001",
            assertion="展示处理结果",
        ),
        _authoritative_fact(
            fact_id="F-REMOVED",
            scope_id="EV-0001",
            assertion="导出入口不再提供",
        ),
    ]
    context = SimpleNamespace(artifacts={})

    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "功能保留展示，同时下线导出入口。",
                "business_modules": [
                    {
                        "name": "结果展示",
                        "objective": "展示处理结果",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("结果展示"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": ["F-SHOW", "F-REMOVED"],
                        "fact_design_routes": [
                            {
                                "fact_id": "F-SHOW",
                                "test_design_item_indexes": [0],
                            },
                            {
                                "fact_id": "F-REMOVED",
                                "test_design_item_indexes": [],
                            },
                        ],
                    }
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": facts,
            "case_budget": 1,
            "batch_case_limit": 1,
        },
    )

    item = result["items"][0]
    assert item["case_fact_contract"]["required_fact_ids"] == [
        "F-SHOW",
        "F-REMOVED",
    ]
    assert item["case_fact_contract"]["required_test_design_item_ids"] == [
        "TD-001-001-001"
    ]
    assert context.artifacts["generation_batch_plan"][
        "unmatched_test_design_fact_ids"
    ] == ["F-REMOVED"]


def test_prepare_batches_distributes_large_module_facts_without_duplication() -> None:
    facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"真实规则 {index}",
        )
        for index in range(1, 61)
    ]
    context = SimpleNamespace(artifacts={})
    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "大模块真实需求",
                "business_modules": [
                    {
                        "name": "大模块",
                        "objective": "覆盖全部真实规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("全部真实规则"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": [fact["fact_id"] for fact in facts],
                        "fact_design_routes": _fact_design_routes(
                            [fact["fact_id"] for fact in facts]
                        ),
                    }
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": facts,
            "case_budget": 8,
            "batch_case_limit": 5,
        },
    )

    assert result["batch_count"] == math.ceil(
        len(facts) / GENERATION_MAX_REQUIRED_FACTS_PER_BATCH
    )
    assert sum(item["case_budget"] for item in result["items"]) == 8
    assert all(1 <= item["case_budget"] <= 5 for item in result["items"])
    assigned_ids = [
        fact["fact_id"]
        for item in result["items"]
        for fact in item["authoritative_facts"]
    ]
    assert sorted(assigned_ids) == sorted(fact["fact_id"] for fact in facts)
    assert len(assigned_ids) == len(set(assigned_ids))
    assert context.artifacts["generation_batch_plan"]["effective_fact_count"] == 60
    assert context.artifacts["generation_batch_plan"]["fact_assignment_count"] == 60
    assert context.artifacts["generation_batch_plan"]["shared_fact_count"] == 0
    assert context.artifacts["generation_batch_plan"]["max_fact_reuse"] == 1
    assert max(item["batch"]["fact_json_chars"] for item in result["items"]) <= (
        GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH
    )
    assert max(item["batch"]["fact_count"] for item in result["items"]) <= (
        GENERATION_MAX_REQUIRED_FACTS_PER_BATCH
    )
    assert all(item["batch"]["semantic_keywords"] == ["大模块"] for item in result["items"])
    for item in result["items"]:
        validate(instance=item, schema=GENERATION_BATCH_ITEM_SCHEMA)


def test_prepare_batches_builds_exact_batch_fact_contract() -> None:
    heavy_facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"重来源规则 {index}",
            page_number=1,
        )
        for index in range(1, 47)
    ]
    light_facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"轻来源规则 {index}",
        )
        for index in range(47, 51)
    ]

    result = prepare_test_case_batches(
        SimpleNamespace(artifacts={}),
        {
            "plan": {
                "requirement_summary": "不同来源负载差异明显的真实需求",
                "business_modules": [
                    {
                        "name": "规则管理",
                        "objective": "覆盖全部规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("全部规则"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": [
                            fact["fact_id"] for fact in [*heavy_facts, *light_facts]
                        ],
                        "fact_design_routes": _fact_design_routes(
                            [
                                fact["fact_id"]
                                for fact in [*heavy_facts, *light_facts]
                            ]
                        ),
                    }
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": [*heavy_facts, *light_facts],
            "case_budget": 10,
            "batch_case_limit": 5,
        },
    )

    assert result["batch_count"] == (
        math.ceil(len(heavy_facts) / GENERATION_MAX_REQUIRED_FACTS_PER_BATCH)
        + math.ceil(len(light_facts) / GENERATION_MAX_REQUIRED_FACTS_PER_BATCH)
    )
    assert sum(item["case_budget"] for item in result["items"]) == 10
    assert all(
        item["batch"]["fact_count"] <= GENERATION_MAX_REQUIRED_FACTS_PER_BATCH
        for item in result["items"]
    )
    assigned_ids = [
        fact["fact_id"]
        for item in result["items"]
        for fact in item["authoritative_facts"]
    ]
    assert assigned_ids == [fact["fact_id"] for fact in [*heavy_facts, *light_facts]]
    for item in result["items"]:
        contract = item["case_fact_contract"]
        assert contract["target_case_ids"] == [
            f"TC-{index:03d}" for index in range(1, item["case_budget"] + 1)
        ]
        assert contract["required_fact_ids"] == [
            fact["fact_id"] for fact in item["authoritative_facts"]
        ]
        slots = contract["coverage_slots"]
        assert [slot["case_id"] for slot in slots] == contract["target_case_ids"]
        assigned_fact_ids = [
            fact_id for slot in slots for fact_id in slot["required_fact_ids"]
        ]
        assert len(assigned_fact_ids) == len(set(assigned_fact_ids))
        assert set(assigned_fact_ids) == set(contract["required_fact_ids"])
        assert max(len(slot["required_fact_ids"]) for slot in slots) - min(
            len(slot["required_fact_ids"]) for slot in slots
        ) <= 1
        assert {
            design_item_id
            for slot in slots
            for design_item_id in slot["required_test_design_item_ids"]
        } == set(contract["required_test_design_item_ids"])


def test_case_fact_contract_groups_facts_by_existing_design_routes() -> None:
    facts = [
        {"fact_id": fact_id}
        for fact_id in ["F-A1", "F-B1", "F-C1", "F-A2", "F-B2", "F-C2"]
    ]
    design_items = [
        {"test_design_item_id": design_id}
        for design_id in ["TD-001", "TD-002", "TD-003"]
    ]
    fact_design_item_ids = {
        "F-A1": ["TD-001"],
        "F-A2": ["TD-001"],
        "F-B1": ["TD-002"],
        "F-B2": ["TD-002"],
        "F-C1": ["TD-003"],
        "F-C2": ["TD-003"],
    }

    contract = _build_case_fact_contract(
        facts,
        case_budget=3,
        test_design_items=design_items,
        fact_design_item_ids=fact_design_item_ids,
    )

    assert contract["coverage_slots"] == [
        {
            "case_id": "TC-001",
            "required_fact_ids": ["F-A1", "F-A2"],
            "required_test_design_item_ids": ["TD-001"],
        },
        {
            "case_id": "TC-002",
            "required_fact_ids": ["F-B1", "F-B2"],
            "required_test_design_item_ids": ["TD-002"],
        },
        {
            "case_id": "TC-003",
            "required_fact_ids": ["F-C1", "F-C2"],
            "required_test_design_item_ids": ["TD-003"],
        },
    ]


def test_prepare_batches_limits_input_payload_and_output_binding_complexity() -> None:
    facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"短规则 {index}",
        )
        for index in range(1, 34)
    ]

    context = SimpleNamespace(artifacts={})
    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "短事实集合",
                "business_modules": [
                    {
                        "name": "规则管理",
                        "objective": "覆盖全部短规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("全部短规则"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": [fact["fact_id"] for fact in facts],
                        "fact_design_routes": _fact_design_routes(
                            [fact["fact_id"] for fact in facts]
                        ),
                    }
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": facts,
            "case_budget": 3,
            "batch_case_limit": 5,
        },
    )

    assert result["batch_count"] == 3
    assert sum(item["case_budget"] for item in result["items"]) == 3
    assert max(len(item["authoritative_facts"]) for item in result["items"]) <= (
        GENERATION_MAX_REQUIRED_FACTS_PER_BATCH
    )
    assert all(
        item["batch"]["fact_json_chars"] <= GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH
        for item in result["items"]
    )
    assert context.artifacts["generation_batch_plan"][
        "max_required_facts_per_batch"
    ] == GENERATION_MAX_REQUIRED_FACTS_PER_BATCH


def test_prepare_batches_rejects_case_budget_below_binding_complexity_minimum() -> None:
    facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"短规则 {index}",
        )
        for index in range(1, GENERATION_MAX_REQUIRED_FACTS_PER_BATCH + 2)
    ]

    with pytest.raises(ValueError, match="case_budget=1, minimum_required=2"):
        prepare_test_case_batches(
            SimpleNamespace(artifacts={}),
            {
                "plan": {
                    "requirement_summary": "输出绑定复杂度超过单包上限",
                    "business_modules": [
                        {
                            "name": "规则管理",
                            "objective": "覆盖全部短规则",
                            "actors": ["用户"],
                            "lifecycle": None,
                            "test_points": _test_points("复杂规则"),
                            "evidence_ids": ["EV-0001"],
                            "fact_ids": [fact["fact_id"] for fact in facts],
                            "fact_design_routes": _fact_design_routes(
                                [fact["fact_id"] for fact in facts]
                            ),
                        }
                    ],
                    "coverage_focus": [],
                    "risks": [],
                },
                "effective_facts": facts,
                "case_budget": 1,
                "batch_case_limit": 5,
            },
        )


def test_prepare_batches_rejects_budget_below_real_context_minimum() -> None:
    facts = [
        _authoritative_fact(
            fact_id=f"F-{page_number:03d}",
            scope_id="EV-0001",
            assertion=f"第 {page_number} 页规则",
            page_number=page_number,
        )
        for page_number in range(1, GENERATION_MAX_PAGES_PER_BATCH + 2)
    ]

    with pytest.raises(
        ValueError,
        match="case_budget=1, minimum_required=2",
    ):
        prepare_test_case_batches(
            SimpleNamespace(artifacts={}),
            {
                "plan": {
                    "requirement_summary": "跨页规则",
                    "business_modules": [
                        {
                            "name": "规则管理",
                            "objective": "覆盖全部跨页规则",
                            "actors": ["用户"],
                            "lifecycle": None,
                            "test_points": _test_points("跨页规则"),
                            "evidence_ids": ["EV-0001"],
                            "fact_ids": [fact["fact_id"] for fact in facts],
                            "fact_design_routes": _fact_design_routes(
                                [fact["fact_id"] for fact in facts]
                            ),
                        }
                    ],
                    "coverage_focus": [],
                    "risks": [],
                },
                "effective_facts": facts,
                "case_budget": 1,
                "batch_case_limit": 5,
            },
        )


def test_prepare_batches_keeps_semantic_page_bundles_and_exact_case_budget() -> None:
    pages = [1, 2, 3, 20, 21, 22]
    facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"第 {page_number} 页真实规则 {index}",
            page_number=page_number,
        )
        for index, page_number in enumerate(
            [page for page in pages for _ in range(2)],
            start=1,
        )
    ]
    context = SimpleNamespace(artifacts={})

    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "跨页课程需求",
                "business_modules": [
                    {
                        "name": "课程学习",
                        "objective": "覆盖课程进入和内容展示",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("语义连续规则"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": [fact["fact_id"] for fact in facts],
                        "fact_design_routes": _fact_design_routes(
                            [fact["fact_id"] for fact in facts]
                        ),
                    }
                ],
                "coverage_focus": ["课程学习：进入课程", "课程学习：查看内容"],
                "risks": [],
            },
            "effective_facts": facts,
            "case_budget": 6,
            "batch_case_limit": 3,
        },
    )

    assert result["batch_count"] == 2
    assert sum(item["case_budget"] for item in result["items"]) == 6
    assert [item["batch"]["source_page_numbers"] for item in result["items"]] == [
        [1, 2, 3],
        [20, 21, 22],
    ]
    assert all(
        len(item["batch"]["source_page_numbers"]) <= GENERATION_MAX_PAGES_PER_BATCH
        for item in result["items"]
    )
    assert {
        fact_id
        for item in result["items"]
        for fact_id in item["batch"]["source_scope_ids"]
    } == {"EV-0001"}
    assert context.artifacts["generation_batch_plan"]["batch_count"] == 2


def test_prepare_batches_compacts_non_contiguous_pages_within_load_limits() -> None:
    facts = [
        _authoritative_fact(
            fact_id=f"F-{index:03d}",
            scope_id="EV-0001",
            assertion=f"第 {page_number} 页真实规则 {index}",
            page_number=page_number,
        )
        for index, page_number in enumerate([4, 4, 5, 5, 24, 24, 25, 25], start=1)
    ]
    context = SimpleNamespace(artifacts={})

    result = prepare_test_case_batches(
        context,
        {
            "plan": {
                "requirement_summary": "包含两个非连续页面区间的需求",
                "business_modules": [
                    {
                        "name": "规则管理",
                        "objective": "覆盖全部真实规则",
                        "actors": ["用户"],
                        "lifecycle": None,
                        "test_points": _test_points("非连续规则"),
                        "evidence_ids": ["EV-0001"],
                        "fact_ids": [fact["fact_id"] for fact in facts],
                        "fact_design_routes": _fact_design_routes(
                            [fact["fact_id"] for fact in facts]
                        ),
                    }
                ],
                "coverage_focus": [],
                "risks": [],
            },
            "effective_facts": facts,
            "case_budget": 4,
            "batch_case_limit": 5,
        },
    )

    assert result["batch_count"] == 1
    assert [item["batch"]["source_page_numbers"] for item in result["items"]] == [
        [4, 5, 24, 25],
    ]
    assert sum(item["case_budget"] for item in result["items"]) == 4
    assert context.artifacts["generation_batch_plan"][
        "requires_contiguous_document_pages"
    ] is False


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
                            "test_points": _test_points("证据约束"),
                            "evidence_ids": [],
                            "fact_ids": ["F-001"],
                            "fact_design_routes": _fact_design_routes(["F-001"]),
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
                            "test_points": _test_points("事实约束"),
                            "evidence_ids": [],
                            "fact_ids": ["F-001"],
                            "fact_design_routes": _fact_design_routes(["F-001"]),
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
