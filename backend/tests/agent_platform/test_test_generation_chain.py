from __future__ import annotations

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError, validate

from core.db.database import SessionLocal
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import _output_model_from_schema
from modules.agent_platform.test_generation_batching import (
    prepare_execution_chain_context,
    validate_execution_chain,
)
from modules.agent_platform.test_generation_workflow import (
    CHAIN_CONTEXT_SCHEMA,
    EXECUTION_CHAIN_SELECTION_SCHEMA,
    EXECUTION_PLAN_SCHEMA,
)


def _context(node_key: str = "validate_chain") -> ToolExecutionContext:
    return ToolExecutionContext(
        db=SessionLocal(),
        user_id=1,
        project_id=2,
        run_id=38,
        node_key=node_key,
        run_input={},
    )


def _case(
    case_id: str,
    precondition: str,
    action: str,
    expected: str,
    *,
    module: str = "通用流程",
) -> dict:
    return {
        "case_id": case_id,
        "title": f"状态迁移 {case_id}",
        "module": module,
        "priority": "P0",
        "preconditions": [precondition] if precondition else [],
        "steps": [{"action": action, "expected": expected}],
        "tags": ["主流程"],
    }


def _cases() -> list[dict]:
    return [
        _case("TC-001", "业务未提交", "提交业务", "业务处理中"),
        _case("TC-002", "业务处理中", "完成处理", "业务已完成"),
        _case(
            "TC-003",
            "业务已完成",
            "查看结果",
            "结果内容正确展示",
            module="结果展示",
        ),
    ]


def _selection(*case_ids: str) -> dict:
    return {
        "name": "业务处理主链",
        "goal": "验证业务从提交到处理完成的连续流程",
        "case_ids": list(case_ids),
    }


def test_prepare_execution_chain_only_returns_small_strict_candidates() -> None:
    context = _context("prepare_chain")
    try:
        result = prepare_execution_chain_context(
            context,
            {
                "plan": {
                    "requirement_summary": "验证业务处理流程",
                    "business_modules": [
                        {
                            "name": "通用流程",
                            "objective": "完成业务处理",
                            "evidence_ids": ["EV-0001"],
                        },
                        {
                            "name": "结果展示",
                            "objective": "查看处理结果",
                            "evidence_ids": ["EV-0002"],
                        },
                    ],
                },
                "test_cases": _cases(),
            },
        )
    finally:
        context.db.close()

    assert set(result) == {"plan_summary", "candidate_chains"}
    assert 1 <= len(result["candidate_chains"]) <= 6
    assert result["candidate_chains"][0]["case_ids"] == [
        "TC-001",
        "TC-002",
        "TC-003",
    ]
    candidate_case = result["candidate_chains"][0]["cases"][1]
    assert candidate_case["from_state"] == "业务处理中"
    assert candidate_case["to_state"] == "业务已完成"
    assert "steps" not in candidate_case


def test_prepare_execution_chain_returns_collection_mode_when_no_exact_edge() -> None:
    cases = _cases()[:2]
    cases[1]["preconditions"] = ["业务处理中 "]
    context = _context("prepare_chain")
    try:
        result = prepare_execution_chain_context(
            context,
            {"plan": {}, "test_cases": cases},
        )
    finally:
        context.db.close()

    assert result["candidate_chains"] == []
    assert context.artifacts["execution_chain_candidates"] == {
        "eligible_case_count": 2,
        "strict_edge_count": 0,
        "candidate_count": 0,
        "candidate_case_counts": [],
        "execution_mode": "collection_only",
    }


def test_validate_execution_plan_uses_only_collections_without_reliable_edge() -> None:
    cases = [
        _case("TC-001", "", "查看课程", "课程内容正确展示", module="课程学习"),
        _case("TC-002", "", "查看作品", "作品内容正确展示", module="作品展示"),
    ]
    context = _context()
    try:
        result = validate_execution_chain(
            context,
            {
                "test_cases": cases,
                "chain_selection": {"name": "", "goal": "", "case_ids": []},
            },
        )
    finally:
        context.db.close()

    assert result["status"] == "passed"
    assert result["main_chain_case_count"] == 0
    assert result["assigned_count"] == 2
    plan = result["execution_plan"]
    assert plan["main_chain_suite_id"] == ""
    assert all(suite["suite_type"] == "collection" for suite in plan["suites"])
    assert [suite["case_ids"] for suite in plan["suites"]] == [
        ["TC-001"],
        ["TC-002"],
    ]


def test_validate_execution_plan_rejects_fake_chain_without_reliable_edge() -> None:
    cases = [
        _case("TC-001", "", "查看课程", "课程内容正确展示"),
        _case("TC-002", "", "查看作品", "作品内容正确展示"),
    ]
    context = _context()
    try:
        with pytest.raises(ValueError, match="无可靠状态边时不能伪造执行主链"):
            validate_execution_chain(
                context,
                {
                    "test_cases": cases,
                    "chain_selection": _selection("TC-001", "TC-002"),
                },
            )
    finally:
        context.db.close()


