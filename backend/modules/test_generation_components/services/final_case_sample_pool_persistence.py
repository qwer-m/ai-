from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PersistedSamplePool:
    doc: Any
    payload: dict[str, Any]
    samples: list[dict[str, Any]]
    sample_count: int
    updated_at: Any


def merge_sample_pool_samples(
    existing_payload: dict[str, Any] | None,
    new_samples: list[dict[str, Any]],
    *,
    max_pool_samples: int,
) -> list[dict[str, Any]]:
    payload = existing_payload if isinstance(existing_payload, dict) else {}
    existing_samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    merged_samples = (existing_samples or []) + (new_samples if isinstance(new_samples, list) else [])
    if len(merged_samples) > max_pool_samples:
        merged_samples = merged_samples[-max_pool_samples:]
    return merged_samples


def persist_sample_pool_samples(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    samples: list[dict[str, Any]],
    max_pool_samples: int,
    load_priority_sample_pool_fn: Callable[..., dict[str, Any] | None],
    upsert_priority_sample_pool_fn: Callable[..., Any],
    after_upsert_fn: Callable[[], None] | None = None,
) -> PersistedSamplePool:
    existing_payload = (
        load_priority_sample_pool_fn(
            db=db,
            project_id=project_id,
            user_id=user_id,
        )
        or {}
    )
    merged_samples = merge_sample_pool_samples(
        existing_payload,
        samples,
        max_pool_samples=max_pool_samples,
    )
    doc = upsert_priority_sample_pool_fn(
        db=db,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id,
        samples=merged_samples,
    )
    if after_upsert_fn is not None:
        after_upsert_fn()
    payload = (
        load_priority_sample_pool_fn(
            db=db,
            project_id=project_id,
            user_id=user_id,
        )
        or {}
    )
    normalized_samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    return PersistedSamplePool(
        doc=doc,
        payload=payload if isinstance(payload, dict) else {},
        samples=normalized_samples,
        sample_count=len(normalized_samples),
        updated_at=payload.get("updated_at") if isinstance(payload, dict) else None,
    )
