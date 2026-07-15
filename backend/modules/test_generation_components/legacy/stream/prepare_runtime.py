from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

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
    lookup_source: str = ""


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
    hydrate_append_existing_cases_fn: Callable[..., list[dict[str, Any]]] | None = None,
    previous_generation_id: int | None = None,
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
            lookup_source="not_append",
        )

    append_lookup_started = time.perf_counter()
    from sqlalchemy import desc

    resolved_model = resolve_lazy_attr(test_generation_model)
    lookup_source = "requirement_text"
    previous_id = int(previous_generation_id or 0)
    if previous_id > 0:
        query = db.query(resolved_model).filter(
            resolved_model.id == previous_id,
            resolved_model.project_id == project_id,
        )
        if user_id:
            query = query.filter(resolved_model.user_id == user_id)
        existing_entry = query.first()
        lookup_source = "previous_generation_id"

    if not existing_entry and previous_id <= 0:
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
        if hydrate_append_existing_cases_fn:
            existing_cases = hydrate_append_existing_cases_fn(
                existing_cases,
                db=db,
                entry=existing_entry,
            )
    record_timing_event_fn(
        "append_existing_lookup",
        append_lookup_started,
        found=bool(existing_entry),
        lookup_source=lookup_source,
        previous_generation_id=previous_id or None,
        existing_unique_count=int(existing_unique_count or 0),
        start_id=int(start_id or 1),
    )
    return AppendExistingState(
        start_id=start_id,
        existing_cases=existing_cases,
        existing_entry=existing_entry,
        existing_unique_count=int(existing_unique_count or 0),
        lookup_source=lookup_source,
    )
