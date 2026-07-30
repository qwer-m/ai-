from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from .runtime import resolve_lazy_attr


@dataclass(frozen=True)
class PrepareRuntimeState:
    client: Any
    request_id: str
    original_requirement: str
    compression_decision: dict[str, Any]
    linked_final_case_signal: dict[str, Any]
    memory_diag: dict[str, Any]
    memory_fabric: Any
    memory_ctx: Any


@dataclass(frozen=True)
class AppendExistingState:
    start_id: int
    existing_cases: list[dict[str, Any]]
    existing_entry: Any
    existing_unique_count: int


_SEMANTIC_COMPILATION_STAGE_LABELS = {
    "current_requirement_atomic_fact_compile": "事实台账",
    "current_requirement_scope_boundary_selection_compile": "范围台账-边界选择",
    "current_requirement_scope_membership_compile": "范围台账-成员归属",
    "current_requirement_scope_binding_compile": "范围台账-事实绑定",
    "current_requirement_graph_compile": "语义图-整体编译",
    "current_requirement_graph_partition_compile": "语义图-局部节点",
    "current_requirement_graph_local_edge_compile": "语义图-局部关系",
    "current_requirement_graph_relation_compile": "语义图-跨分片关系",
    "current_requirement_graph_workflow_compile": "语义图-工作流",
}

_ACTIVE_SEMANTIC_COMPILATIONS: set[str] = set()
_ACTIVE_SEMANTIC_COMPILATIONS_LOCK = threading.Lock()


class SemanticCompilationInFlightError(RuntimeError):
    """同一真实需求已有语义编译在执行，拒绝并发启动第二条链路。"""

    code = "semantic_compilation_in_flight"

    def __init__(self, lock_name: str) -> None:
        self.lock_name = str(lock_name)
        super().__init__("同一项目、用户和需求已有语义编译正在执行")


class SemanticCompilationLockError(RuntimeError):
    """跨进程语义编译锁不可用时失败关闭。"""

    code = "semantic_compilation_lock_failed"


