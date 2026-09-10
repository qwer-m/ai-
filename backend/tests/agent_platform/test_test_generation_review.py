from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.agent_platform.test_generation_review import (
    merge_final_review_batches,
    merge_final_review_recheck_records,
    merge_final_review_repairs,
    postprocess_final_review_batch_item,
    postprocess_final_review_repair_item,
    postprocess_global_final_review_output,
    prepare_final_review_batches,
    prepare_final_review_rechecks,
    prepare_final_review_repairs,
    prepare_terminal_final_review_repairs,
)


def _fact(fact_id: str, assertion: str) -> dict:
    return {
        "fact_id": fact_id,
        "assertion": assertion,
        "scope_id": "EV-0001",
        "status": "effective",
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
        "source_anchor": {
            "source_kind": "inline",
            "requirement_sha256": "a" * 64,
            "source_offset_start": 0,
            "source_offset_end": len(assertion),
            "quote": assertion,
        },
    }


def _case(case_id: str, title: str, expected: str) -> dict:
    return {
        "case_id": case_id,
        "title": title,
        "module": "投稿流程",
        "priority": "P0",
        "preconditions": ["用户已登录"],
        "test_input": "角色=已登录用户",
        "steps": [{"action": "点击投稿按钮", "expected": expected}],
        "tags": ["投稿"],
        "test_design_item_ids": [],
    }


def _binding(case_id: str, fact_id: str) -> dict:
    return {
        "case_id": case_id,
        "precondition_bindings": [{"precondition_index": 0, "fact_ids": [fact_id]}],
        "test_input_fact_ids": [fact_id],
        "step_bindings": [
            {
                "step_index": 0,
                "action_fact_ids": [fact_id],
                "expected_fact_ids": [fact_id],
            }
        ],
    }


def _inline_repair_output(cases: list[dict], bindings: list[dict]) -> dict:
    bindings_by_case_id = {binding["case_id"]: binding for binding in bindings}
    inline_cases: list[dict] = []
    for case in cases:
        binding = bindings_by_case_id[case["case_id"]]
        preconditions_by_index = {
            item["precondition_index"]: item
            for item in binding["precondition_bindings"]
        }
        steps_by_index = {
            item["step_index"]: item for item in binding["step_bindings"]
        }
        inline_cases.append(
            {
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
                "tags": list(case.get("tags") or []),
                "test_design_item_ids": list(case["test_design_item_ids"]),
            }
        )
    return {
        "case_patches": [
            {"case_id": case["case_id"], **inline_case}
            for case, inline_case in zip(cases, inline_cases, strict=True)
        ]
    }


def _map_record(index: int, output: dict) -> dict:
    return {"item_index": index, "input_hash": "真实运行哈希", "output": output}


def _review(*, approved: bool) -> dict:
    return {
        "phase": "final_review",
        "approved": approved,
        "summary": "批次通过" if approved else "预期结果不准确",
        "differences": [] if approved else [
            {
                "case_id": "TC-001",
                "category": "business_semantics",
                "field_path": "steps[0].expected",
                "detail": "预期结果与事实不一致",
                "related_fact_ids": ["F-001"],
                "repair_instruction": "按事实F-001修正预期结果",
            }
        ],
    }


def test_final_review_postprocessors_fill_deterministic_metadata() -> None:
    context = SimpleNamespace(artifacts={})
    fact = _fact("F-001", "投稿成功后显示成功提示")
    case = _case("TC-001", "投稿成功", "显示成功提示")
    raw_review = {
        "approved": False,
        "differences": [
            {
                "case_id": "TC-001",
                "category": "business_semantics",
                "field_path": "steps[0].expected",
                "detail": "预期绑定需要调整",
                "repair_instruction": "将预期绑定到 F-001",
            }
        ],
    }

    batch = postprocess_final_review_batch_item(
        context,
        {
            "item_input": {
                "test_cases": [case],
                "case_fact_bindings": [_binding("TC-001", "F-001")],
                "review_facts": [
                    {
                        "fact_id": fact["fact_id"],
                        "assertion": fact["assertion"],
                        "value_policy": fact["value_policy"],
                        "governed_values": fact["governed_values"],
                    }
                ],
            },
            "item_output": raw_review,
        },
    )
    assert batch["phase"] == "final_review"
    assert batch["summary"] == "批次终审未通过：发现 1 项差异。"
    assert batch["differences"][0]["related_fact_ids"] == ["F-001"]

    global_review = postprocess_global_final_review_output(
        context,
        {
            "input_payload": {"case_index": [{"case_id": "TC-001"}]},
            "output": {
                **raw_review,
                "differences": [
                    {
                        **raw_review["differences"][0],
                        "field_path": "last_expected",
                        "related_fact_ids": [],
                    }
                ],
            },
        },
    )
    assert global_review["phase"] == "final_review"
    assert global_review["summary"] == "全局终审未通过：发现 1 项新增跨批差异。"


def test_global_review_rejects_fields_not_present_in_case_index() -> None:
    context = SimpleNamespace(artifacts={})

    with pytest.raises(ValueError, match="输入中不可见的字段"):
        postprocess_global_final_review_output(
            context,
            {
                "input_payload": {
                    "case_index": [{"case_id": "TC-040"}],
                    "batch_review": {"differences": []},
                },
                "output": {
                    "approved": False,
                    "differences": [
                        {
                            "case_id": "TC-040",
                            "category": "state_coherence",
                            "field_path": "preconditions[0]",
                            "detail": "引用了全局终审看不到的前置条件",
                            "related_fact_ids": [],
                            "repair_instruction": "修改前置条件",
                        }
                    ],
                },
            },
        )


