from __future__ import annotations

import copy
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import modules.test_generation_components.control.requirement_graph_partition_compiler as compiler
from modules.test_generation_components.control.model_envelope_call import (
    EnvelopeCallResult,
)


class _WorkerClient:
    def __init__(self, runtime_id: int) -> None:
        self.runtime_id = runtime_id
        self.calls: list[tuple[str, str]] = []


class _WorkerDb:
    def __init__(self, runtime_id: int) -> None:
        self.runtime_id = runtime_id
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _envelope(phase: str, shard_id: str) -> EnvelopeCallResult:
    return EnvelopeCallResult(
        envelope_id=f"{phase}-{shard_id}",
        status="response",
        raw_text="{}",
        response_metadata={},
        attempts=(),
        cache_lookup_enabled=False,
        physical_call_count=1,
        transport_failure_count=0,
        transport_retry_count=0,
    )


def _phase_result(
    *,
    phase: str,
    shard_id: str,
    status: str = "validated",
) -> compiler._PhaseCallResult:
    if phase == "local_node":
        value = {
            "confidence": 0.95,
            "nodes": [
                {
                    "node_id": f"{shard_id}_N01",
                    "kind": "trigger",
                }
            ],
            "edges": [],
            "fact_dispositions": [],
        }
    elif phase == "local_edge":
        value = {
            "confidence": 0.94,
            "edges": [
                {
                    "edge_id": f"{shard_id}_E01",
                    "type": "triggers",
                }
            ],
        }
    else:
        value = {
            "confidence": 0.93,
            "primary_flow": {"node_ids": [], "edge_ids": []},
            "workflow_blueprints": [],
        }
    return compiler._PhaseCallResult(
        value=value if status == "validated" else {},
        status=status,
        envelopes=(_envelope(phase, shard_id),),
        attempts=(
            {
                "phase": phase,
                "shard_id": shard_id,
                "attempt": 1,
                "status": status,
            },
        ),
    )


def _install_parallel_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_edge_shard: str = "",
    raised_node_shard: str = "",
) -> dict[str, Any]:
    partitions = [
        SimpleNamespace(shard_id=shard_id, fact_ids=(f"F_{shard_id}",))
        for shard_id in ("P001", "P002", "P003")
    ]
    parent_client = object()
    parent_db = object()
    runtimes: list[tuple[_WorkerClient, _WorkerDb]] = []
    active_local_nodes = 0
    max_active_local_nodes = 0
    active_lock = threading.Lock()
    first_pair_started = threading.Event()
    workflow_calls: list[tuple[Any, Any]] = []

    monkeypatch.setattr(
        compiler,
        "partition_requirement_graph_facts",
        lambda ledger: partitions,
    )
    monkeypatch.setattr(
        compiler,
        "build_mechanical_context_partition_result",
        lambda ledger, partition: None,
    )
    monkeypatch.setattr(
        compiler,
        "build_requirement_graph_partition_user_input",
        lambda ledger, partition: "{}",
    )
    monkeypatch.setattr(
        compiler,
        "build_requirement_graph_local_edge_user_input",
        lambda ledger, partition, local_result: "{}",
    )
    monkeypatch.setattr(
        compiler,
        "build_mechanical_requirement_graph",
        lambda ledger, local_results: {
            "nodes": [
                copy.deepcopy(node)
                for local_result in local_results
                for node in local_result["nodes"]
            ],
            "edges": [
                {
                    "edge_id": "E_WORKFLOW",
                    "type": "triggers",
                }
            ],
        },
    )
    monkeypatch.setattr(
        compiler,
        "select_requirement_graph_relation_facts",
        lambda ledger: [],
    )
    monkeypatch.setattr(
        compiler,
        "partition_relation_fact_ids",
        lambda fact_ids: [],
    )
    monkeypatch.setattr(
        compiler,
        "build_requirement_graph_workflow_user_input",
        lambda ledger, graph: "{}",
    )

    def fake_call_json_phase(
        *,
        client: Any,
        phase: str,
        shard_id: str,
        db: Any,
        **kwargs: Any,
    ) -> compiler._PhaseCallResult:
        nonlocal active_local_nodes, max_active_local_nodes
        if phase == "workflow":
            workflow_calls.append((client, db))
            return _phase_result(phase=phase, shard_id=shard_id)
        assert isinstance(client, _WorkerClient)
        assert isinstance(db, _WorkerDb)
        client.calls.append((shard_id, phase))
        if phase == "local_node":
            if shard_id == raised_node_shard:
                raise RuntimeError(f"node failed: {shard_id}")
            with active_lock:
                active_local_nodes += 1
                max_active_local_nodes = max(
                    max_active_local_nodes,
                    active_local_nodes,
                )
                if active_local_nodes == 2:
                    first_pair_started.set()
            first_pair_started.wait(1)
            time.sleep({"P001": 0.04, "P002": 0.005, "P003": 0.01}[shard_id])
            with active_lock:
                active_local_nodes -= 1
        status = (
            "contract_invalid"
            if phase == "local_edge" and shard_id == failed_edge_shard
            else "validated"
        )
        return _phase_result(
            phase=phase,
            shard_id=shard_id,
            status=status,
        )

    monkeypatch.setattr(compiler, "_call_json_phase", fake_call_json_phase)

    @contextmanager
    def worker_runtime_factory():
        runtime_id = len(runtimes) + 1
        worker_client = _WorkerClient(runtime_id)
        worker_db = _WorkerDb(runtime_id)
        runtimes.append((worker_client, worker_db))
        try:
            yield worker_client, worker_db
        finally:
            worker_db.close()

    return {
        "parent_client": parent_client,
        "parent_db": parent_db,
        "runtimes": runtimes,
        "workflow_calls": workflow_calls,
        "worker_runtime_factory": worker_runtime_factory,
        "max_active": lambda: max_active_local_nodes,
    }


