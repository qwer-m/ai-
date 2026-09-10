"""Canonical Celery task names used by producers, beat, and workers."""

from __future__ import annotations

from enum import Enum


class TaskName(str, Enum):
    PARSE_KNOWLEDGE_DOCUMENT = "modules.orchestration.tasks.parse_knowledge_document_task"
    AUDIT_KNOWLEDGE_INDEX_CONSISTENCY = (
        "modules.orchestration.tasks.audit_knowledge_index_consistency_task"
    )
    RUN_RAG_EVAL = "modules.orchestration.tasks.run_rag_eval_task"
    RUN_AGENT_WORKFLOW = "modules.orchestration.tasks.run_agent_workflow_task"
    RECOVER_EXPIRED_AGENT_RUNS = (
        "modules.orchestration.tasks.recover_expired_agent_runs_task"
    )
    CLEANUP_LOGS = "modules.orchestration.tasks.cleanup_logs_task"


def task_name_value(task_name: TaskName | str) -> str:
    if isinstance(task_name, TaskName):
        return task_name.value
    return str(task_name)
