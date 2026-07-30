from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable

from modules.orchestration.background_task_governance import (
    run_governed_threadpool_map,
)

from .ai_runtime_isolation import AIRuntimeIsolationGuard
from .model_envelope_call import (
    MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
    EnvelopeCallResult,
    classify_response_termination,
    invoke_model_envelope,
)
from .requirement_graph_partition_contract import (
    RequirementGraphPartitionContractError,
    build_mechanical_context_partition_result,
    build_mechanical_requirement_graph,
    build_requirement_graph_local_edge_prompt,
    build_requirement_graph_local_edge_user_input,
    build_requirement_graph_partition_prompt,
    build_requirement_graph_partition_user_input,
    build_requirement_graph_relation_prompt,
    build_requirement_graph_relation_user_input,
    build_requirement_graph_workflow_prompt,
    build_requirement_graph_workflow_user_input,
    partition_relation_fact_ids,
    partition_requirement_graph_facts,
    select_requirement_graph_relation_facts,
    validate_requirement_graph_partition_response,
    validate_requirement_graph_local_edge_response,
    validate_requirement_graph_relation_response,
    validate_requirement_graph_workflow_response,
)


MAX_GRAPH_PARTITION_PHASE_ATTEMPTS = 6
MAX_GRAPH_FACT_PARTITION_WORKERS = 2


@dataclass(frozen=True)
class RequirementGraphPartitionCompilationResult:
    response: dict[str, Any]
    envelopes: tuple[EnvelopeCallResult, ...]
    phase_attempts: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.diagnostics.get("partition_compile_success") is True

    @property
    def status(self) -> str:
        return str(self.diagnostics.get("partition_compile_status") or "contract_invalid")


@dataclass(frozen=True)
class _PhaseCallResult:
    value: dict[str, Any]
    status: str
    envelopes: tuple[EnvelopeCallResult, ...]
    attempts: tuple[dict[str, Any], ...]

    @property
    def success(self) -> bool:
        return self.status == "validated"


@dataclass(frozen=True)
class _LocalPartitionResult:
    shard_id: str
    value: dict[str, Any]
    status: str
    failed_phase: str
    envelopes: tuple[EnvelopeCallResult, ...]
    attempts: tuple[dict[str, Any], ...]

    @property
    def success(self) -> bool:
        return self.status == "validated"