def _compile(harness: dict[str, Any]) -> compiler.RequirementGraphPartitionCompilationResult:
    return compiler.compile_partitioned_requirement_graph_response(
        client=harness["parent_client"],
        normalized_scope_ledger={},
        db=harness["parent_db"],
        max_tokens=4096,
        task_type="generation",
        request_timeout_seconds=120,
        worker_runtime_factory=harness["worker_runtime_factory"],
    )


def test_graph_fact_shards_are_bounded_ordered_and_resource_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_parallel_harness(monkeypatch)

    result = _compile(harness)

    assert result.success is True
    assert harness["max_active"]() == compiler.MAX_GRAPH_FACT_PARTITION_WORKERS == 2
    assert len(harness["runtimes"]) == 3
    assert all(worker_db.closed for _, worker_db in harness["runtimes"])
    assert {
        tuple(worker_client.calls)
        for worker_client, _ in harness["runtimes"]
    } == {
        (("P001", "local_node"), ("P001", "local_edge")),
        (("P002", "local_node"), ("P002", "local_edge")),
        (("P003", "local_node"), ("P003", "local_edge")),
    }
    assert [item["node_id"] for item in result.response["semantic_graph"]["nodes"]] == [
        "P001_N01",
        "P002_N01",
        "P003_N01",
    ]
    assert [item["shard_id"] for item in result.phase_attempts] == [
        "P001",
        "P001",
        "P002",
        "P002",
        "P003",
        "P003",
        "W001",
    ]
    assert [item.envelope_id for item in result.envelopes] == [
        "local_node-P001",
        "local_edge-P001",
        "local_node-P002",
        "local_edge-P002",
        "local_node-P003",
        "local_edge-P003",
        "workflow-W001",
    ]
    assert harness["workflow_calls"] == [
        (harness["parent_client"], harness["parent_db"])
    ]


def test_graph_fact_shard_failure_is_fail_closed_after_ordered_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_parallel_harness(
        monkeypatch,
        failed_edge_shard="P002",
    )

    result = _compile(harness)

    assert result.success is False
    assert result.response == {}
    assert result.diagnostics["partition_compile_failed_phase"] == "local_edge"
    assert result.diagnostics["partition_compile_failed_shard_id"] == "P002"
    assert [item["shard_id"] for item in result.phase_attempts] == [
        "P001",
        "P001",
        "P002",
        "P002",
        "P003",
        "P003",
    ]
    assert all(worker_db.closed for _, worker_db in harness["runtimes"])


def test_graph_fact_shard_worker_exception_propagates_after_resource_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_parallel_harness(
        monkeypatch,
        raised_node_shard="P002",
    )

    with pytest.raises(RuntimeError, match="node failed: P002"):
        _compile(harness)

    assert len(harness["runtimes"]) == 3
    assert all(worker_db.closed for _, worker_db in harness["runtimes"])


def test_graph_fact_shards_reject_shared_worker_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install_parallel_harness(monkeypatch)
    shared_client = _WorkerClient(1)
    shared_db = _WorkerDb(1)

    @contextmanager
    def shared_runtime_factory():
        try:
            yield shared_client, shared_db
        finally:
            shared_db.close()

    harness["worker_runtime_factory"] = shared_runtime_factory

    with pytest.raises(
        RuntimeError,
        match="禁止共享 provider、AIClient 或 DB Session",
    ):
        _compile(harness)

    assert shared_db.closed is True


def test_graph_partition_retry_ceiling_is_not_reduced() -> None:
    assert compiler.MAX_GRAPH_PARTITION_PHASE_ATTEMPTS == 6
