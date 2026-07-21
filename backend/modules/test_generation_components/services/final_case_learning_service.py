"""Derive reusable sample-pool signals from human-final test cases."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.db.models import LogEntry
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.testing.priority_sample_pool_store import (
    append_learning_event,
    load_priority_sample_pool,
    upsert_priority_sample_pool,
)
from ..repositories.history_repository import (
    TestGenerationHistoryRepository,
)
from .final_case_parsing import (
    _text,
    parse_test_cases_payload,
    parse_test_cases_spreadsheet_bytes,
)
from .final_case_learning_derivation import build_learning_samples_from_final_cases
from .final_case_sample_learning import _fingerprint
from .final_case_workflow_contracts import (
    _workflow_contract_candidates_from_derived,
    upsert_workflow_contracts_from_derived,
)
from .final_case_sample_pool_persistence import persist_sample_pool_samples
from .final_case_linked_sources import resolve_final_case_sources
from .final_case_quality_ledger_lookup import (
    find_generation_quality_ledger as _find_generation_quality_ledger,
    parse_gen_diag_payload as _parse_gen_diag_payload,
)
from .final_case_evaluation_learning import (
    _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
    _MAX_EVALUATION_LEARNING_CANDIDATES,
    _aggregate_evaluation_learning_candidates,
    _as_text_list,
    _candidate_has_sample_shape,
    _compact_evaluation_metrics,
    _confidence_from_metrics,
    _evaluation_candidate_bucket_rank,
    _evaluation_candidate_field_limit,
    _evaluation_candidate_key,
    _evaluation_learning_candidate_quality_gate,
    _filter_quality_evaluation_sample_for_apply,
    _merge_evaluation_candidate_bucket,
    _summarize_candidate_texts,
    _summarize_evaluation_defect_pattern,
    build_learning_candidates_from_evaluation_result,
    parse_evaluation_result_payload,
)

_MAX_POOL_SAMPLES = 5000


class FinalCaseLearningService:
    """Service for writing final-case learning signals into the existing pool."""

    def __init__(self, db):
        self._db = db
        self.history_repo = TestGenerationHistoryRepository(db)
        self.knowledge_repo = KnowledgeDocumentRepository(db)

    def learn_from_case_pair(
        self,
        *,
        project_id: int,
        user_id: int,
        generated_cases: Any,
        final_cases: Any,
        generation_id: int | None = None,
        include_negative_samples: bool = True,
        dry_run: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.history_repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        normalized_generated = parse_test_cases_payload(generated_cases)
        normalized_final = parse_test_cases_payload(final_cases)
        if not normalized_final:
            return (
                "no_final_cases",
                {
                    "project_id": project_id,
                    "samples": [],
                    "diagnostics": {
                        "generated_case_count": len(normalized_generated),
                        "final_case_count": 0,
                    },
                },
            )

        requirement_text = ""
        ledger: dict[str, Any] = {}
        effective_generation_id = generation_id
        if generation_id:
            entry = self.history_repo.get_generation(generation_id=int(generation_id))
            if not entry or int(getattr(entry, "project_id", 0) or 0) != int(project_id):
                return "generation_not_found", None
            requirement_text = getattr(entry, "requirement_text", "") or ""
            ledger = self._find_quality_ledger(entry)
            if not normalized_generated:
                normalized_generated = parse_test_cases_payload(getattr(entry, "generated_result", None))

        derived = build_learning_samples_from_final_cases(
            generated_cases=normalized_generated,
            final_cases=normalized_final,
            requirement_text=requirement_text,
            generation_id=effective_generation_id,
            linked_doc_ids=[],
            include_negative_samples=include_negative_samples,
            quality_ledger=ledger,
        )
        if dry_run:
            return (
                "ok",
                {
                    "project_id": project_id,
                    "artifact_doc_id": None,
                    "derived": derived,
                    "sample_pool_count": None,
                    "updated_at": None,
                    "dry_run": True,
                },
            )

        persisted_pool = persist_sample_pool_samples(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=None,
            samples=derived["samples"],
            max_pool_samples=_MAX_POOL_SAMPLES,
            load_priority_sample_pool_fn=load_priority_sample_pool,
            upsert_priority_sample_pool_fn=upsert_priority_sample_pool,
        )
        workflow_contracts = upsert_workflow_contracts_from_derived(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            derived=derived,
        )
        return (
            "ok",
            {
                "project_id": project_id,
                "artifact_doc_id": persisted_pool.doc.id,
                "derived": derived,
                "workflow_contracts": workflow_contracts,
                "sample_pool_count": persisted_pool.sample_count,
                "updated_at": persisted_pool.updated_at,
                "dry_run": False,
            },
        )

    def build_learning_candidates_from_evaluation(
        self,
        *,
        project_id: int,
        user_id: int,
        evaluation_result: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.history_repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        derived = build_learning_candidates_from_evaluation_result(evaluation_result)
        return (
            "ok",
            {
                "project_id": project_id,
                **derived,
            },
        )

    def apply_learning_candidates(
        self,
        *,
        project_id: int,
        user_id: int,
        candidates: list[dict[str, Any]],
        dry_run: bool = True,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.history_repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        candidate_items = candidates if isinstance(candidates, list) else []
        samples: list[dict[str, Any]] = []
        rejected_sample_count = 0
        rejected_sample_examples: list[dict[str, Any]] = []
        for candidate in candidate_items:
            if not isinstance(candidate, dict):
                continue
            sample = candidate.get("sample")
            normalized_sample: dict[str, Any] | None = None
            if isinstance(sample, dict):
                normalized_sample = dict(sample)
            elif _candidate_has_sample_shape(candidate):
                normalized_sample = dict(candidate)
            if normalized_sample is None:
                continue
            gated_sample = _filter_quality_evaluation_sample_for_apply(normalized_sample)
            if gated_sample is None:
                rejected_sample_count += 1
                rejected_sample_examples.append(
                    {
                        "id": normalized_sample.get("case_id") or normalized_sample.get("id"),
                        "text": _text(
                            normalized_sample.get("user_comment")
                            or normalized_sample.get("title")
                            or normalized_sample.get("pattern_summary")
                        )[:160],
                    }
                )
                continue
            samples.append(gated_sample)
        samples = samples[:_MAX_EVALUATION_LEARNING_CANDIDATES]
        derived = {
            "samples": samples,
            "diagnostics": {
                "candidate_count": len(candidate_items),
                "sample_count": len(samples),
                "rejected_sample_count": rejected_sample_count,
                "rejected_sample_examples": rejected_sample_examples[:8],
                "positive_sample_count": sum(1 for item in samples if str(item.get("signal_type") or "") == "positive"),
                "negative_sample_count": sum(1 for item in samples if str(item.get("signal_type") or "") == "negative"),
                "target": "priority_sample_pool",
                "source": "quality_evaluation_defect",
                "candidate_quality_policy": _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
            },
        }
        if dry_run:
            return (
                "ok",
                {
                    "project_id": project_id,
                    "artifact_doc_id": None,
                    "derived": derived,
                    "sample_pool_count": None,
                    "updated_at": None,
                    "dry_run": True,
                },
            )

        now_iso = datetime.utcnow().isoformat()
        accepted_candidate_ids: list[str] = []
        for sample in samples:
            sample["learning_status"] = "user_confirmed"
            sample["learning_confirmed_at"] = now_iso
            sample["learning_confirmed_by"] = int(user_id)
            cid = sample.get("case_id") or sample.get("id")
            if cid:
                accepted_candidate_ids.append(str(cid))

        persisted_pool = persist_sample_pool_samples(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=None,
            samples=samples,
            max_pool_samples=_MAX_POOL_SAMPLES,
            load_priority_sample_pool_fn=load_priority_sample_pool,
            upsert_priority_sample_pool_fn=upsert_priority_sample_pool,
            after_upsert_fn=lambda: append_learning_event(
                db=self._db,
                project_id=project_id,
                user_id=user_id,
                event_type="quality_evaluation_candidates_applied",
                event_payload={
                    "candidate_count": len(candidate_items),
                    "accepted_count": len(samples),
                    "accepted_candidate_ids": accepted_candidate_ids,
                    "source": "quality_evaluation_defect",
                },
            ),
        )
        return (
            "ok",
            {
                "project_id": project_id,
                "artifact_doc_id": persisted_pool.doc.id,
                "derived": derived,
                "sample_pool_count": persisted_pool.sample_count,
                "updated_at": persisted_pool.updated_at,
                "dry_run": False,
            },
        )

    def learn_from_generation_final_cases(
        self,
        *,
        generation_id: int,
        user_id: int,
        final_cases: list[dict[str, Any]] | None = None,
        final_case_doc_ids: list[int] | None = None,
        source_doc_ids: list[int] | None = None,
        include_linked_docs: bool = True,
        include_negative_samples: bool = True,
        dry_run: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        entry = self.history_repo.get_generation(generation_id=generation_id)
        if not entry:
            return "not_found", None
        if entry.project_id is None:
            if entry.user_id != user_id:
                return "not_found", None
            return "project_not_found", None
        if not self.history_repo.get_owned_project(project_id=entry.project_id, user_id=user_id):
            return "not_found", None

        generated_cases = parse_test_cases_payload(entry.generated_result)
        final_case_sources = resolve_final_case_sources(
            entry=entry,
            final_cases=final_cases,
            final_case_doc_ids=final_case_doc_ids,
            source_doc_ids=source_doc_ids,
            include_linked_docs=include_linked_docs,
            knowledge_repo=self.knowledge_repo,
            find_linked_final_case_docs_fn=self._find_linked_final_case_docs,
            parse_test_cases_payload_fn=parse_test_cases_payload,
        )
        linked_docs = final_case_sources.linked_docs
        effective_final_cases = final_case_sources.effective_final_cases
        if not effective_final_cases:
            return (
                "no_final_cases",
                {
                    "generation_id": generation_id,
                    "project_id": entry.project_id,
                    "linked_doc_ids": final_case_sources.linked_doc_ids,
                    "samples": [],
                    "diagnostics": {
                        "generated_case_count": len(generated_cases),
                        "final_case_count": 0,
                    },
                },
            )

        derived = build_learning_samples_from_final_cases(
            generated_cases=generated_cases,
            final_cases=effective_final_cases,
            requirement_text=entry.requirement_text or "",
            generation_id=generation_id,
            linked_doc_ids=final_case_sources.linked_doc_ids_int,
            include_negative_samples=include_negative_samples,
            quality_ledger=self._find_quality_ledger(entry),
        )
        if dry_run:
            return (
                "ok",
                {
                    "project_id": entry.project_id,
                    "generation_id": generation_id,
                    "artifact_doc_id": None,
                    "linked_doc_ids": final_case_sources.linked_doc_ids,
                    "derived": derived,
                    "sample_pool_count": None,
                    "updated_at": None,
                    "dry_run": True,
                },
            )
        persisted_pool = persist_sample_pool_samples(
            db=self._db,
            project_id=entry.project_id,
            user_id=user_id,
            generation_id=generation_id,
            samples=derived["samples"],
            max_pool_samples=_MAX_POOL_SAMPLES,
            load_priority_sample_pool_fn=load_priority_sample_pool,
            upsert_priority_sample_pool_fn=upsert_priority_sample_pool,
        )
        workflow_contracts = upsert_workflow_contracts_from_derived(
            db=self._db,
            project_id=entry.project_id,
            user_id=user_id,
            derived=derived,
        )
        return (
            "ok",
            {
                "project_id": entry.project_id,
                "generation_id": generation_id,
                "artifact_doc_id": persisted_pool.doc.id,
                "linked_doc_ids": final_case_sources.linked_doc_ids,
                "derived": derived,
                "workflow_contracts": workflow_contracts,
                "sample_pool_count": persisted_pool.sample_count,
                "updated_at": persisted_pool.updated_at,
            },
        )

    def _find_linked_final_case_docs(self, entry: Any) -> list[Any]:
        if entry.project_id is None:
            return []
        candidates = self.knowledge_repo.list_project_docs_created_desc(
            project_id=entry.project_id,
            limit=200,
        )
        requirement_fingerprint = _fingerprint(entry.requirement_text or "")
        source_ids = [
            int(doc.id)
            for doc in candidates
            if doc.doc_type == "requirement"
            and doc.id is not None
            and _fingerprint(doc.content or "") == requirement_fingerprint
        ]
        return self.knowledge_repo.list_linked_test_cases_for_sources(
            project_id=entry.project_id,
            source_doc_ids=source_ids,
        )

    def _find_quality_ledger(self, entry: Any) -> dict[str, Any]:
        return _find_generation_quality_ledger(
            db=self._db,
            log_entry_model=LogEntry,
            entry=entry,
        )