def test_batch_review_discards_duplicate_claim_for_distinct_fact_obligations() -> None:
    context = SimpleNamespace(artifacts={})
    first_case = _case("TC-001", "原创要求", "作品必须本人原创")
    second_case = _case("TC-002", "禁止侵权", "作品不得抄袭搬运")
    first_fact = _fact("F-ORIGINAL", "发布的作文必须为本人原创完成")
    second_fact = _fact("F-PLAGIARISM", "严禁抄袭、搬运或代写代发")
    first_binding = _binding("TC-001", "F-ORIGINAL")
    first_binding["precondition_bindings"] = []
    first_binding["step_bindings"][0]["action_fact_ids"] = []
    second_binding = _binding("TC-002", "F-PLAGIARISM")
    second_binding["precondition_bindings"] = []
    second_binding["step_bindings"][0]["action_fact_ids"] = []

    normalized = postprocess_final_review_batch_item(
        context,
        {
            "item_input": {
                "test_cases": [first_case, second_case],
                "case_fact_bindings": [first_binding, second_binding],
                "review_facts": [
                    {
                        "fact_id": fact["fact_id"],
                        "assertion": fact["assertion"],
                        "value_policy": "exact",
                        "governed_values": [],
                    }
                    for fact in (first_fact, second_fact)
                ],
            },
            "item_output": {
                "approved": False,
                "differences": [
                    {
                        "case_id": "TC-001",
                        "category": "semantic_duplicate",
                        "field_path": "steps[0].expected",
                        "detail": "正向原创要求与禁止抄袭重复",
                        "repair_instruction": "删除原创要求",
                    }
                ],
            },
        },
    )

    assert normalized["approved"] is True
    assert normalized["differences"] == []
    assert "剔除 1 项" in normalized["summary"]


def test_batch_review_accepts_known_fact_proposed_for_new_field_binding() -> None:
    context = SimpleNamespace(artifacts={})
    case = _case("TC-001", "投稿审核", "进入审核中")

    normalized = postprocess_final_review_batch_item(
        context,
        {
            "item_input": {
                "test_cases": [case],
                "case_fact_bindings": [_binding("TC-001", "F-STATE")],
                "review_facts": [
                    {
                        "fact_id": "F-STATE",
                        "assertion": "提交后进入审核中",
                        "value_policy": "exact",
                        "governed_values": [],
                    },
                    {
                        "fact_id": "F-COMPARE",
                        "assertion": "与原作文差异过大时不通过",
                        "value_policy": "exact",
                        "governed_values": [],
                    },
                ],
            },
            "item_output": {
                "approved": False,
                "differences": [
                    {
                        "case_id": "TC-001",
                        "category": "unsupported_business_rule",
                        "field_path": "steps[0].expected",
                        "detail": "当前预期缺少差异过大时不通过的规则",
                        "repair_instruction": "补充 F-COMPARE 并建立字段绑定",
                    }
                ],
            },
        },
    )

    assert normalized["differences"][0]["related_fact_ids"] == [
        "F-COMPARE",
        "F-STATE",
    ]


def test_batch_review_accepts_cross_field_fact_bound_to_same_case() -> None:
    context = SimpleNamespace(artifacts={})
    case = _case("TC-001", "投稿审核", "重复投稿不额外奖励")
    binding = _binding("TC-001", "F-REPEAT")
    binding["precondition_bindings"][0]["fact_ids"] = ["F-FIRST"]

    normalized = postprocess_final_review_batch_item(
        context,
        {
            "item_input": {
                "test_cases": [case],
                "case_fact_bindings": [binding],
                "review_facts": [
                    {
                        "fact_id": "F-REPEAT",
                        "assertion": "重复投稿不额外奖励",
                        "value_policy": "exact",
                        "governed_values": [],
                    },
                    {
                        "fact_id": "F-FIRST",
                        "assertion": "每个主题可获得一次首次投稿奖励",
                        "value_policy": "exact",
                        "governed_values": [],
                    },
                ],
            },
            "item_output": {
                "approved": False,
                "differences": [
                    {
                        "case_id": "TC-001",
                        "category": "business_semantics",
                        "field_path": "steps[0].expected",
                        "detail": "F-REPEAT 与前置条件 F-FIRST 的适用范围需要区分",
                        "repair_instruction": "明确首次奖励和重复投稿规则",
                    }
                ],
            },
        },
    )

    assert normalized["differences"][0]["related_fact_ids"] == [
        "F-FIRST",
        "F-REPEAT",
    ]


def test_batch_review_matches_complete_fact_id_in_difference_text() -> None:
    context = SimpleNamespace(artifacts={})
    case = _case("TC-001", "投稿审核", "进入审核中")

    normalized = postprocess_final_review_batch_item(
        context,
        {
            "item_input": {
                "test_cases": [case],
                "case_fact_bindings": [_binding("TC-001", "F-10")],
                "review_facts": [
                    {
                        "fact_id": "F-1",
                        "assertion": "另一个独立要求",
                        "value_policy": "exact",
                        "governed_values": [],
                    },
                    {
                        "fact_id": "F-10",
                        "assertion": "提交后进入审核中",
                        "value_policy": "exact",
                        "governed_values": [],
                    },
                ],
            },
            "item_output": {
                "approved": False,
                "differences": [
                    {
                        "case_id": "TC-001",
                        "category": "business_semantics",
                        "field_path": "steps[0].expected",
                        "detail": "按 F-10 校验审核状态",
                        "repair_instruction": "保持 F-10 的状态语义",
                    }
                ],
            },
        },
    )

    assert normalized["differences"][0]["related_fact_ids"] == ["F-10"]