def test_validate_chain_builds_transitions_and_module_collections() -> None:
    context = _context()
    try:
        result = validate_execution_chain(
            context,
            {
                "test_cases": _cases(),
                "chain_selection": _selection("TC-001", "TC-002"),
            },
        )
    finally:
        context.db.close()

    assert result["status"] == "passed"
    assert result["assigned_count"] == 3
    assert result["main_chain_case_count"] == 2
    plan = result["execution_plan"]
    assert plan["main_chain_suite_id"] == "suite-main"
    assert plan["suites"][0]["transitions"] == [
        {
            "case_id": "TC-001",
            "from_state": "业务未提交",
            "to_state": "业务处理中",
        },
        {
            "case_id": "TC-002",
            "from_state": "业务处理中",
            "to_state": "业务已完成",
        },
    ]
    assert plan["suites"][1]["name"] == "结果展示用例集"
    assert plan["suites"][1]["case_ids"] == ["TC-003"]


def test_validate_chain_rejects_discontinuous_selection() -> None:
    context = _context()
    try:
        with pytest.raises(ValueError, match="chain 相邻迁移不连续"):
            validate_execution_chain(
                context,
                {
                    "test_cases": _cases(),
                    "chain_selection": _selection("TC-001", "TC-003"),
                },
            )
    finally:
        context.db.close()


def test_validate_chain_uses_only_last_step_assertion_as_terminal_state() -> None:
    cases = _cases()
    cases[0]["steps"] = [
        {"action": "提交业务", "expected": "业务处理中"},
        {"action": "等待处理", "expected": "业务已受理"},
    ]
    context = _context()
    try:
        with pytest.raises(ValueError, match="chain 相邻迁移不连续"):
            validate_execution_chain(
                context,
                {
                    "test_cases": cases,
                    "chain_selection": _selection("TC-001", "TC-002"),
                },
            )
    finally:
        context.db.close()


def test_validate_chain_rejects_case_without_entry_state() -> None:
    cases = _cases()
    cases[0]["preconditions"] = []
    context = _context()
    try:
        with pytest.raises(ValueError, match="无有效入口或终态"):
            validate_execution_chain(
                context,
                {
                    "test_cases": cases,
                    "chain_selection": _selection("TC-001", "TC-002"),
                },
            )
    finally:
        context.db.close()


def test_validate_chain_rejects_duplicate_source_case_id() -> None:
    cases = _cases()
    cases.append(dict(cases[0]))
    context = _context()
    try:
        with pytest.raises(ValueError, match="源测试用例 case_id 重复"):
            validate_execution_chain(
                context,
                {
                    "test_cases": cases,
                    "chain_selection": _selection("TC-001", "TC-002"),
                },
            )
    finally:
        context.db.close()


def test_chain_selection_schema_rejects_old_full_suite_contract() -> None:
    old_contract = {
        "main_chain_suite_id": "suite-main",
        "suites": [
            {
                "suite_id": "suite-main",
                "name": "旧主链",
                "goal": "旧契约",
                "suite_type": "chain",
                "case_ids": [],
                "transitions": [],
            }
        ],
    }

    with pytest.raises(JsonSchemaValidationError):
        validate(instance=old_contract, schema=EXECUTION_CHAIN_SELECTION_SCHEMA)


def test_chain_contracts_allow_explicit_collection_only_mode() -> None:
    validate(
        instance={
            "plan_summary": {"requirement_summary": "", "business_modules": []},
            "candidate_chains": [],
        },
        schema=CHAIN_CONTEXT_SCHEMA,
    )
    validate(
        instance={"name": "", "goal": "", "case_ids": []},
        schema=EXECUTION_CHAIN_SELECTION_SCHEMA,
    )
    validate(
        instance={
            "main_chain_suite_id": "",
            "suites": [
                {
                    "suite_id": "suite-module-001",
                    "name": "课程学习用例集",
                    "goal": "执行课程学习模块独立用例",
                    "suite_type": "collection",
                    "case_ids": ["TC-001"],
                    "transitions": [],
                }
            ],
        },
        schema=EXECUTION_PLAN_SCHEMA,
    )


def test_chain_selection_sdk_output_model_accepts_collection_only_mode() -> None:
    output_model = _output_model_from_schema(
        EXECUTION_CHAIN_SELECTION_SCHEMA,
        "test_execution_chain_builder_output",
    )

    output = output_model(name="", goal="", case_ids=[])

    assert output.model_dump() == {"name": "", "goal": "", "case_ids": []}
