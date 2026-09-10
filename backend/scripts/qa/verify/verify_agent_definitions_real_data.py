"""只读提取真实定义，在隔离数据库验证内置同步、项目工具继承和事务边界。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentDefinition, AgentRun, AgentToolBinding, AgentToolDefinition,
    AgentWorkflowDefinition, KnowledgeDocument, Project,
)
from modules.agent_platform.contracts import (
    AgentRunCreate, WorkflowDefinitionCreate, parse_execution_definition,
)
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import (
    BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS,
)
from modules.agent_platform.seed import seed_builtin_definitions
from modules.agent_platform.service import AgentPlatformService
from modules.agent_platform.sources import (
    SourceSnapshot, assert_same_source, persisted_source_snapshot,
)
# 复用真实回放已有的 LONGTEXT 方言转换注册，文档内容保持原样。
from scripts.qa.verify.verify_lifecycle_sources_real_data import sqlite_longtext as _sqlite_longtext


DEFINITION_MODELS = (
    AgentDefinition, AgentToolDefinition, AgentWorkflowDefinition, AgentToolBinding,
)
REPLAY_MODELS = (Project, *DEFINITION_MODELS, KnowledgeDocument, AgentRun)


def record_values(record: Any, *, timestamps: bool = True) -> dict[str, Any]:
    """复制真实列值；生成列交给隔离数据库按模型计算。"""
    return {
        column.key: deepcopy(getattr(record, column.key))
        for column in record.__table__.columns
        if column.computed is None
        and (timestamps or column.key not in {"created_at", "updated_at"})
    }


def definition_snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    return {
        model.__tablename__: [
            record_values(row, timestamps=False)
            for row in db.scalars(select(model).order_by(model.id)).all()
        ]
        for model in DEFINITION_MODELS
    }


def snapshot_digest(snapshot: Any) -> str:
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@contextmanager
def isolated_database(records: list[Any]) -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        for model in REPLAY_MODELS:
            model.__table__.create(engine)
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            db.add_all([type(record)(**record_values(record)) for record in records])
            db.commit()
            yield db
    finally:
        engine.dispose()


def synchronize(db: Session, project: Project) -> None:
    seed_builtin_definitions(db=db, project_id=project.id, user_id=project.user_id)


def assert_registry_values(db: Session, project_id: int) -> None:
    agent_fields = (
        "name", "description", "instructions", "model", "output_schema", "runtime_config",
    )
    tool_fields = (
        "name", "description", "handler_key", "input_schema", "output_schema",
        "risk_level", "requires_approval",
    )
    for spec in BUILTIN_AGENT_SPECS:
        row = db.scalars(select(AgentDefinition).where(
            AgentDefinition.project_id.is_(None),
            AgentDefinition.agent_key == spec["agent_key"],
            AgentDefinition.version == int(spec.get("version") or 1),
        )).one()
        assert row.enabled and row.builtin
        assert all(getattr(row, key) == spec[key] for key in agent_fields), row.agent_key
    for spec in BUILTIN_TOOL_SPECS:
        row = db.scalars(select(AgentToolDefinition).where(
            AgentToolDefinition.project_id == project_id,
            AgentToolDefinition.tool_key == spec["tool_key"],
        )).one()
        assert row.enabled
        assert all(getattr(row, key) == spec[key] for key in tool_fields), row.tool_key
    for spec in BUILTIN_WORKFLOW_SPECS:
        row = db.scalars(select(AgentWorkflowDefinition).where(
            AgentWorkflowDefinition.project_id == project_id,
            AgentWorkflowDefinition.workflow_key == spec["workflow_key"],
            AgentWorkflowDefinition.version == int(spec.get("version") or 1),
        )).one()
        assert row.enabled and row.builtin
        assert row.name == spec["name"] and row.description == spec["description"]
        assert row.definition == parse_execution_definition(spec["definition"]).model_dump()


def verify_synchronization(db: Session, project: Project) -> dict[str, Any]:
    synchronize(db, project)
    db.commit()
    assert_registry_values(db, project.id)
    first = definition_snapshot(db)
    synchronize(db, project)
    db.commit()
    assert definition_snapshot(db) == first, "重复同步改变了定义内容或主键"

    agent = db.scalars(select(AgentDefinition).where(
        AgentDefinition.project_id.is_(None), AgentDefinition.builtin.is_(True),
        AgentDefinition.enabled.is_(True),
    ).order_by(AgentDefinition.id)).first()
    workflow = db.scalars(select(AgentWorkflowDefinition).where(
        AgentWorkflowDefinition.project_id == project.id,
        AgentWorkflowDefinition.builtin.is_(True), AgentWorkflowDefinition.enabled.is_(True),
    ).order_by(AgentWorkflowDefinition.id)).first()
    tool = db.scalars(select(AgentToolDefinition).where(
        AgentToolDefinition.project_id == project.id,
        AgentToolDefinition.builtin.is_(True), AgentToolDefinition.enabled.is_(True),
    ).order_by(AgentToolDefinition.id)).first()
    assert agent is not None and workflow is not None and tool is not None
    original_ids_versions = (agent.id, agent.version, workflow.id, workflow.version, tool.id)
    # 仅删除隔离副本的配置字段，不编造业务文本，也不修改版本号。
    agent.instructions, agent.runtime_config = "", {}
    workflow.definition, tool.input_schema = {}, {}
    db.commit()
    synchronize(db, project)
    db.commit()
    assert_registry_values(db, project.id)
    assert original_ids_versions == (agent.id, agent.version, workflow.id, workflow.version, tool.id)
    assert definition_snapshot(db) == first
    return {
        "status": "passed", "idempotent": "passed", "same_version_repair": "passed",
        "repaired_agent_id": agent.id, "repaired_workflow_id": workflow.id,
        "repaired_tool_id": tool.id, "content_sha256": snapshot_digest(first),
        "row_counts": {name: len(rows) for name, rows in first.items()},
    }


def verify_retirement(db: Session, project: Project) -> dict[str, Any]:
    cases = (
        (AgentDefinition, BUILTIN_AGENT_SPECS, "agent_key", AgentDefinition.project_id.is_(None)),
        (AgentWorkflowDefinition, BUILTIN_WORKFLOW_SPECS, "workflow_key",
         AgentWorkflowDefinition.project_id == project.id),
    )
    version_results = []
    for model, specs, key_name, scope in cases:
        versions = {spec[key_name]: int(spec.get("version") or 1) for spec in specs}
        rows = db.scalars(select(model).where(scope, model.builtin.is_(True))).all()
        old = [row for row in rows if getattr(row, key_name) in versions
               and row.version < versions[getattr(row, key_name)]]
        source = "persisted_history"
        if not old:
            # 缺少历史版本时仅变更真实定义副本的版本元数据，正文和配置保持真实。
            current = next((row for row in rows if row.enabled and row.version > 1), None)
            if current is not None:
                values = record_values(current)
                values.pop("id")
                values["version"] = current.version - 1
                previous = model(**values)
                db.add(previous)
                db.flush()
                old = [previous]
                source = "real_definition_copy_with_previous_version_metadata"
        for row in old:
            row.enabled = True
        db.commit()
        synchronize(db, project)
        db.commit()
        assert all(not row.enabled for row in old)
        version_results.append({
            "table": model.__tablename__, "status": "passed" if old else "not_tested",
            "source": source, "disabled_row_ids": [row.id for row in old],
            "reason": None if old else "所有真实定义均为第一版，没有有效的更早版本可回放",
        })

    active_keys = {str(spec["tool_key"]) for spec in BUILTIN_TOOL_SPECS}
    retired_tools = db.scalars(select(AgentToolDefinition).where(
        AgentToolDefinition.project_id == project.id,
        AgentToolDefinition.builtin.is_(True),
        AgentToolDefinition.tool_key.notin_(active_keys),
    )).all()
    retired_ids = [tool.id for tool in retired_tools]
    bindings = db.scalars(select(AgentToolBinding).where(
        AgentToolBinding.tool_definition_id.in_(retired_ids),
    )).all() if retired_ids else []
    for row in [*retired_tools, *bindings]:
        row.enabled = True
    db.commit()
    synchronize(db, project)
    db.commit()
    assert all(not row.enabled for row in [*retired_tools, *bindings])
    return {
        "old_versions": version_results,
        "retired_tools": {
            "status": "passed" if retired_tools else "not_tested",
            "disabled_tool_ids": retired_ids,
            "disabled_binding_ids": [binding.id for binding in bindings],
            "reason": None if retired_tools else "选定项目没有真实退役工具，未构造工具键",
        },
    }


def verify_tool_inheritance(db: Session, project: Project) -> dict[str, Any]:
    repo = AgentDefinitionRepository(db)
    candidates = db.scalars(select(AgentDefinition).where(
        AgentDefinition.project_id.is_(None), AgentDefinition.builtin.is_(True),
        AgentDefinition.enabled.is_(True),
    ).order_by(AgentDefinition.id)).all()
    tools = repo.list_tools(project_id=project.id)
    for template in candidates:
        resolved = repo.get_agent(project_id=project.id, agent_key=template.agent_key)
        if resolved is None or resolved.id != template.id:
            continue
        inherited = repo.list_agent_tools(template.id, project_id=project.id)
        inherited_ids = {tool.id for tool in inherited}
        addition = next((tool for tool in tools if tool.id not in inherited_ids), None)
        if inherited and addition is not None:
            break
    else:
        raise AssertionError("真实数据中没有可继承工具且可追加其他真实工具的全局 Agent")

    baseline = definition_snapshot(db)
    override = repo.bind_tool_to_agent(
        agent=template, tool=addition, project_id=project.id, user_id=project.user_id,
    )
    assert override.project_id == project.id and not override.builtin
    assert {tool.id for tool in repo.list_agent_tools(override.id, project_id=project.id)} == (
        inherited_ids | {addition.id}
    )
    db.rollback()
    assert definition_snapshot(db) == baseline, "仓储绑定提前提交了事务"

    # 通过真实服务入口重放一次首次绑定，覆盖仓储封装到调用方的完整链路。
    service = AgentPlatformService(db)
    assert service.bind_tool(
        project_id=project.id, agent_key=template.agent_key,
        tool_key=addition.tool_key, user_id=project.user_id,
    ) == "bound"
    override = repo.get_agent(project_id=project.id, agent_key=template.agent_key)
    assert override is not None and override.project_id == project.id
    expected_ids = inherited_ids | {addition.id}
    assert {tool.id for tool in repo.list_agent_tools(override.id, project_id=project.id)} == expected_ids
    override_values = record_values(override)
    binding_count = db.scalar(select(func.count()).select_from(AgentToolBinding))
    synchronize(db, project)
    db.commit()
    assert record_values(override) == override_values, "同步覆盖了项目自定义定义"
    assert repo.get_agent(project_id=project.id, agent_key=template.agent_key).id == override.id
    assert service.bind_tool(
        project_id=project.id, agent_key=template.agent_key,
        tool_key=addition.tool_key, user_id=project.user_id,
    ) == "bound"
    assert db.scalar(select(func.count()).select_from(AgentToolBinding)) == binding_count
    assert {tool.id for tool in repo.list_agent_tools(override.id, project_id=project.id)} == expected_ids
    assert {tool.id for tool in repo.list_agent_tools(template.id, project_id=project.id)} == inherited_ids
    return {
        "status": "passed", "source_global_agent_id": template.id,
        "agent_key": template.agent_key, "inherited_tool_ids": sorted(inherited_ids),
        "added_real_tool_id": addition.id, "added_real_tool_key": addition.tool_key,
        "project_override_id_in_isolated_database": override.id,
        "repository_rollback": "passed", "service_binding": "passed",
        "project_override_preserved": "passed", "repeated_binding_idempotent": "passed",
    }


def verify_seed_rollback(db: Session, project: Project) -> dict[str, Any]:
    before = definition_snapshot(db)
    original_level = project.level
    # 调用方的未提交写入用于检验 seed 是否偷偷提交整个会话。
    project.level = int(project.level or 1) + 1
    db.flush()
    synchronize(db, project)
    assert project.level != original_level
    db.rollback()
    assert project.level == original_level, "seed 提交了调用方事务"
    assert definition_snapshot(db) == before
    return {"status": "passed", "unrelated_caller_write_rolled_back": "passed"}


def verify_global_uniqueness(db: Session) -> dict[str, Any]:
    source = db.scalars(select(AgentDefinition).where(
        AgentDefinition.project_id.is_(None), AgentDefinition.builtin.is_(True),
        AgentDefinition.enabled.is_(True),
    ).order_by(AgentDefinition.id)).first()
    assert source is not None
    source_id, source_key, source_version = source.id, source.agent_key, source.version
    values = record_values(source)
    values.pop("id")
    try:
        with db.begin_nested():
            db.add(AgentDefinition(**values))
            db.flush()
    except IntegrityError:
        pass
    else:
        raise AssertionError("全局同 key、同版本重复定义未被数据库约束拒绝")
    assert db.scalar(select(func.count()).select_from(AgentDefinition).where(
        AgentDefinition.project_id.is_(None), AgentDefinition.agent_key == source_key,
        AgentDefinition.version == source_version,
    )) == 1
    return {
        "status": "passed", "source_agent_id": source_id,
        "duplicate_rejected_by_sqlite_schema": "passed",
        "mysql_concurrent_insert": "not_tested_in_sqlite",
    }


def load_service_replay_source(
    online: Session, project: Project,
) -> tuple[AgentRun, KnowledgeDocument, AgentWorkflowDefinition] | None:
    run_ids = online.scalars(select(AgentRun.id).where(
        AgentRun.project_id == project.id, AgentRun.user_id == project.user_id,
        AgentRun.status == "success",
    ).order_by(AgentRun.id.desc())).all()
    for run_id in run_ids:
        run = online.get(AgentRun, run_id)
        assert run is not None
        source = persisted_source_snapshot(run)
        if source is None or source.document_id is None:
            continue
        document = online.get(KnowledgeDocument, source.document_id)
        workflow = online.get(AgentWorkflowDefinition, run.workflow_definition_id)
        if document is None or workflow is None or document.project_id != project.id:
            continue
        try:
            current_source = SourceSnapshot.from_document(document)
        except ValueError:
            continue
        if source != current_source:
            continue
        return run, document, workflow
    return None


def verify_service_run_creation(
    db: Session,
    project: Project,
    source_run: AgentRun,
    source_document: KnowledgeDocument,
    source_workflow: AgentWorkflowDefinition,
) -> dict[str, Any]:
    service = AgentPlatformService(db)
    catalog = service.list_catalog(project_id=project.id, user_id=project.user_id)
    assert catalog is not None and all(catalog.values())
    assert all(row.project_id in {None, project.id} for row in catalog["agents"])
    assert all(row.project_id == project.id for key in ("tools", "workflows") for row in catalog[key])

    historical = db.get(AgentRun, source_run.id)
    document = db.get(KnowledgeDocument, source_document.id)
    assert historical is not None and document is not None
    historical_values, document_values = record_values(historical), record_values(document)
    before_count = db.scalar(select(func.count()).select_from(AgentRun))
    request = AgentRunCreate(
        project_id=project.id, workflow_key=source_workflow.workflow_key,
        input_payload=deepcopy(source_run.input_payload),
    )
    created, status = service.create_run(request=request, user_id=project.user_id, dispatch=False)
    assert status == "created" and created is not None, status
    assert created.status == "pending" and created.task_id is None
    assert created.output_payload == {} and created.run_context["usage"]["requests"] == 0
    assert request.input_payload == source_run.input_payload
    assert created.input_payload == source_run.input_payload
    assert db.scalar(select(func.count()).select_from(AgentRun)) == before_count + 1

    repeated, status = service.create_run(request=request, user_id=project.user_id, dispatch=False)
    assert status == "already_active" and repeated is not None, status
    assert repeated.id == created.id
    assert db.scalar(select(func.count()).select_from(AgentRun)) == before_count + 1

    # 使用运行时相同的来源解析与一致性检查，不进入模型执行流程。
    stored_source = persisted_source_snapshot(created)
    resolved_source = service.runs.resolve_source_snapshot(
        project_id=project.id, input_payload=created.input_payload,
    )
    historical_source = persisted_source_snapshot(historical)
    assert stored_source is not None and resolved_source is not None
    assert_same_source(stored_source, resolved_source)
    assert stored_source == resolved_source == historical_source
    assert record_values(historical) == historical_values
    assert record_values(document) == document_values
    return {
        "status": "passed", "source_run_id": source_run.id,
        "source_document_id": source_document.id, "source_workflow_id": source_workflow.id,
        "created_run_id_in_isolated_database": created.id,
        "catalog_row_counts": {key: len(rows) for key, rows in catalog.items()},
        "service_catalog": "passed", "create_run_without_dispatch": "passed",
        "duplicate_request_returns_same_active_run": "passed",
        "runtime_source_snapshot_consistent": "passed", "source_key": stored_source.key,
        "real_input_sha256": snapshot_digest(source_run.input_payload),
        "historical_run_and_document_preserved": "passed",
    }


def verify_service_workflow_version(
    db: Session, project: Project, source: AgentWorkflowDefinition,
) -> dict[str, Any]:
    service = AgentPlatformService(db)
    highest = db.scalar(select(func.max(AgentWorkflowDefinition.version)).where(
        AgentWorkflowDefinition.project_id == project.id,
        AgentWorkflowDefinition.workflow_key == source.workflow_key,
    ))
    assert highest is not None
    definition = parse_execution_definition(deepcopy(source.definition))
    request = WorkflowDefinitionCreate(
        project_id=project.id, workflow_key=source.workflow_key, name=source.name,
        description=source.description, definition=definition, version=int(highest) + 1,
    )
    before_count = db.scalar(select(func.count()).select_from(AgentWorkflowDefinition))
    created, status = service.create_workflow(request=request, user_id=project.user_id)
    assert status == "created" and created is not None, status
    assert created.definition == definition.model_dump()
    assert created.name == source.name and created.description == source.description
    assert db.scalar(select(func.count()).select_from(AgentWorkflowDefinition)) == before_count + 1

    # 仅提升真实定义副本的版本元数据，再重放已有低版本，业务定义不做改写。
    existing_request = request.model_copy(update={"version": source.version})
    duplicate, status = service.create_workflow(request=existing_request, user_id=project.user_id)
    assert duplicate is None and status == "version_exists", status
    assert db.scalar(select(func.count()).select_from(AgentWorkflowDefinition)) == before_count + 1
    assert service.definitions.get_workflow_version(
        project_id=project.id, workflow_key=source.workflow_key, version=source.version,
    ) is not None
    return {
        "status": "passed", "source_workflow_id": source.id,
        "workflow_key": source.workflow_key, "existing_version": source.version,
        "created_version_in_isolated_database": created.version,
        "created_workflow_id_in_isolated_database": created.id,
        "real_definition_sha256": snapshot_digest(definition.model_dump()),
        "service_workflow_creation": "passed", "lower_existing_version_rejected": "passed",
    }


def load_real_records(online: Session, project_id: int | None) -> tuple[Project, list[Any]]:
    if project_id is None:
        project_id = online.scalar(select(AgentToolDefinition.project_id).join(
            Project, Project.id == AgentToolDefinition.project_id,
        ).where(Project.user_id.is_not(None)).group_by(
            AgentToolDefinition.project_id,
        ).order_by(func.count().desc(), AgentToolDefinition.project_id).limit(1))
    project = online.get(Project, project_id) if project_id is not None else None
    if project is None or project.user_id is None:
        raise ValueError("未找到有工具定义及真实所有者的项目")
    agents = online.scalars(select(AgentDefinition).where(or_(
        AgentDefinition.project_id.is_(None), AgentDefinition.project_id == project.id,
    )).order_by(AgentDefinition.id)).all()
    tools = online.scalars(select(AgentToolDefinition).where(
        AgentToolDefinition.project_id == project.id,
    ).order_by(AgentToolDefinition.id)).all()
    workflows = online.scalars(select(AgentWorkflowDefinition).where(
        AgentWorkflowDefinition.project_id == project.id,
    ).order_by(AgentWorkflowDefinition.id)).all()
    bindings = online.scalars(select(AgentToolBinding).where(
        AgentToolBinding.agent_definition_id.in_([agent.id for agent in agents]),
        AgentToolBinding.tool_definition_id.in_([tool.id for tool in tools]),
    ).order_by(AgentToolBinding.id)).all()
    if not agents or not tools or not workflows:
        raise ValueError("选定项目缺少真实 Agent、工具或工作流定义")
    return project, [project, *agents, *tools, *workflows, *bindings]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, help="默认选择真实工具定义最多的项目")
    parser.add_argument("--output", type=Path, help="将结构化结果写入指定 JSON 文件")
    args = parser.parse_args()
    with SessionLocal(autoflush=False) as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        project, records = load_real_records(online, args.project_id)
        service_source = load_service_replay_source(online, project)
        if service_source is not None:
            source_run, source_document, source_workflow = service_source
            records.extend([source_document, source_run])
        report = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "source_project_id": project.id, "source_user_id": project.user_id,
            "source_row_counts": {
                model.__tablename__: sum(isinstance(row, model) for row in records)
                for model in REPLAY_MODELS
            },
            "source_snapshot_sha256": snapshot_digest([
                {"table": row.__tablename__, "values": record_values(row)} for row in records
            ]),
            "online_transaction": "READ ONLY", "online_database_writes": 0,
            "model_calls": 0, "replay_database": "sqlite+pysqlite:///:memory:",
            "mysql_lock_and_concurrency": "not_tested_in_sqlite",
        }
        with isolated_database(records) as isolated:
            copied_project = isolated.get(Project, project.id)
            assert copied_project is not None
            report["synchronization"] = verify_synchronization(isolated, copied_project)
            report["retirement"] = verify_retirement(isolated, copied_project)
            report["tool_inheritance"] = verify_tool_inheritance(isolated, copied_project)
            report["seed_rollback"] = verify_seed_rollback(isolated, copied_project)
            report["global_uniqueness"] = verify_global_uniqueness(isolated)
            if service_source is None:
                report["service_run_creation"] = {
                    "status": "not_tested",
                    "reason": "选定项目没有来源快照与现存文档一致的真实成功运行",
                }
            else:
                report["service_run_creation"] = verify_service_run_creation(
                    isolated, copied_project, source_run, source_document, source_workflow,
                )
            workflow_source = next((
                row for row in records
                if isinstance(row, AgentWorkflowDefinition) and row.enabled and row.builtin
            ), None) if service_source is None else source_workflow
            if workflow_source is None:
                report["service_workflow_version"] = {
                    "status": "not_tested", "reason": "选定项目没有可回放的真实启用工作流",
                }
            else:
                report["service_workflow_version"] = verify_service_workflow_version(
                    isolated, copied_project, workflow_source,
                )
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
