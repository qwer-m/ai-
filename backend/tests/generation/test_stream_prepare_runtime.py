from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy.orm import Session

from modules.orchestration.background_task_governance import (
    iter_governed_threadpool_map,
)
from modules.test_generation_components.legacy.stream.prepare_runtime import (
    SemanticCompilationInFlightError,
    SemanticCompilationObservingClient,
    SemanticCompilationProgressTracker,
    iter_semantic_compilation_with_heartbeat,
    record_prepare_timing_event,
    resolve_append_existing_state,
    resolve_stream_prepare_runtime,
    semantic_compilation_singleflight,
)


class _MemoryContext:
    @classmethod
    def from_runtime(cls, **kwargs):
        return {"ctx": dict(kwargs)}


class _EmptyQuery:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _RejectLazyModelDb:
    def __init__(self) -> None:
        self.query_model = None

    def query(self, model):
        from modules.test_generation_components.legacy.stream.runtime import LazyAttrProxy

        assert not isinstance(model, LazyAttrProxy)
        self.query_model = model
        return _EmptyQuery()


class _WorkerDb:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _CompileResult:
    def __init__(self, value: str) -> None:
        self.value = value


class _RecordingClient:
    def __init__(self, response: str = "compiled") -> None:
        self.response = response
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.last_response_metadata = {"model": "real-model", "cached": False}

    def generate_response(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls.append((args, kwargs))
        return self.response


def test_resolve_stream_prepare_runtime_records_core_prepare_events() -> None:
    events: list[dict[str, object]] = []
    client = object()

    state = resolve_stream_prepare_runtime(
        user_id=7,
        db=object(),
        project_id=11,
        requirement="course schedule requirement",
        compress=True,
        get_client_for_user_fn=lambda user_id, db: client,
        requirement_compression_decision_fn=lambda requirement, **kwargs: {
            "char_count": len(requirement),
            "min_chars": 1000,
            "should_compress": False,
        },
        resolve_linked_final_case_signal_fn=lambda **kwargs: {
            "linked_final_case_count": 3,
            "source_doc_ids": [1, 2],
        },
        init_memory_diag_fn=lambda: {"memory_reads": {}},
        get_memory_fabric_fn=lambda: "memory-fabric",
        memory_context_cls=_MemoryContext,
        record_timing_event_fn=lambda stage, started_at, **fields: events.append(
            {"stage": stage, **fields}
        )
        or events[-1],
    )

    assert state.client is client
    assert state.original_requirement == "course schedule requirement"
    assert state.compression_decision["char_count"] == len("course schedule requirement")
    assert state.linked_final_case_signal["linked_final_case_count"] == 3
    assert state.memory_fabric == "memory-fabric"
    assert state.memory_ctx["ctx"]["run_id"] == state.request_id
    assert [event["stage"] for event in events] == [
        "client_resolution",
        "requirement_compression_decision",
        "linked_final_case_signal",
        "memory_fabric_init",
    ]


def test_resolve_append_existing_state_noops_without_append() -> None:
    events: list[dict[str, object]] = []

    state = resolve_append_existing_state(
        db=object(),
        append=False,
        project_id=1,
        user_id=2,
        original_requirement="REQ",
        test_generation_model=object(),
        prepare_append_existing_cases_fn=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("append helper should not run")
        ),
        normalize_json_structure_fn=lambda value: value,
        deduplicate_test_cases_fn=lambda value: value,
        count_unique_test_cases_fn=lambda value: len(value),
        record_timing_event_fn=lambda stage, started_at, **fields: events.append(
            {"stage": stage, **fields}
        )
        or events[-1],
    )

    assert state.start_id == 1
    assert state.existing_cases == []
    assert state.existing_entry is None
    assert state.existing_unique_count == 0
    assert events == []