def test_final_review_groups_same_source_duplicate_assertions_for_coverage() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-001", "普通用户第一个单元可以试学"),
        _fact("F-002", "普通用户第一个单元可以试学"),
    ]
    case = _case("TC-001", "普通用户试学", "进入学习页面")
    binding = _binding("TC-001", "F-002")
    source_input = {
        "test_cases": [case],
        "case_fact_bindings": [_binding("TC-001", "F-001")],
        "authoritative_facts": facts,
        "required_fact_ids": ["F-001"],
        "target_case_ids": ["TC-001"],
        "target_case_count": 1,
        "test_design_items": [],
    }

    normalized = postprocess_final_review_repair_item(
        context,
        {
            "item_input": source_input,
            "item_output": _inline_repair_output([case], [binding]),
        },
    )

    assert normalized["case_fact_bindings"][0]["case_id"] == "TC-001"


def test_final_review_honors_reviewed_semantic_duplicate_fact_group() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-GENERIC", "次数为0时点击按钮显示次数已用完"),
        _fact("F-SPECIFIC", "次数为0时点击去批改按钮显示次数已用完"),
    ]
    case = _case("TC-001", "次数拦截", "显示次数已用完")
    source_input = {
        "test_cases": [case],
        "case_fact_bindings": [_binding("TC-001", "F-GENERIC")],
        "authoritative_facts": facts,
        "required_fact_ids": ["F-GENERIC", "F-SPECIFIC"],
        "target_case_ids": ["TC-001"],
        "target_case_count": 1,
        "test_design_items": [],
        "review_result": {
            "differences": [
                {
                    "case_id": "TC-001",
                    "category": "semantic_duplicate",
                    "repair_scope": "cohort",
                    "related_fact_ids": ["F-GENERIC", "F-SPECIFIC"],
                }
            ]
        },
    }

    normalized = postprocess_final_review_repair_item(
        context,
        {
            "item_input": source_input,
            "item_output": _inline_repair_output(
                [case],
                [_binding("TC-001", "F-SPECIFIC")],
            ),
        },
    )

    covered = normalized["case_fact_bindings"][0]["step_bindings"][0]
    assert covered["expected_fact_ids"] == ["F-SPECIFIC"]


def test_final_review_repair_rejects_still_duplicate_business_steps() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-001", "点击照片缩略图打开大图"),
        _fact("F-002", "支持左右切换照片"),
    ]
    first = _case("TC-001", "查看照片大图", "打开照片大图")
    second = _case("TC-002", "查看照片大图验证", "打开照片大图")
    first_binding = _binding("TC-001", "F-001")
    second_binding = _binding("TC-002", "F-002")

    with pytest.raises(ValueError, match="修复后仍存在相同前置条件和步骤"):
        postprocess_final_review_repair_item(
            context,
            {
                "item_input": {
                    "test_cases": [first, second],
                    "case_fact_bindings": [first_binding, second_binding],
                    "authoritative_facts": facts,
                    "required_fact_ids": ["F-001", "F-002"],
                    "target_case_ids": ["TC-001", "TC-002"],
                    "target_case_count": 2,
                    "test_design_items": [],
                    "review_result": {
                        "differences": [
                            {
                                "case_id": "TC-002",
                                "category": "semantic_duplicate",
                                "repair_scope": "cohort",
                                "related_fact_ids": ["F-001", "F-002"],
                            }
                        ]
                    },
                },
                "item_output": _inline_repair_output(
                    [first, second],
                    [first_binding, second_binding],
                ),
            },
        )


def test_final_review_repair_applies_patch_by_case_id() -> None:
    context = SimpleNamespace(artifacts={})
    case = _case("TC-001", "普通用户试学", "进入学习页面")
    output = _inline_repair_output([case], [_binding("TC-001", "F-001")])
    output["case_patches"][0] = {
        "case_id": "TC-001",
        "steps": output["case_patches"][0]["steps"],
    }
    output["case_patches"][0]["steps"][0]["expected"] = "进入试学页面"

    normalized = postprocess_final_review_repair_item(
        context,
        {
            "item_input": {
                "test_cases": [case],
                "case_fact_bindings": [_binding("TC-001", "F-001")],
                "authoritative_facts": [_fact("F-001", "普通用户第一个单元可以试学")],
                "required_fact_ids": ["F-001"],
                "target_case_ids": ["TC-001"],
                "target_case_count": 1,
                "test_design_items": [],
            },
            "item_output": output,
        },
    )

    assert normalized["test_cases"][0]["case_id"] == "TC-001"
    assert normalized["test_cases"][0]["module"] == case["module"]
    assert normalized["test_cases"][0]["steps"][0]["expected"] == "进入试学页面"


