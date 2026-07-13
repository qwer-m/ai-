"""Business service for test generation history routes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.db.models import LogEntry
from sqlalchemy import or_
from modules.testing.evaluation_artifact_store import load_compare_artifact_payload
from modules.testing.priority_sample_pool_store import (
    add_samples_to_pool,
    bulk_archive_priority_samples,
    confirm_priority_sample_in_pool,
    load_priority_sample_pool,
    remove_priority_sample_from_pool,
    update_priority_sample_in_pool,
    upsert_priority_sample_pool,
)
from modules.testing.sample_pool_shadow_store import shadow_read_consistency_check
from ..execution.execution_suite import build_execution_suite, parse_generated_cases_payload
from ..postprocess.case_access import case_id as case_access_id
from ..postprocess.case_contract import project_persistable_cases
from ..repositories.history_repository import (
    TestGenerationHistoryRepository,
)
from .history_response_helpers import (
    build_generation_bundle_payload,
    build_history_comparison,
    build_history_list_item,
    build_priority_sample_pool_consistency_response,
    build_priority_sample_pool_mutation_response,
    build_priority_sample_pool_response,
    cap_priority_sample_pool_samples,
    has_history_comparison,
    load_priority_sample_pool_payload,
    priority_sample_pool_collections,
    learning_events_from_priority_sample_pool,
)


def _public_generation_payload(generated_result: str) -> Any:
    try:
        payload = json.loads(generated_result or "[]")
    except Exception:
        return None
    if isinstance(payload, list):
        return project_persistable_cases(payload)
    if isinstance(payload, dict):
        projected = dict(payload)
        for key in ("cases", "test_cases", "generated_result", "final_cases", "items", "data"):
            value = projected.get(key)
            if isinstance(value, list):
                projected[key] = project_persistable_cases(value)
                break
        return projected
    return payload


def _public_generation_result_text(generated_result: str) -> str:
    payload = _public_generation_payload(generated_result)
    if payload is None:
        return generated_result or ""
    return json.dumps(payload, ensure_ascii=False)


def _case_lookup_key(case: dict[str, Any], index: int) -> str:
    return str(case_access_id(case) or case.get("case_id") or case.get("id") or f"TC-{index:03d}").strip()


def _compact_suite_metadata_by_case_id(suite_hint: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
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
        for case_ref in suite.get("cases") or []:
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
            ):
                value = case_ref.get(key)
                if value not in (None, "", []):
                    metadata[key] = value
            if "execution_sequence" not in metadata and case_ref.get("suite_order") not in (None, "", []):
                metadata["execution_sequence"] = case_ref.get("suite_order")
            metadata_by_case_id[case_id] = metadata
    return metadata_by_case_id


def _build_execution_suite_from_generated_result(
    generated_result: str,
    *,
    suite_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cases = parse_generated_cases_payload(generated_result or "")
    metadata_by_case_id = _compact_suite_metadata_by_case_id(suite_hint)
    if metadata_by_case_id and cases:
        hydrated_cases: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            hydrated = dict(case)
            metadata = metadata_by_case_id.get(_case_lookup_key(hydrated, index))
            if metadata:
                hydrated.update(metadata)
            hydrated_cases.append(hydrated)
        return build_execution_suite(hydrated_cases)
    if cases:
        return build_execution_suite(cases)
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


def _load_execution_suite_diagnostic(db: Any, entry: Any) -> dict[str, Any] | None:
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
            return _build_execution_suite_from_generated_result(
                getattr(entry, "generated_result", "") or "",
                suite_hint=suite_compact,
            )
    return None
from routers.automation.test_generation_shared import (
    build_history_key,
    find_matching_comparison,
)


class TestGenerationHistoryService:
    """Use-case layer for test generation history retrieval and bundle composition."""

    __test__ = False

    def __init__(self, db):
        self.repo = TestGenerationHistoryRepository(db)
        self._db = db

    def list_generations(self, *, project_id: int, user_id: int) -> tuple[str, list[dict[str, Any]]]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", []

        rows = self.repo.list_project_generations(project_id=project_id)
        latest_by_key: dict[str, Any] = {}
        for row in rows:
            key = build_history_key(row.requirement_text or "")
            if key not in latest_by_key:
                latest_by_key[key] = row

        dedup_rows = sorted(
            latest_by_key.values(),
            key=lambda item: (item.created_at or datetime.min, item.id or 0),
            reverse=True,
        )
        result: list[dict[str, Any]] = []
        for row in dedup_rows:
            matched = find_matching_comparison(
                project_id=row.project_id or 0,
                user_id=user_id,
                generated_result=row.generated_result or "",
                generation_created_at=row.created_at,
                db=self._db,
            )
            artifact = None
            if row.project_id is not None:
                artifact = load_compare_artifact_payload(
                    db=self._db,
                    project_id=row.project_id,
                    user_id=user_id,
                    generation_id=row.id,
                )
            execution_suite = _load_execution_suite_diagnostic(self._db, row) or _build_execution_suite_from_generated_result(
                row.generated_result or ""
            )
            result.append(
                build_history_list_item(
                    row=row,
                    execution_suite=execution_suite,
                    has_comparison=has_history_comparison(
                        generated_result=row.generated_result or "",
                        matched=matched,
                        artifact=artifact,
                    ),
                )
            )
        return "ok", result

    def get_generation(self, *, generation_id: int, user_id: int) -> tuple[str, dict[str, Any] | list[Any] | None]:
        entry = self.repo.get_generation(generation_id=generation_id)
        if not entry:
            return "not_found", None

        if entry.project_id is not None:
            if not self.repo.get_owned_project(project_id=entry.project_id, user_id=user_id):
                return "not_found", None
        elif entry.user_id != user_id:
            return "not_found", None

        if entry.generated_result:
            public_payload = _public_generation_payload(entry.generated_result)
            if public_payload is not None:
                return "ok", public_payload
        return (
            "ok",
            {
                "id": entry.id,
                "project_id": entry.project_id,
                "requirement_text": entry.requirement_text or "",
                "generated_result": entry.generated_result,
                "created_at": entry.created_at,
            },
        )

    def get_bundle(self, *, generation_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        entry = self.repo.get_generation(generation_id=generation_id)
        if not entry:
            return "not_found", None

        if entry.project_id is not None:
            if not self.repo.get_owned_project(project_id=entry.project_id, user_id=user_id):
                return "not_found", None
        elif entry.user_id != user_id:
            return "not_found", None

        raw_generated_result = entry.generated_result or ""
        generated_result = _public_generation_result_text(raw_generated_result)
        execution_suite = _load_execution_suite_diagnostic(self._db, entry) or _build_execution_suite_from_generated_result(
            raw_generated_result
        )
        matched = (
            find_matching_comparison(
                project_id=entry.project_id or 0,
                user_id=user_id,
                generated_result=raw_generated_result,
                generation_created_at=entry.created_at,
                db=self._db,
            )
            if entry.project_id is not None
            else None
        )
        artifact = (
            load_compare_artifact_payload(
                db=self._db,
                project_id=entry.project_id or 0,
                user_id=user_id,
                generation_id=entry.id,
            )
            if entry.project_id is not None
            else None
        )

        comparison = build_history_comparison(
            generated_result=raw_generated_result,
            matched=matched,
            artifact=artifact,
        )
        return (
            "ok",
            build_generation_bundle_payload(
                entry=entry,
                generated_result=generated_result,
                comparison=comparison,
                execution_suite=execution_suite,
            ),
        )

    def get_execution_suite(self, *, generation_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        entry = self.repo.get_generation(generation_id=generation_id)
        if not entry:
            return "not_found", None

        if entry.project_id is not None:
            if not self.repo.get_owned_project(project_id=entry.project_id, user_id=user_id):
                return "not_found", None
        elif entry.user_id != user_id:
            return "not_found", None

        return "ok", _load_execution_suite_diagnostic(self._db, entry) or _build_execution_suite_from_generated_result(
            entry.generated_result or ""
        )

    def get_priority_sample_pool(self, *, project_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        )
        return "ok", build_priority_sample_pool_response(project_id=project_id, payload=payload)

    def save_priority_sample_pool(
        self,
        *,
        project_id: int,
        user_id: int,
        generation_id: int | None,
        samples: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        safe_samples = cap_priority_sample_pool_samples(samples)

        doc = upsert_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            samples=safe_samples,
        )
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return (
            "ok",
            build_priority_sample_pool_mutation_response(
                project_id=project_id,
                payload=payload,
                doc=doc,
            ),
        )

    def delete_priority_sample_pool_item(
        self,
        *,
        project_id: int,
        user_id: int,
        generation_id: int | None,
        sample_id: str,
        delete_reason: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        doc = remove_priority_sample_from_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            sample_id=sample_id,
            delete_reason=delete_reason,
        )
        if not doc:
            return "sample_not_found", None
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return (
            "ok",
            build_priority_sample_pool_mutation_response(
                project_id=project_id,
                payload=payload,
                doc=doc,
            ),
        )

    def add_priority_sample_pool_items(
        self,
        *,
        project_id: int,
        user_id: int,
        generation_id: int | None,
        samples: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        safe_samples = samples if isinstance(samples, list) else []
        if not safe_samples:
            return "no_samples", None
        doc = add_samples_to_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            incoming=safe_samples,
        )
        if not doc:
            return "store_error", None
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return (
            "ok",
            build_priority_sample_pool_mutation_response(
                project_id=project_id,
                payload=payload,
                doc=doc,
            ),
        )

    def update_priority_sample_pool_item(
        self,
        *,
        project_id: int,
        user_id: int,
        generation_id: int | None,
        sample_id: str,
        patch: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        doc = update_priority_sample_in_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            sample_id=sample_id,
            patch=patch,
        )
        if not doc:
            return "sample_not_found", None
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return (
            "ok",
            build_priority_sample_pool_mutation_response(
                project_id=project_id,
                payload=payload,
                doc=doc,
            ),
        )

    def confirm_priority_sample_pool_item(
        self,
        *,
        project_id: int,
        user_id: int,
        generation_id: int | None,
        sample_id: str,
        patch: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        doc = confirm_priority_sample_in_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            sample_id=sample_id,
            patch=patch,
        )
        if not doc:
            return "sample_not_found", None
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return (
            "ok",
            build_priority_sample_pool_mutation_response(
                project_id=project_id,
                payload=payload,
                doc=doc,
            ),
        )

    def get_priority_sample_pool_consistency(
        self,
        *,
        project_id: int,
        user_id: int,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        samples, patterns, learning_events = priority_sample_pool_collections(payload)
        consistency = shadow_read_consistency_check(
            db=self._db,
            project_id=project_id,
            json_samples=samples,
            json_patterns=patterns,
            json_events=learning_events,
        )
        return (
            "ok",
            build_priority_sample_pool_consistency_response(
                project_id=project_id,
                payload=payload,
                consistency=consistency,
            ),
        )

    def bulk_archive_priority_sample_pool_items(
        self,
        *,
        project_id: int,
        user_id: int,
        generation_id: int | None,
        sample_ids: list[str],
        delete_reason: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        doc = bulk_archive_priority_samples(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            sample_ids=sample_ids,
            delete_reason=delete_reason,
        )
        if not doc:
            return "no_samples_archived", None
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return (
            "ok",
            build_priority_sample_pool_mutation_response(
                project_id=project_id,
                payload=payload,
                doc=doc,
            ),
        )

    def get_learning_selection_history(
        self,
        *,
        project_id: int,
        user_id: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", []
        payload = load_priority_sample_pool_payload(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            loader=load_priority_sample_pool,
        )
        return "ok", learning_events_from_priority_sample_pool(payload)
