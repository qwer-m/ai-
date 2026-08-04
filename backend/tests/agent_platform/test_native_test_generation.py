from __future__ import annotations

import pytest

from core.db.database import SessionLocal
from core.db.model_defs import KnowledgeDocument, Project
from modules.agent_platform.registry import ToolExecutionContext, tool_registry
from modules.agent_platform.test_generation_workflow import (
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
            "expected_result": "数据库存在对应项目且页面展示一致",
            "tags": ["主流程"],
        },
        {
            "case_id": "TC-002",
            "title": "重复项目名称被拒绝",
            "module": "项目管理",
            "priority": "P1",
            "preconditions": ["同名项目已存在"],
            "steps": [
                {
                    "action": "再次提交相同项目名称",
                    "expected": "系统拒绝提交并提示项目名称已存在",
                }
            ],
            "expected_result": "项目数量不变且返回明确冲突信息",
            "tags": ["异常路径"],
        },
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
                "case_budget": 3,
                "test_cases": _cases(),
            },
        )

        assert result["status"] == "passed"
        assert result["validated_count"] == 2
        assert result["priority_counts"] == {"P0": 1, "P1": 1, "P2": 0}
        assert context.artifacts == {}
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
            },
        )
        assert result["run_id"] == context.run_id
        assert context.artifacts["test_generation"]["run_id"] == context.run_id
        assert context.artifacts["test_generation"]["test_cases"] == _cases()
        assert result["persisted_count"] == 2
    finally:
        db.rollback()
        db.close()


def test_registry_contains_only_native_platform_tools() -> None:
    assert tool_registry.keys() == [
        "testing.persist_automation_evaluation",
        "testing.persist_test_case_evaluation",
        "testing.persist_test_cases",
        "testing.resolve_requirement_evidence",
        "testing.validate_test_cases",
    ]


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
            .order_by(KnowledgeDocument.id.asc())
            .first()
        )
        assert requirement is not None
        project = db.query(Project).filter(Project.id == requirement.project_id).one()
        result = resolve_requirement_evidence(
            _context(db, project_id=project.id, user_id=project.user_id),
            {
                "requirement": "",
                "requirement_doc_id": requirement.id,
            },
        )

        assert result["requirement"] == requirement.content.strip()
        assert result["source"]["document_id"] == requirement.id
        assert set(result) == {"requirement", "source"}
    finally:
        db.close()