def test_final_review_repair_cannot_drop_existing_effective_fact_coverage() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-COURSE-199", "购买作文课一年，价格为199元"),
        _fact("F-TICKET-10", "单次购买批改券价格为10元，数量为10次"),
    ]
    case = _case("TC-075", "不同商品购买", "进入199元作文课购买流程")
    case["steps"].append(
        {
            "action": "单次购买批改券",
            "expected": "价格为10元，数量为10次",
        }
    )
    binding = _binding("TC-075", "F-COURSE-199")
    binding["step_bindings"].append(
        {
            "step_index": 1,
            "action_fact_ids": ["F-TICKET-10"],
            "expected_fact_ids": ["F-TICKET-10"],
        }
    )
    generation_inputs = [
        {
            "case_budget": 1,
            "batch": {"batch_id": "M005-B001", "module_name": "投稿流程"},
            "authoritative_facts": facts,
            "plan": {"test_design_items": []},
        }
    ]
    prepared = prepare_final_review_batches(
        context,
        {
            "generation_inputs": generation_inputs,
            "generation": {
                "test_cases": [case],
                "case_fact_bindings": [binding],
                "batch_count": 1,
                "case_count": 1,
            },
            "batch_case_limit": 1,
        },
    )
    repairs = prepare_final_review_repairs(
        context,
        {
            "review_inputs": prepared["items"],
            "review_records": [
                _map_record(
                    0,
                    {
                        "phase": "final_review",
                        "approved": False,
                        "summary": "购买步骤疑似重复",
                        "differences": [
                            {
                                "case_id": "TC-075",
                                "category": "business_semantics",
                                "field_path": "steps[1]",
                                "detail": "两个购买步骤疑似重复",
                                "related_fact_ids": [
                                    "F-COURSE-199",
                                    "F-TICKET-10",
                                ],
                                "repair_instruction": "删除冗余购买步骤",
                            }
                        ],
                    },
                )
            ],
            "generation_inputs": generation_inputs,
        },
    )

    repair_input = repairs["items"][0]
    assert repair_input["repair_cycle"] == 1
    assert repair_input["required_fact_ids"] == [
        "F-COURSE-199",
        "F-TICKET-10",
    ]
    repaired_case = dict(case)
    repaired_case["steps"] = list(case["steps"][:1])
    repaired_binding = _binding("TC-075", "F-COURSE-199")

    with pytest.raises(ValueError, match="仍未覆盖要求事实.*F-TICKET-10") as exc_info:
        postprocess_final_review_repair_item(
            context,
            {
                "item_input": repair_input,
                "item_output": _inline_repair_output(
                    [repaired_case],
                    [repaired_binding],
                ),
            },
        )
    assert "F-COURSE-199=购买作文课一年，价格为199元" not in str(exc_info.value)
    assert "F-TICKET-10=单次购买批改券价格为10元，数量为10次" in str(
        exc_info.value
    )


def test_followup_review_repair_receives_incremented_cycle() -> None:
    context = SimpleNamespace(
        artifacts={
            "final_review_repair_cycles": [
                {
                    "cycle_number": 1,
                    "repair_batch_count": 1,
                    "preserved_batch_count": 0,
                }
            ]
        }
    )
    fact = _fact("F-001", "提交后进入审核中状态")
    case = _case("TC-001", "投稿审核", "进入审核中状态")
    generation_input = {
        "batch": {"batch_id": "M001-B001"},
        "authoritative_facts": [fact],
    }
    review_input = {
        "review_batch": {
            "batch_id": "R-001",
            "batch_number": 1,
            "batch_count": 1,
            "module_name": case["module"],
            "generation_batch_ids": ["M001-B001"],
            "case_ids": ["TC-001"],
        },
        "test_cases": [case],
        "case_fact_bindings": [_binding("TC-001", "F-001")],
        "review_facts": [
            {
                "fact_id": "F-001",
                "assertion": fact["assertion"],
                "value_policy": "exact",
                "governed_values": [],
            }
        ],
        "test_design_items": [],
        "audit_summary": {},
    }

    result = prepare_final_review_repairs(
        context,
        {
            "review_inputs": [review_input],
            "review_records": [_map_record(0, _review(approved=False))],
            "generation_inputs": [generation_input],
        },
    )

    assert result["items"][0]["repair_cycle"] == 2


