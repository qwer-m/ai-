from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import AgentContextRecord, User
from modules.agent_platform.contracts import (
    AgentDefinitionCreate,
    AgentRunCreate,
    AgentToolBindingRequest,
    ApprovalDecision,
    WorkflowDefinitionCreate,
)
from modules.knowledge_base_components.context.context_memory_repository import (
    ContextMemoryRepository,
)
from modules.knowledge_base_components.context.context_requests import (
    ContextRecordCreateRequest,
    ContextRecordConfirmationRequest,
    ContextRecordDecisionRequest,
    ContextRecordReplaceRequest,
    ContextRetrievalFeedbackRequest,
)
from modules.knowledge_base_components.ports.context_memory_port import ContextSourceRef
from modules.agent_platform.dispatcher import start_agent_run_worker
from modules.agent_platform.excel_export import (
    build_test_cases_excel,
    test_cases_export_filename,
)
from modules.agent_platform.serialization import (
    serialize_agent,
    serialize_context_record,
    serialize_event,
    serialize_run,
    serialize_run_summary,
    serialize_tool,
    serialize_workflow,
)
from modules.agent_platform.service import AgentPlatformService
from modules.system_components.repositories.project_repository import ProjectRepository


router = APIRouter(prefix="/agents", tags=["Agent 平台"])


def _service(db: Session) -> AgentPlatformService:
    return AgentPlatformService(db, start_agent_run_worker)


def _run_payload(service: AgentPlatformService, run) -> dict:
    return serialize_run(
        run,
        node_runs=service.runs.list_node_runs(run_id=run.id),
        approvals=service.runs.list_approvals(run_id=run.id),
    )


def _owned_project(db: Session, *, project_id: int, user_id: int):
    return ProjectRepository(db).get_owned_project(project_id=int(project_id), user_id=int(user_id))


def _owned_context_record(db: Session, *, record_id: int, user_id: int) -> AgentContextRecord | None:
    record = db.get(AgentContextRecord, int(record_id))
    if record is None:
        return None
    # 记录管理权限不能从用户提交的 project_id 推导，必须同时属于当前用户。
    if record.user_id != int(user_id):
        return None
    if record.project_id is not None and _owned_project(
        db, project_id=int(record.project_id), user_id=int(user_id),
    ) is None:
        return None
    return record


def _context_source_from_retrieval(db: Session, run, request: ContextRetrievalFeedbackRequest) -> ContextSourceRef:
    from modules.agent_platform.checkpoint_storage import read_run_context_retrievals

    retrievals = read_run_context_retrievals(db, run)
    if not isinstance(retrievals, list) or request.retrieval_index >= len(retrievals):
        raise ValueError("运行中不存在指定的上下文检索记录")
    return _context_source_from_manifest(retrievals[request.retrieval_index], request.source_id)


def _context_source_from_manifest(record, source_id: str) -> ContextSourceRef:
    """反馈只能引用明确读出的那次真实 manifest，不能由请求自行声明来源。"""
    manifest = record.get("manifest") if isinstance(record, dict) else None
    if not isinstance(manifest, dict):
        raise ValueError("上下文检索记录缺少 manifest")
    candidates = list(manifest.get("selected") or []) + list(manifest.get("rejected") or [])
    for candidate in candidates:
        source = candidate.get("source") if isinstance(candidate, dict) else None
        if isinstance(source, dict) and source.get("source_id") == source_id:
            return ContextSourceRef(
                source_kind=str(source.get("source_kind") or ""),
                source_id=str(source.get("source_id") or ""),
                scope=str(source.get("scope") or ""),
                scope_id=source.get("scope_id"),
                revision=str(source.get("revision") or ""),
                content_hash=str(source.get("content_hash") or ""),
                status=str(source.get("status") or "available"),
            )
    raise ValueError("反馈来源不在该次上下文检索 manifest 中")


