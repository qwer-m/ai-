"""Canonical Celery task names used by producers, beat, and workers."""

from __future__ import annotations

from enum import Enum


class TaskName(str, Enum):
    GENERATE_TEST_CASES = "modules.orchestration.tasks.generate_test_cases_task"
    PARSE_KNOWLEDGE_DOCUMENT = "modules.orchestration.tasks.parse_knowledge_document_task"
    BUILD_CONTEXT_SNAPSHOT = "modules.orchestration.tasks.build_context_snapshot_task"
    AUDIT_KNOWLEDGE_INDEX_CONSISTENCY = (
        "modules.orchestration.tasks.audit_knowledge_index_consistency_task"
    )
    COMPARE_TEST_CASES = "modules.orchestration.tasks.compare_test_cases_task"
    RUN_RAG_EVAL = "modules.orchestration.tasks.run_rag_eval_task"
    RUN_PIPELINE = "modules.orchestration.tasks.run_pipeline_task"
    RECOVER_EXPIRED_PIPELINE_RUNS = (
        "modules.orchestration.tasks.recover_expired_pipeline_runs_task"
    )
    CLEANUP_LOGS = "modules.orchestration.tasks.cleanup_logs_task"
    ARCHIVE_OLD_DATA = "modules.orchestration.tasks.archive_old_data_task"


def task_name_value(task_name: TaskName | str) -> str:
    if isinstance(task_name, TaskName):
        return task_name.value
    return str(task_name)
