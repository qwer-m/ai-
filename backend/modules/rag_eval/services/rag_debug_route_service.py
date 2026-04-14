"""Route-facing business service for RAG debug/promote endpoints."""

from __future__ import annotations

import time
from typing import Any

from core.ai.ai_client import get_client_for_user
from core.db.models import Project, RagDataset, RagDatasetSample
from modules.rag_eval.analysis.debug_display import resolve_debug_display_fields
from modules.rag_eval.services.rag_retrieval_service import run_retrieval_debug


class RagDebugRouteService:
    """Use-case layer for sample promotion and single-query debug."""

    def __init__(self, db):
        self.db = db

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        row = self.db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
        return bool(row)

    def promote_sample_to_dataset(
        self,
        *,
        sample_id: int,
        target_dataset_type: str,
        user_id: int,
    ) -> tuple[str, dict[str, Any] | None]:
        target_type = (target_dataset_type or "").strip().lower()
        if target_type not in {"challenge", "regression"}:
            return "invalid_target_type", None

        sample = (
            self.db.query(RagDatasetSample)
            .join(RagDataset, RagDataset.id == RagDatasetSample.dataset_id)
            .filter(RagDatasetSample.id == sample_id, RagDataset.user_id == user_id)
            .first()
        )
        if not sample:
            return "sample_not_found", None

        target_name = "自动回流-挑战集" if target_type == "challenge" else "自动回流-回归集"
        target_ds = (
            self.db.query(RagDataset)
            .filter(
                RagDataset.user_id == user_id,
                RagDataset.type == target_type,
                RagDataset.name == target_name,
            )
            .first()
        )
        if not target_ds:
            target_ds = RagDataset(
                user_id=user_id,
                name=target_name,
                type=target_type,
                description="由评测失败样本自动回流生成",
            )
            self.db.add(target_ds)
            self.db.commit()
            self.db.refresh(target_ds)

        clone = RagDatasetSample(
            dataset_id=target_ds.id,
            query=sample.query,
            gold_docs=sample.gold_docs or [],
            gold_chunks=sample.gold_chunks or [],
            gold_answer=sample.gold_answer,
            answer_points=sample.answer_points or [],
            tags=sample.tags or [],
            difficulty=sample.difficulty,
            metadata_filters=sample.metadata_filters or {},
            expected_doc_version=sample.expected_doc_version,
            enabled=True,
        )
        self.db.add(clone)
        self.db.commit()
        self.db.refresh(clone)
        return "ok", {"success": True, "target_dataset_id": target_ds.id, "target_sample_id": clone.id}

    def rag_single_debug(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        query = str(payload.get("query") or "").strip()
        retrieval_started = time.perf_counter()
        retrieval_result = run_retrieval_debug(
            query=query,
            project_id=project_id,
            db=self.db,
            user_id=user_id,
            config={
                "retrieval": {
                    "top_k": int(payload.get("limit") or 5),
                    "retrieval_mode": str(payload.get("retrieval_mode") or "hybrid"),
                    "recall_top_k": payload.get("recall_top_k"),
                    "rerank_top_n": payload.get("rerank_top_n"),
                    "max_chunks_per_doc": int(payload.get("max_chunks_per_doc") or 2),
                    "min_docs": int(payload.get("min_docs") or 2),
                    "title_weight": float(payload.get("title_weight") or 0.15),
                    "keyword_weight": float(payload.get("keyword_weight") or 0.25),
                    "vector_weight": float(payload.get("vector_weight") or 0.6),
                    "redundancy_threshold": float(payload.get("redundancy_threshold") or 0.88),
                },
                "context": {"max_tokens": int(payload.get("max_tokens") or 1800)},
                "advanced": {
                    "enable_query_rewrite": bool(payload.get("enable_query_rewrite", True)),
                    "enable_rerank": bool(payload.get("enable_rerank", True)),
                },
            },
        )
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000

        context_text = str(retrieval_result.get("context") or "")
        generation_started = time.perf_counter()
        answer = ""
        if context_text:
            client = get_client_for_user(user_id, self.db)
            answer = client.generate_response(
                user_input=f"问题：{query}\n\n上下文：\n{context_text}\n\n请仅基于上下文回答。",
                system_prompt="你是RAG调试助手，禁止编造。",
                db=self.db,
                model=payload.get("llm_model") or None,
                task_type="general",
            )
        generation_ms = (time.perf_counter() - generation_started) * 1000

        token_usage = {
            "input_tokens": int(max(1, len(context_text) / 4)),
            "output_tokens": int(max(0, len(answer) / 4)),
        }
        token_usage["total_tokens"] = token_usage["input_tokens"] + token_usage["output_tokens"]

        debug = retrieval_result.get("debug") or {}
        display_context, display_answer, blocked_reason = resolve_debug_display_fields(
            context_text=context_text,
            answer=answer,
            debug=debug,
        )
        return (
            "ok",
            {
                "query": query,
                "rewritten_queries": debug.get("rewrite_queries") or [],
                "raw_retrieved_chunks": debug.get("dedup_chunks") or [],
                "reranked_chunks": debug.get("rerank_top") or [],
                "final_context": display_context,
                "llm_output": display_answer,
                "generation_skipped": bool(not answer and not context_text),
                "context_blocked_reason": blocked_reason,
                "token_usage": token_usage,
                "timing_ms": {
                    "retrieval": retrieval_ms,
                    "generation": generation_ms,
                    "total": retrieval_ms + generation_ms,
                },
                "doc_hit_stats": retrieval_result.get("doc_hit_stats") or [],
                "dominance_warning": retrieval_result.get("dominance_warning"),
                "multi_doc_hint": retrieval_result.get("multi_doc_hint"),
                "retrieval_options": retrieval_result.get("retrieval_options") or {},
                "debug": debug,
            },
        )