def _raise_result_error(reason: str) -> None:
    mapping = {
        "project_not_found": (404, "项目不存在"),
        "workflow_not_found": (404, "工作流不存在"),
        "run_not_found": (404, "运行记录不存在"),
        "run_result_not_found": (409, "当前运行尚无可导出的测试用例"),
        "run_result_invalid": (500, "当前运行的测试用例产物格式异常"),
        "document_not_found": (404, "需求文档不存在"),
        "approval_not_found": (404, "审批记录不存在"),
        "approval_run_not_waiting": (409, "运行不在待审批状态，不能继续此审批"),
        "definition_not_found": (404, "智能体或工具定义不存在"),
        "version_exists": (409, "相同版本已存在"),
        "run_not_retryable": (409, "当前运行状态不可重试"),
        "run_retry_budget_exhausted": (409, "当前检查点的执行预算已耗尽，续跑不会增加额度，请创建新的生成任务"),
        "run_source_already_active": (409, "同一需求来源已有运行正在执行，请等待完成或取消后再续跑"),
        "run_attempt_reset_forbidden": (409, "运行进行中，不能重置执行次数"),
        "run_version_mismatch": (409, "运行版本已变化，不能混用旧节点结果，请新建 Run"),
        "run_checkpoint_version_mismatch": (409, "旧动作检查点需要显式迁移，不能直接续跑或自动升级"),
        "run_source_changed": (409, "需求文档已变化，请重新生成，不能续跑旧任务"),
        "run_source_snapshot_missing": (409, "运行缺少来源快照，不能安全恢复，请重新生成"),
        "run_contract_missing": (409, "运行缺少当前执行契约，不能继续旧路径，请重新生成"),
        "run_contract_invalid": (409, "运行执行契约已失效，请重新生成"),
    }
    if reason.startswith("unknown_node_reference:"):
        raise HTTPException(status_code=422, detail=f"工作流引用不存在: {reason.split(':', 1)[1]}")
    if reason.startswith("invalid_run_input:"):
        raise HTTPException(status_code=422, detail=f"运行输入不符合工作流契约: {reason.split(':', 1)[1]}")
    status_code, detail = mapping.get(reason, (400, reason))
    raise HTTPException(status_code=status_code, detail=detail)


