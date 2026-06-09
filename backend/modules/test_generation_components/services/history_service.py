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
from ..repositories.history_repository import (
    TestGenerationHistoryRepository,
)
from routers.automation.test_generation_shared import (
    build_history_key,
    extract_history_title,
    find_matching_comparison,
    infer_compare_filename,
    normalize_case_text,
)


class TestGenerationHistoryService:
    """Use-case layer for test generation history retrieval and bundle composition."""

    __test__ = False

    def __init__(self, db):
        self.repo = TestGenerationHistoryRepository(db)
        self._db = db

    @staticmethod
    def _is_reliable_matched_comparison(generated_result: str, matched_generated_result: str) -> bool:
        """Guard fuzzy matching results to avoid loading unrelated historical comparison content."""
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
            has_artifact_comparison = bool(
                (artifact or {}).get("comparison_result") or (artifact or {}).get("modified_test_case")
            )
            has_reliable_matched_comparison = bool(
                matched
                and self._is_reliable_matched_comparison(
                    row.generated_result or "",
                    getattr(matched, "generated_test_case", "") or "",
                )
            )
            result.append(
                {
                    "id": row.id,
                    "project_id": row.project_id,
                    "requirement_text": row.requirement_text or "",
                    "created_at": row.created_at,
                    "history_title": extract_history_title(row.requirement_text or ""),
                    "history_key": build_history_key(row.requirement_text or ""),
                    "has_comparison": has_reliable_matched_comparison or has_artifact_comparison,
                }
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

        comparison = None
        artifact_modified = (artifact or {}).get("modified_test_case") or ""
        artifact_result = (artifact or {}).get("comparison_result") or ""
        has_artifact_comparison = bool(artifact_modified or artifact_result)
        has_reliable_matched_comparison = bool(
            matched
            and self._is_reliable_matched_comparison(
                generated_result,
                getattr(matched, "generated_test_case", "") or "",
            )
        )
        if has_artifact_comparison:
            comparison = {
                "id": None,
                "modified_test_case": artifact_modified,
                "comparison_result": artifact_result,
                "source_filename": (artifact or {}).get("source_filename") or infer_compare_filename(artifact_modified),
                "created_at": (artifact or {}).get("updated_at"),
                "artifact_doc_id": (artifact or {}).get("artifact_doc_id"),
                "source_file_content_type": (artifact or {}).get("source_file_content_type"),
                "source_file_size": (artifact or {}).get("source_file_size"),
                "ocr": (artifact or {}).get("ocr"),
            }
        elif has_reliable_matched_comparison and matched:
            merged_modified = matched.modified_test_case or ""
            comparison = {
                "id": matched.id,
                "modified_test_case": merged_modified,
                "comparison_result": matched.comparison_result or "",
                "source_filename": getattr(matched, "source_filename", None) or infer_compare_filename(merged_modified),
                "created_at": matched.created_at,
                "artifact_doc_id": (artifact or {}).get("artifact_doc_id"),
                "source_file_content_type": (artifact or {}).get("source_file_content_type"),
                "source_file_size": (artifact or {}).get("source_file_size"),
                "ocr": (artifact or {}).get("ocr"),
            }
        has_comparison = bool(comparison and (comparison.get("comparison_result") or comparison.get("modified_test_case")))
        return (
            "ok",
            {
                "generation": {
                    "id": entry.id,
                    "project_id": entry.project_id,
                    "requirement_text": entry.requirement_text or "",
                    "generated_result": generated_result,
                    "created_at": entry.created_at,
                    "history_title": extract_history_title(entry.requirement_text or ""),
                    "history_key": build_history_key(entry.requirement_text or ""),
                },
                "comparison": comparison,
                "comparison_status": "found" if has_comparison else "missing",
            },
        )

    def get_priority_sample_pool(self, *, project_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        )
        if not payload:
            return (
                "ok",
                {
                    "project_id": project_id,
                    "generation_id": None,
                    "samples": [],
                    "patterns": [],
                    "signals": [],
                    "learning_events": [],
                    "updated_at": None,
                    "artifact_doc_id": None,
                },
            )
        samples = payload.get("samples")
        learning_events = payload.get("learning_events")
        patterns = payload.get("patterns")
        signals_data = payload.get("signals")
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": samples if isinstance(samples, list) else [],
                "patterns": patterns if isinstance(patterns, list) else [],
                "signals": signals_data if isinstance(signals_data, list) else [],
                "learning_events": learning_events if isinstance(learning_events, list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": payload.get("artifact_doc_id"),
            },
        )

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
        safe_samples = samples if isinstance(samples, list) else []
        # Avoid accidentally writing unbounded payloads.
        if len(safe_samples) > 5000:
            safe_samples = safe_samples[:5000]

        doc = upsert_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            samples=safe_samples,
        )
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": payload.get("samples") if isinstance(payload.get("samples"), list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": doc.id,
            },
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
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": payload.get("samples") if isinstance(payload.get("samples"), list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": doc.id,
            },
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
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": payload.get("samples") if isinstance(payload.get("samples"), list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": doc.id,
            },
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
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": payload.get("samples") if isinstance(payload.get("samples"), list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": doc.id,
            },
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
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": payload.get("samples") if isinstance(payload.get("samples"), list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": doc.id,
            },
        )

    def get_priority_sample_pool_consistency(
        self,
        *,
        project_id: int,
        user_id: int,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        patterns = payload.get("patterns") if isinstance(payload.get("patterns"), list) else []
        learning_events = payload.get("learning_events") if isinstance(payload.get("learning_events"), list) else []
        consistency = shadow_read_consistency_check(
            db=self._db,
            project_id=project_id,
            json_samples=samples,
            json_patterns=patterns,
            json_events=learning_events,
        )
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "json_sample_count": len(samples),
                "json_pattern_count": len(patterns),
                "json_event_count": len(learning_events),
                "consistency": consistency,
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": payload.get("artifact_doc_id"),
            },
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
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        ) or {}
        return (
            "ok",
            {
                "project_id": project_id,
                "generation_id": payload.get("generation_id"),
                "samples": payload.get("samples") if isinstance(payload.get("samples"), list) else [],
                "updated_at": payload.get("updated_at"),
                "artifact_doc_id": doc.id,
            },
        )

    def get_learning_selection_history(
        self,
        *,
        project_id: int,
        user_id: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", []
        payload = load_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        )
        if not payload:
            return "ok", []
        events = payload.get("learning_events")
        if isinstance(events, list):
            return "ok", events
        return "ok", []
