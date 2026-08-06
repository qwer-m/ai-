from __future__ import annotations

import re
import unicodedata

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError, validate

from core.db.database import SessionLocal
from core.db.model_defs import KnowledgeDocument, Project
from modules.knowledge_base_components.document.document_asset_service import (
    load_document_manifest,
)
from modules.agent_platform.registry import ToolExecutionContext, tool_registry
from modules.agent_platform.test_generation_workflow import (
    CASE_SCHEMA,
    EVIDENCE_OUTPUT_SCHEMA,
    PLAN_SCHEMA,
    PLANNER_OUTPUT_SCHEMA,
    persist_generated_test_cases,
    resolve_requirement_evidence,
    validate_generated_test_cases,
)


def _cases() -> list[dict]:
    return [
        {
            "case_id": "TC-001",
            "title": "管理员创建项目",
            "module": "项目管理",
            "priority": "P0",
            "preconditions": ["管理员已登录"],
            "steps": [
                {
                    "action": "输入唯一项目名称并提交",
                    "expected": "项目创建成功且出现在项目列表中",
                }
            ],
            "tags": ["主流程"],
        },
        {
            "case_id": "TC-002",
            "title": "重复项目名称被拒绝",
            "module": "项目管理",
            "priority": "P1",
            "preconditions": ["项目创建成功且出现在项目列表中"],
            "steps": [
                {
                    "action": "再次提交相同项目名称",
                    "expected": "系统拒绝提交并提示项目名称已存在",
                }
            ],
            "tags": ["异常路径"],
        },
    ]


def _case_fact_bindings() -> list[dict]:
    return [
        {
            "case_id": case["case_id"],
            "precondition_bindings": [
                {"precondition_index": index, "fact_ids": ["FACT-001"]}
                for index, _ in enumerate(case["preconditions"])
            ],
            "step_bindings": [
                {
                    "step_index": index,
                    "action_fact_ids": ["FACT-001"],
                    "expected_fact_ids": ["FACT-001"],
                }
                for index, _ in enumerate(case["steps"])
            ],
        }
        for case in _cases()
    ]


def _context(db, *, project_id: int = 66, user_id: int = 1) -> ToolExecutionContext:
    return ToolExecutionContext(
        db=db,
        user_id=user_id,
        project_id=project_id,
        run_id=1001,
        node_key="validate",
        run_input={"requirement": "管理员可以创建项目，项目名称必须唯一。"},
    )


def test_native_validation_accepts_complete_real_cases() -> None:
    db = SessionLocal()
    try:
        context = _context(db)
        result = validate_generated_test_cases(
            context,
            {
                "requirement": "管理员可以创建项目，项目名称必须唯一。",
                "case_budget": 2,
                "test_cases": _cases(),
            },
        )

        assert result["status"] == "passed"
        assert result["validated_count"] == 2
        assert result["priority_counts"] == {"P0": 1, "P1": 1, "P2": 0}
        assert context.artifacts == {}
    finally:
        db.close()


def test_native_validation_rejects_under_target_count() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="测试用例数量未达到精确目标"):
            validate_generated_test_cases(
                _context(db),
                {
                    "requirement": "管理员可以创建项目，项目名称必须唯一。",
                    "case_budget": 3,
                    "test_cases": _cases(),
                },
            )
    finally:
        db.close()


def test_native_validation_rejects_semantic_duplicates() -> None:
    cases = _cases()
    cases[1]["title"] = "管理员创建项目"
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="用例语义重复"):
            validate_generated_test_cases(
                _context(db),
                {
                    "requirement": "管理员可以创建项目，项目名称必须唯一。",
                    "case_budget": 2,
                    "test_cases": cases,
                },
            )
    finally:
        db.close()


def test_native_validation_rejects_empty_step_assertion() -> None:
    cases = _cases()
    cases[0]["steps"][0]["expected"] = ""
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="第 1 条用例的第 1 个预期不能为空"):
            validate_generated_test_cases(
                _context(db),
                {
                    "requirement": "管理员可以创建项目，项目名称必须唯一。",
                    "case_budget": 2,
                    "test_cases": cases,
                },
            )
    finally:
        db.close()


def test_agent_case_schema_rejects_redundant_expected_result() -> None:
    case = {**_cases()[0], "expected_result": "不再允许的重复断言"}

    with pytest.raises(JsonSchemaValidationError):
        validate(instance=case, schema=CASE_SCHEMA)


def test_planner_schema_forbids_evidence_ids() -> None:
    plan = {
        "requirement_summary": "用户可以进入课程学习。",
        "business_modules": [
            {
                "name": "课程学习",
                "objective": "验证课程进入与学习",
                "actors": ["用户"],
                "lifecycle": None,
            }
        ],
        "coverage_focus": ["课程进入"],
        "risks": ["课程入口不可用"],
    }

    validate(instance=plan, schema=PLANNER_OUTPUT_SCHEMA)
    plan["business_modules"][0]["evidence_ids"] = ["EV-0001"]
    with pytest.raises(JsonSchemaValidationError):
        validate(instance=plan, schema=PLANNER_OUTPUT_SCHEMA)


