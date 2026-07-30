from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

import modules.test_generation_components.control.current_requirement_blueprint as blueprint
import modules.test_generation_components.control.requirement_fact_ledger_compiler as compiler
import modules.test_generation_components.legacy.json_generation_impl as json_impl
from modules.test_generation_components.control.model_envelope_call import (
    EnvelopeCallResult,
)
from modules.test_generation_components.control.feedback_control_state import (
    FeedbackControlState,
)
from modules.test_generation_components.legacy.json_generation_runtime import (
    JsonGenerationRuntimeState,
)
from modules.test_generation_components.legacy.stream.prepare_runtime import (
    SemanticCompilationObservingClient,
    SemanticCompilationProgressTracker,
)


class _Provider:
    pass


class _Client:
    def __init__(self, runtime_id: int) -> None:
        self.runtime_id = runtime_id
        self.provider = _Provider()


class _Db:
    def __init__(self, runtime_id: int) -> None:
        self.runtime_id = runtime_id
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _envelope(chunk_index: int) -> EnvelopeCallResult:
    return EnvelopeCallResult(
        envelope_id=f"fact-ledger-chunk-{chunk_index}",
        status="response",
        raw_text="{}",
        response_metadata={},
        attempts=(),
        cache_lookup_enabled=False,
        physical_call_count=1,
        transport_failure_count=0,
        transport_retry_count=0,
    )


def _catalog() -> list[dict[str, str]]:
    return [
        {
            "ref": f"EV_{index:012x}",
            "quote": f"通用原子事实 {index}",
        }
        for index in range(1, 4)
    ]


def _install_chunk_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_chunk_index: int = 0,
) -> dict[str, Any]:
    chunks = tuple(
        compiler._CatalogChunk(
            index=index,
            items=(item,),
            budget_units=1,
            fingerprint=f"chunk-{index}",
        )
        for index, item in enumerate(_catalog(), start=1)
    )
    runtimes: list[tuple[_Client, _Db]] = []
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    first_pair_started = threading.Event()
    completion_order: list[int] = []
    merge_order: list[int] = []

    monkeypatch.setattr(
        compiler,
        "_partition_source_evidence_catalog",
        lambda items, max_tokens: (list(chunks), 1, 0),
    )
    monkeypatch.setattr(
        compiler,
        "validate_requirement_fact_ledger_fingerprints",
        lambda ledger: {"valid": True, "error_codes": []},
    )

    def compile_chunk(*, client: Any, db: Any, chunk_index: int, **kwargs: Any):
        nonlocal active, max_active
        assert isinstance(client, _Client)
        assert isinstance(db, _Db)
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                first_pair_started.set()
        first_pair_started.wait(1)
        time.sleep({1: 0.04, 2: 0.005, 3: 0.01}[chunk_index])
        with active_lock:
            active -= 1
            completion_order.append(chunk_index)
        status = (
            "contract_invalid"
            if chunk_index == failed_chunk_index
            else "validated"
        )
        normalized_ledger = {
            "chunk_index": chunk_index,
            "fingerprint": f"ledger-{chunk_index}",
            "diagnostics": {
                "source_evidence_count": 3,
                "target_source_evidence_count": 1,
                "source_disposition_count": 1,
                "fact_count": 1,
            },
        }
        return compiler._CatalogCompilation(
            status=status,
            normalized_ledger=normalized_ledger,
            attempts=(
                {
                    "chunk_index": chunk_index,
                    "status": status,
                },
            ),
            envelope_results=(_envelope(chunk_index),),
            fresh_candidate_trigger_codes=(),
            validated_attempt=1 if status == "validated" else 0,
            last_parseable_candidate=None,
        )

    monkeypatch.setattr(compiler, "_compile_catalog_chunk", compile_chunk)

    def merge_chunks(chunk_ledgers, *, source_evidence_catalog):  # noqa: ANN001, ARG001
        merge_order.extend(item["chunk_index"] for item in chunk_ledgers)
        return {}, [], 0

    monkeypatch.setattr(compiler, "_merge_chunk_raw_declarations", merge_chunks)
    monkeypatch.setattr(
        compiler,
        "normalize_requirement_fact_ledger",
        lambda candidate, **kwargs: {
            "valid": True,
            "fingerprint": "merged-ledger",
            "diagnostics": {
                "fact_count": 3,
                "source_evidence_count": 3,
                "source_disposition_count": 3,
                "error_codes": [],
            },
        },
    )

    @contextmanager
    def worker_runtime_factory():
        runtime_id = len(runtimes) + 1
        worker_client = _Client(runtime_id)
        worker_db = _Db(runtime_id)
        runtimes.append((worker_client, worker_db))
        try:
            yield worker_client, worker_db
        finally:
            worker_db.close()

    return {
        "runtimes": runtimes,
        "completion_order": completion_order,
        "merge_order": merge_order,
        "max_active": lambda: max_active,
        "worker_runtime_factory": worker_runtime_factory,
    }