@router.get("/catalog")
def get_catalog(
    project_id: int = Query(gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = _service(db).list_catalog(project_id=project_id, user_id=current_user.id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "agents": [serialize_agent(item) for item in catalog["agents"]],
        "tools": [serialize_tool(item) for item in catalog["tools"]],
        "workflows": [serialize_workflow(item) for item in catalog["workflows"]],
    }


@router.get("")
def list_agents(
    project_id: int = Query(gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = _service(db).list_catalog(project_id=project_id, user_id=current_user.id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"items": [serialize_agent(item) for item in catalog["agents"]]}


@router.post("", status_code=201)
def create_agent(
    request: AgentDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    value, reason = _service(db).create_agent(request=request, user_id=current_user.id)
    if value is None:
        _raise_result_error(reason)
    return serialize_agent(value)


@router.post("/tool-bindings")
def bind_agent_tool(
    request: AgentToolBindingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reason = _service(db).bind_tool(
        project_id=request.project_id,
        agent_key=request.agent_key,
        tool_key=request.tool_key,
        user_id=current_user.id,
    )
    if reason != "bound":
        _raise_result_error(reason)
    return {"status": "bound"}


@router.get("/workflows")
def list_workflows(
    project_id: int = Query(gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = _service(db).list_catalog(project_id=project_id, user_id=current_user.id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"items": [serialize_workflow(item) for item in catalog["workflows"]]}


@router.get("/context-records")
def list_context_records(
    project_id: int = Query(gt=0),
    source_kind: str | None = Query(default=None, min_length=1, max_length=40),
    status: str | None = Query(default=None, min_length=1, max_length=20),
    origin_run_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if _owned_project(db, project_id=project_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        records = ContextMemoryRepository(db).list_records(
            user_id=current_user.id,
            project_id=project_id,
            source_kind=source_kind,
            status=status,
            origin_run_id=origin_run_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"items": [serialize_context_record(item) for item in records]}


@router.post("/context-records", status_code=201)
def create_context_record(
    request: ContextRecordCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if request.project_id is not None and _owned_project(
        db, project_id=request.project_id, user_id=current_user.id,
    ) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    repository = ContextMemoryRepository(db)
    try:
        record = repository.create_pending(
            user_id=current_user.id,
            project_id=request.project_id,
            source_kind=request.source_kind,
            scope=request.scope,
            scope_id=request.scope_id,
            revision=request.revision,
            content=request.content,
            evidence_refs=request.evidence_refs,
            origin_run_id=request.origin_run_id,
        )
        db.commit()
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return serialize_context_record(record)


@router.post("/context-records/{record_id}/confirm")
def confirm_context_record(
    record_id: int,
    request: ContextRecordConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _owned_context_record(db, record_id=record_id, user_id=current_user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="长期上下文记录不存在")
    try:
        record = ContextMemoryRepository(db).confirm_pending(
            source_id=f"memory:{record.id}",
            confirmed_by_user_id=current_user.id,
            **(request.model_dump() if request is not None else {}),
        )
        db.commit()
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return serialize_context_record(record)


@router.post("/context-records/{record_id}/reject")
def reject_context_record(
    record_id: int,
    request: ContextRecordDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _owned_context_record(db, record_id=record_id, user_id=current_user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="长期上下文记录不存在")
    try:
        record = ContextMemoryRepository(db).reject_pending(
            source_id=f"memory:{record.id}", revision=request.revision, reason=request.reason,
        )
        db.commit()
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return serialize_context_record(record)


@router.post("/context-records/{record_id}/revoke")
def revoke_context_record(
    record_id: int,
    request: ContextRecordDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _owned_context_record(db, record_id=record_id, user_id=current_user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="长期上下文记录不存在")
    try:
        ContextMemoryRepository(db).revoke(
            source_id=f"memory:{record.id}",
            revision=request.revision,
            reason=request.reason,
        )
        db.commit()
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return serialize_context_record(record)


@router.post("/context-records/{record_id}/replace", status_code=201)
def replace_context_record(
    record_id: int,
    request: ContextRecordReplaceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _owned_context_record(db, record_id=record_id, user_id=current_user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="长期上下文记录不存在")
    if not request.reason.strip():
        raise HTTPException(status_code=422, detail="替代长期上下文必须说明原因")
    try:
        replacement = ContextMemoryRepository(db).replace_active(
            source_id=f"memory:{record.id}",
            revision=request.revision,
            user_id=current_user.id,
            content=request.content,
            evidence_refs=request.evidence_refs,
            new_revision=request.new_revision,
            origin_run_id=record.origin_run_id,
            reason=request.reason,
        )
        db.commit()
    except (LookupError, PermissionError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return serialize_context_record(replacement)


@router.post("/workflows", status_code=201)
def create_workflow(
    request: WorkflowDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    value, reason = _service(db).create_workflow(request=request, user_id=current_user.id)
    if value is None:
        _raise_result_error(reason)
    return serialize_workflow(value)


@router.get("/runs")
def list_runs(
    project_id: int = Query(gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    workflow_key: str | None = Query(default=None, min_length=1, max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    rows = service.list_runs(
        project_id=project_id,
        user_id=current_user.id,
        limit=limit,
        workflow_key=workflow_key,
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"items": [_run_payload(service, row) for row in rows]}


@router.get("/runs/active")
def get_active_run(
    project_id: int = Query(gt=0),
    workflow_key: str = Query(min_length=1, max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    run = service.get_active_run(
        project_id=project_id,
        user_id=current_user.id,
        workflow_key=workflow_key,
    )
    return {"run": serialize_run_summary(run) if run is not None else None}


@router.get("/runs/reuse-candidate")
def get_generation_reuse_candidate(
    project_id: int = Query(gt=0),
    requirement_doc_id: int = Query(gt=0),
    workflow_key: str = Query(default="test_generation", min_length=1, max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidate, reason = _service(db).get_generation_reuse_candidate(
        project_id=project_id,
        user_id=current_user.id,
        workflow_key=workflow_key,
        requirement_doc_id=requirement_doc_id,
    )
    if reason not in {"found", "not_found"}:
        _raise_result_error(reason)
    return {"candidate": candidate}


@router.post("/runs", status_code=202)
def create_run(
    request: AgentRunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    value, reason = service.create_run(request=request, user_id=current_user.id)
    if value is None:
        _raise_result_error(reason)
    return {"run": _run_payload(service, value), "status": reason}


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    run = service.get_run(run_id=run_id, user_id=current_user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"run": _run_payload(service, run)}


@router.get("/runs/{run_id}/summary")
def get_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    run = service.get_run_summary(run_id=run_id, user_id=current_user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"run": serialize_run_summary(run)}


@router.get("/runs/{run_id}/metrics")
def get_run_metrics(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    result = service.get_run_evaluation_summary(
        run_id=run_id,
        user_id=current_user.id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    run, metrics = result
    return {
        "run_id": int(run.id),
        "input_hash": (run.run_context or {}).get("input_hash"),
        "logical_task_id": (run.run_context or {}).get("logical_task_id"),
        "attempt_id": (run.run_context or {}).get("attempt_id"),
        "metrics": metrics,
    }


@router.post("/runs/{run_id}/context-feedback", status_code=201)
def record_context_feedback(
    run_id: int,
    request: ContextRetrievalFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = _service(db).get_run(run_id=run_id, user_id=current_user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    try:
        source = _context_source_from_retrieval(db, run, request)
        record = ContextMemoryRepository(db).record_feedback(
            run_id=run.id,
            source=source,
            outcome={
                "retrieval_index": request.retrieval_index,
                "source_id": request.source_id,
                "outcome": request.outcome,
                "notes": request.notes,
                "evidence_refs": request.evidence_refs,
                "evaluator_type": "human",
            },
        )
        db.commit()
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return serialize_context_record(record)


@router.get("/runs/{run_id}/test-cases.xlsx")
def export_run_test_cases(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    export_data, reason = _service(db).get_test_case_export_data(
        run_id=run_id,
        user_id=current_user.id,
    )
    if export_data is None:
        _raise_result_error(reason)
    excel_bytes = build_test_cases_excel(export_data["test_cases"])
    _display_name, disposition = test_cases_export_filename(
        str(export_data.get("source_filename") or ""),
        run_id,
    )
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(excel_bytes)),
        },
    )


@router.get("/runs/{run_id}/events")
def list_run_events(
    run_id: int,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    # 事件读取只需要确认归属和 Run id；不要为增量接口加载完整运行产物。
    run = service.get_run_summary(run_id=run_id, user_id=current_user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    rows = service.runs.list_events_after(
        run_id=run.id,
        after_sequence=after_sequence,
        # 多取一条判断是否还有下一页，但不把它返回给前端。
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_sequence = max(
        [after_sequence, *(int(item.sequence or 0) for item in items)]
    )
    return {
        "items": [serialize_event(item) for item in items],
        "after_sequence": after_sequence,
        "next_sequence": next_sequence,
        "has_more": has_more,
    }


@router.post("/runs/{run_id}/retry", status_code=202)
def retry_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    value, reason = service.retry_run(run_id=run_id, user_id=current_user.id)
    if value is None:
        _raise_result_error(reason)
    return {"run": _run_payload(service, value)}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    value, reason = service.cancel_run(run_id=run_id, user_id=current_user.id)
    if value is None:
        _raise_result_error(reason)
    return {"run": _run_payload(service, value), "status": reason}


@router.post("/runs/{run_id}/reset-attempt")
def reset_run_attempt(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    value, reason = service.reset_run_attempt(run_id=run_id, user_id=current_user.id)
    if value is None:
        _raise_result_error(reason)
    return {"run": _run_payload(service, value), "status": reason}


@router.post("/approvals/{approval_id}/decision")
def decide_approval(
    approval_id: int,
    decision: ApprovalDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        value, reason = _service(db).decide_approval(
            approval_id=approval_id, decision=decision, user_id=current_user.id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if value is None:
        _raise_result_error(reason)
    return {"approval_id": value.id, "status": reason}
