from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from modules.agent_platform.contracts import (
    AgentDefinitionCreate,
    AgentRunCreate,
    AgentToolBindingRequest,
    ApprovalDecision,
    WorkflowDefinitionCreate,
)
from modules.agent_platform.dispatcher import start_agent_run_worker
from modules.agent_platform.excel_export import (
    build_test_cases_excel,
    test_cases_export_filename,
)
from modules.agent_platform.serialization import (
    serialize_agent,
    serialize_event,
    serialize_run,
    serialize_run_summary,
    serialize_tool,
    serialize_workflow,
)
from modules.agent_platform.service import AgentPlatformService


router = APIRouter(prefix="/agents", tags=["Agent 平台"])


def _service(db: Session) -> AgentPlatformService:
    return AgentPlatformService(db, start_agent_run_worker)


def _run_payload(service: AgentPlatformService, run) -> dict:
    return serialize_run(
        run,
        node_runs=service.repo.list_node_runs(run_id=run.id),
        approvals=service.repo.list_approvals(run_id=run.id),
    )


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
        "run_source_already_active": (409, "同一需求来源已有运行正在执行，请等待完成或取消后再续跑"),
        "run_attempt_reset_forbidden": (409, "运行进行中，不能重置执行次数"),
        "run_version_mismatch": (409, "运行版本已变化，不能混用旧节点结果，请新建 Run"),
        "run_source_changed": (409, "需求文档已变化，请重新生成，不能续跑旧任务"),
        "run_source_snapshot_missing": (409, "旧运行缺少来源快照，不能安全恢复，请重新生成"),
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    run = service.get_active_run(project_id=project_id, user_id=current_user.id)
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
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _service(db)
    run = service.get_run(run_id=run_id, user_id=current_user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {
        "items": [
            serialize_event(item)
            for item in service.repo.list_events(run_id=run.id, limit=limit)
        ]
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
    value, reason = _service(db).decide_approval(
        approval_id=approval_id,
        decision=decision,
        user_id=current_user.id,
    )
    if value is None:
        _raise_result_error(reason)
    return {"approval_id": value.id, "status": reason}