def _compile(
    harness: dict[str, Any],
    *,
    parent_client: Any | None = None,
    parent_db: Any | None = None,
):
    return compiler.compile_requirement_atomic_fact_ledger(
        client=parent_client if parent_client is not None else _Client(0),
        source_evidence_catalog=_catalog(),
        db=parent_db if parent_db is not None else _Db(0),
        max_tokens=1200,
        request_timeout_seconds=120,
        worker_runtime_factory=harness["worker_runtime_factory"],
    )


def test_fact_ledger_chunks_are_bounded_isolated_and_merged_by_source_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_chunk_harness(monkeypatch)

    result = _compile(harness)

    assert result.success is True
    assert harness["max_active"]() == compiler.MAX_FACT_LEDGER_CHUNK_WORKERS == 2
    assert harness["completion_order"] != [1, 2, 3]
    assert harness["merge_order"] == [1, 2, 3]
    assert len(harness["runtimes"]) == 3
    assert len({id(client) for client, _ in harness["runtimes"]}) == 3
    assert len({id(client.provider) for client, _ in harness["runtimes"]}) == 3
    assert len({id(db) for _, db in harness["runtimes"]}) == 3
    assert all(db.closed for _, db in harness["runtimes"])
    assert result.diagnostics["fact_ledger_compile_parallel_chunks_enabled"] is True
    assert result.diagnostics["fact_ledger_compile_chunk_max_workers"] == 2
    assert [
        item["chunk_index"]
        for item in result.diagnostics["fact_ledger_compile_attempts"]
    ] == [1, 2, 3]


def test_fact_ledger_chunk_failure_discards_all_partial_ledgers_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_chunk_harness(monkeypatch, failed_chunk_index=2)

    result = _compile(harness)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.normalized_ledger == {}
    assert result.diagnostics["fact_ledger_compile_failed_chunk_index"] == 2
    assert result.diagnostics["fact_ledger_compile_completed_chunk_count"] == 1
    assert [
        item["chunk_index"]
        for item in result.diagnostics["fact_ledger_compile_attempts"]
    ] == [1, 2]
    assert harness["merge_order"] == []
    assert all(db.closed for _, db in harness["runtimes"])


@pytest.mark.parametrize("shared_resource", ["client", "provider", "db"])
def test_fact_ledger_chunks_reject_shared_provider_client_or_db(
    monkeypatch: pytest.MonkeyPatch,
    shared_resource: str,
) -> None:
    harness = _install_chunk_harness(monkeypatch)
    parent_client = _Client(0)
    parent_db = _Db(0)
    shared_provider = parent_client.provider
    created_dbs: list[_Db] = []

    @contextmanager
    def shared_runtime_factory():
        runtime_id = len(created_dbs) + 1
        worker_client = (
            parent_client
            if shared_resource == "client"
            else _Client(runtime_id)
        )
        if shared_resource == "provider":
            worker_client.provider = shared_provider
        worker_db = parent_db if shared_resource == "db" else _Db(runtime_id)
        created_dbs.append(worker_db)
        try:
            yield worker_client, worker_db
        finally:
            worker_db.close()

    harness["worker_runtime_factory"] = shared_runtime_factory

    with pytest.raises(
        RuntimeError,
        match="A1 fact ledger 分片 worker 禁止共享 provider、AIClient 或 DB Session",
    ):
        _compile(
            harness,
            parent_client=parent_client,
            parent_db=parent_db,
        )

    assert created_dbs
    assert all(db.closed for db in created_dbs)


def test_fact_ledger_rejects_distinct_observers_over_shared_ai_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_chunk_harness(monkeypatch)
    shared_client = _Client(0)
    shared_client.provider = None
    parent_client = SemanticCompilationObservingClient(
        shared_client,
        SemanticCompilationProgressTracker(),
    )
    parent_db = _Db(0)

    @contextmanager
    def wrapped_shared_runtime_factory():
        worker_db = _Db(1)
        try:
            yield (
                SemanticCompilationObservingClient(
                    shared_client,
                    SemanticCompilationProgressTracker(),
                ),
                worker_db,
            )
        finally:
            worker_db.close()

    harness["worker_runtime_factory"] = wrapped_shared_runtime_factory

    with pytest.raises(
        RuntimeError,
        match="A1 fact ledger 分片 worker 禁止共享 provider、AIClient 或 DB Session",
    ):
        _compile(
            harness,
            parent_client=parent_client,
            parent_db=parent_db,
        )


