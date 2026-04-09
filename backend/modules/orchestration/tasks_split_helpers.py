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
from core.db.database import SessionLocal
from core.db.models import LogEntry
from modules.domain.knowledge_base import knowledge_base
from modules.knowledge_base_components.document.index_audit import run_index_consistency_audit
from modules.knowledge_base_components.document.offline_parse import cleanup_offline_file
from modules.domain.stage25_switches import STAGE25_SWITCHES
from modules.testing.test_generation import test_generator

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


@celery_app.task(bind=True, name="modules.orchestration.tasks.generate_test_cases_task")
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
    current_biz_key: str = "",
    only_current_biz: bool = False,
    multi_pass: bool = True,
    generation_mode: str = "",
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
            current_biz_key=current_biz_key,
            only_current_biz=only_current_biz,
            multi_pass=multi_pass,
            generation_mode=generation_mode,
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
    name="modules.orchestration.tasks.build_context_snapshot_task",
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
