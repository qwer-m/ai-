from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.settings.config import settings
from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)
from .test_generation_generate_routes_runtime import (
    WorkflowKind,
    WorkflowStage,
    build_generation_qm,
    context_orchestrator,
    get_current_user,
    get_db,
    get_owned_project,
    knowledge_base,
    log_to_db,
    log_workflow_trace,
    test_generator,
)
from schemas.automation.test_generation import TestGenRequest

router = APIRouter()


def _raise_generation_error(db: Session, project_id: int, user_id: int, payload: dict) -> None:
    log_to_db(
        db,
        project_id,
        "system",
        f"GEN_DIAG:{json.dumps({'kind': 'generation_summary', **payload}, ensure_ascii=False)}",
        user_id=user_id,
    )
    raise HTTPException(status_code=502, detail=payload)


def _handle_generation_error_payload(db: Session, request: TestGenRequest, user_id: int, result) -> None:
    if isinstance(result, list) and len(result) == 0:
        _raise_generation_error(
            db,
            request.project_id,
            user_id,
            {
                "error_code": "EMPTY_GENERATED_RESULT",
                "error_message": "生成完成但最终测试用例为空",
                "final_status": "empty_result_failed",
                "empty_result_guard_triggered": True,
                "empty_result_stage": "api_response_guard",
            },
        )
    if not isinstance(result, dict):
        return

    error_code = str(result.get("error_code") or result.get("error") or "").strip()
    if error_code == "EMPTY_GENERATED_RESULT":
        _raise_generation_error(
            db,
            request.project_id,
            user_id,
            {
                "error_code": "EMPTY_GENERATED_RESULT",
                "error_message": str(result.get("error_message") or "生成完成但最终测试用例为空"),
                "final_status": str(result.get("final_status") or "empty_result_failed"),
                "empty_result_guard_triggered": bool(result.get("empty_result_guard_triggered", True)),
                "empty_result_stage": str(result.get("empty_result_stage") or "generation_postprocess"),
            },
        )
    if error_code == "LOW_QUALITY_GENERATED_CASES":
        _raise_generation_error(
            db,
            request.project_id,
            user_id,
            {
                "error_code": "LOW_QUALITY_GENERATED_CASES",
                "error_message": str(result.get("error_message") or "生成结果未通过质量门禁"),
                "final_status": str(result.get("final_status") or "quality_gate_failed"),
                "quality_gate_failed": bool(result.get("quality_gate_failed", True)),
                "failed_checks": list(result.get("failed_checks") or []),
                "priority_final_null_count": int(result.get("priority_final_null_count") or 0),
                "invalid_priority_final_case_ids": list(result.get("invalid_priority_final_case_ids") or []),
                "non_assertable_expected_result_count": int(result.get("non_assertable_expected_result_count") or 0),
                "truncated_text_count": int(result.get("truncated_text_count") or 0),
                "non_assertable_case_ids": list(result.get("non_assertable_case_ids") or []),
                "truncated_case_ids": list(result.get("truncated_case_ids") or []),
            },
        )
    if error_code == "execution_plan_failed":
        _raise_generation_error(
            db,
            request.project_id,
            user_id,
            {
                "error_code": "execution_plan_failed",
                "error_message": str(result.get("error_message") or "生成结果未通过执行计划门禁"),
                "final_status": str(result.get("final_status") or "execution_plan_failed"),
                "persistence_gate_failed": bool(result.get("persistence_gate_failed", True)),
                "failure_reasons": list(result.get("failure_reasons") or []),
                "metrics": dict(result.get("metrics") or {}),
                "state_conflicts": list(result.get("state_conflicts") or []),
            },
        )


@router.post("/generate-tests")
def generate_tests(
    request: TestGenRequest,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    get_owned_project(request.project_id, db, current_user.id)

    context_bundle = context_orchestrator.assemble_context(
        WorkflowKind.TEST_GENERATION,
        request.project_id,
        db,
        user_id=current_user.id,
        query_text=request.requirement[:800],
        requirement_text=request.requirement[:2000],
        include_knowledge=True,
        include_logs=True,
        knowledge_limit=5,
        log_limit=10,
    )
    log_workflow_trace(
        db,
        request.project_id,
        current_user.id,
        WorkflowKind.TEST_GENERATION,
        WorkflowStage.CONTEXT,
        {
            "action": "generate_tests",
            "compress": request.compress,
            "expected_count": request.expected_count,
            **context_bundle["diagnostics"],
        },
    )
    log_to_db(
        db,
        request.project_id,
        "system",
        (
            f"开始生成测试用例(批次{request.batch_index}): 长度={len(request.requirement)}, "
            f"压缩={request.compress}, 期望数量={request.expected_count}, "
            f"批次大小={request.batch_size}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}"
        ),
        user_id=current_user.id,
    )
    result = test_generator.generate_test_cases_json(
        request.requirement,
        request.project_id,
        db,
        "requirement",
        request.compress,
        request.expected_count,
        request.batch_size,
        request.batch_index,
        user_id=current_user.id,
        current_biz_key=request.current_biz_key,
        only_current_biz=request.only_current_biz,
        multi_pass=request.multi_pass,
        generation_mode=request.generation_mode,
        enable_sample_pool_feedback=request.enable_sample_pool_feedback,
    )
    _handle_generation_error_payload(db, request, current_user.id, result)
    try:
        count = len(result) if isinstance(result, list) else 0
        log_to_db(
            db,
            request.project_id,
            "system",
            f"测试用例生成完成(批次{request.batch_index}): 数量={count}",
            user_id=current_user.id,
        )
        kb_ctx = knowledge_base.get_all_context(db, request.project_id, user_id=current_user.id) if db else ""
        diag = {
            "kind": "gen_diag",
            "mode": "text",
            "doc_type": "requirement",
            "compress": request.compress,
            "expected_count": request.expected_count,
            "generated_count": count,
            "requirement_length": len(request.requirement),
            "kb_length": len(kb_ctx or ""),
            "model": settings.MODEL_NAME,
            "max_tokens": settings.MAX_TOKENS,
            "batch_index": request.batch_index,
            "generation_mode": request.generation_mode or ("multi_pass" if request.multi_pass else "single_pass"),
        }
        log_to_db(db, request.project_id, "system", f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}", user_id=current_user.id)
        try:
            qm = build_generation_qm(result)
            qm["batch_index"] = request.batch_index
            log_to_db(db, request.project_id, "system", f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}", user_id=current_user.id)
        except Exception:
            pass
    except Exception:
        log_to_db(db, request.project_id, "system", "测试用例生成完成", user_id=current_user.id)
    return result


@router.post("/generate-tests/async")
async def generate_tests_async(
    request: TestGenRequest,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    get_owned_project(request.project_id, db, current_user.id)

    queue_result = submit_background_task(
        BackgroundTaskKind.TEST_GENERATION,
        kwargs={
            "requirement": request.requirement,
            "project_id": request.project_id,
            "doc_type": "requirement",
            "compress": request.compress,
            "expected_count": request.expected_count,
            "batch_index": request.batch_index,
            "batch_size": request.batch_size,
            "user_id": current_user.id,
            "current_biz_key": request.current_biz_key,
            "only_current_biz": request.only_current_biz,
            "multi_pass": request.multi_pass,
            "generation_mode": request.generation_mode,
            "enable_sample_pool_feedback": request.enable_sample_pool_feedback,
        },
        business_id=request.project_id,
        reason="generate_tests_async",
    )
    return {
        "task_id": queue_result.id,
        "status": "PENDING",
        "message": "Task submitted successfully",
        "queue_result": queue_result.to_dict(),
    }