def test_final_review_repairs_split_located_cases_into_small_batches() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [_fact(f"F-{index:03d}", f"规则{index}") for index in range(1, 6)]
    cases = [
        _case(f"TC-{index:03d}", f"用例{index}", f"错误预期{index}")
        for index in range(1, 6)
    ]
    bindings = [
        _binding(f"TC-{index:03d}", f"F-{index:03d}")
        for index in range(1, 6)
    ]
    review_input = {
        "review_batch": {
            "batch_id": "R-001",
            "batch_number": 1,
            "batch_count": 1,
            "module_name": "投稿流程",
            "generation_batch_ids": ["M001-B001"],
            "case_ids": [case["case_id"] for case in cases],
        },
        "test_cases": cases,
        "case_fact_bindings": bindings,
        "review_facts": [
            {
                "fact_id": fact["fact_id"],
                "assertion": fact["assertion"],
                "value_policy": "exact",
                "governed_values": [],
            }
            for fact in facts
        ],
        "test_design_items": [],
        "audit_summary": {},
    }
    review = {
        "phase": "final_review",
        "approved": False,
        "summary": "五条用例均需修复",
        "differences": [
            {
                "case_id": f"TC-{index:03d}",
                "category": "business_semantics",
                "field_path": "steps[0].expected",
                "detail": f"用例{index}的预期错误",
                "related_fact_ids": [f"F-{index:03d}"],
                "repair_instruction": f"修正用例{index}的预期",
            }
            for index in range(1, 6)
        ],
    }

    result = prepare_final_review_repairs(
        context,
        {
            "review_inputs": [review_input],
            "review_records": [_map_record(0, review)],
            "generation_inputs": [
                {
                    "batch": {"batch_id": "M001-B001"},
                    "authoritative_facts": facts,
                }
            ],
        },
    )

    assert result["repair_batch_count"] == 5
    assert [item["target_case_ids"] for item in result["items"]] == [
        ["TC-001"],
        ["TC-002"],
        ["TC-003"],
        ["TC-004"],
        ["TC-005"],
    ]
    assert [item["review_batch"]["batch_id"] for item in result["items"]] == [
        "R-001-C001",
        "R-001-C002",
        "R-001-C003",
        "R-001-C004",
        "R-001-C005",
    ]
    assert [
        [difference["case_id"] for difference in item["review_result"]["differences"]]
        for item in result["items"]
    ] == [["TC-001"], ["TC-002"], ["TC-003"], ["TC-004"], ["TC-005"]]
    assert context.artifacts["final_review_repair_plan"] == {
        "repair_batch_count": 5,
        "source_repair_batch_count": 1,
        "preserved_batch_count": 0,
        "max_cases_per_repair_batch": 1,
    }
    repaired_generation = {
        "test_cases": cases,
        "case_fact_bindings": bindings,
    }
    rechecks = prepare_final_review_rechecks(
        context,
        {"repair_inputs": result["items"], "generation": repaired_generation},
    )
    recheck_ids = [
        item["review_batch"]["batch_id"] for item in rechecks["items"]
    ]
    assert len(set(recheck_ids)) == 5
    followup_records = [
        _map_record(
            index,
            {
                "phase": "final_review",
                "approved": False,
                "summary": "仍需修复",
                "differences": [
                    {
                        "case_id": f"TC-{index + 1:03d}",
                        "category": "business_semantics",
                        "field_path": "steps[0].expected",
                        "detail": "预期仍不准确",
                        "related_fact_ids": [f"F-{index + 1:03d}"],
                        "repair_instruction": "继续修正预期",
                    }
                ],
            },
        )
        for index in range(5)
    ]
    followups = prepare_final_review_repairs(
        context,
        {
            "review_inputs": rechecks["items"],
            "review_records": followup_records,
            "generation_inputs": [
                {
                    "batch": {"batch_id": "M001-B001"},
                    "authoritative_facts": facts,
                }
            ],
        },
    )
    assert [
        item["review_batch"]["batch_id"] for item in followups["items"]
    ] == recheck_ids
    merged_records = merge_final_review_recheck_records(
        context,
        {
            "baseline_inputs": rechecks["items"],
            "baseline_records": followup_records,
            "replacement_inputs": rechecks["items"],
            "replacement_records": [
                _map_record(index, _review(approved=True))
                for index in range(5)
            ],
        },
    )
    assert merged_records["replaced_count"] == 5


def test_state_coherence_repair_stays_on_located_case_without_target_id() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-MEMBER", "会员用户点击课程模块进入课程列表页"),
        _fact("F-GUEST", "普通用户点击锁定单元进入商品详情页"),
    ]
    member_case = _case("TC-085", "会员用户课程导航", "进入课程列表页")
    member_case["preconditions"] = ["当前登录用户为会员用户"]
    guest_case = _case("TC-086", "普通用户付费权限", "进入商品详情页")
    guest_case["preconditions"] = ["当前登录用户为普通用户"]
    member_binding = _binding("TC-085", "F-MEMBER")
    member_binding["step_bindings"][0]["expected_fact_ids"] = ["F-GUEST"]
    guest_binding = _binding("TC-086", "F-GUEST")
    review_input = {
        "review_batch": {
            "batch_id": "R-001",
            "batch_number": 1,
            "batch_count": 1,
            "module_name": "投稿流程",
            "generation_batch_ids": ["M001-B001"],
            "case_ids": ["TC-085", "TC-086"],
        },
        "test_cases": [member_case, guest_case],
        "case_fact_bindings": [member_binding, guest_binding],
        "review_facts": [
            {
                "fact_id": fact["fact_id"],
                "assertion": fact["assertion"],
                "value_policy": "exact",
                "governed_values": [],
            }
            for fact in facts
        ],
        "test_design_items": [],
        "audit_summary": {},
    }
    review = {
        "phase": "final_review",
        "approved": False,
        "summary": "会员用例引用了普通用户事实",
        "differences": [
            {
                "case_id": "TC-085",
                "category": "state_coherence",
                "field_path": "steps[0].expected",
                "detail": "前置角色与预期事实冲突",
                "related_fact_ids": ["F-GUEST"],
                "repair_instruction": "将普通用户事实调整到角色匹配的用例",
            }
        ],
    }

    result = prepare_final_review_repairs(
        context,
        {
            "review_inputs": [review_input],
            "review_records": [_map_record(0, review)],
            "generation_inputs": [
                {
                    "batch": {"batch_id": "M001-B001"},
                    "authoritative_facts": facts,
                }
            ],
        },
    )

    assert result["repair_batch_count"] == 1
    assert result["items"][0]["target_case_ids"] == ["TC-085"]
    assert set(result["items"][0]["required_fact_ids"]) == {"F-MEMBER", "F-GUEST"}
    assert (
        "F-GUEST=普通用户点击锁定单元进入商品详情页；"
        "F-MEMBER=会员用户点击课程模块进入课程列表页"
        in result["items"][0]["repair_requirements"][0]
    )
    assert "重新分配事实" in result["items"][0]["repair_requirements"][-2]