def test_resolve_append_existing_state_resolves_lazy_model_before_query() -> None:
    from modules.test_generation_components.legacy.stream.runtime import LazyAttrProxy

    events: list[dict[str, object]] = []
    db = _RejectLazyModelDb()

    state = resolve_append_existing_state(
        db=db,
        append=True,
        project_id=1,
        user_id=2,
        original_requirement="REQ",
        test_generation_model=LazyAttrProxy("core.db.models", "TestGeneration"),
        prepare_append_existing_cases_fn=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("append helper should not run without existing entry")
        ),
        normalize_json_structure_fn=lambda value: value,
        deduplicate_test_cases_fn=lambda value: value,
        count_unique_test_cases_fn=lambda value: len(value),
        record_timing_event_fn=lambda stage, started_at, **fields: events.append(
            {"stage": stage, **fields}
        )
        or events[-1],
    )

    assert getattr(db.query_model, "__name__", "") == "TestGeneration"
    assert state.existing_entry is None
    assert events and events[0]["stage"] == "append_existing_lookup"


def test_record_prepare_timing_event_appends_non_null_fields() -> None:
    events: list[dict[str, object]] = []

    event = record_prepare_timing_event(events, "stage", 0.0, kept=True, skipped=None)

    assert event["stage"] == "stage"
    assert event["kept"] is True
    assert "skipped" not in event
    assert events == [event]


def test_semantic_compilation_observing_client_transparently_forwards_call() -> None:
    client = _RecordingClient(response="原始响应")
    tracker = SemanticCompilationProgressTracker()
    observing_client = SemanticCompilationObservingClient(client, tracker)
    user_input = json.dumps(
        {
            "input_type": "current_requirement_graph_partition_compile",
            "shard_id": "M003",
            "attempt": 2,
        },
        ensure_ascii=False,
    )

    result = observing_client.generate_response(
        user_input,
        "系统提示词",
        db="db-session",
        max_tokens=1234,
        task_type="test_generation",
    )

    assert result == "原始响应"
    assert client.calls == [
        (
            (user_input, "系统提示词"),
            {
                "db": "db-session",
                "max_tokens": 1234,
                "task_type": "test_generation",
            },
        )
    ]
    assert observing_client.last_response_metadata == client.last_response_metadata
    progress = tracker.snapshot()
    assert progress.stage == "语义图-局部节点"
    assert progress.shard_id == "M003"
    assert progress.attempt == 2
    assert progress.started_model_calls == 1
    assert progress.completed_model_calls == 1
    assert progress.failed_model_calls == 0


def test_semantic_compilation_helper_emits_real_progress_and_closes_worker_db() -> None:
    source_db = Session()
    worker_db = _WorkerDb()
    request_client = _RecordingClient(response="request-client")
    worker_client = _RecordingClient(response="worker-client")
    release = threading.Event()
    timer = threading.Timer(0.05, release.set)
    timer.start()
    factory_calls: list[tuple[int | None, object]] = []

    def compile_state(state, *, client, db, **kwargs):  # noqa: ANN001, ARG001
        assert state == {"state": "original"}
        assert db is worker_db
        response = client.generate_response(
            json.dumps(
                {
                    "input_type": "current_requirement_graph_relation_compile",
                    "relation_shard_id": "R002",
                }
            ),
            "prompt",
            db=db,
        )
        release.wait(1)
        return _CompileResult(response)

    try:
        updates = list(
            iter_semantic_compilation_with_heartbeat(
                feedback_control_state={"state": "original"},
                client=request_client,
                requirement_text="真实需求",
                db=source_db,
                project_id=9,
                user_id=7,
                merge_control_state_fn=compile_state,
                create_db_session_fn=lambda: worker_db,
                get_client_for_user_fn=lambda user_id, db: (
                    factory_calls.append((user_id, db)) or worker_client
                ),
                iter_governed_threadpool_map_fn=iter_governed_threadpool_map,
                heartbeat_interval_seconds=0.005,
            )
        )
    finally:
        release.set()
        timer.cancel()
        source_db.close()

    heartbeats = [update for update in updates if update.kind == "heartbeat"]
    assert heartbeats
    assert any(update.progress.stage == "语义图-跨分片关系" for update in heartbeats)
    assert any(update.progress.shard_id == "R002" for update in heartbeats)
    assert factory_calls == [(7, worker_db)]
    assert request_client.calls == []
    assert len(worker_client.calls) == 1
    assert worker_db.closed is True
    assert updates[-1].kind == "result"
    assert updates[-1].result.value == "worker-client"


