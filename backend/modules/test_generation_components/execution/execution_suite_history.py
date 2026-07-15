from __future__ import annotations

import json
from typing import Any

from core.db.models import LogEntry
from sqlalchemy import or_

from ..postprocess.case_access import case_id as case_access_id
from .execution_suite import build_execution_suite, parse_generated_cases_payload


_VALID_PRIORITIES = {"P0", "P1", "P2"}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _valid_priority(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in _VALID_PRIORITIES else ""


def _case_lookup_key(case: dict[str, Any], index: int) -> str:
    return str(case_access_id(case) or case.get("case_id") or case.get("id") or f"TC-{index:03d}").strip()


def execution_suite_metadata_by_case_id(suite_hint: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(suite_hint, dict):
        return {}
    metadata_by_case_id: dict[str, dict[str, Any]] = {}
    for suite in suite_hint.get("suites") or []:
        if not isinstance(suite, dict):
            continue
        suite_meta = {
            "execution_group": suite.get("execution_group"),
            "chain_id": suite.get("suite_id"),
            "group_setup": suite.get("group_setup"),
            "group_teardown": suite.get("group_teardown"),
        }
        suite_cases = [item for item in (suite.get("cases") or []) if isinstance(item, dict)]
        for case_ref in suite_cases:
            if not isinstance(case_ref, dict):
                continue
            case_id = str(case_ref.get("case_id") or "").strip()
            if not case_id:
                continue
            metadata: dict[str, Any] = {
                key: value
                for key, value in suite_meta.items()
                if value not in (None, "", [])
            }
            for key in (
                "execution_sequence",
                "suite_order",
                "depends_on",
                "role",
                "session_key",
                "fixture_key",
                "setup_hint",
                "teardown_hint",
                "source_state",
                "target_state",
                "action",
                "transition_action",
                "description",
                "test_module",
                "preconditions",
                "steps",
                "test_input",
                "expected_result",
            ):
                value = case_ref.get(key)
                if value not in (None, "", []):
                    metadata[key] = value
            priority = _valid_priority(case_ref.get("priority"))
            if priority:
                metadata["priority"] = priority
                metadata["priority_final"] = priority
            if "execution_sequence" not in metadata and case_ref.get("suite_order") not in (None, "", []):
                metadata["execution_sequence"] = case_ref.get("suite_order")
            if str(suite_meta.get("execution_group") or "").strip().lower() == "main_smoke":
                metadata.setdefault("workflow_id", suite_meta.get("chain_id") or "main_smoke_chain")
                metadata.setdefault("path_type", "positive")
                metadata.setdefault("blocking", False)
                metadata.setdefault("destructive", False)
                metadata.setdefault("can_advance_main_flow", True)
                metadata.setdefault("state_transition_confidence", 0.9)
                if case_ref.get("suite_order") not in (None, "", []):
                    metadata.setdefault("main_chain_step", case_ref.get("suite_order"))
                transition = {
                    key: metadata[key]
                    for key in (
                        "workflow_id",
                        "source_state",
                        "action",
                        "target_state",
                        "path_type",
                        "blocking",
                        "destructive",
                        "can_advance_main_flow",
                        "state_transition_confidence",
                    )
                    if key in metadata
                }
                if transition:
                    metadata["workflow_transition"] = transition
            metadata_by_case_id[case_id] = metadata
    return metadata_by_case_id


def hydrate_cases_from_execution_suite(
    cases: list[dict[str, Any]],
    suite_hint: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Hydrate public persisted cases with execution metadata for append-time gates."""
    metadata_by_case_id = execution_suite_metadata_by_case_id(suite_hint)
    if not metadata_by_case_id or not isinstance(cases, list):
        return cases

    hydrated_cases: list[dict[str, Any]] = []
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            continue
        case = dict(item)
        metadata = metadata_by_case_id.get(_case_lookup_key(case, index))
        if metadata:
            for key, value in metadata.items():
                if key in {"priority", "priority_final"}:
                    priority = _valid_priority(value)
                    if priority:
                        if not _valid_priority(case.get("priority")):
                            case["priority"] = priority
                        if not _valid_priority(case.get("priority_final")):
                            case["priority_final"] = priority
                    continue
                if not _has_value(case.get(key)):
                    case[key] = value
        fallback_priority = _valid_priority(case.get("priority"))
        if fallback_priority and not _valid_priority(case.get("priority_final")):
            case["priority_final"] = fallback_priority
        hydrated_cases.append(case)
    return hydrated_cases


def build_execution_suite_from_generated_result(
    generated_result: str,
    *,
    suite_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cases = parse_generated_cases_payload(generated_result or "")
    hydrated_cases = hydrate_cases_from_execution_suite(cases, suite_hint)
    if hydrated_cases:
        return build_execution_suite(hydrated_cases)
    if isinstance(suite_hint, dict):
        return suite_hint
    return build_execution_suite(generated_result or "")


def _parse_gen_diag_message(message: str) -> dict[str, Any] | None:
    text = str(message or "")
    if not text.startswith("GEN_DIAG:"):
        return None
    try:
        payload = json.loads(text.split(":", 1)[1])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_execution_suite_diagnostic(db: Any, entry: Any) -> dict[str, Any] | None:
    generation_id = getattr(entry, "id", None)
    if not generation_id or not hasattr(db, "query"):
        return None
    try:
        query = db.query(LogEntry).filter(
            LogEntry.message.like('GEN_DIAG:%"kind": "generation_execution_suite"%'),
            or_(
                LogEntry.message.like(f'%"generation_id": {int(generation_id)}%'),
                LogEntry.message.like(f'%"generation_id":{int(generation_id)}%'),
            ),
        )
        project_id = getattr(entry, "project_id", None)
        user_id = getattr(entry, "user_id", None)
        if project_id is not None:
            query = query.filter(LogEntry.project_id == project_id)
        if user_id is not None:
            query = query.filter(LogEntry.user_id == user_id)
        rows = query.order_by(LogEntry.id.desc()).limit(5).all()
    except Exception:
        return None

    for row in rows:
        payload = _parse_gen_diag_message(getattr(row, "message", "") or "")
        if not payload or payload.get("kind") != "generation_execution_suite":
            continue
        try:
            payload_generation_id = int(payload.get("generation_id") or 0)
        except Exception:
            payload_generation_id = 0
        if payload_generation_id != int(generation_id):
            continue
        suite = payload.get("execution_suite")
        if isinstance(suite, dict) and not bool(suite.get("omitted_due_to_size")):
            return suite
        suite_compact = payload.get("execution_suite_compact")
        if isinstance(suite_compact, dict):
            return build_execution_suite_from_generated_result(
                getattr(entry, "generated_result", "") or "",
                suite_hint=suite_compact,
            )
    return None


def hydrate_append_existing_cases_from_diagnostic(
    existing_cases: list[dict[str, Any]],
    *,
    db: Any,
    entry: Any,
) -> list[dict[str, Any]]:
    suite = load_execution_suite_diagnostic(db, entry)
    if not suite:
        return existing_cases
    return hydrate_cases_from_execution_suite(existing_cases, suite)


__all__ = [
    "build_execution_suite_from_generated_result",
    "execution_suite_metadata_by_case_id",
    "hydrate_append_existing_cases_from_diagnostic",
    "hydrate_cases_from_execution_suite",
    "load_execution_suite_diagnostic",
]