def test_cohort_repair_without_target_case_id_stays_on_source_case() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-GUEST", "游客点击批改功能跳转登录页"),
        _fact("F-MEMBER", "会员点击收费单元进入课程"),
    ]
    guest_case = _case("TC-101", "游客批改权限", "跳转登录页")
    member_case = _case("TC-102", "会员收费单元", "进入课程")
    review_input = {
        "review_batch": {
            "batch_id": "R-001",
            "batch_number": 1,
            "batch_count": 1,
            "module_name": "权限流程",
            "generation_batch_ids": ["M001-B001"],
            "case_ids": ["TC-101", "TC-102"],
        },
        "test_cases": [guest_case, member_case],
        "case_fact_bindings": [
            _binding("TC-101", "F-GUEST"),
            _binding("TC-102", "F-MEMBER"),
        ],
        "review_facts": [
            {
                "fact_id": fact["fact_id"],
                "assertion": fact["assertion"],
                "value_policy": "exact",
                "governed_values": [],
            }
            for fact in facts
        ],
        "test_design_items": [],
        "audit_summary": {},
    }
    review = {
        "phase": "final_review",
        "approved": False,
        "summary": "同一用例混合了不同角色场景",
        "differences": [
            {
                "case_id": "TC-101",
                "category": "business_semantics",
                "field_path": "steps[0].action",
                "detail": "步骤中的角色发生无过渡切换",
                "related_fact_ids": ["F-GUEST"],
                "repair_scope": "cohort",
                "repair_instruction": "在同批用例槽位间重新分配两个角色场景",
            }
        ],
    }

    result = prepare_final_review_repairs(
        context,
        {
            "review_inputs": [review_input],
            "review_records": [_map_record(0, review)],
            "generation_inputs": [
                {
                    "batch": {"batch_id": "M001-B001"},
                    "authoritative_facts": facts,
                }
            ],
        },
    )

    assert result["repair_batch_count"] == 1
    assert result["items"][0]["target_case_ids"] == ["TC-101"]
    assert result["items"][0]["required_fact_ids"] == ["F-GUEST"]


def test_repair_instruction_expands_to_explicit_same_batch_destination_case() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _fact("F-COST-BASE", "单个年级每月调用成本需要核算"),
        _fact("F-COST-TOTAL", "四个年级每月调用的单次成本合计为0.298元"),
        _fact("F-AUDIT", "后台支持评论审核通过和拒绝"),
    ]
    cost_case = _case("TC-087", "调用成本核算", "展示单年级调用成本")
    audit_case = _case("TC-089", "后台评论审核", "评论审核状态更新")
    cost_binding = _binding("TC-087", "F-COST-BASE")
    audit_binding = _binding("TC-089", "F-AUDIT")
    audit_binding["step_bindings"][0]["expected_fact_ids"] = [
        "F-AUDIT",
        "F-COST-TOTAL",
    ]
    review_input = {
        "review_batch": {
            "batch_id": "R-001",
            "batch_number": 1,
            "batch_count": 1,
            "module_name": "投稿流程",
            "generation_batch_ids": ["M001-B001"],
            "case_ids": ["TC-087", "TC-089"],
        },
        "test_cases": [cost_case, audit_case],
        "case_fact_bindings": [cost_binding, audit_binding],
        "review_facts": [
            {
                "fact_id": fact["fact_id"],
                "assertion": fact["assertion"],
                "value_policy": "exact",
                "governed_values": [],
            }
            for fact in facts
        ],
        "test_design_items": [],
        "audit_summary": {},
    }
    review = {
        "phase": "final_review",
        "approved": False,
        "summary": "成本事实分配到了审核用例",
        "differences": [
            {
                "case_id": "TC-089",
                "category": "business_semantics",
                "field_path": "steps[0].expected",
                "detail": "成本事实与评论审核语义无关",
                "related_fact_ids": ["F-COST-TOTAL"],
                "repair_instruction": "从TC-089移除成本核算步骤，并合并至TC-087",
            }
        ],
    }

    result = prepare_final_review_repairs(
        context,
        {
            "review_inputs": [review_input],
            "review_records": [_map_record(0, review)],
            "generation_inputs": [
                {
                    "batch": {"batch_id": "M001-B001"},
                    "authoritative_facts": facts,
                }
            ],
        },
    )

    assert result["repair_batch_count"] == 1
    repair_input = result["items"][0]
    assert repair_input["target_case_ids"] == ["TC-087", "TC-089"]
    assert set(repair_input["required_fact_ids"]) == {
        "F-COST-BASE",
        "F-COST-TOTAL",
        "F-AUDIT",
    }
    assert "同时修改源用例和目标用例" in repair_input["repair_requirements"][-2]


def test_final_review_repair_defers_unchanged_output_to_recheck() -> None:
    context = SimpleNamespace(artifacts={})
    case = _case("TC-001", "投稿成功", "显示失败提示")
    binding = _binding("TC-001", "F-001")
    source_input = {
        "test_cases": [
                {
                    **case,
                    "preconditions": [
                        {"text": "用户已登录", "fact_ids": ["F-001"]}
                    ],
                    "test_input": {
                        "text": case["test_input"],
                        "fact_ids": ["F-001"],
                    },
                    "steps": [
                    {
                        "action": "点击投稿按钮",
                        "expected": "显示失败提示",
                        "fact_bindings": {
                            "action": ["F-001"],
                            "expected": ["F-001"],
                        },
                    }
                ],
            }
        ],
        "authoritative_facts": [_fact("F-001", "投稿成功后显示成功提示")],
        "required_fact_ids": ["F-001"],
        "target_case_ids": ["TC-001"],
        "target_case_count": 1,
        "test_design_items": [],
        "repair_requirements": ["按事实修正预期结果"],
    }

    result = postprocess_final_review_repair_item(
        context,
        {
            "item_input": source_input,
            "item_output": _inline_repair_output([case], [binding]),
        },
    )

    assert result["review_noop"] is True
    assert result["test_cases"][0]["case_id"] == "TC-001"