def test_fact_ledger_shared_runtime_failure_does_not_wait_for_long_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_chunk_harness(monkeypatch)
    parent_client = _Client(0)
    parent_db = _Db(0)
    started = threading.Event()
    release = threading.Event()
    factory_lock = threading.Lock()
    factory_call_count = 0
    original_compile = compiler._compile_catalog_chunk

    def blocking_compile(*, client: _Client, **kwargs: Any):
        if client.runtime_id != 1:
            return original_compile(client=client, **kwargs)
        started.set()
        assert release.wait(2)
        return original_compile(client=client, **kwargs)

    monkeypatch.setattr(compiler, "_compile_catalog_chunk", blocking_compile)

    @contextmanager
    def partially_shared_runtime_factory():
        nonlocal factory_call_count
        with factory_lock:
            factory_call_count += 1
            runtime_id = factory_call_count
        if runtime_id == 1:
            worker_client = _Client(runtime_id)
        else:
            assert started.wait(1)
            worker_client = parent_client
        worker_db = _Db(runtime_id)
        try:
            yield worker_client, worker_db
        finally:
            worker_db.close()

    harness["worker_runtime_factory"] = partially_shared_runtime_factory
    started_at = time.perf_counter()
    try:
        with pytest.raises(RuntimeError):
            _compile(
                harness,
                parent_client=parent_client,
                parent_db=parent_db,
            )
        assert time.perf_counter() - started_at < 1
        assert started.is_set() is True
        assert release.is_set() is False
    finally:
        release.set()


@pytest.mark.parametrize("use_factory", [False, True])
def test_fact_ledger_without_factory_or_with_single_chunk_stays_serial(
    monkeypatch: pytest.MonkeyPatch,
    use_factory: bool,
) -> None:
    harness = _install_chunk_harness(monkeypatch)
    parent_client = _Client(0)
    parent_db = _Db(0)
    calls: list[tuple[Any, Any]] = []
    factory_called = False
    only_chunk = compiler._CatalogChunk(
        index=1,
        items=(compiler.normalize_source_evidence_catalog(_catalog())["items"][0],),
        budget_units=1,
        fingerprint="single",
    )
    monkeypatch.setattr(
        compiler,
        "_partition_source_evidence_catalog",
        lambda items, max_tokens: ([only_chunk], 1, 0),
    )
    original_compile = compiler._compile_catalog_chunk

    def recording_compile(*, client: Any, db: Any, **kwargs: Any):
        calls.append((client, db))
        return original_compile(client=client, db=db, **kwargs)

    monkeypatch.setattr(compiler, "_compile_catalog_chunk", recording_compile)

    @contextmanager
    def forbidden_factory():
        nonlocal factory_called
        factory_called = True
        yield _Client(9), _Db(9)

    result = compiler.compile_requirement_atomic_fact_ledger(
        client=parent_client,
        source_evidence_catalog=_catalog(),
        db=parent_db,
        max_tokens=1200,
        request_timeout_seconds=120,
        worker_runtime_factory=forbidden_factory if use_factory else None,
    )

    assert result.success is True
    assert calls == [(parent_client, parent_db)]
    assert factory_called is False
    assert result.diagnostics["fact_ledger_compile_parallel_chunks_enabled"] is False
    assert result.diagnostics["fact_ledger_compile_chunk_max_workers"] == 1