def test_merged_plan_schema_requires_catalog_ids_for_every_module() -> None:
    base = {
        "requirement_summary": "用户可以进入课程学习。",
        "coverage_focus": ["课程进入"],
        "risks": ["页面事实遗漏"],
    }
    text_module = {
        "name": "课程学习",
        "objective": "验证课程进入与学习",
        "actors": ["用户"],
        "lifecycle": None,
        "evidence_ids": ["EV-0001"],
    }
    second_module = {
        "name": "页面布局",
        "objective": "核验真实页图布局",
        "actors": ["用户"],
        "lifecycle": None,
        "evidence_ids": ["EV-0002"],
    }

    validate(
        instance={**base, "business_modules": [text_module, second_module]},
        schema=PLAN_SCHEMA,
    )
    second_module["evidence_ids"] = []
    with pytest.raises(JsonSchemaValidationError):
        validate(
            instance={**base, "business_modules": [text_module, second_module]},
            schema=PLAN_SCHEMA,
        )


def test_native_validation_rejects_empty_grounded_result() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="事实对齐后没有可用测试用例"):
            validate_generated_test_cases(
                _context(db),
                {
                    "requirement": "管理员可以创建项目，项目名称必须唯一。",
                    "case_budget": 2,
                    "test_cases": [],
                },
            )
    finally:
        db.close()


def test_native_persistence_writes_run_artifact_only() -> None:
    db = SessionLocal()
    context = _context(db)
    try:
        result = persist_generated_test_cases(
            context,
            {
                "requirement": "管理员可以创建项目，项目名称必须唯一。",
                "evidence_source": {
                    "kind": "inline",
                    "document_id": None,
                    "filename": "",
                    "doc_type": "inline_requirement",
                    "content_hash": "0" * 64,
                    },
                    "test_cases": _cases(),
                    "case_fact_bindings": _case_fact_bindings(),
                    "execution_plan": {
                        "main_chain_suite_id": "FLOW-001",
                        "suites": [
                            {
                                "suite_id": "FLOW-001",
                                "name": "项目创建主链",
                                "goal": "验证项目创建与重复名称约束",
                                "suite_type": "chain",
                                "case_ids": [],
                                "transitions": [
                                    {
                                        "case_id": "TC-001",
                                        "from_state": "管理员已登录",
                                        "to_state": "项目创建成功且出现在项目列表中",
                                    },
                                    {
                                        "case_id": "TC-002",
                                        "from_state": "项目创建成功且出现在项目列表中",
                                        "to_state": "系统拒绝提交并提示项目名称已存在",
                                    },
                                ],
                            }
                        ],
                    },
                },
            )
        assert result["run_id"] == context.run_id
        assert context.artifacts["test_generation"]["run_id"] == context.run_id
        assert context.artifacts["test_generation"]["test_cases"] == _cases()
        assert context.artifacts["test_generation"]["case_fact_bindings"] == _case_fact_bindings()
        assert context.artifacts["test_generation"]["execution_plan"]["main_chain_suite_id"] == "FLOW-001"
        assert result["persisted_count"] == 2
    finally:
        db.rollback()
        db.close()


def test_registry_contains_only_native_platform_tools() -> None:
    expected_testing_tools = {
        "testing.merge_grounded_generation_batches",
        "testing.merge_plan_evidence_routing",
        "testing.persist_automation_evaluation",
        "testing.persist_test_case_evaluation",
        "testing.persist_test_cases",
        "testing.prepare_execution_chain",
        "testing.prepare_test_case_batches",
        "testing.resolve_requirement_evidence",
        "testing.validate_execution_chain",
        "testing.validate_test_cases",
    }
    assert expected_testing_tools.issubset(set(tool_registry.keys()))


def test_requirement_evidence_reads_real_requirement_document_only() -> None:
    db = SessionLocal()
    try:
        requirement = (
            db.query(KnowledgeDocument)
            .join(Project, Project.id == KnowledgeDocument.project_id)
            .filter(
                KnowledgeDocument.doc_type == "requirement",
                KnowledgeDocument.parse_status == "success",
                KnowledgeDocument.content != "",
            )
            # 使用最新完成解析的真实需求，避免旧库中仅有正文、没有原始索引分片的脏记录。
            .order_by(KnowledgeDocument.id.desc())
            .first()
        )
        assert requirement is not None
        project = db.query(Project).filter(Project.id == requirement.project_id).one()
        manifest = load_document_manifest(int(requirement.id))
        if int(manifest.get("schema_version") or 0) != 3:
            with pytest.raises(ValueError, match="必须重新解析"):
                resolve_requirement_evidence(
                    _context(db, project_id=project.id, user_id=project.user_id),
                    {
                        "requirement": "",
                        "requirement_doc_id": requirement.id,
                    },
                )
            return
        result = resolve_requirement_evidence(
            _context(db, project_id=project.id, user_id=project.user_id),
            {
                "requirement": "",
                "requirement_doc_id": requirement.id,
            },
        )

        expected_requirement = unicodedata.normalize("NFKC", requirement.content)
        expected_requirement = re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+",
            " ",
            expected_requirement,
        )
        assert result["requirement"] == expected_requirement
        assert "\x01" not in result["requirement"]
        assert result["source"]["content_hash"] == requirement.content_hash
        assert result["source"]["document_id"] == requirement.id
        validate(instance=result, schema=EVIDENCE_OUTPUT_SCHEMA)
        assert set(result) == {"requirement", "source", "evidence_catalog"}
        assert result["evidence_catalog"]["document_id"] == requirement.id
        assert result["evidence_catalog"]["items"]
        evidence_ids = [
            item["evidence_id"] for item in result["evidence_catalog"]["items"]
        ]
        assert evidence_ids[0] == "EV-0001"
        assert len(evidence_ids) == len(set(evidence_ids))
    finally:
        db.close()