def test_final_review_only_rechecks_incrementally_repaired_batches() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [_fact("F-001", "投稿成功后显示成功提示")]
    cases = [
        _case("TC-001", "投稿成功", "显示失败提示"),
        _case("TC-002", "再次投稿", "显示成功提示"),
    ]
    bindings = [_binding("TC-001", "F-001"), _binding("TC-002", "F-001")]
    generation_inputs = [
        {
            "case_budget": 1,
            "batch": {"batch_id": "M001-B001", "module_name": "投稿流程"},
            "authoritative_facts": facts,
        },
        {
            "case_budget": 1,
            "batch": {"batch_id": "M001-B002", "module_name": "投稿流程"},
            "authoritative_facts": facts,
        },
    ]
    generation = {
        "test_cases": cases,
        "case_fact_bindings": bindings,
        "batch_count": 2,
        "case_count": 2,
    }

    prepared = prepare_final_review_batches(
        context,
        {
            "generation_inputs": generation_inputs,
            "generation": generation,
            "batch_case_limit": 1,
        },
    )
    assert prepared["batch_count"] == 1
    assert prepared["items"][0]["review_batch"]["generation_batch_ids"] == [
        "M001-B001",
        "M001-B002",
    ]
    assert "authoritative_facts" not in prepared["items"][0]
    assert prepared["items"][0]["review_facts"] == [
        {
            "fact_id": "F-001",
            "assertion": "投稿成功后显示成功提示",
            "value_policy": "exact",
            "governed_values": [],
        }
    ]
    assert "source_anchor" not in prepared["items"][0]["review_facts"][0]

    repairs = prepare_final_review_repairs(
        context,
        {
                "review_inputs": prepared["items"],
                "review_records": [_map_record(0, _review(approved=False))],
                "generation_inputs": generation_inputs,
        },
    )
    assert repairs["repair_batch_count"] == 1
    assert repairs["items"][0]["target_case_ids"] == ["TC-001"]
    assert repairs["items"][0]["target_case_count"] == 1
    assert "case_fact_bindings" not in repairs["items"][0]
    assert repairs["items"][0]["test_cases"][0]["steps"][0][
        "fact_bindings"
    ] == {"action": ["F-001"], "expected": ["F-001"]}
    repaired_cases = [_case("TC-001", "投稿成功", "显示成功提示")]
    merged = merge_final_review_repairs(
        context,
        {
            "generation": generation,
            "repair_inputs": repairs["items"],
            "repair_records": [
                _map_record(
                    0,
                    {
                        "test_cases": repaired_cases,
                        "case_fact_bindings": [bindings[0]],
                    },
                )
            ],
        },
    )
    assert merged["test_cases"][0]["steps"][0]["expected"] == "显示成功提示"
    assert merged["test_cases"][1] == cases[1]

    rechecks = prepare_final_review_rechecks(
        context,
        {
            "repair_inputs": repairs["items"],
            "generation": merged,
            "generation_inputs": [
                {
                    **generation_inputs[0],
                    "authoritative_facts": [
                        *facts,
                        _fact("F-BATCH-EXTRA", "原始大批次中的其他事实"),
                    ],
                }
            ],
        },
    )
    assert rechecks["recheck_batch_count"] == 1
    assert [fact["fact_id"] for fact in rechecks["items"][0]["review_facts"]] == [
        "F-001"
    ]
    superseded_review = {
        "phase": "final_review",
        "approved": False,
        "summary": "已被复审替换的旧结论",
        "differences": [
            {
                "case_id": "TC-001",
                "category": "semantic_duplicate",
                "field_path": "步骤0,1",
                "detail": "旧模型误判两个步骤重复",
                "related_fact_ids": ["F-001"],
                "repair_instruction": "删除其中一个步骤",
            }
        ],
    }
    repairs["items"][0]["review_batch"]["batch_id"] = "R-001-C001"
    rechecks["items"][0]["review_batch"]["batch_id"] = "R-001-C001"
    summary = merge_final_review_batches(
        context,
        {
            "review_inputs": prepared["items"],
            "review_records": [_map_record(0, superseded_review)],
            "repair_inputs": repairs["items"],
            "recheck_inputs": rechecks["items"],
            "recheck_records": [_map_record(0, _review(approved=True))],
            "audit_summary": {
                "approved": True,
                "case_count": 2,
                "effective_fact_count": 1,
                "covered_fact_count": 1,
                "uncovered_fact_ids": [],
                "invalid_fact_ids": [],
                "duplicate_case_ids": [],
                "test_design_item_count": 0,
                "covered_test_design_item_count": 0,
                "uncovered_test_design_item_ids": [],
                "invalid_test_design_item_ids": [],
                "summary": "确定性审计通过",
                "differences": [],
            },
        },
    )
    assert summary["approved"] is True
    assert "修复并复审 1 个批次" in summary["summary"]


