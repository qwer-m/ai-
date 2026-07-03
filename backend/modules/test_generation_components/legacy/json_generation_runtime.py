from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class JsonGenerationRuntimeState:
    client: Any
    request_id: str
    original_requirement: str
    linked_final_case_signal: dict[str, Any]
    memory_diag: dict[str, Any]
    memory_fabric: Any
    memory_ctx: Any


def resolve_json_generation_runtime(
    *,
    user_id: int | None,
    db: Any,
    project_id: int,
    requirement: str,
    get_client_for_user_fn: Callable[..., Any],
    resolve_linked_final_case_signal_fn: Callable[..., dict[str, Any]],
    init_memory_diag_fn: Callable[[], dict[str, Any]],
    get_memory_fabric_fn: Callable[[], Any],
    memory_context_cls: Any,
) -> JsonGenerationRuntimeState:
    client = get_client_for_user_fn(user_id, db)
    request_id = uuid.uuid4().hex
    original_requirement = requirement
    linked_final_case_signal = resolve_linked_final_case_signal_fn(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=original_requirement,
    )
    memory_diag = init_memory_diag_fn()
    memory_fabric = None
    try:
        memory_fabric = get_memory_fabric_fn()
    except Exception:
        memory_fabric = None
    memory_ctx = memory_context_cls.from_runtime(
        user_id=user_id,
        project_id=project_id,
        run_id=request_id,
        request_id=request_id,
    )
    return JsonGenerationRuntimeState(
        client=client,
        request_id=request_id,
        original_requirement=original_requirement,
        linked_final_case_signal=linked_final_case_signal,
        memory_diag=memory_diag,
        memory_fabric=memory_fabric,
        memory_ctx=memory_ctx,
    )