def _semantic_compilation_lock_name(
    *,
    project_id: int,
    user_id: int | None,
    requirement_text: str,
) -> str:
    identity = "\x1f".join(
        (
            str(int(project_id or 0)),
            str(int(user_id or 0)),
            str(requirement_text or ""),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
    return f"qoder:semantic:{digest}"


@contextmanager
def semantic_compilation_singleflight(
    *,
    db: Any,
    project_id: int,
    user_id: int | None,
    requirement_text: str,
) -> Iterator[str]:
    """用进程内登记和 MySQL 命名锁阻止同一需求并发编译。"""

    lock_name = _semantic_compilation_lock_name(
        project_id=project_id,
        user_id=user_id,
        requirement_text=requirement_text,
    )
    with _ACTIVE_SEMANTIC_COMPILATIONS_LOCK:
        if lock_name in _ACTIVE_SEMANTIC_COMPILATIONS:
            raise SemanticCompilationInFlightError(lock_name)
        _ACTIVE_SEMANTIC_COMPILATIONS.add(lock_name)

    mysql_lock_acquired = False
    try:
        if isinstance(db, Session) and db.bind is not None:
            try:
                mysql_lock_acquired = bool(
                    int(
                        db.execute(
                            text("SELECT GET_LOCK(:lock_name, 0)"),
                            {"lock_name": lock_name},
                        ).scalar()
                        or 0
                    )
                    == 1
                )
            except Exception as exc:
                raise SemanticCompilationLockError(
                    "获取语义编译跨进程锁失败"
                ) from exc
            if not mysql_lock_acquired:
                raise SemanticCompilationInFlightError(lock_name)
        yield lock_name
    finally:
        if mysql_lock_acquired:
            try:
                db.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": lock_name},
                )
            except Exception:
                # Session 关闭时 MySQL 会自动释放命名锁；这里不能覆盖主异常。
                pass
        with _ACTIVE_SEMANTIC_COMPILATIONS_LOCK:
            _ACTIVE_SEMANTIC_COMPILATIONS.discard(lock_name)


@dataclass(frozen=True)
class SemanticCompilationProgress:
    stage: str
    input_type: str
    shard_id: str
    attempt: int
    started_model_calls: int
    completed_model_calls: int
    failed_model_calls: int


@dataclass(frozen=True)
class SemanticCompilationUpdate:
    kind: str
    progress: SemanticCompilationProgress
    result: Any = None


class SemanticCompilationProgressTracker:
    """从真实模型调用边界记录语义编译进度，不修改调用参数。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = "准备语义编译"
        self._input_type = ""
        self._shard_id = ""
        self._attempt = 0
        self._started_model_calls = 0
        self._completed_model_calls = 0
        self._failed_model_calls = 0

    @staticmethod
    def _request_metadata(user_input: Any) -> dict[str, Any]:
        if not isinstance(user_input, str):
            return {}
        try:
            payload = json.loads(user_input)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, Mapping):
            return {}
        input_type = str(payload.get("input_type") or "")
        shard_id = str(
            payload.get("shard_id")
            or payload.get("relation_shard_id")
            or ""
        )
        try:
            attempt = max(0, int(payload.get("attempt") or 0))
        except (TypeError, ValueError):
            attempt = 0
        return {
            "input_type": input_type,
            "stage": _SEMANTIC_COMPILATION_STAGE_LABELS.get(
                input_type,
                input_type or "语义编译",
            ),
            "shard_id": shard_id,
            "attempt": attempt,
        }

    def model_call_started(self, user_input: Any) -> None:
        metadata = self._request_metadata(user_input)
        with self._lock:
            self._started_model_calls += 1
            self._stage = str(metadata.get("stage") or "语义编译")
            self._input_type = str(metadata.get("input_type") or "")
            self._shard_id = str(metadata.get("shard_id") or "")
            self._attempt = int(metadata.get("attempt") or 0)

    def model_call_finished(self, *, failed: bool) -> None:
        with self._lock:
            self._completed_model_calls += 1
            if failed:
                self._failed_model_calls += 1

    def snapshot(self) -> SemanticCompilationProgress:
        with self._lock:
            return SemanticCompilationProgress(
                stage=self._stage,
                input_type=self._input_type,
                shard_id=self._shard_id,
                attempt=self._attempt,
                started_model_calls=int(self._started_model_calls),
                completed_model_calls=int(self._completed_model_calls),
                failed_model_calls=int(self._failed_model_calls),
            )


class SemanticCompilationObservingClient:
    """透明转发 AI client，仅观察语义编译的真实 generate_response 调用。"""

    def __init__(self, client: Any, tracker: SemanticCompilationProgressTracker) -> None:
        self._client = client
        self._tracker = tracker

    @property
    def isolated_runtime_identity_client(self) -> Any:
        """供分片隔离守卫识别观察包装器背后的真实 AIClient。"""

        return self._client

    def generate_response(self, *args: Any, **kwargs: Any) -> Any:
        user_input = args[0] if args else kwargs.get("user_input")
        self._tracker.model_call_started(user_input)
        failed = False
        try:
            return self._client.generate_response(*args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            self._tracker.model_call_finished(failed=failed)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@contextmanager
def isolated_ai_runtime(
    *,
    user_id: int,
    create_db_session_fn: Callable[[], Any],
    get_client_for_user_fn: Callable[..., Any],
    client_transform_fn: Callable[[Any], Any] | None = None,
) -> Iterator[tuple[Any, Any]]:
    """为一个线程创建独立 DB Session 与 AIClient，并统一关闭 Session。"""

    worker_db = create_db_session_fn()
    try:
        worker_client = get_client_for_user_fn(user_id, worker_db)
        if client_transform_fn is not None:
            worker_client = client_transform_fn(worker_client)
        yield worker_client, worker_db
    finally:
        worker_db.close()


def iter_semantic_compilation_with_heartbeat(
    *,
    feedback_control_state: Any,
    client: Any,
    requirement_text: str,
    db: Any,
    project_id: int,
    user_id: int | None,
    merge_control_state_fn: Callable[..., Any],
    create_db_session_fn: Callable[[], Any],
    get_client_for_user_fn: Callable[..., Any],
    iter_governed_threadpool_map_fn: Callable[..., Any],
    heartbeat_interval_seconds: float = 15.0,
) -> Iterator[SemanticCompilationUpdate]:
    """在单 worker 中执行原语义编译，并向流式请求交付观察型心跳。"""

    tracker = SemanticCompilationProgressTracker()

    def _compile(_: None) -> Any:
        def _merge(
            *,
            execution_client: Any,
            execution_db: Any,
            isolated_ai_runtime_factory: Callable[[], Any] | None,
        ) -> Any:
            return merge_control_state_fn(
                feedback_control_state,
                client=execution_client,
                requirement_text=requirement_text,
                db=execution_db,
                project_id=project_id,
                user_id=user_id,
                isolated_ai_runtime_factory=(
                    isolated_ai_runtime_factory
                ),
            )

        # 没有用户级模型配置时不能安全克隆当前 client，因此保持单线程原语义。
        if not (isinstance(db, Session) and bool(user_id)):
            return _merge(
                execution_client=SemanticCompilationObservingClient(
                    client,
                    tracker,
                ),
                execution_db=db,
                isolated_ai_runtime_factory=None,
            )

        def _observed_client(worker_client: Any) -> Any:
            return SemanticCompilationObservingClient(worker_client, tracker)

        def _isolated_ai_runtime_factory() -> Any:
            return isolated_ai_runtime(
                user_id=int(user_id),
                create_db_session_fn=create_db_session_fn,
                get_client_for_user_fn=get_client_for_user_fn,
                client_transform_fn=_observed_client,
            )

        with isolated_ai_runtime(
            user_id=int(user_id),
            create_db_session_fn=create_db_session_fn,
            get_client_for_user_fn=get_client_for_user_fn,
            client_transform_fn=_observed_client,
        ) as (execution_client, execution_db):
            return _merge(
                execution_client=execution_client,
                execution_db=execution_db,
                isolated_ai_runtime_factory=(
                    _isolated_ai_runtime_factory
                ),
            )

    with semantic_compilation_singleflight(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=requirement_text,
    ):
        for update in iter_governed_threadpool_map_fn(
            profile_key="test_generation_semantic_compile_threadpool",
            items=[None],
            worker=_compile,
            max_workers=1,
            thread_name_prefix="semantic-compile",
            business_id=project_id,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        ):
            if update.kind == "heartbeat":
                yield SemanticCompilationUpdate(
                    kind="heartbeat",
                    progress=tracker.snapshot(),
                )
                continue
            item_result = update.item_result
            if item_result is None:
                continue
            if item_result.exception is not None:
                raise item_result.exception
            yield SemanticCompilationUpdate(
                kind="result",
                progress=tracker.snapshot(),
                result=item_result.result,
            )


def record_prepare_timing_event(
    timing_events: list[dict[str, Any]],
    stage: str,
    started_at: float,
    **fields: Any,
) -> dict[str, Any]:
    event = {
        "stage": str(stage or "unknown"),
        "duration_ms": max(0, int(round((time.perf_counter() - started_at) * 1000))),
    }
    for key, value in fields.items():
        if value is not None:
            event[key] = value
    timing_events.append(event)
    return event


def resolve_stream_prepare_runtime(
    *,
    user_id: int | None,
    db: Any,
    project_id: int,
    requirement: str,
    compress: bool,
    get_client_for_user_fn: Callable[..., Any],
    requirement_compression_decision_fn: Callable[..., dict[str, Any]],
    resolve_linked_final_case_signal_fn: Callable[..., dict[str, Any]],
    init_memory_diag_fn: Callable[[], dict[str, Any]],
    get_memory_fabric_fn: Callable[[], Any],
    memory_context_cls: Any,
    record_timing_event_fn: Callable[..., dict[str, Any]],
) -> PrepareRuntimeState:
    client_started = time.perf_counter()
    client = get_client_for_user_fn(user_id, db)
    request_id = uuid.uuid4().hex
    record_timing_event_fn(
        "client_resolution",
        client_started,
        db_available=bool(db),
        user_scoped=user_id is not None,
    )

    original_requirement = requirement
    compression_decision_started = time.perf_counter()
    compression_decision = requirement_compression_decision_fn(
        requirement,
        compress_requested=bool(compress),
    )
    record_timing_event_fn(
        "requirement_compression_decision",
        compression_decision_started,
        char_count=int(compression_decision.get("char_count") or 0),
        min_chars=int(compression_decision.get("min_chars") or 0),
        should_compress=bool(compression_decision.get("should_compress")),
    )

    linked_signal_started = time.perf_counter()
    linked_final_case_signal = resolve_linked_final_case_signal_fn(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=original_requirement,
    )
    record_timing_event_fn(
        "linked_final_case_signal",
        linked_signal_started,
        linked_final_case_count=int(linked_final_case_signal.get("linked_final_case_count") or 0),
        source_doc_count=int(len(linked_final_case_signal.get("source_doc_ids") or [])),
    )

    memory_diag = init_memory_diag_fn()
    memory_fabric = None
    memory_fabric_started = time.perf_counter()
    try:
        memory_fabric = get_memory_fabric_fn()
    except Exception:
        memory_fabric = None
    record_timing_event_fn(
        "memory_fabric_init",
        memory_fabric_started,
        available=bool(memory_fabric),
    )
    memory_ctx = memory_context_cls.from_runtime(
        user_id=user_id,
        project_id=project_id,
        run_id=request_id,
        request_id=request_id,
    )
    return PrepareRuntimeState(
        client=client,
        request_id=request_id,
        original_requirement=original_requirement,
        compression_decision=compression_decision,
        linked_final_case_signal=linked_final_case_signal,
        memory_diag=memory_diag,
        memory_fabric=memory_fabric,
        memory_ctx=memory_ctx,
    )


def resolve_append_existing_state(
    *,
    db: Any,
    append: bool,
    project_id: int,
    user_id: int | None,
    original_requirement: str,
    test_generation_model: Any,
    prepare_append_existing_cases_fn: Callable[..., tuple[list[dict[str, Any]], int, int]],
    normalize_json_structure_fn: Callable[..., Any],
    deduplicate_test_cases_fn: Callable[..., list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[..., int],
    record_timing_event_fn: Callable[..., dict[str, Any]],
) -> AppendExistingState:
    start_id = 1
    existing_cases: list[dict[str, Any]] = []
    existing_entry = None
    existing_unique_count = 0

    if not (db and append):
        return AppendExistingState(
            start_id=start_id,
            existing_cases=existing_cases,
            existing_entry=existing_entry,
            existing_unique_count=existing_unique_count,
        )

    append_lookup_started = time.perf_counter()
    from sqlalchemy import desc

    resolved_model = resolve_lazy_attr(test_generation_model)
    query = db.query(resolved_model).filter(
        resolved_model.project_id == project_id,
        resolved_model.requirement_text == original_requirement,
    )
    if user_id:
        query = query.filter(resolved_model.user_id == user_id)
    existing_entry = query.order_by(desc(resolved_model.created_at)).first()

    if existing_entry and existing_entry.generated_result:
        existing_cases, existing_unique_count, start_id = prepare_append_existing_cases_fn(
            existing_entry.generated_result,
            normalize_json_structure_fn=normalize_json_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            count_unique_test_cases_fn=count_unique_test_cases_fn,
        )
    record_timing_event_fn(
        "append_existing_lookup",
        append_lookup_started,
        found=bool(existing_entry),
        existing_unique_count=int(existing_unique_count or 0),
        start_id=int(start_id or 1),
    )
    return AppendExistingState(
        start_id=start_id,
        existing_cases=existing_cases,
        existing_entry=existing_entry,
        existing_unique_count=int(existing_unique_count or 0),
    )