def test_followup_recheck_records_replace_only_matching_batches() -> None:
    context = SimpleNamespace(artifacts={})

    def review_input(batch_id: str) -> dict:
        return {
            "review_batch": {"batch_id": batch_id},
        }

    baseline_inputs = [review_input("R-001"), review_input("R-002")]
    baseline_records = [
        _map_record(0, _review(approved=False)),
        _map_record(1, _review(approved=True)),
    ]
    replacement_inputs = [review_input("R-001")]
    replacement_records = [_map_record(0, _review(approved=True))]

    merged = merge_final_review_recheck_records(
        context,
        {
            "baseline_inputs": baseline_inputs,
            "baseline_records": baseline_records,
            "replacement_inputs": replacement_inputs,
            "replacement_records": replacement_records,
        },
    )

    assert merged["baseline_count"] == 2
    assert merged["replaced_count"] == 1
    assert [item["item_index"] for item in merged["items"]] == [0, 1]
    assert [item["output"]["approved"] for item in merged["items"]] == [True, True]


def test_split_followup_rechecks_merge_back_into_parent_batch_by_case_id() -> None:
    context = SimpleNamespace(artifacts={})

    def review_input(batch_id: str, case_ids: list[str]) -> dict:
        return {
            "review_batch": {"batch_id": batch_id},
            "test_cases": [{"case_id": case_id} for case_id in case_ids],
        }

    first_difference = {
        "case_id": "TC-001",
        "category": "executability",
        "field_path": "preconditions[0]",
        "detail": "前置条件不足",
        "related_fact_ids": [],
        "repair_instruction": "补充前置条件",
    }
    second_difference = {
        **first_difference,
        "case_id": "TC-002",
        "detail": "状态衔接不完整",
    }
    replacement_difference = {
        **second_difference,
        "detail": "修复后仍缺少状态切换",
    }
    merged = merge_final_review_recheck_records(
        context,
        {
            "baseline_inputs": [review_input("R-005", ["TC-001", "TC-002"])],
            "baseline_records": [
                _map_record(
                    0,
                    {
                        "phase": "final_review",
                        "approved": False,
                        "summary": "两条用例待修复",
                        "differences": [first_difference, second_difference],
                    },
                )
            ],
            "replacement_inputs": [
                review_input("R-005-C001", ["TC-001"]),
                review_input("R-005-C002", ["TC-002"]),
            ],
            "replacement_records": [
                _map_record(0, _review(approved=True)),
                _map_record(
                    1,
                    {
                        "phase": "final_review",
                        "approved": False,
                        "summary": "仍需修复",
                        "differences": [replacement_difference],
                    },
                ),
            ],
        },
    )

    assert merged["baseline_count"] == 1
    assert merged["replaced_count"] == 1
    assert merged["items"][0]["output"]["approved"] is False
    assert merged["items"][0]["output"]["differences"] == [replacement_difference]


def test_global_review_does_not_repeat_batch_differences() -> None:
    context = SimpleNamespace(artifacts={})
    batch_difference = _review(approved=False)["differences"][0]
    repeated_difference = {
        **batch_difference,
        "related_fact_ids": [],
    }
    global_difference = {
        "case_id": "TC-002",
        "category": "priority_conflict",
        "field_path": "priority",
        "detail": "相同业务风险的用例优先级冲突",
        "related_fact_ids": [],
        "repair_instruction": "统一相同业务风险的优先级",
    }
    input_payload = {
        "case_index": [{"case_id": "TC-001"}, {"case_id": "TC-002"}],
        "batch_review": {
            "phase": "final_review",
            "approved": False,
            "summary": "分批终审未通过",
            "differences": [batch_difference],
        },
    }

    normalized = postprocess_global_final_review_output(
        context,
        {
            "input_payload": input_payload,
            "output": {
                "approved": False,
                "differences": [repeated_difference, global_difference],
            },
        },
    )

    assert normalized["approved"] is False
    assert normalized["differences"] == [global_difference]
    assert normalized["summary"] == "全局终审未通过：发现 1 项新增跨批差异。"

    only_repeated = postprocess_global_final_review_output(
        context,
        {
            "input_payload": input_payload,
            "output": {
                "approved": False,
                "differences": [repeated_difference],
            },
        },
    )
    assert only_repeated["approved"] is True
    assert only_repeated["differences"] == []


def test_global_review_difference_is_routed_into_terminal_repair() -> None:
    context = SimpleNamespace(artifacts={})
    fact = _fact("F-001", "未开通课程时提示购买")
    case = _case("TC-001", "未开通课程提示", "进入课程首页")
    binding = _binding("TC-001", "F-001")
    generation_inputs = [
        {
            "case_budget": 1,
            "batch": {"batch_id": "M001-B001", "module_name": "投稿流程"},
            "authoritative_facts": [fact],
            "plan": {"test_design_items": []},
        }
    ]

    result = prepare_terminal_final_review_repairs(
        context,
        {
            "generation_inputs": generation_inputs,
            "generation": {
                "test_cases": [case],
                "case_fact_bindings": [binding],
                "batch_count": 1,
                "case_count": 1,
            },
            "batch_case_limit": 1,
            "batch_review": _review(approved=True),
            "global_review": {
                "phase": "final_review",
                "approved": False,
                "summary": "最终预期无法闭环",
                "differences": [
                    {
                        "case_id": "TC-001",
                        "category": "executability",
                        "field_path": "last_expected",
                        "detail": "最终预期没有体现未开通状态",
                        "related_fact_ids": [],
                        "repair_instruction": "按未开通课程状态修正最终预期",
                    }
                ],
            },
        },
    )

    assert result["repair_batch_count"] == 1
    assert len(result["review_inputs"]) == len(result["review_records"]) == 1
    repair = result["items"][0]
    assert repair["target_case_ids"] == ["TC-001"]
    assert repair["review_result"]["differences"][0]["field_path"] == (
        "steps[0].expected"
    )
    assert repair["required_fact_ids"] == ["F-001"]