def _call_json_phase(
    *,
    client: Any,
    phase: str,
    shard_id: str,
    user_input: str,
    system_prompt: str,
    validator: Callable[[Any], dict[str, Any]],
    db: Any,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
) -> _PhaseCallResult:
    """每个小分片执行有界 fresh attempt，且不把失败候选正文带入下一轮。"""

    envelopes: list[EnvelopeCallResult] = []
    attempts: list[dict[str, Any]] = []
    final_status = "contract_invalid"
    previous_error_code = ""
    previous_error_details: Any = None
    prior_feedback: list[dict[str, Any]] = []
    for attempt in range(1, MAX_GRAPH_PARTITION_PHASE_ATTEMPTS + 1):
        # fresh attempt 必须拥有不同的请求身份，否则响应缓存会原样返回同一
        # 份无效候选。只传稳定错误码，不携带旧候选正文。
        try:
            attempt_payload = json.loads(user_input)
        except (TypeError, ValueError, json.JSONDecodeError):
            attempt_payload = {"payload": user_input}
        if isinstance(attempt_payload, dict):
            attempt_payload["attempt"] = int(attempt)
            attempt_payload["recompile_reason_codes"] = (
                [previous_error_code] if previous_error_code else []
            )
            if previous_error_code:
                attempt_payload["recompile_feedback"] = {
                    "code": previous_error_code,
                    "details": copy.deepcopy(previous_error_details),
                }
                if attempt >= 3 and prior_feedback:
                    attempt_payload["recompile_feedback"]["prior_errors"] = (
                        copy.deepcopy(prior_feedback[:-1])
                    )
        call_user_input = json.dumps(
            attempt_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        envelope = invoke_model_envelope(
            client=client,
            envelope_id=f"requirement-graph-{phase}-{shard_id}-a{attempt}",
            user_input=call_user_input,
            system_prompt=system_prompt,
            db=db,
            max_tokens=int(max_tokens),
            task_type=str(task_type or "generation"),
            request_timeout_seconds=float(request_timeout_seconds),
            max_transport_replays=MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
        )
        envelopes.append(envelope)
        diagnostic: dict[str, Any] = {
            "phase": str(phase),
            "shard_id": str(shard_id),
            "attempt": int(attempt),
            "input_chars": len(call_user_input),
            "raw_chars": len(envelope.raw_text),
            "status": str(envelope.status),
            "model_envelope": envelope.to_diagnostic(),
        }
        if envelope.status != "response":
            attempts.append(diagnostic)
            return _PhaseCallResult(
                value={},
                status=str(envelope.status),
                envelopes=tuple(envelopes),
                attempts=tuple(attempts),
            )
        termination = classify_response_termination(envelope.response_metadata)
        if termination in {"truncated", "incomplete"}:
            final_status = (
                "output_truncated" if termination == "truncated" else "output_incomplete"
            )
            diagnostic["status"] = final_status
            diagnostic["response_termination"] = termination
            attempts.append(diagnostic)
            continue
        try:
            parsed = json.loads(envelope.raw_text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            final_status = "parse_failed"
            parse_details = (
                {
                    "message": str(exc.msg),
                    "line": int(exc.lineno),
                    "column": int(exc.colno),
                }
                if isinstance(exc, json.JSONDecodeError)
                else {"message": type(exc).__name__}
            )
            diagnostic.update(
                {
                    "status": final_status,
                    "error_code": "graph_partition_json_parse_failed",
                    "error_type": type(exc).__name__,
                    "error_details": copy.deepcopy(parse_details),
                }
            )
            previous_error_code = "graph_partition_json_parse_failed"
            previous_error_details = copy.deepcopy(parse_details)
            prior_feedback.append(
                {
                    "code": previous_error_code,
                    "details": copy.deepcopy(parse_details),
                }
            )
            attempts.append(diagnostic)
            continue
        try:
            validated = validator(parsed)
        except RequirementGraphPartitionContractError as exc:
            final_status = "contract_invalid"
            diagnostic.update(
                {
                    "status": final_status,
                    "error_code": exc.code,
                }
            )
            previous_error_code = exc.code
            previous_error_details = copy.deepcopy(exc.details)
            prior_feedback.append(
                {
                    "code": exc.code,
                    "details": copy.deepcopy(exc.details),
                }
            )
            attempts.append(diagnostic)
            continue
        diagnostic["status"] = "validated"
        attempts.append(diagnostic)
        return _PhaseCallResult(
            value=validated,
            status="validated",
            envelopes=tuple(envelopes),
            attempts=tuple(attempts),
        )
    return _PhaseCallResult(
        value={},
        status=final_status,
        envelopes=tuple(envelopes),
        attempts=tuple(attempts),
    )


def _failed_result(
    *,
    status: str,
    failed_phase: str,
    failed_shard_id: str,
    envelopes: list[EnvelopeCallResult],
    attempts: list[dict[str, Any]],
    fact_partition_count: int,
    completed_fact_partitions: int,
    relation_partition_count: int,
    completed_relation_partitions: int,
) -> RequirementGraphPartitionCompilationResult:
    return RequirementGraphPartitionCompilationResult(
        response={},
        envelopes=tuple(envelopes),
        phase_attempts=tuple(copy.deepcopy(attempts)),
        diagnostics={
            "partition_compile_status": str(status),
            "partition_compile_success": False,
            "partition_compile_failed_phase": str(failed_phase),
            "partition_compile_failed_shard_id": str(failed_shard_id),
            "partition_compile_fact_shard_count": int(fact_partition_count),
            "partition_compile_completed_fact_shard_count": int(completed_fact_partitions),
            "partition_compile_relation_shard_count": int(relation_partition_count),
            "partition_compile_completed_relation_shard_count": int(
                completed_relation_partitions
            ),
            "partition_compile_provider_call_count": sum(
                item.provider_call_count for item in envelopes
            ),
            "partition_compile_cache_hit_count": sum(
                item.cache_hit_count for item in envelopes
            ),
            "partition_compile_cache_miss_count": sum(
                item.cache_miss_count for item in envelopes
            ),
        },
    )


def _compile_model_fact_partition(
    *,
    partition: Any,
    client: Any,
    normalized_scope_ledger: dict[str, Any],
    db: Any,
    local_prompt: str,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
) -> _LocalPartitionResult:
    """单个事实分片内部严格按 local_node → local_edge 顺序执行。"""

    envelopes: list[EnvelopeCallResult] = []
    attempts: list[dict[str, Any]] = []
    user_input = build_requirement_graph_partition_user_input(
        normalized_scope_ledger,
        partition,
    )
    phase_result = _call_json_phase(
        client=client,
        phase="local_node",
        shard_id=partition.shard_id,
        user_input=user_input,
        system_prompt=local_prompt,
        validator=lambda response: validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=normalized_scope_ledger,
            partition=partition,
            require_local_closure=False,
        ),
        db=db,
        max_tokens=max_tokens,
        task_type=task_type,
        request_timeout_seconds=request_timeout_seconds,
    )
    envelopes.extend(phase_result.envelopes)
    attempts.extend(phase_result.attempts)
    if not phase_result.success:
        return _LocalPartitionResult(
            shard_id=str(partition.shard_id),
            value={},
            status=phase_result.status,
            failed_phase="local",
            envelopes=tuple(envelopes),
            attempts=tuple(attempts),
        )

    local_result = copy.deepcopy(phase_result.value)
    if any(
        isinstance(node, dict) and node.get("kind") != "capability"
        for node in local_result.get("nodes") or []
    ):
        edge_input = build_requirement_graph_local_edge_user_input(
            normalized_scope_ledger,
            partition,
            local_result,
        )
        edge_result = _call_json_phase(
            client=client,
            phase="local_edge",
            shard_id=partition.shard_id,
            user_input=edge_input,
            system_prompt=build_requirement_graph_local_edge_prompt(),
            validator=lambda response: validate_requirement_graph_local_edge_response(
                response,
                normalized_scope_ledger=normalized_scope_ledger,
                partition=partition,
                local_result=local_result,
            ),
            db=db,
            max_tokens=max_tokens,
            task_type=task_type,
            request_timeout_seconds=request_timeout_seconds,
        )
        envelopes.extend(edge_result.envelopes)
        attempts.extend(edge_result.attempts)
        if not edge_result.success:
            return _LocalPartitionResult(
                shard_id=str(partition.shard_id),
                value={},
                status=edge_result.status,
                failed_phase="local_edge",
                envelopes=tuple(envelopes),
                attempts=tuple(attempts),
            )
        local_result["confidence"] = min(
            float(local_result.get("confidence") or 1.0),
            float(edge_result.value.get("confidence") or 1.0),
        )
        local_result["edges"] = copy.deepcopy(
            edge_result.value.get("edges") or []
        )

    return _LocalPartitionResult(
        shard_id=str(partition.shard_id),
        value=local_result,
        status="validated",
        failed_phase="",
        envelopes=tuple(envelopes),
        attempts=tuple(attempts),
    )


def compile_partitioned_requirement_graph_response(
    *,
    client: Any,
    normalized_scope_ledger: dict[str, Any],
    db: Any = None,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
    worker_runtime_factory: Callable[[], Any] | None = None,
) -> RequirementGraphPartitionCompilationResult:
    """分阶段生成完整旧 Graph 响应；只有全部阶段成功才返回候选。"""

    fact_partitions = partition_requirement_graph_facts(normalized_scope_ledger)
    envelopes: list[EnvelopeCallResult] = []
    attempts: list[dict[str, Any]] = []
    local_results: list[dict[str, Any]] = []
    mechanical_context_partition_count = 0
    local_prompt = build_requirement_graph_partition_prompt()
    partition_results: dict[int, _LocalPartitionResult] = {}
    model_partition_items: list[tuple[int, Any]] = []
    for partition_index, partition in enumerate(fact_partitions):
        mechanical_context_result = build_mechanical_context_partition_result(
            normalized_scope_ledger,
            partition,
        )
        if mechanical_context_result is not None:
            mechanical_context_partition_count += 1
            partition_results[partition_index] = _LocalPartitionResult(
                shard_id=str(partition.shard_id),
                value=copy.deepcopy(mechanical_context_result),
                status="validated",
                failed_phase="",
                envelopes=(),
                attempts=(
                    {
                        "phase": "local",
                        "shard_id": partition.shard_id,
                        "attempt": 0,
                        "input_chars": 0,
                        "raw_chars": 0,
                        "status": "validated",
                        "source": "mechanical_context_disposition",
                    },
                ),
            )
            continue
        model_partition_items.append((partition_index, partition))

    def _compile_with_parent_runtime(
        item: tuple[int, Any],
    ) -> tuple[int, _LocalPartitionResult]:
        partition_index, partition = item
        return partition_index, _compile_model_fact_partition(
            partition=partition,
            client=client,
            normalized_scope_ledger=normalized_scope_ledger,
            db=db,
            local_prompt=local_prompt,
            max_tokens=max_tokens,
            task_type=task_type,
            request_timeout_seconds=request_timeout_seconds,
        )

    if worker_runtime_factory is not None and len(model_partition_items) > 1:
        runtime_isolation = AIRuntimeIsolationGuard(
            parent_client=client,
            parent_db=db,
            error_message=(
                "Graph 分片 worker 禁止共享 provider、AIClient 或 DB Session"
            ),
        )

        def _compile_with_isolated_runtime(
            item: tuple[int, Any],
        ) -> tuple[int, _LocalPartitionResult]:
            with worker_runtime_factory() as (worker_client, worker_db):
                runtime_isolation.claim(
                    client=worker_client,
                    db=worker_db,
                )
                partition_index, partition = item
                return partition_index, _compile_model_fact_partition(
                    partition=partition,
                    client=worker_client,
                    normalized_scope_ledger=normalized_scope_ledger,
                    db=worker_db,
                    local_prompt=local_prompt,
                    max_tokens=max_tokens,
                    task_type=task_type,
                    request_timeout_seconds=request_timeout_seconds,
                )

        governed_results = run_governed_threadpool_map(
            profile_key="test_generation_graph_fact_shard_threadpool",
            items=model_partition_items,
            worker=_compile_with_isolated_runtime,
            max_workers=MAX_GRAPH_FACT_PARTITION_WORKERS,
            thread_name_prefix="graph-fact-shard",
        )
        for governed_result in governed_results:
            if governed_result.exception is not None:
                raise governed_result.exception
            partition_index, partition_result = governed_result.result
            partition_results[int(partition_index)] = partition_result
    else:
        for item in model_partition_items:
            partition_index, partition_result = _compile_with_parent_runtime(item)
            partition_results[int(partition_index)] = partition_result

    # 所有并发结果先按原分片索引排序，再确定性归并和执行 fail-closed。
    first_failure: _LocalPartitionResult | None = None
    for partition_index in range(len(fact_partitions)):
        partition_result = partition_results[partition_index]
        envelopes.extend(partition_result.envelopes)
        attempts.extend(copy.deepcopy(partition_result.attempts))
        if partition_result.success:
            local_results.append(copy.deepcopy(partition_result.value))
        elif first_failure is None:
            first_failure = partition_result

    if first_failure is not None:
        return _failed_result(
            status=first_failure.status,
            failed_phase=first_failure.failed_phase,
            failed_shard_id=first_failure.shard_id,
            envelopes=envelopes,
            attempts=attempts,
            fact_partition_count=len(fact_partitions),
            completed_fact_partitions=len(local_results),
            relation_partition_count=0,
            completed_relation_partitions=0,
        )

    try:
        graph = build_mechanical_requirement_graph(
            normalized_scope_ledger,
            local_results,
        )
    except RequirementGraphPartitionContractError as exc:
        attempts.append(
            {
                "phase": "mechanical_merge",
                "shard_id": "M001",
                "attempt": 1,
                "input_chars": 0,
                "raw_chars": 0,
                "status": "contract_invalid",
                "error_code": exc.code,
                "error_path": exc.path,
                "error_details": copy.deepcopy(exc.details),
            }
        )
        return _failed_result(
            status="contract_invalid",
            failed_phase="mechanical_merge",
            failed_shard_id="M001",
            envelopes=envelopes,
            attempts=attempts,
            fact_partition_count=len(fact_partitions),
            completed_fact_partitions=len(local_results),
            relation_partition_count=0,
            completed_relation_partitions=0,
        )

    relation_fact_ids = select_requirement_graph_relation_facts(
        normalized_scope_ledger
    )
    relation_partitions = partition_relation_fact_ids(relation_fact_ids)
    completed_relations = 0
    relation_prompt = build_requirement_graph_relation_prompt()
    relation_confidences: list[float] = []
    for relation_shard_id, fact_ids in relation_partitions:
        user_input = build_requirement_graph_relation_user_input(
            normalized_scope_ledger,
            graph,
            relation_shard_id=relation_shard_id,
            fact_ids=fact_ids,
        )
        phase_result = _call_json_phase(
            client=client,
            phase="relation",
            shard_id=relation_shard_id,
            user_input=user_input,
            system_prompt=relation_prompt,
            validator=lambda response, current_id=relation_shard_id, current_facts=fact_ids: (
                validate_requirement_graph_relation_response(
                    response,
                    graph=graph,
                    relation_shard_id=current_id,
                    fact_ids=current_facts,
                )
            ),
            db=db,
            max_tokens=max_tokens,
            task_type=task_type,
            request_timeout_seconds=request_timeout_seconds,
        )
        envelopes.extend(phase_result.envelopes)
        attempts.extend(phase_result.attempts)
        if not phase_result.success:
            return _failed_result(
                status=phase_result.status,
                failed_phase="relation",
                failed_shard_id=relation_shard_id,
                envelopes=envelopes,
                attempts=attempts,
                fact_partition_count=len(fact_partitions),
                completed_fact_partitions=len(local_results),
                relation_partition_count=len(relation_partitions),
                completed_relation_partitions=completed_relations,
            )
        graph["edges"].extend(copy.deepcopy(phase_result.value.get("edges") or []))
        relation_confidences.append(float(phase_result.value.get("confidence") or 0.0))
        completed_relations += 1

    control_edge_count = sum(
        1
        for edge in graph.get("edges") or []
        if isinstance(edge, dict) and edge.get("type") in {"triggers", "transitions"}
    )
    workflow_confidence = 1.0
    workflows: list[dict[str, Any]] = []
    primary_flow = {"node_ids": [], "edge_ids": []}
    if control_edge_count:
        workflow_input = build_requirement_graph_workflow_user_input(
            normalized_scope_ledger,
            graph,
        )
        workflow_result = _call_json_phase(
            client=client,
            phase="workflow",
            shard_id="W001",
            user_input=workflow_input,
            system_prompt=build_requirement_graph_workflow_prompt(),
            validator=lambda response: validate_requirement_graph_workflow_response(
                response,
                graph=graph,
            ),
            db=db,
            max_tokens=max_tokens,
            task_type=task_type,
            request_timeout_seconds=request_timeout_seconds,
        )
        envelopes.extend(workflow_result.envelopes)
        attempts.extend(workflow_result.attempts)
        if not workflow_result.success:
            return _failed_result(
                status=workflow_result.status,
                failed_phase="workflow",
                failed_shard_id="W001",
                envelopes=envelopes,
                attempts=attempts,
                fact_partition_count=len(fact_partitions),
                completed_fact_partitions=len(local_results),
                relation_partition_count=len(relation_partitions),
                completed_relation_partitions=completed_relations,
            )
        workflow_confidence = float(workflow_result.value.get("confidence") or 0.0)
        primary_flow = copy.deepcopy(workflow_result.value["primary_flow"])
        workflows = copy.deepcopy(workflow_result.value["workflow_blueprints"])

    graph["primary_flow"] = primary_flow
    confidences = [
        float(item.get("confidence") or 0.0) for item in local_results
    ] + relation_confidences + [workflow_confidence]
    response = {
        "confidence": min(confidences) if confidences else 1.0,
        "semantic_graph": graph,
        "workflow_blueprints": workflows,
    }
    diagnostics = {
        "partition_compile_status": "validated",
        "partition_compile_success": True,
        "partition_compile_failed_phase": "",
        "partition_compile_failed_shard_id": "",
        "partition_compile_fact_shard_count": len(fact_partitions),
        "partition_compile_completed_fact_shard_count": len(local_results),
        "partition_compile_mechanical_context_shard_count": (
            mechanical_context_partition_count
        ),
        "partition_compile_relation_fact_count": len(relation_fact_ids),
        "partition_compile_relation_shard_count": len(relation_partitions),
        "partition_compile_completed_relation_shard_count": completed_relations,
        "partition_compile_workflow_called": bool(control_edge_count),
        "partition_compile_node_count": len(graph.get("nodes") or []),
        "partition_compile_edge_count": len(graph.get("edges") or []),
        "partition_compile_control_edge_count": int(control_edge_count),
        "partition_compile_provider_call_count": sum(
            item.provider_call_count for item in envelopes
        ),
        "partition_compile_cache_hit_count": sum(
            item.cache_hit_count for item in envelopes
        ),
        "partition_compile_cache_miss_count": sum(
            item.cache_miss_count for item in envelopes
        ),
    }
    return RequirementGraphPartitionCompilationResult(
        response=response,
        envelopes=tuple(envelopes),
        phase_attempts=tuple(copy.deepcopy(attempts)),
        diagnostics=diagnostics,
    )


__all__ = [
    "MAX_GRAPH_FACT_PARTITION_WORKERS",
    "MAX_GRAPH_PARTITION_PHASE_ATTEMPTS",
    "RequirementGraphPartitionCompilationResult",
    "compile_partitioned_requirement_graph_response",
]
