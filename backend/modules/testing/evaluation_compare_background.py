from __future__ import annotations

import json
from typing import Any

from core.db.database import SessionLocal
from core.db.models import TestGenerationComparison
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.orchestration.context_orchestrator import context_orchestrator
from modules.testing.evaluation import evaluator
from modules.testing.evaluation_artifact_store import upsert_compare_artifact


def persist_compare_result(
    *,
    comparison_id: int,
    project_id: int,
    user_id: int,
    result: str | dict[str, Any],
) -> None:
    result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
    progress_db = SessionLocal()
    try:
        row = (
            progress_db.query(TestGenerationComparison)
            .filter(
                TestGenerationComparison.id == comparison_id,
                TestGenerationComparison.project_id == project_id,
                TestGenerationComparison.user_id == user_id,
            )
            .first()
        )
        if row is not None:
            row.comparison_result = result_text
            progress_db.commit()
    except Exception:
        progress_db.rollback()
    finally:
        progress_db.close()


def run_compare_background_job(
    *,
    comparison_id: int,
    generated_test_case: str,
    modified_test_case: str,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    requirement_text: str,
    upload_persist: dict[str, Any],
) -> None:
    db = SessionLocal()
    try:

        def progress_callback(payload: dict[str, Any]) -> None:
            persist_compare_result(
                comparison_id=comparison_id,
                project_id=project_id,
                user_id=user_id,
                result=payload,
            )

        try:
            context_bundle = context_orchestrator.assemble_context(
                WorkflowKind.EVALUATION,
                project_id,
                db,
                user_id=user_id,
                requirement_text=generated_test_case,
                include_knowledge=True,
                include_logs=True,
                knowledge_limit=3,
                log_limit=8,
            )
            log_workflow_trace(
                db,
                project_id,
                user_id,
                WorkflowKind.EVALUATION,
                WorkflowStage.CONTEXT,
                {"action": "compare_test_cases_background", **context_bundle["diagnostics"]},
            )
        except Exception:
            db.rollback()

        try:
            result = evaluator.compare_test_cases(
                generated_test_case,
                modified_test_case,
                db=db,
                project_id=project_id,
                user_id=user_id,
                requirement_text=requirement_text,
                persist_result=False,
                progress_callback=progress_callback,
                comparison_id=comparison_id,
            )
        except Exception as exc:
            result = evaluator.build_background_exception_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                fallback_reason=f"后台模型评测异常：{exc}",
                comparison_id=comparison_id,
            )

        persist_compare_result(
            comparison_id=comparison_id,
            project_id=project_id,
            user_id=user_id,
            result=result,
        )

        if generation_id is not None:
            try:
                artifact_payload: dict[str, Any] = {
                    "project_id": project_id,
                    "source_filename": upload_persist.get("filename", ""),
                    "source_file_content_type": upload_persist.get("content_type", ""),
                    "source_file_size": upload_persist.get("size", 0),
                    "modified_test_case": modified_test_case,
                    "requirement_text": requirement_text,
                    "comparison_result": result,
                    "ocr": {
                        "source": upload_persist.get("ocr_source", "unknown"),
                        "ok": bool(upload_persist.get("ocr_ok", False)),
                        "cloud_fallback": bool(upload_persist.get("cloud_fallback", False)),
                        "error": upload_persist.get("ocr_error", ""),
                    },
                }
                upsert_compare_artifact(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    generation_id=generation_id,
                    payload=artifact_payload,
                )
            except Exception:
                db.rollback()
    finally:
        db.close()