def test_blueprint_entry_passes_isolated_runtime_factory_into_fact_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runtime_factory():
        return None

    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        blueprint,
        "build_requirement_business_evidence_view",
        lambda requirement: ("可验证业务证据", {}),
    )
    monkeypatch.setattr(
        blueprint,
        "_build_source_quote_catalog",
        lambda evidence_source: _catalog(),
    )
    monkeypatch.setattr(
        blueprint,
        "_source_quote_catalog_coverage",
        lambda evidence_source, catalog: {"complete": True},
    )

    def compile_fact_ledger(**kwargs: Any):
        captured.update(kwargs)
        return SimpleNamespace(
            success=False,
            status="contract_invalid",
            diagnostics={
                "fact_ledger_compile_status": "contract_invalid",
                "fact_ledger_compile_success": False,
            },
        )

    monkeypatch.setattr(
        blueprint,
        "compile_requirement_atomic_fact_ledger",
        compile_fact_ledger,
    )

    blueprints, diagnostics = blueprint.extract_current_requirement_blueprints(
        client=SimpleNamespace(generate_response=lambda *args, **kwargs: "{}"),
        requirement_text="可验证业务证据",
        isolated_ai_runtime_factory=runtime_factory,
    )

    assert blueprints == []
    assert diagnostics["semantic_pipeline_failed_stage"] == "fact_ledger"
    assert captured["worker_runtime_factory"] is runtime_factory


def test_merge_entry_forwards_isolated_runtime_factory_to_blueprint_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runtime_factory():
        return None

    captured: dict[str, Any] = {}

    def extract(**kwargs: Any):
        captured.update(kwargs)
        return [], {
            "semantic_compile_success": False,
            "current_requirement_blueprint_status": "fact_ledger_contract_invalid",
        }

    monkeypatch.setattr(
        blueprint,
        "extract_current_requirement_blueprints",
        extract,
    )

    blueprint.merge_current_requirement_blueprint_control_state(
        FeedbackControlState.empty(),
        client=object(),
        requirement_text="可验证业务证据",
        isolated_ai_runtime_factory=runtime_factory,
    )

    assert captured["isolated_ai_runtime_factory"] is runtime_factory


class _JsonHarness(json_impl.LegacyGenerationJsonMixin):
    def _is_active_db_session(self, db: Any) -> bool:
        return True

    def _run_snapshot_readiness_gate(self, **kwargs: Any) -> dict[str, Any]:
        return {"proceed": True, "gate_debug": {"snapshot_wait_result": "ready"}}

    def _resolve_kb_context_with_hybrid(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "kb_context": "可验证业务上下文",
            "context_source": "current_requirement",
            "fusion_debug": {},
            "abort_generation": False,
        }


def test_json_entry_builds_fresh_isolated_runtime_factory_for_semantic_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StopAfterFactory(RuntimeError):
        pass

    source_db = Session()
    created_dbs: list[_Db] = []
    created_clients: list[_Client] = []

    def create_db_session() -> _Db:
        worker_db = _Db(len(created_dbs) + 1)
        created_dbs.append(worker_db)
        return worker_db

    def create_client(user_id: int, worker_db: _Db) -> _Client:
        worker_client = _Client(len(created_clients) + 1)
        created_clients.append(worker_client)
        return worker_client

    monkeypatch.setattr(json_impl, "create_db_session", create_db_session)
    monkeypatch.setattr(json_impl, "get_client_for_user", create_client)
    monkeypatch.setattr(
        json_impl,
        "resolve_json_generation_runtime",
        lambda **kwargs: JsonGenerationRuntimeState(
            client=_Client(0),
            request_id="json-runtime-factory",
            original_requirement="可验证业务证据",
            linked_final_case_signal={},
            memory_diag={},
            memory_fabric=None,
            memory_ctx=None,
        ),
    )
    monkeypatch.setattr(
        json_impl,
        "build_feedback_control_state",
        lambda **kwargs: FeedbackControlState.empty(),
    )
    monkeypatch.setattr(
        json_impl,
        "merge_generation_mode_control_state",
        lambda state, **kwargs: FeedbackControlState.from_any(state),
    )

    def capture_factory(state: Any, **kwargs: Any):
        runtime_factory = kwargs["isolated_ai_runtime_factory"]
        assert callable(runtime_factory)
        with runtime_factory() as (first_client, first_db):
            assert first_client is not kwargs["client"]
            assert first_db is not kwargs["db"]
        with runtime_factory() as (second_client, second_db):
            assert second_client is not first_client
            assert second_db is not first_db
        raise _StopAfterFactory

    monkeypatch.setattr(
        json_impl,
        "merge_current_requirement_blueprint_control_state",
        capture_factory,
    )

    try:
        with pytest.raises(_StopAfterFactory):
            _JsonHarness().generate_test_cases_json(
                requirement="可验证业务证据",
                project_id=7,
                db=source_db,
                user_id=9,
            )
    finally:
        source_db.close()

    assert len(created_dbs) == 2
    assert len({id(item) for item in created_dbs}) == 2
    assert len({id(item) for item in created_clients}) == 2
    assert all(item.closed for item in created_dbs)