def test_semantic_compilation_helper_closes_worker_db_and_reraises() -> None:
    source_db = Session()
    worker_db = _WorkerDb()

    try:
        with pytest.raises(ValueError, match="compile failed"):
            list(
                iter_semantic_compilation_with_heartbeat(
                    feedback_control_state={},
                    client=_RecordingClient(),
                    requirement_text="真实需求",
                    db=source_db,
                    project_id=9,
                    user_id=7,
                    merge_control_state_fn=lambda *args, **kwargs: (_ for _ in ()).throw(
                        ValueError("compile failed")
                    ),
                    create_db_session_fn=lambda: worker_db,
                    get_client_for_user_fn=lambda user_id, db: _RecordingClient(),
                    iter_governed_threadpool_map_fn=iter_governed_threadpool_map,
                    heartbeat_interval_seconds=0.005,
                )
            )
    finally:
        source_db.close()

    assert worker_db.closed is True


def test_semantic_compilation_passes_fresh_graph_worker_runtime_factory() -> None:
    source_db = Session()
    created_dbs: list[_WorkerDb] = []
    created_clients: list[_RecordingClient] = []
    factory_calls: list[tuple[int | None, object]] = []

    def create_db_session() -> _WorkerDb:
        worker_db = _WorkerDb()
        created_dbs.append(worker_db)
        return worker_db

    def create_client(user_id, worker_db):  # noqa: ANN001
        client = _RecordingClient(response=f"client-{len(created_clients) + 1}")
        created_clients.append(client)
        factory_calls.append((user_id, worker_db))
        return client

    def compile_state(state, *, client, db, **kwargs):  # noqa: ANN001, ARG001
        runtime_factory = kwargs["isolated_ai_runtime_factory"]
        assert callable(runtime_factory)
        with runtime_factory() as (first_client, first_db):
            assert first_client is not client
            assert first_db is not db
        with runtime_factory() as (second_client, second_db):
            assert second_client is not first_client
            assert second_db is not first_db
        return _CompileResult("compiled")

    try:
        updates = list(
            iter_semantic_compilation_with_heartbeat(
                feedback_control_state={},
                client=_RecordingClient(response="request-client"),
                requirement_text="真实需求",
                db=source_db,
                project_id=9,
                user_id=7,
                merge_control_state_fn=compile_state,
                create_db_session_fn=create_db_session,
                get_client_for_user_fn=create_client,
                iter_governed_threadpool_map_fn=iter_governed_threadpool_map,
                heartbeat_interval_seconds=0.005,
            )
        )
    finally:
        source_db.close()

    assert updates[-1].kind == "result"
    assert len(created_dbs) == 3
    assert len({id(item) for item in created_dbs}) == 3
    assert len({id(item) for item in created_clients}) == 3
    assert factory_calls == [(7, worker_db) for worker_db in created_dbs]
    assert all(worker_db.closed for worker_db in created_dbs)


def test_semantic_compilation_singleflight_rejects_same_identity_and_releases() -> None:
    identity = {
        "db": object(),
        "project_id": 9,
        "user_id": 7,
        "requirement_text": "真实需求",
    }

    with semantic_compilation_singleflight(**identity) as first_lock_name:
        assert first_lock_name.startswith("qoder:semantic:")
        assert len(first_lock_name) <= 64
        with pytest.raises(SemanticCompilationInFlightError) as exc_info:
            with semantic_compilation_singleflight(**identity):
                pass
        assert exc_info.value.code == "semantic_compilation_in_flight"
        assert exc_info.value.lock_name == first_lock_name

    with semantic_compilation_singleflight(**identity) as released_lock_name:
        assert released_lock_name == first_lock_name


def test_semantic_compilation_singleflight_distinguishes_requirement_identity() -> None:
    common = {"db": object(), "project_id": 9, "user_id": 7}

    with semantic_compilation_singleflight(
        **common,
        requirement_text="需求 A",
    ) as first_lock_name:
        with semantic_compilation_singleflight(
            **common,
            requirement_text="需求 B",
        ) as second_lock_name:
            assert first_lock_name != second_lock_name
