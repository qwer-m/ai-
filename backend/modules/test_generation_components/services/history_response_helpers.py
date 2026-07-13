"""Response assembly helpers for test generation history service."""

from __future__ import annotations

from typing import Any, Callable

from routers.automation.test_generation_shared import (
    build_history_key,
    extract_history_title,
    infer_compare_filename,
    normalize_case_text,
)

MAX_PRIORITY_SAMPLE_POOL_SAMPLES = 5000


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def cap_priority_sample_pool_samples(
    samples: Any,
    *,
    max_items: int = MAX_PRIORITY_SAMPLE_POOL_SAMPLES,
) -> list[Any]:
    safe_samples = samples if isinstance(samples, list) else []
    if len(safe_samples) > max_items:
        return safe_samples[:max_items]
    return safe_samples


def load_priority_sample_pool_payload(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    loader: Callable[..., dict[str, Any] | None],
) -> dict[str, Any]:
    return loader(db=db, project_id=project_id, user_id=user_id) or {}


def is_reliable_matched_comparison(generated_result: str, matched_generated_result: str) -> bool:
    left = normalize_case_text(generated_result or "")
    right = normalize_case_text(matched_generated_result or "")
    if not left or not right:
        return False
    if left == right:
        return True

    left_compact = "".join((left or "").split())
    right_compact = "".join((right or "").split())
    if not left_compact or not right_compact:
        return False

    shorter = left_compact if len(left_compact) <= len(right_compact) else right_compact
    longer = right_compact if len(left_compact) <= len(right_compact) else left_compact
    return len(shorter) >= 1000 and shorter in longer


def build_history_comparison(
    *,
    generated_result: str,
    matched: Any,
    artifact: dict[str, Any] | None,
) -> dict[str, Any] | None:
    artifact_payload = artifact or {}
    artifact_modified = artifact_payload.get("modified_test_case") or ""
    artifact_result = artifact_payload.get("comparison_result") or ""
    if artifact_modified or artifact_result:
        return {
            "id": None,
            "modified_test_case": artifact_modified,
            "comparison_result": artifact_result,
            "source_filename": artifact_payload.get("source_filename") or infer_compare_filename(artifact_modified),
            "created_at": artifact_payload.get("updated_at"),
            "artifact_doc_id": artifact_payload.get("artifact_doc_id"),
            "source_file_content_type": artifact_payload.get("source_file_content_type"),
            "source_file_size": artifact_payload.get("source_file_size"),
            "ocr": artifact_payload.get("ocr"),
        }

    if not matched:
        return None
    if not is_reliable_matched_comparison(
        generated_result,
        getattr(matched, "generated_test_case", "") or "",
    ):
        return None

    merged_modified = matched.modified_test_case or ""
    return {
        "id": matched.id,
        "modified_test_case": merged_modified,
        "comparison_result": matched.comparison_result or "",
        "source_filename": getattr(matched, "source_filename", None) or infer_compare_filename(merged_modified),
        "created_at": matched.created_at,
        "artifact_doc_id": artifact_payload.get("artifact_doc_id"),
        "source_file_content_type": artifact_payload.get("source_file_content_type"),
        "source_file_size": artifact_payload.get("source_file_size"),
        "ocr": artifact_payload.get("ocr"),
    }


def has_history_comparison(
    *,
    generated_result: str,
    matched: Any,
    artifact: dict[str, Any] | None,
) -> bool:
    artifact_payload = artifact or {}
    if artifact_payload.get("comparison_result") or artifact_payload.get("modified_test_case"):
        return True
    return bool(
        matched
        and is_reliable_matched_comparison(
            generated_result,
            getattr(matched, "generated_test_case", "") or "",
        )
    )


def build_execution_suite_summary(execution_suite: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_count": int(execution_suite.get("case_count") or 0),
        "suite_count": int(execution_suite.get("suite_count") or 0),
        "runnable_suite_count": int(execution_suite.get("runnable_suite_count") or 0),
        "linear_executable": bool(execution_suite.get("linear_executable")),
        "execution_readiness": str(execution_suite.get("execution_readiness") or ""),
        "warning_count": int(len(execution_suite.get("warnings") or [])),
        "main_suite_id": str(execution_suite.get("main_suite_id") or ""),
    }


