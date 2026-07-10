from __future__ import annotations

from modules.test_generation_components.legacy.stream.prepare_runtime import (
    record_prepare_timing_event,
    resolve_append_existing_state,
    resolve_stream_prepare_runtime,
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
