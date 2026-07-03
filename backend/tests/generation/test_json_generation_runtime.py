from __future__ import annotations

from modules.test_generation_components.legacy.json_generation_runtime import (
    resolve_json_generation_runtime,
)


class _MemoryContext:
    @classmethod
    def from_runtime(cls, **kwargs):
        return {"ctx": dict(kwargs)}


def test_resolve_json_generation_runtime_wires_core_state() -> None:
    calls: list[tuple[str, object]] = []
    client = object()
    db = object()

    state = resolve_json_generation_runtime(
        user_id=7,
        db=db,
        project_id=11,
        requirement="course schedule requirement",
        get_client_for_user_fn=lambda user_id, db_arg: calls.append(
            ("client", (user_id, db_arg))
        )
        or client,
        resolve_linked_final_case_signal_fn=lambda **kwargs: calls.append(
            ("linked", dict(kwargs))
        )
        or {"linked_final_case_count": 2, "source_doc_ids": [101, 102]},
        init_memory_diag_fn=lambda: calls.append(("memory_diag", None))
        or {"memory_reads": {}},
        get_memory_fabric_fn=lambda: calls.append(("memory_fabric", None))
        or "memory-fabric",
        memory_context_cls=_MemoryContext,
    )

    assert state.client is client
    assert len(state.request_id) == 32
    int(state.request_id, 16)
    assert state.original_requirement == "course schedule requirement"
    assert state.linked_final_case_signal["linked_final_case_count"] == 2
    assert state.memory_diag == {"memory_reads": {}}
    assert state.memory_fabric == "memory-fabric"
    assert state.memory_ctx["ctx"] == {
        "user_id": 7,
        "project_id": 11,
        "run_id": state.request_id,
        "request_id": state.request_id,
    }
    assert calls[0] == ("client", (7, db))
    assert calls[1][0] == "linked"
    assert calls[1][1]["requirement_text"] == "course schedule requirement"


def test_resolve_json_generation_runtime_swallows_memory_fabric_error() -> None:
    def failing_memory_fabric():
        raise RuntimeError("fabric unavailable")

    state = resolve_json_generation_runtime(
        user_id=None,
        db=None,
        project_id=5,
        requirement="refund requirement",
        get_client_for_user_fn=lambda user_id, db: "client",
        resolve_linked_final_case_signal_fn=lambda **kwargs: {
            "linked_final_case_count": 0,
            "source_doc_ids": [],
        },
        init_memory_diag_fn=lambda: {"memory_reads": {}},
        get_memory_fabric_fn=failing_memory_fabric,
        memory_context_cls=_MemoryContext,
    )

    assert state.memory_fabric is None
    assert state.memory_ctx["ctx"]["run_id"] == state.request_id