def build_history_list_item(
    *,
    row: Any,
    execution_suite: dict[str, Any],
    has_comparison: bool,
) -> dict[str, Any]:
    requirement_text = row.requirement_text or ""
    return {
        "id": row.id,
        "project_id": row.project_id,
        "requirement_text": requirement_text,
        "created_at": row.created_at,
        "history_title": extract_history_title(requirement_text),
        "history_key": build_history_key(requirement_text),
        "has_comparison": has_comparison,
        "execution_suite_summary": build_execution_suite_summary(execution_suite),
    }


def build_generation_bundle_payload(
    *,
    entry: Any,
    generated_result: str,
    comparison: dict[str, Any] | None,
    execution_suite: dict[str, Any],
) -> dict[str, Any]:
    requirement_text = entry.requirement_text or ""
    has_comparison = bool(
        comparison and (comparison.get("comparison_result") or comparison.get("modified_test_case"))
    )
    return {
        "generation": {
            "id": entry.id,
            "project_id": entry.project_id,
            "requirement_text": requirement_text,
            "generated_result": generated_result,
            "created_at": entry.created_at,
            "history_title": extract_history_title(requirement_text),
            "history_key": build_history_key(requirement_text),
        },
        "comparison": comparison,
        "comparison_status": "found" if has_comparison else "missing",
        "execution_suite": execution_suite,
    }


def build_empty_priority_sample_pool_response(project_id: int) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "generation_id": None,
        "samples": [],
        "patterns": [],
        "signals": [],
        "learning_events": [],
        "updated_at": None,
        "artifact_doc_id": None,
    }


def build_priority_sample_pool_response(
    *,
    project_id: int,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if not payload:
        return build_empty_priority_sample_pool_response(project_id)
    return {
        "project_id": project_id,
        "generation_id": payload.get("generation_id"),
        "samples": _as_list(payload.get("samples")),
        "patterns": _as_list(payload.get("patterns")),
        "signals": _as_list(payload.get("signals")),
        "learning_events": _as_list(payload.get("learning_events")),
        "updated_at": payload.get("updated_at"),
        "artifact_doc_id": payload.get("artifact_doc_id"),
    }


def build_priority_sample_pool_mutation_response(
    *,
    project_id: int,
    payload: dict[str, Any] | None,
    doc: Any,
) -> dict[str, Any]:
    safe_payload = payload or {}
    return {
        "project_id": project_id,
        "generation_id": safe_payload.get("generation_id"),
        "samples": _as_list(safe_payload.get("samples")),
        "updated_at": safe_payload.get("updated_at"),
        "artifact_doc_id": doc.id,
    }


def priority_sample_pool_collections(payload: dict[str, Any] | None) -> tuple[list[Any], list[Any], list[Any]]:
    safe_payload = payload or {}
    return (
        _as_list(safe_payload.get("samples")),
        _as_list(safe_payload.get("patterns")),
        _as_list(safe_payload.get("learning_events")),
    )


def build_priority_sample_pool_consistency_response(
    *,
    project_id: int,
    payload: dict[str, Any] | None,
    consistency: dict[str, Any],
) -> dict[str, Any]:
    safe_payload = payload or {}
    samples, patterns, learning_events = priority_sample_pool_collections(safe_payload)
    return {
        "project_id": project_id,
        "generation_id": safe_payload.get("generation_id"),
        "json_sample_count": len(samples),
        "json_pattern_count": len(patterns),
        "json_event_count": len(learning_events),
        "consistency": consistency,
        "updated_at": safe_payload.get("updated_at"),
        "artifact_doc_id": safe_payload.get("artifact_doc_id"),
    }


def learning_events_from_priority_sample_pool(payload: dict[str, Any] | None) -> list[Any]:
    safe_payload = payload or {}
    return _as_list(safe_payload.get("learning_events"))
