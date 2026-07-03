"""Business service for test generation history routes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

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
from ..execution.execution_suite import build_execution_suite
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
            execution_suite = build_execution_suite(row.generated_result or "")
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
            try:
                return "ok", json.loads(entry.generated_result)
            except Exception:
                pass
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

        generated_result = entry.generated_result or ""
        execution_suite = build_execution_suite(generated_result)
        matched = (
            find_matching_comparison(
                project_id=entry.project_id or 0,
                user_id=user_id,
                generated_result=generated_result,
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
            generated_result=generated_result,
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
        status, payload = self.get_generation(generation_id=generation_id, user_id=user_id)
        if status != "ok":
            return status, None
        return "ok", build_execution_suite(payload)

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
