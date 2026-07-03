from concurrent.futures import TimeoutError as FutureTimeoutError

from sqlalchemy.orm import Session

from modules.orchestration.background_task_governance import run_governed_threadpool_call
from ...context.snapshot_wait_gate import wait_snapshot_ready_gate
from ..runtime import LazyAttrProxy


SessionLocal = LazyAttrProxy("core.db.database", "SessionLocal")
knowledge_base = LazyAttrProxy("modules.domain.knowledge_base", "knowledge_base")


class LegacyGenerationContextGateMixin:

    def _is_active_db_session(self, db: Session | None) -> bool:
        """仅对真实 SQLAlchemy Session 执行 DB 读写或门禁逻辑。"""
        return isinstance(db, Session)

    def _try_sync_snapshot_retry_once(
        self,
        project_id: int,
        user_id: int | None,
        timeout_sec: int,
    ) -> dict:
        """
        触发一次“限时同步 snapshot 重建”。

        说明：
        1. 只尝试一次，避免无限重试。
        2. 使用独立数据库会话，避免跨线程复用 Session。
        3. 用 timeout 控制等待时长，超时后立即返回失败。
        """

        def _worker() -> dict:
            retry_db = SessionLocal()
            try:
                return knowledge_base.get_or_build_context_snapshot(
                    project_id=project_id,
                    db=retry_db,
                    user_id=user_id,
                    force_rebuild=True,
                    prefer_async_rebuild=False,
                )
            finally:
                retry_db.close()

        safe_timeout = max(2, int(timeout_sec or 0))
        try:
            result = run_governed_threadpool_call(
                profile_key="snapshot_sync_retry_threadpool",
                target=_worker,
                timeout=safe_timeout,
                max_workers=1,
                thread_name_prefix="snapshot-sync-retry",
                business_id=project_id,
            )
            ok = bool(result.get("success") and (result.get("snapshot_text") or "").strip())
            return {
                "success": ok,
                "result": result,
                "error": "" if ok else str(result.get("fallback_reason") or "sync_retry_empty_snapshot"),
            }
        except FutureTimeoutError:
            return {
                "success": False,
                "result": None,
                "error": f"sync_snapshot_retry_timeout:{safe_timeout}s",
            }
        except Exception as e:
            return {"success": False, "result": None, "error": f"sync_snapshot_retry_exception:{e}"}

    def _run_snapshot_readiness_gate(
        self,
        project_id: int,
        user_id: int | None,
        status_messages: list[str] | None = None,
    ) -> dict:
        """
        生成前 snapshot 门禁：
        - 仅做“是否可开始生成”的判定与等待，不改动现有 snapshot 构建主逻辑；
        - 通过短轮询等待 snapshot ready，避免“本次请求吃不到刚入队快照”。
        """

        def _get_status() -> dict:
            # 中文注释：每次轮询使用新会话，避免长事务下读取不到最新状态。
            gate_db = SessionLocal()
            try:
                return knowledge_base.get_context_snapshot_status(project_id=project_id, db=gate_db) or {}
            finally:
                gate_db.close()

        def _enqueue() -> dict:
            gate_db = SessionLocal()
            try:
                return knowledge_base.enqueue_context_snapshot_rebuild(
                    project_id=project_id,
                    db=gate_db,
                    user_id=user_id,
                    force_rebuild=False,
                ) or {}
            finally:
                gate_db.close()

        gate_result = wait_snapshot_ready_gate(
            get_status_fn=_get_status,
            enqueue_rebuild_fn=_enqueue,
            status_messages=status_messages,
        )
        gate_debug = gate_result.get("gate_debug") or {}
        print(
            "snapshot readiness gate: "
            f"enabled={gate_debug.get('snapshot_gate_enabled')} "
            f"before={gate_debug.get('snapshot_status_before_generation')} "
            f"after={gate_debug.get('snapshot_status_after_wait')} "
            f"poll={gate_debug.get('snapshot_wait_poll_count')} "
            f"elapsed_ms={gate_debug.get('snapshot_wait_elapsed_ms')} "
            f"result={gate_debug.get('snapshot_wait_result')} "
            f"queue={gate_debug.get('snapshot_wait_queue_status')}/{gate_debug.get('snapshot_wait_queue_reason')}"
        )
        if status_messages is not None:
            status_messages.append(
                "snapshot gate result: "
                f"{gate_debug.get('snapshot_wait_result')} "
                f"(poll={gate_debug.get('snapshot_wait_poll_count')}, "
                f"elapsed_ms={gate_debug.get('snapshot_wait_elapsed_ms')})"
            )
        return gate_result
