"""
Celery 任务模块。

当前包含：
1. 测试用例生成任务（原有能力）。
2. 知识库离线解析任务（阶段1核心链路）。
3. 日志与历史数据维护任务（原有能力）。
"""

from __future__ import annotations

import json
import logging

from celery.exceptions import SoftTimeLimitExceeded

from celery_config import celery_app
from core.database import SessionLocal
from core.models import LogEntry
from modules.knowledge_base import knowledge_base
from modules.knowledge_base_components.index_audit import run_index_consistency_audit
from modules.knowledge_base_components.offline_parse import cleanup_offline_file
from modules.stage25_switches import STAGE25_SWITCHES
from modules.test_generation import test_generator

logger = logging.getLogger(__name__)


def _is_retryable_snapshot_error(err: Exception) -> bool:
    """判断快照构建异常是否适合重试（网络/超时类）。"""
    text = str(err or "").lower()
    retry_keywords = [
        "ssl",
        "eof",
        "timeout",
        "timed out",
        "connection",
        "network",
        "temporarily unavailable",
    ]
    return any(k in text for k in retry_keywords)


@celery_app.task(bind=True, name="modules.tasks.generate_test_cases_task")
def generate_test_cases_task(
    self,
    requirement: str,
    project_id: int,
    doc_type: str = "requirement",
    compress: bool = False,
    expected_count: int = 20,
    batch_index: int = 0,
    batch_size: int = 20,
    user_id: int = None,
):
    """异步生成测试用例。"""
    db = SessionLocal()
    try:
        self.update_state(state="STARTED", meta={"status": "Generating test cases..."})
        result = test_generator.generate_test_cases_json(
            requirement=requirement,
            project_id=project_id,
            db=db,
            doc_type=doc_type,
            compress=compress,
            expected_count=expected_count,
            batch_index=batch_index,
            batch_size=batch_size,
            user_id=user_id,
        )
        return result
    except Exception as e:
        # 不手工写入 FAILURE 元数据，避免写入非 Celery 异常结构导致结果后端解码失败。
        logger.exception("测试用例生成任务异常 task_id=%s err=%s", getattr(self.request, "id", None), e)
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="modules.tasks.build_context_snapshot_task",
    max_retries=1,
    default_retry_delay=5,
    soft_time_limit=240,
    time_limit=300,
)
def build_context_snapshot_task(
    self,
    project_id: int,
    user_id: int = None,
    force_rebuild: bool = False,
):
    """
    项目级上下文快照异步构建任务。

    设计要点：
    1. 只负责后台预热，不阻塞在线生成链路。
    2. 网络抖动类异常允许有限重试，业务类失败直接落库可见。
    """
    db = SessionLocal()
    retry_count = int(getattr(self.request, "retries", 0) or 0)
    task_id = getattr(self.request, "id", None)
    max_retries = int(getattr(self, "max_retries", 0) or 0)

    logger.info(
        "快照构建任务启动 project_id=%s task_id=%s retry=%s force=%s",
        project_id,
        task_id,
        retry_count,
        force_rebuild,
    )

    try:
        self.update_state(
            state="STARTED",
            meta={"status": "项目上下文快照构建中", "project_id": project_id},
        )
        result = knowledge_base.get_or_build_context_snapshot(
            project_id=project_id,
            db=db,
            user_id=user_id,
            force_rebuild=force_rebuild,
            prefer_async_rebuild=False,
        )
        if result.get("success"):
            logger.info("快照构建任务完成 project_id=%s task_id=%s", project_id, task_id)
            return result

        fallback_reason = str(result.get("fallback_reason") or "")
        # no_docs 属于业务空语料，不是失败，不需要重试。
        if fallback_reason == "no_docs":
            return result

        # 统一抛出异常给 Celery 记录，且仅对可恢复问题做一次重试。
        raise RuntimeError(f"snapshot_build_failed:{fallback_reason}")
    except Exception as e:
        logger.exception(
            "快照构建任务异常 project_id=%s task_id=%s retry=%s/%s err=%s",
            project_id,
            task_id,
            retry_count,
            max_retries,
            e,
        )
        if retry_count < max_retries and _is_retryable_snapshot_error(e):
            raise self.retry(exc=e)
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="modules.tasks.parse_knowledge_document_task",
    max_retries=2,
    default_retry_delay=8,
    soft_time_limit=300,
    time_limit=360,
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
    db = SessionLocal()
    retry_count = int(getattr(self.request, "retries", 0) or 0)
    task_id = getattr(self.request, "id", None)

    logger.info(
        "离线解析任务启动 doc_id=%s task_id=%s retry=%s file=%s",
        document_id,
        task_id,
        retry_count,
        file_path,
    )

    try:
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
                knowledge_base.mark_document_parse_retry(
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
            knowledge_base.mark_document_parse_failed(
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

        cleanup_offline_file(file_path)
        # 让 Celery 统一记录 FAILURE 元信息，避免手工 update_state 写入不完整异常结构。
        raise e
    finally:
        db.close()


@celery_app.task(bind=True, name="modules.tasks.audit_knowledge_index_consistency_task")
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
    db = SessionLocal()
    try:
        if not STAGE25_SWITCHES.index_audit_enabled:
            return {"enabled": False, "message": "index_audit_disabled"}

        self.update_state(
            state="STARTED",
            meta={"status": "知识库索引一致性巡检中", "project_id": project_id},
        )
        report = run_index_consistency_audit(
            db=db,
            project_id=project_id,
            user_id=user_id,
            limit=limit,
        )
        try:
            db.add(
                LogEntry(
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


@celery_app.task(bind=True, name="modules.tasks.cleanup_logs_task")
def cleanup_logs_task(self, retention_hours: int = 72):
    """清理过期日志。"""
    from datetime import datetime, timedelta

    from core.models import LogEntry

    db = SessionLocal()
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


@celery_app.task(bind=True, name="modules.tasks.archive_old_data_task")
def archive_old_data_task(self, retention_days: int = 30):
    """归档并清理过期日志与测试生成记录。"""
    from datetime import datetime, timedelta
    import os

    from core.models import LogEntry, TestGeneration

    db = SessionLocal()
    cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
    archive_dir = "archive_data"
    os.makedirs(archive_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report = {"archived_logs": 0, "archived_tests": 0, "status": "success"}

    try:
        logs = db.query(LogEntry).filter(LogEntry.created_at < cutoff_date).all()
        if logs:
            log_data = [
                {
                    "id": l.id,
                    "project_id": l.project_id,
                    "type": l.log_type,
                    "msg": l.message,
                    "created_at": str(l.created_at),
                }
                for l in logs
            ]
            with open(os.path.join(archive_dir, f"logs_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)

            for l in logs:
                db.delete(l)
            report["archived_logs"] = len(logs)

        tests = db.query(TestGeneration).filter(TestGeneration.created_at < cutoff_date).all()
        if tests:
            test_data = [
                {
                    "id": t.id,
                    "project_id": t.project_id,
                    "requirement": t.requirement_text[:100] + "...",
                    "result_preview": (t.generated_result or "")[:100],
                    "created_at": str(t.created_at),
                }
                for t in tests
            ]
            with open(os.path.join(archive_dir, f"tests_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(test_data, f, ensure_ascii=False, indent=2)

            for t in tests:
                db.delete(t)
            report["archived_tests"] = len(tests)

        db.commit()
        return report
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
