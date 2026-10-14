"""使用真实 MySQL 已有记录，只读验证知识库列表与共享项目权限。"""

from collections import defaultdict
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import KnowledgeDocument, Project, User
from modules.automation_components.services.api_automation_service import APIAutomationService
from modules.automation_components.services.ui_automation_service import UIAutomationService
from modules.automation_components.services.ui_test_case_service import UITestCaseService
from modules.knowledge_base_components.repositories.knowledge_document_repository import USER_MANAGED_DOC_TYPES
from modules.system_components.repositories.project_repository import ProjectRepository
from modules.testing_components.services.standard_interface_service import StandardInterfaceService
from routers.orchestration.evaluation_history_routes import get_evaluation_history
from routers.orchestration.evaluation_shared import get_owned_project
from routers.system import common


@pytest.fixture(scope="module")
def real_db():
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield db
        finally:
            db.rollback()


@pytest.fixture(scope="module")
def document_groups(real_db):
    """只读取比较排序和过滤结果所需的真实元数据。"""
    rows = real_db.query(
        KnowledgeDocument.id, KnowledgeDocument.project_id, KnowledgeDocument.doc_type,
        KnowledgeDocument.source_doc_id, KnowledgeDocument.display_order, KnowledgeDocument.created_at,
    ).join(Project, Project.id == KnowledgeDocument.project_id).all()
    groups = defaultdict(list)
    for row in rows:
        groups[row.project_id].append(row)
    if not groups:
        pytest.skip("真实数据库中没有可验证的项目文档")
    return groups


def _is_default_visible(row):
    return row.doc_type in USER_MANAGED_DOC_TYPES and not (
        row.doc_type == "test_case" and row.source_doc_id is not None
    )


def _document_order(row):
    # MySQL 降序时 NULL 在末尾，创建时间升序时 NULL 在开头。
    return (
        row.display_order is None, -float(row.display_order or 0),
        row.created_at is not None, row.created_at or datetime.min, row.id,
    )


def _owner(real_db, project_id):
    project = real_db.get(Project, project_id)
    owner = real_db.get(User, project.user_id) if project is not None else None
    if owner is None:
        pytest.skip("真实项目没有可用的所属用户")
    return owner


def test_list_knowledge_orders_by_display_order(real_db, document_groups):
    for project_id, rows in document_groups.items():
        visible = [row for row in rows if _is_default_visible(row)]
        if len(visible) > 1 and len({row.display_order for row in visible}) > 1:
            break
    else:
        pytest.skip("真实文档缺少同一项目下的不同排序权重")
    expected = sorted(visible, key=_document_order)
    result = common.list_knowledge(
        project_id=project_id, page=1, page_size=len(visible),
        db=real_db, current_user=_owner(real_db, project_id),
    )
    assert [item["id"] for item in result["documents"]] == [row.id for row in expected]
    assert result["pagination"]["total"] == len(visible)
    print({"project_id": project_id, "ordered_document_count": len(visible)})


def test_list_knowledge_clamps_out_of_range_page(real_db, document_groups):
    for project_id, rows in document_groups.items():
        visible = [row for row in rows if _is_default_visible(row)]
        if len(visible) > 1:
            break
    else:
        pytest.skip("真实文档数量不足以验证超页回落")
    page_size = max(1, len(visible) // 2)
    total_pages = (len(visible) + page_size - 1) // page_size
    result = common.list_knowledge(
        project_id=project_id, page=total_pages + 1, page_size=page_size,
        db=real_db, current_user=_owner(real_db, project_id),
    )
    expected = sorted(visible, key=_document_order)[(total_pages - 1) * page_size :]
    assert result["pagination"] == {
        "page": total_pages, "page_size": page_size, "total": len(visible), "total_pages": total_pages,
    }
    assert [item["id"] for item in result["documents"]] == [row.id for row in expected]


def test_knowledge_list_hides_real_internal_artifacts(real_db, document_groups):
    for project_id, rows in document_groups.items():
        public_ids = {row.id for row in rows if row.doc_type in USER_MANAGED_DOC_TYPES}
        internal_ids = {row.id for row in rows if row.doc_type not in USER_MANAGED_DOC_TYPES}
        if public_ids and internal_ids:
            break
    else:
        pytest.skip("真实数据库没有同时包含公开文档与内部产物的项目，未虚构过滤样本")
    arguments = {
        "project_id": project_id, "page_size": len(rows), "include_linked_test_cases": True,
        "db": real_db, "current_user": _owner(real_db, project_id),
    }
    public = common.list_knowledge(**arguments)
    all_documents = common.list_knowledge(**arguments, include_internal_artifacts=True)
    assert {item["id"] for item in public["documents"]} == public_ids
    assert {item["id"] for item in all_documents["documents"]} == public_ids | internal_ids


@pytest.fixture(scope="module")
def ownership_sample(real_db):
    project = real_db.query(Project).join(User, User.id == Project.user_id).order_by(Project.id).first()
    if project is None:
        pytest.skip("真实数据库中没有所属用户有效的项目")
    other_user = real_db.query(User).filter(User.id != project.user_id).order_by(User.id).first()
    if other_user is None:
        pytest.skip("真实数据库不足两名用户，无法验证跨用户权限")
    return project, _owner(real_db, project.id), other_user


def test_services_share_project_repository_and_request_session(real_db, ownership_sample):
    project, owner, other_user = ownership_sample
    for service_type in (APIAutomationService, UIAutomationService, UITestCaseService, StandardInterfaceService):
        service = service_type(real_db)
        assert type(service.projects) is ProjectRepository
        assert service.projects.db is service.repo.db is real_db
        assert service.projects.get_owned_project(project_id=project.id, user_id=owner.id) is project
        assert service.projects.get_owned_project(project_id=project.id, user_id=other_user.id) is None
        assert not hasattr(service.repo, "get_owned_project")
    print({"project_id": project.id, "owner_id": owner.id, "other_user_id": other_user.id, "service_count": 4})


def test_project_permission_contracts_survive_repository_merge(real_db, ownership_sample):
    project, owner, other_user = ownership_sample
    assert get_owned_project(project.id, real_db, owner.id) is project
    assert "documents" in common.list_knowledge(project_id=project.id, db=real_db, current_user=owner)
    assert "history" in get_evaluation_history(project_id=project.id, db=real_db, current_user=owner)
    with pytest.raises(HTTPException) as denied:
        get_owned_project(project.id, real_db, other_user.id)
    assert (denied.value.status_code, denied.value.detail) == (404, "Project not found")
    for route in (common.list_knowledge, get_evaluation_history):
        with pytest.raises(HTTPException) as denied:
            route(project_id=project.id, db=real_db, current_user=other_user)
        assert (denied.value.status_code, denied.value.detail) == (404, "Project not found")
