from __future__ import annotations

from typing import Any, Iterable

from .streaming_case_keys import case_signature

SOURCE_METADATA_FIELDS = (
    "candidate_index",
    "origin_candidate_index",
    "origin_case_id",
    "origin_batch_index",
    "origin_batch_case_index",
    "origin_source_stage",
)


def _dict_cases(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in (items or []) if isinstance(item, dict)]


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _case_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("case_id") or item.get("caseId") or "").strip()


def _source_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in SOURCE_METADATA_FIELDS if _has_value(item.get(field))}


def _set_value(target: dict[str, Any], field: str, value: Any, *, overwrite: bool) -> None:
    if not _has_value(value):
        return
    if overwrite or not _has_value(target.get(field)):
        target[field] = value


def annotate_case_source_metadata(
    cases: Iterable[Any] | None,
    *,
    source_stage: str,
    start_index: int = 1,
    batch_index: int | None = None,
    set_candidate_index: bool = True,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    safe_start = max(1, int(start_index or 1))
    stage = str(source_stage or "").strip()
    for offset, item in enumerate(_dict_cases(cases), start=0):
        source_index = safe_start + offset
        updated = dict(item)
        existing_candidate_index = _positive_int(updated.get("candidate_index"))
        source_candidate_index = existing_candidate_index or source_index
        if set_candidate_index:
            _set_value(updated, "candidate_index", source_candidate_index, overwrite=overwrite)
        _set_value(
            updated,
            "origin_candidate_index",
            _positive_int(updated.get("origin_candidate_index")) or source_candidate_index,
            overwrite=overwrite,
        )
        _set_value(updated, "origin_case_id", updated.get("origin_case_id") or _case_id(updated), overwrite=overwrite)
        _set_value(updated, "origin_source_stage", updated.get("origin_source_stage") or stage, overwrite=overwrite)

        resolved_batch_index = (
            _positive_int(updated.get("origin_batch_index"))
            or _positive_int(batch_index)
            or _positive_int(updated.get("batch_index"))
            or _positive_int(updated.get("prompt_batch_index"))
        )
        _set_value(updated, "origin_batch_index", resolved_batch_index, overwrite=overwrite)
        resolved_batch_case_index = _positive_int(updated.get("origin_batch_case_index"))
        if resolved_batch_case_index or resolved_batch_index:
            _set_value(
                updated,
                "origin_batch_case_index",
                resolved_batch_case_index or offset + 1,
                overwrite=overwrite,
            )
        output.append(updated)
    return output


def apply_case_source_metadata(
    cases: Iterable[Any] | None,
    *,
    source_cases: Iterable[Any] | None,
) -> list[dict[str, Any]]:
    source_by_id: dict[str, dict[str, Any]] = {}
    source_by_signature: dict[str, dict[str, Any]] = {}
    for item in _dict_cases(source_cases):
        metadata = _source_metadata(item)
        if not metadata:
            continue
        case_id = _case_id(item)
        if case_id and case_id not in source_by_id:
            source_by_id[case_id] = metadata
        signature = case_signature(item)
        if signature and signature not in source_by_signature:
            source_by_signature[signature] = metadata

    output: list[dict[str, Any]] = []
    for item in _dict_cases(cases):
        updated = dict(item)
        metadata = source_by_id.get(_case_id(updated))
        if metadata is None:
            signature = case_signature(updated)
            metadata = source_by_signature.get(signature) if signature else None
        for field, value in dict(metadata or {}).items():
            _set_value(updated, field, value, overwrite=False)
        output.append(updated)
    return output


__all__ = [
    "SOURCE_METADATA_FIELDS",
    "annotate_case_source_metadata",
    "apply_case_source_metadata",
]
