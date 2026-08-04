"""Route-facing business service for RAG eval run endpoints."""

from __future__ import annotations

from collections import Counter
from typing import Any

from core.db.model_defs import Project, RagDatasetSample, RagEvalRun, RagEvalSampleResult
from modules.rag_eval.repositories.rag_eval_repo import get_run, list_run_sample_results
from modules.rag_eval.services.rag_eval_compare_service import compare_runs
from modules.rag_eval.services.rag_eval_service import resume_eval_run, start_eval_run, stop_eval_run
from schemas.rag.rag_eval import RagEvalRunOut, RagEvalSampleResultOut, RagEvalSamplesPage


class RagRunRouteService:
    """Use-case layer for run create/control/query endpoints."""

    def __init__(self, db):
        self.db = db

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        row = self.db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
        return bool(row)

    def create_run(
        self,
        *,
        user_id: int,
        project_id: int,
        dataset_id: int,
        config: dict[str, Any],
        run_name: str | None,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        try:
            run = start_eval_run(
                db=self.db,
                user_id=user_id,
                project_id=project_id,
                dataset_id=dataset_id,
                config=config,
                run_name=run_name,
            )
            return "ok", {"run_id": run.id, "status": run.status}
        except ValueError as exc:
            return f"error:{exc}", None

    def stop_run(self, *, run_id: int, user_id: int) -> tuple[str, RagEvalRunOut | None]:
        try:
            run = stop_eval_run(db=self.db, user_id=user_id, run_id=run_id)
            return "ok", RagEvalRunOut.model_validate(run)
        except ValueError as exc:
            return f"error:{exc}", None

    def resume_run(self, *, run_id: int, user_id: int) -> tuple[str, RagEvalRunOut | None]:
        try:
            run = resume_eval_run(db=self.db, user_id=user_id, run_id=run_id)
            return "ok", RagEvalRunOut.model_validate(run)
        except ValueError as exc:
            return f"error:{exc}", None

    def compare_runs(self, *, run_a: int, run_b: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        if run_a == run_b:
            return "same_run", None
        try:
            result = compare_runs(db=self.db, run_a_id=run_a, run_b_id=run_b, user_id=user_id)
            return "ok", result
        except ValueError as exc:
            return f"error:{exc}", None

    def get_run_status(self, *, run_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        run = get_run(self.db, run_id, user_id)
        if not run:
            return "run_not_found", None

        total = int(run.total_samples or 0)
        finished = int(run.finished_samples or 0)
        progress = {
            "total_samples": total,
            "finished_samples": finished,
            "progress_pct": (finished / total * 100.0) if total > 0 else 0.0,
            "status": run.status,
            "cursor": int(run.cursor or 0),
        }
        rows = self.db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run.id).all()
        sample_stats = {
            "failure_reason_counts": dict(Counter([str(x.failure_reason) for x in rows if x.failure_reason])),
            "correct_count": sum(1 for x in rows if x.answer_correct),
            "incorrect_count": sum(1 for x in rows if not x.answer_correct),
        }
        return (
            "ok",
            {
                "run": RagEvalRunOut.model_validate(run),
                "progress": progress,
                "metrics": run.metrics_json or {},
                "sample_stats": sample_stats,
            },
        )

    def get_run_samples(
        self,
        *,
        run_id: int,
        user_id: int,
        page: int,
        page_size: int,
        tag: str | None,
        failure_reason: str | None,
        answer_correct: bool | None,
        correctness: str | None,
        sample_ids_text: str | None,
    ) -> tuple[str, RagEvalSamplesPage | None]:
        run = get_run(self.db, run_id, user_id)
        if not run:
            return "run_not_found", None

        if answer_correct is None and correctness:
            correctness_norm = correctness.strip().lower()
            if correctness_norm in {"correct", "true", "1", "yes"}:
                answer_correct = True
            elif correctness_norm in {"incorrect", "false", "0", "no"}:
                answer_correct = False

        selected_sample_ids: list[int] = []
        if sample_ids_text:
            selected_sample_ids = [int(x.strip()) for x in sample_ids_text.split(",") if x.strip().isdigit()]

        rows, total = list_run_sample_results(
            self.db,
            run_id,
            page=page,
            page_size=page_size,
            tag=tag,
            failure_reason=failure_reason,
            answer_correct=answer_correct,
            sample_ids=selected_sample_ids or None,
        )

        sample_ids = [int(x.sample_id) for x in rows]
        sample_map = (
            {
                x.id: x
                for x in self.db.query(RagDatasetSample)
                .filter(RagDatasetSample.id.in_(sample_ids))
                .all()
            }
            if sample_ids
            else {}
        )

        items: list[RagEvalSampleResultOut] = []
        for row in rows:
            sample = sample_map.get(int(row.sample_id))
            payload: dict[str, Any] = {
                **row.__dict__,
                "sample_query": sample.query if sample else None,
                "gold_docs": list(sample.gold_docs or []) if sample else [],
                "gold_chunks": [str(x) for x in (sample.gold_chunks or [])] if sample else [],
                "expected_answer": sample.gold_answer if sample else None,
                "answer_points": list(sample.answer_points or []) if sample else [],
                "tags": list(sample.tags or []) if sample else [],
                "difficulty": sample.difficulty if sample else None,
            }
            items.append(RagEvalSampleResultOut.model_validate(payload))

        return "ok", RagEvalSamplesPage(items=items, page=page, page_size=page_size, total=total)

