"""项目级 context snapshot 编排层。"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument, ProjectContextSnapshot
from modules.knowledge_base_components.context.context_snapshot_parts import (
    _build_source_breakdown,
    _query_snapshot_row,
    _save_snapshot_success,
)
from modules.knowledge_base_components.snapshot.snapshot_builder import (
    SNAPSHOT_CONFIG,
    build_corpus_hash,
    build_snapshot_text,
    collect_project_docs,
    decide_rebuild_mode,
    doc_content_hash,
    estimate_source_stats,
    mark_snapshot_failed,
    merge_incremental_snapshot,
    safe_json_loads,
)
from modules.knowledge_base_components.snapshot.snapshot_readiness import evaluate_snapshot_readiness

logger = logging.getLogger(__name__)

def enqueue_context_snapshot_rebuild_impl(
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
    force_rebuild: bool = False,
) -> dict:
    """触发快照异步重建，带 pending 防抖。"""
    snapshot, schema_compatible = _query_snapshot_row(db, project_id)
    if not schema_compatible:
        return {"queued": False, "reason": "snapshot_schema_incompatible"}
    now = datetime.utcnow()
    if snapshot and snapshot.build_status == "pending" and snapshot.updated_at:
        # 仅当 pending 已有真实构建痕迹时才防抖，避免首次入队被误判跳过。
        has_trace = bool(
            (snapshot.rebuild_reason or "").strip()
            or snapshot.last_built_at
            or snapshot.last_used_at
            or (snapshot.build_error or "").strip()
        )
        if has_trace and (
            now - snapshot.updated_at
        ).total_seconds() < SNAPSHOT_CONFIG.enqueue_cooldown_seconds:
            return {
                "queued": False,
                "reason": "already_pending",
                "cooldown_seconds": SNAPSHOT_CONFIG.enqueue_cooldown_seconds,
            }

    if not snapshot:
        snapshot = ProjectContextSnapshot(project_id=project_id, user_id=user_id, build_status="pending")
        db.add(snapshot)
    else:
        snapshot.build_status = "pending"
        snapshot.build_error = None
        snapshot.user_id = user_id
    snapshot.rebuild_reason = "manual" if force_rebuild else "incremental_merge"
    db.commit()

    try:
        from modules.orchestration.tasks import build_context_snapshot_task

        task_result = build_context_snapshot_task.delay(
            project_id=project_id,
            user_id=user_id,
            force_rebuild=force_rebuild,
        )
        # 中文注释：统一 reason 枚举，便于上层日志直接区分 queued/already_pending/enqueue_failed。
        return {"queued": True, "task_id": task_result.id, "reason": "queued"}
    except Exception as e:
        mark_snapshot_failed(snapshot, f"enqueue_failed:{e}", snapshot.rebuild_reason or "manual", db)
        return {"queued": False, "reason": "enqueue_failed", "error": str(e)}



def get_or_build_context_snapshot_impl(
    module,
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
    force_rebuild: bool = False,
    prefer_async_rebuild: bool = False,
) -> dict:
    """获取或构建快照；失败由调用方 fallback 到 RAG。"""
    build_started_ts = time.perf_counter()
    corpus = collect_project_docs(module, db, project_id, user_id)
    if not corpus:
        snapshot, schema_compatible = _query_snapshot_row(db, project_id)
        if not schema_compatible:
            snapshot = None
        readiness = evaluate_snapshot_readiness(
            snapshot=snapshot,
            current_corpus_hash="",
            current_doc_count=0,
            changed_doc_ids=[],
        )
        return {
            "success": False,
            "fallback_reason": "no_docs",
            "rebuild_reason": "no_docs",
            "snapshot_text": "",
            **readiness,
            "snapshot_version": 0,
            "snapshot_fingerprint": "",
            "build_latency_ms": 0.0,
            "prewarm_task_id": None,
        }

    source_breakdown = _build_source_breakdown(corpus, request_user_id=user_id)
    corpus_hash, current_fingerprints = build_corpus_hash(corpus)
    snapshot, schema_compatible = _query_snapshot_row(db, project_id)
    if not schema_compatible:
        return {
            "success": False,
            "fallback_reason": "snapshot_schema_incompatible",
            "rebuild_reason": "schema_incompatible",
            "snapshot_text": "",
            "snapshot_version": 0,
            "snapshot_fingerprint": "",
            "build_latency_ms": 0.0,
            "prewarm_task_id": None,
            **evaluate_snapshot_readiness(
                snapshot=None,
                current_corpus_hash=corpus_hash,
                current_doc_count=len(corpus),
                changed_doc_ids=[str(item.get("doc_id")) for item in corpus if item.get("doc_id") is not None],
            ),
        }
    if not snapshot:
        snapshot = ProjectContextSnapshot(project_id=project_id, user_id=user_id, build_status="pending")
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

    previous_fingerprints = safe_json_loads(snapshot.source_fingerprints)
    changed_doc_ids = [
        doc_id for doc_id, fp in current_fingerprints.items() if previous_fingerprints.get(doc_id) != fp
    ]
    mode = decide_rebuild_mode(snapshot, changed_doc_ids, len(corpus), force_rebuild)
    readiness = evaluate_snapshot_readiness(
        snapshot=snapshot,
        current_corpus_hash=corpus_hash,
        current_doc_count=len(corpus),
        changed_doc_ids=changed_doc_ids,
    )

    if mode == "reuse" and snapshot.corpus_hash == corpus_hash:
        snapshot.last_used_at = datetime.utcnow()
        snapshot.rebuild_reason = "reuse"
        db.commit()
        return {
            "success": True,
            "cache_hit": True,
            "rebuild_reason": "reuse",
            "snapshot_text": snapshot.snapshot_text or "",
            "corpus_hash": corpus_hash,
            "source_doc_count": len(corpus),
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
            **readiness,
            "prewarm_task_id": None,
        }

    # 在线链路优先异步预热，当前请求继续走 RAG fallback。
    if prefer_async_rebuild and SNAPSHOT_CONFIG.async_prewarm_enabled:
        queue_result = enqueue_context_snapshot_rebuild_impl(
            project_id=project_id,
            db=db,
            user_id=user_id,
            force_rebuild=(mode == "manual"),
        )
        return {
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_queued"
            if queue_result.get("queued")
            else "snapshot_async_rebuild_skip",
            "rebuild_reason": mode,
            "queue_result": queue_result,
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
            **readiness,
            "prewarm_task_id": queue_result.get("task_id"),
        }

    from core.ai.ai_client import get_client_for_user

    client = get_client_for_user(user_id, db)
    snapshot.build_status = "pending"
    snapshot.build_error = None
    snapshot.user_id = user_id
    snapshot.rebuild_reason = mode
    db.commit()

    if mode in ("full_rebuild", "manual"):
        build_result = build_snapshot_text(
            client=client,
            db=db,
            sources=corpus,
            batch_prompt="请将以下项目知识压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
            merge_prompt="请将以下分批知识摘要合并为一份项目级上下文快照，要求去重、结构清晰、覆盖关键规则。输出纯文本。",
        )
        if not build_result.get("success"):
            mark_snapshot_failed(
                snapshot,
                build_result.get("error") or "snapshot_full_rebuild_failed",
                mode,
                db,
                observability=build_result.get("build_observability"),
            )
            return {
                "success": False,
                "fallback_reason": "snapshot_full_rebuild_failed",
                "rebuild_reason": mode,
                "snapshot_version": int(snapshot.snapshot_version or 0),
                "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
                "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
            }

        snapshot_text = (build_result.get("text") or "")[: SNAPSHOT_CONFIG.max_snapshot_chars]
        build_latency_ms = max(0.0, (time.perf_counter() - build_started_ts) * 1000.0)
        _save_snapshot_success(
            snapshot=snapshot,
            snapshot_text=snapshot_text,
            corpus_hash=corpus_hash,
            current_fingerprints=current_fingerprints,
            source_doc_count=len(corpus),
            rebuild_reason=mode,
            full_rebuild=True,
            build_latency_ms=build_latency_ms,
            db=db,
        )
        build_observability = {
            **(build_result.get("build_observability") or {}),
            **source_breakdown,
        }
        logger.info(
            "snapshot build success project_id=%s mode=%s stats=%s",
            project_id,
            mode,
            json.dumps(build_observability, ensure_ascii=False),
        )
        return {
            "success": True,
            "cache_hit": False,
            "rebuild_reason": mode,
            "snapshot_text": snapshot.snapshot_text or "",
            "corpus_hash": corpus_hash,
            "source_doc_count": len(corpus),
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or build_latency_ms),
            **evaluate_snapshot_readiness(
                snapshot=snapshot,
                current_corpus_hash=corpus_hash,
                current_doc_count=len(corpus),
                changed_doc_ids=[],
            ),
            "build_observability": build_observability,
            "prewarm_task_id": None,
        }

    changed_set = set(changed_doc_ids)
    changed_items = [item for item in corpus if str(item["doc_id"]) in changed_set]
    delta_result = build_snapshot_text(
        client=client,
        db=db,
        sources=changed_items,
        batch_prompt="请将以下新增或变更知识压缩为增量摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
        merge_prompt="请将以下增量摘要合并去重为一份简洁的增量知识补充。输出纯文本。",
    )
    if not delta_result.get("success"):
        mark_snapshot_failed(
            snapshot,
            delta_result.get("error") or "snapshot_incremental_merge_failed",
            "incremental_merge",
            db,
            observability=delta_result.get("build_observability"),
        )
        return {
            "success": False,
            "fallback_reason": "snapshot_incremental_merge_failed",
            "rebuild_reason": "incremental_merge",
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
        }

    merged_text, merge_info = merge_incremental_snapshot(
        snapshot.snapshot_text or "",
        delta_result.get("text") or "",
        SNAPSHOT_CONFIG.max_snapshot_chars,
    )
    build_latency_ms = max(0.0, (time.perf_counter() - build_started_ts) * 1000.0)
    _save_snapshot_success(
        snapshot=snapshot,
        snapshot_text=merged_text,
        corpus_hash=corpus_hash,
        current_fingerprints=current_fingerprints,
        source_doc_count=len(corpus),
        rebuild_reason="incremental_merge",
        full_rebuild=False,
        build_latency_ms=build_latency_ms,
        db=db,
    )
    delta_source_breakdown = _build_source_breakdown(changed_items, request_user_id=user_id)
    delta_observability = {
        **(delta_result.get("build_observability") or {}),
        **delta_source_breakdown,
    }
    logger.info(
        "snapshot incremental success project_id=%s stats=%s merge=%s",
        project_id,
        json.dumps(delta_observability, ensure_ascii=False),
        json.dumps(merge_info, ensure_ascii=False),
    )
    return {
        "success": True,
        "cache_hit": False,
        "rebuild_reason": "incremental_merge",
        "snapshot_text": snapshot.snapshot_text or "",
        "corpus_hash": corpus_hash,
        "source_doc_count": len(corpus),
        "snapshot_version": int(snapshot.snapshot_version or 0),
        "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
        "build_latency_ms": float(snapshot.last_build_latency_ms or build_latency_ms),
        **evaluate_snapshot_readiness(
            snapshot=snapshot,
            current_corpus_hash=corpus_hash,
            current_doc_count=len(corpus),
            changed_doc_ids=[],
        ),
        "build_observability": {
            **delta_observability,
            "final_merge_mode": merge_info.get("merge_mode"),
            "merge_trimmed": bool(merge_info.get("merge_trimmed")),
        },
        "prewarm_task_id": None,
    }


def get_context_snapshot_status_impl(project_id: int, db: Session) -> dict:
    """查询快照状态，并补充输入预算相关观测字段。"""
    try:
        snapshot, schema_compatible = _query_snapshot_row(db, project_id)
    except SQLAlchemyError:
        snapshot = None
        schema_compatible = False
    docs = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.project_id == project_id)
        .order_by(KnowledgeDocument.id.asc())
        .all()
    )
    parts = [f"{doc.id}:{doc_content_hash(doc)}" for doc in docs]
    current_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest() if parts else ""
    if snapshot:
        previous_fingerprints = safe_json_loads(snapshot.source_fingerprints)
        changed_doc_ids = [f"{doc.id}" for doc in docs if previous_fingerprints.get(f"{doc.id}") != doc_content_hash(doc)]
    else:
        changed_doc_ids = [f"{doc.id}" for doc in docs]
    readiness = evaluate_snapshot_readiness(
        snapshot=snapshot,
        current_corpus_hash=current_hash,
        current_doc_count=len(docs),
        changed_doc_ids=changed_doc_ids,
    )
    estimated_stats = estimate_source_stats(docs)
    source_type_counts: dict[str, int] = {}
    source_owner_counts: dict[str, int] = {}
    requirement_like_types = {"requirement", "product_requirement", "incomplete"}
    requirement_like_count = 0
    non_requirement_examples: list[str] = []
    for doc in docs:
        doc_type = str(doc.doc_type or "unknown")
        source_type_counts[doc_type] = int(source_type_counts.get(doc_type, 0)) + 1
        owner_key = "none" if doc.user_id is None else str(doc.user_id)
        source_owner_counts[owner_key] = int(source_owner_counts.get(owner_key, 0)) + 1
        if doc_type in requirement_like_types:
            requirement_like_count += 1
        elif len(non_requirement_examples) < 8:
            non_requirement_examples.append(f"{doc.id}:{doc.filename}:{doc_type}")

    if not snapshot:
        return {
            "exists": False,
            **readiness,
            "project_id": project_id,
            "current_corpus_hash": current_hash,
            "snapshot_corpus_hash": "",
            "corpus_hash": current_hash,
            "snapshot_hash": "",
            "snapshot_version": 0,
            "snapshot_fingerprint": "",
            "build_latency_ms": 0.0,
            "source_doc_count": 0,
            "last_built_at": None,
            "last_used_at": None,
            "build_error": None,
            "rebuild_reason": None,
            "incremental_merge_count": 0,
            "is_stale": True,
            "prewarm_task_id": None,
            "build_status": "not_exists",
            "input_soft_limit": SNAPSHOT_CONFIG.input_soft_limit,
            "single_doc_limit": SNAPSHOT_CONFIG.single_doc_max_chars,
            "batch_max_docs": SNAPSHOT_CONFIG.batch_max_docs,
            "final_merge_limit": SNAPSHOT_CONFIG.final_merge_limit,
            "source_doc_type_counts": source_type_counts,
            "source_owner_user_counts": source_owner_counts,
            "source_requirement_like_doc_count": requirement_like_count,
            "source_non_requirement_doc_count": max(0, len(docs) - requirement_like_count),
            "source_non_requirement_examples": non_requirement_examples,
            "schema_compatible": bool(schema_compatible),
            **estimated_stats,
        }

    return {
        "exists": True,
        **readiness,
        "project_id": project_id,
        "current_corpus_hash": current_hash,
        "snapshot_corpus_hash": snapshot.corpus_hash,
        # 中文注释：保留旧字段，兼容老调用方。
        "corpus_hash": current_hash,
        "snapshot_hash": snapshot.corpus_hash,
        "snapshot_version": int(snapshot.snapshot_version or 0),
        "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
        "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
        "source_doc_count": int(snapshot.source_doc_count or 0),
        "last_built_at": snapshot.last_built_at.isoformat() if snapshot.last_built_at else None,
        "last_used_at": snapshot.last_used_at.isoformat() if snapshot.last_used_at else None,
        "build_error": snapshot.build_error,
        "rebuild_reason": snapshot.rebuild_reason,
        "incremental_merge_count": int(snapshot.incremental_merge_count or 0),
        "is_stale": bool(readiness.get("snapshot_status") == "stale"),
        "prewarm_task_id": None,
        "build_status": snapshot.build_status,
        "input_soft_limit": SNAPSHOT_CONFIG.input_soft_limit,
        "single_doc_limit": SNAPSHOT_CONFIG.single_doc_max_chars,
        "batch_max_docs": SNAPSHOT_CONFIG.batch_max_docs,
        "final_merge_limit": SNAPSHOT_CONFIG.final_merge_limit,
        "source_doc_type_counts": source_type_counts,
        "source_owner_user_counts": source_owner_counts,
        "source_requirement_like_doc_count": requirement_like_count,
        "source_non_requirement_doc_count": max(0, len(docs) - requirement_like_count),
        "source_non_requirement_examples": non_requirement_examples,
        "schema_compatible": bool(schema_compatible),
        **estimated_stats,
    }


