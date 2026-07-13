from __future__ import annotations

import os
from typing import Any


_WRITE_OPT_IN_ENV = "ALLOW_QA_VERIFY_DB_WRITE"


def require_explicit_db_write_opt_in(script_name: str) -> None:
    """Verification scripts write disposable rows; require explicit opt-in."""
    if os.getenv(_WRITE_OPT_IN_ENV) == "1":
        return
    raise SystemExit(
        f"{script_name} writes temporary verification data to the configured database. "
        f"Set {_WRITE_OPT_IN_ENV}=1 to run it; created rows will be cleaned up by project_id."
    )


def cleanup_project_test_data(db: Any, project_id: int | None) -> None:
    if not project_id:
        return

    from core.db.models import (
        APIExecution,
        Evaluation,
        KnowledgeDocument,
        LogEntry,
        PipelineRun,
        Project,
        ProjectContextSnapshot,
        ProjectPipelineConfig,
        RagEvalRun,
        RagEvalSampleResult,
        RecallMetric,
        StandardInterface,
        TestGeneration,
        TestGenerationComparison,
        UIErrorOperation,
        UIExecution,
        UITestCase,
    )

    try:
        pid = int(project_id)
        db.query(UIErrorOperation).filter(UIErrorOperation.project_id == pid).delete(synchronize_session=False)
        db.query(UIExecution).filter(UIExecution.project_id == pid).delete(synchronize_session=False)
        db.query(APIExecution).filter(APIExecution.project_id == pid).delete(synchronize_session=False)
        db.query(Evaluation).filter(Evaluation.project_id == pid).delete(synchronize_session=False)
        db.query(TestGenerationComparison).filter(TestGenerationComparison.project_id == pid).delete(
            synchronize_session=False
        )
        db.query(LogEntry).filter(LogEntry.project_id == pid).delete(synchronize_session=False)
        db.query(RecallMetric).filter(RecallMetric.project_id == pid).delete(synchronize_session=False)
        db.query(StandardInterface).filter(StandardInterface.project_id == pid).delete(synchronize_session=False)
        db.query(UITestCase).filter(UITestCase.project_id == pid).delete(synchronize_session=False)
        db.query(PipelineRun).filter(PipelineRun.project_id == pid).delete(synchronize_session=False)
        db.query(ProjectPipelineConfig).filter(ProjectPipelineConfig.project_id == pid).delete(
            synchronize_session=False
        )
        run_ids = [row[0] for row in db.query(RagEvalRun.id).filter(RagEvalRun.project_id == pid).all()]
        if run_ids:
            db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id.in_(run_ids)).delete(
                synchronize_session=False
            )
            db.query(RagEvalRun).filter(RagEvalRun.id.in_(run_ids)).delete(synchronize_session=False)
        db.query(ProjectContextSnapshot).filter(ProjectContextSnapshot.project_id == pid).delete(
            synchronize_session=False
        )
        db.query(TestGeneration).filter(TestGeneration.project_id == pid).delete(synchronize_session=False)
        db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == pid).update(
            {KnowledgeDocument.source_doc_id: None},
            synchronize_session=False,
        )
        db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == pid).delete(synchronize_session=False)
        db.query(Project).filter(Project.id == int(project_id)).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
