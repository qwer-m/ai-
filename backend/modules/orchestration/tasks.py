"""
Celery 任务模块。

当前包含知识库、评测、Agent 工作流和维护任务。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from celery_config import celery_app
from modules.orchestration.task_names import TaskName

logger = logging.getLogger(__name__)

def _session_local():
    from core.db.database import SessionLocal

    return SessionLocal


def _knowledge_base() -> Any:
    from modules.domain.knowledge_base import knowledge_base

    return knowledge_base


def _log_entry_model():
    from core.db.model_defs import LogEntry

    return LogEntry


def _run_index_consistency_audit(*args: Any, **kwargs: Any) -> Any:
    from modules.knowledge_base_components.document.index_audit import run_index_consistency_audit

    return run_index_consistency_audit(*args, **kwargs)


def _cleanup_offline_file(*args: Any, **kwargs: Any) -> Any:
    from modules.knowledge_base_components.document.offline_parse import cleanup_offline_file

    return cleanup_offline_file(*args, **kwargs)


def execute_eval_run(*args: Any, **kwargs: Any) -> Any:
    from modules.rag_eval.services.rag_eval_service import execute_eval_run as real_execute_eval_run

    return real_execute_eval_run(*args, **kwargs)


def run_agent_workflow(*args: Any, **kwargs: Any) -> Any:
    from modules.agent_platform.runtime import run_agent_workflow as real_run_agent_workflow

    return real_run_agent_workflow(*args, **kwargs)


def recover_expired_agent_runs(*args: Any, **kwargs: Any) -> Any:
    from modules.agent_platform.recovery import (
        recover_expired_agent_runs as real_recover_expired_agent_runs,
    )

    return real_recover_expired_agent_runs(*args, **kwargs)

@celery_app.task(
    bind=True,
    name=TaskName.PARSE_KNOWLEDGE_DOCUMENT.value,
    max_retries=2,
    default_retry_delay=8,
    soft_time_limit=180,
    time_limit=240,
)
def parse_knowledge_document_task(
    self,
    document_id: int,
    file_path: str,
    force: bool = False,
    user_id: int = None,
):
    """
    知识库离线解析任务（阶段1核心链路）。

    状态流转：
    pending -> parsing -> success
                        -> failed（最终失败）
                        -> pending（重试前回退）
    """
    db = _session_local()()
    retry_count = int(getattr(self.request, "retries", 0) or 0)
    task_id = getattr(self.request, "id", None)
    knowledge_base = None

    logger.info(
        "离线解析任务启动 doc_id=%s task_id=%s retry=%s file=%s",
        document_id,
        task_id,
        retry_count,
        file_path,
    )

    try:
        knowledge_base = _knowledge_base()
        self.update_state(
            state="STARTED",
            meta={"status": "知识库文档离线解析中", "document_id": document_id},
        )
        result = knowledge_base.parse_document_offline(
            doc_id=document_id,
            file_path=file_path,
            db=db,
            force=force,
            user_id=user_id,
            task_id=task_id,
            retry_count=retry_count,
        )
        logger.info(
            "离线解析任务完成 doc_id=%s task_id=%s result=%s",
            document_id,
            task_id,
            result,
        )
        return result
    except Exception as e:
        # 超时异常归一化为可读错误，避免前端看到晦涩异常名称。
        if isinstance(e, SoftTimeLimitExceeded):
            e = TimeoutError("离线解析超时，任务已中断。")

        max_retries = int(getattr(self, "max_retries", 0) or 0)
        logger.exception(
            "离线解析任务异常 doc_id=%s task_id=%s retry=%s/%s err=%s",
            document_id,
            task_id,
            retry_count,
            max_retries,
            e,
        )

        if retry_count < max_retries:
            # 重试前把状态回退为 pending；若文档已被并发任务成功，内部会跳过覆盖。
            try:
                kb = knowledge_base or _knowledge_base()
                kb.mark_document_parse_retry(
                    doc_id=document_id,
                    retry_count=retry_count + 1,
                    error=e,
                    db=db,
                    task_id=task_id,
                )
            except Exception as mark_error:
                logger.exception(
                    "离线解析任务回写重试状态失败 doc_id=%s task_id=%s err=%s",
                    document_id,
                    task_id,
                    mark_error,
                )
            raise self.retry(exc=e)

        # 达到最大重试次数后，标记最终失败。
        try:
            kb = knowledge_base or _knowledge_base()
            kb.mark_document_parse_failed(
                doc_id=document_id,
                error=e,
                db=db,
                task_id=task_id,
                retry_count=retry_count,
            )
        except Exception as mark_error:
            logger.exception(
                "离线解析任务回写最终失败状态失败 doc_id=%s task_id=%s err=%s",
                document_id,
                task_id,
                mark_error,
            )

        _cleanup_offline_file(file_path)
        # 让 Celery 统一记录 FAILURE 元信息，避免手工 update_state 写入不完整异常结构。
        raise e
    finally:
        db.close()


@celery_app.task(bind=True, name=TaskName.AUDIT_KNOWLEDGE_INDEX_CONSISTENCY.value)
def audit_knowledge_index_consistency_task(
    self,
    project_id: int | None = None,
    user_id: int | None = None,
    limit: int = 5000,
):
    """
    关系库/向量库一致性巡检任务。

    默认作为定时任务运行，也支持手动触发（可限定 project_id）。
    """
    db = _session_local()()
    try:
        self.update_state(
            state="STARTED",
            meta={"status": "知识库索引一致性巡检中", "project_id": project_id},
        )
        report = _run_index_consistency_audit(
            db=db,
            project_id=project_id,
            user_id=user_id,
            limit=limit,
        )
        try:
            db.add(
                _log_entry_model()(
                    project_id=project_id,
                    user_id=user_id,
                    log_type="system",
                    message=f"RAG_INDEX_AUDIT:{json.dumps(report, ensure_ascii=False)}",
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        return report
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name=TaskName.RUN_RAG_EVAL.value,
    soft_time_limit=3600,
    time_limit=3660,
)
def run_rag_eval_task(self, run_id: int, user_id: int):
    """Run a persisted RAG evaluation through Celery."""
    self.update_state(
        state="STARTED",
        meta={"status": "rag_eval_running", "run_id": run_id},
    )
    execute_eval_run(run_id=run_id, user_id=user_id)
    return {"run_id": run_id, "status": "completed"}


@celery_app.task(
    bind=True,
    name=TaskName.RUN_AGENT_WORKFLOW.value,
    soft_time_limit=3600,
    time_limit=3660,
)
def run_agent_workflow_task(self, run_id: int):
    """通过 Celery 执行一次持久化 Agent 工作流。"""
    task_id = getattr(getattr(self, "request", None), "id", None)
    self.update_state(
        state="STARTED",
        meta={
            "status": "agent_run_running",
            "run_id": run_id,
            "task_id": task_id,
        },
    )
    result = run_agent_workflow(run_id=run_id, task_id=task_id)
    return {"run_id": run_id, **result}


@celery_app.task(bind=True, name=TaskName.RECOVER_EXPIRED_AGENT_RUNS.value)
def recover_expired_agent_runs_task(self, limit: int = 20):
    """重新投递租约已过期的 Agent Run。"""
    self.update_state(
        state="STARTED",
        meta={"status": "agent_run_recovery_running", "limit": limit},
    )
    return recover_expired_agent_runs(limit=limit)


@celery_app.task(bind=True, name=TaskName.CLEANUP_LOGS.value)
def cleanup_logs_task(self, retention_hours: int = 72):
    """清理过期日志。"""
    from datetime import datetime, timedelta

    from core.db.model_defs import LogEntry

    db = _session_local()()
    cutoff_date = datetime.utcnow() - timedelta(hours=retention_hours)

    try:
        deleted_count = (
            db.query(LogEntry)
            .filter(LogEntry.created_at < cutoff_date)
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"deleted_logs": deleted_count}
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
