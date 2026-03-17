"""项目级上下文快照构建与复用组件。"""
from __future__ import annotations
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from core.models import KnowledgeDocument, ProjectContextSnapshot
def _env_bool(key: str, default: bool) -> bool:
    """环境变量布尔解析。"""
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}
@dataclass(frozen=True)
class SnapshotBuildConfig:
    """快照阈值配置。"""
    max_docs: int = max(1, int(os.getenv("RAG_SNAPSHOT_MAX_DOCS", "200")))
    max_snapshot_chars: int = max(4000, int(os.getenv("RAG_SNAPSHOT_MAX_CHARS", "120000")))
    incremental_doc_threshold: int = max(1, int(os.getenv("RAG_SNAPSHOT_INCREMENTAL_DOC_THRESHOLD", "8")))
    incremental_ratio_threshold: float = float(os.getenv("RAG_SNAPSHOT_INCREMENTAL_RATIO_THRESHOLD", "0.30"))
    full_rebuild_hours: int = max(1, int(os.getenv("RAG_SNAPSHOT_FULL_REBUILD_HOURS", "24")))
    max_incremental_merges: int = max(1, int(os.getenv("RAG_SNAPSHOT_MAX_INCREMENTAL_MERGES", "4")))
    # 异步预热：在线链路可只入队重建，本次先走 RAG fallback。
    async_prewarm_enabled: bool = _env_bool("RAG_SNAPSHOT_ASYNC_PREWARM", True)
    # 防抖：pending 短窗口内不重复入队。
    enqueue_cooldown_seconds: int = max(5, int(os.getenv("RAG_SNAPSHOT_ENQUEUE_COOLDOWN_SECONDS", "30")))
SNAPSHOT_CONFIG = SnapshotBuildConfig()
def _safe_json_loads(raw: Optional[str]) -> dict:
    """容错解析 JSON。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}
def _doc_content_hash(doc: KnowledgeDocument) -> str:
    """取文档指纹：优先 content_hash。"""
    if doc.content_hash:
        return str(doc.content_hash)
    return hashlib.sha256((doc.content or "").encode("utf-8")).hexdigest()
def _collect_project_docs(module, db: Session, project_id: int, user_id: Optional[int]) -> list[dict]:
    """收集项目语料，优先复用文档 summary。"""
    docs = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.project_id == project_id)
        .order_by(KnowledgeDocument.created_at.asc(), KnowledgeDocument.id.asc())
        .limit(SNAPSHOT_CONFIG.max_docs)
        .all()
    )
    corpus: list[dict] = []
    for doc in docs:
        summary_or_content = module._ensure_summary(doc, db, user_id)
        text = (summary_or_content or "").strip()
        if not text:
            continue
        corpus.append(
            {
                "doc_id": int(doc.id),
                "filename": doc.filename or f"doc_{doc.id}",
                "text": text,
                "fingerprint": _doc_content_hash(doc),
            }
        )
    return corpus
def _build_corpus_hash(corpus: list[dict]) -> tuple[str, dict[str, str]]:
    """构建语料哈希与指纹映射。"""
    fingerprints = {str(item["doc_id"]): str(item["fingerprint"]) for item in corpus}
    parts = [f'{item["doc_id"]}:{item["fingerprint"]}' for item in corpus]
    corpus_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return corpus_hash, fingerprints
def _compress_with_client(client, text: str, prompt: str, db: Session) -> tuple[bool, str, str]:
    """统一压缩调用，返回(成功, 文本, 错误)。"""
    try:
        result = client.compress_context(text, prompt=prompt, db=db)
        if result and isinstance(result, str) and not result.startswith("Error") and not result.startswith("Exception"):
            return True, result, ""
        return False, "", (result or "compression_returned_empty")
    except Exception as e:
        return False, "", str(e)
def _decide_rebuild_mode(
    snapshot: Optional[ProjectContextSnapshot],
    changed_doc_ids: list[str],
    current_doc_count: int,
    force_rebuild: bool,
) -> str:
    """返回 reuse/incremental_merge/full_rebuild/manual。"""
    if force_rebuild:
        return "manual"
    if not snapshot or snapshot.build_status != "success" or not (snapshot.snapshot_text or "").strip():
        return "full_rebuild"
    if not changed_doc_ids:
        return "reuse"
    changed_count = len(changed_doc_ids)
    changed_ratio = changed_count / max(1, current_doc_count)
    if changed_count >= SNAPSHOT_CONFIG.incremental_doc_threshold:
        return "full_rebuild"
    if changed_ratio >= SNAPSHOT_CONFIG.incremental_ratio_threshold:
        return "full_rebuild"
    if (snapshot.incremental_merge_count or 0) >= SNAPSHOT_CONFIG.max_incremental_merges:
        return "full_rebuild"
    if snapshot.last_full_built_at and datetime.utcnow() - snapshot.last_full_built_at >= timedelta(
        hours=SNAPSHOT_CONFIG.full_rebuild_hours
    ):
        return "full_rebuild"
    return "incremental_merge"
def _mark_snapshot_failed(snapshot: ProjectContextSnapshot, build_error: str, rebuild_reason: str, db: Session) -> None:
    """失败统一落库，保证失败可见。"""
    snapshot.build_status = "failed"
    snapshot.build_error = (build_error or "")[:2000]
    snapshot.rebuild_reason = rebuild_reason
    db.commit()
def enqueue_context_snapshot_rebuild_impl(
    project_id: int, db: Session, user_id: Optional[int] = None, force_rebuild: bool = False
) -> dict:
    """触发快照异步重建；支持 pending 防抖。"""
    snapshot = db.query(ProjectContextSnapshot).filter(ProjectContextSnapshot.project_id == project_id).first()
    now = datetime.utcnow()
    if snapshot and snapshot.build_status == "pending" and snapshot.updated_at:
        if (now - snapshot.updated_at).total_seconds() < SNAPSHOT_CONFIG.enqueue_cooldown_seconds:
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
        from modules.tasks import build_context_snapshot_task
        task_result = build_context_snapshot_task.delay(project_id=project_id, user_id=user_id, force_rebuild=force_rebuild)
        return {"queued": True, "task_id": task_result.id, "reason": "enqueued"}
    except Exception as e:
        _mark_snapshot_failed(snapshot, f"enqueue_failed:{e}", snapshot.rebuild_reason or "manual", db)
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
    corpus = _collect_project_docs(module, db, project_id, user_id)
    if not corpus:
        return {"success": False, "fallback_reason": "no_docs", "rebuild_reason": "no_docs", "snapshot_text": ""}
    corpus_hash, current_fingerprints = _build_corpus_hash(corpus)
    snapshot = db.query(ProjectContextSnapshot).filter(ProjectContextSnapshot.project_id == project_id).first()
    if not snapshot:
        snapshot = ProjectContextSnapshot(project_id=project_id, user_id=user_id, build_status="pending")
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
    previous_fingerprints = _safe_json_loads(snapshot.source_fingerprints)
    changed_doc_ids = [doc_id for doc_id, fp in current_fingerprints.items() if previous_fingerprints.get(doc_id) != fp]
    mode = _decide_rebuild_mode(snapshot, changed_doc_ids, len(corpus), force_rebuild)
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
            "snapshot_status": snapshot.build_status,
        }
    # 在线请求启用异步预热时，不在请求线程压缩，直接入队并让调用方走 RAG。
    if prefer_async_rebuild and SNAPSHOT_CONFIG.async_prewarm_enabled:
        queue_result = enqueue_context_snapshot_rebuild_impl(
            project_id=project_id, db=db, user_id=user_id, force_rebuild=(mode == "manual")
        )
        return {
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_queued" if queue_result.get("queued") else "snapshot_async_rebuild_skip",
            "rebuild_reason": mode,
            "queue_result": queue_result,
            "snapshot_status": snapshot.build_status,
        }
    from core.ai_client import get_client_for_user
    client = get_client_for_user(user_id, db)
    snapshot.build_status = "pending"
    snapshot.build_error = None
    snapshot.user_id = user_id
    snapshot.rebuild_reason = mode
    db.commit()
    if mode in ("full_rebuild", "manual"):
        full_context = "\n\n".join([f"--- Document: {item['filename']} ---\n{item['text']}" for item in corpus])
        ok, compressed, err = _compress_with_client(
            client,
            full_context,
            prompt="请将以下项目知识压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
            db=db,
        )
        if not ok:
            _mark_snapshot_failed(snapshot, err, mode, db)
            return {"success": False, "fallback_reason": "snapshot_full_rebuild_failed", "rebuild_reason": mode}
        snapshot.snapshot_text = compressed[: SNAPSHOT_CONFIG.max_snapshot_chars]
        snapshot.corpus_hash = corpus_hash
        snapshot.source_doc_count = len(corpus)
        snapshot.source_fingerprints = json.dumps(current_fingerprints, ensure_ascii=False)
        snapshot.build_status = "success"
        snapshot.rebuild_reason = mode
        snapshot.last_built_at = datetime.utcnow()
        snapshot.last_full_built_at = datetime.utcnow()
        snapshot.last_used_at = datetime.utcnow()
        snapshot.incremental_merge_count = 0
        db.commit()
        return {
            "success": True,
            "cache_hit": False,
            "rebuild_reason": mode,
            "snapshot_text": snapshot.snapshot_text or "",
            "corpus_hash": corpus_hash,
            "source_doc_count": len(corpus),
            "snapshot_status": snapshot.build_status,
        }
    changed_items = [item for item in corpus if str(item["doc_id"]) in set(changed_doc_ids)]
    delta_context = "\n\n".join([f"--- Delta Document: {item['filename']} ---\n{item['text']}" for item in changed_items])
    ok, delta_summary, err = _compress_with_client(
        client,
        delta_context,
        prompt="请将以下新增或变更知识压缩为增量摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
        db=db,
    )
    if not ok:
        _mark_snapshot_failed(snapshot, err, "incremental_merge", db)
        return {"success": False, "fallback_reason": "snapshot_incremental_merge_failed", "rebuild_reason": "incremental_merge"}
    # 受控合并：旧快照+增量摘要，不对旧快照再次摘要，避免 summary-of-summary 漂移。
    merged = (snapshot.snapshot_text or "").strip()
    if delta_summary and delta_summary not in merged:
        merged = f"{merged}\n\n[增量知识补充]\n{delta_summary}".strip()
    if len(merged) > SNAPSHOT_CONFIG.max_snapshot_chars:
        _mark_snapshot_failed(snapshot, "merged_snapshot_too_large", "incremental_merge", db)
        return {"success": False, "fallback_reason": "snapshot_too_large_need_full_rebuild", "rebuild_reason": "incremental_merge"}
    snapshot.snapshot_text = merged
    snapshot.corpus_hash = corpus_hash
    snapshot.source_doc_count = len(corpus)
    snapshot.source_fingerprints = json.dumps(current_fingerprints, ensure_ascii=False)
    snapshot.build_status = "success"
    snapshot.rebuild_reason = "incremental_merge"
    snapshot.last_built_at = datetime.utcnow()
    snapshot.last_used_at = datetime.utcnow()
    snapshot.incremental_merge_count = int(snapshot.incremental_merge_count or 0) + 1
    db.commit()
    return {
        "success": True,
        "cache_hit": False,
        "rebuild_reason": "incremental_merge",
        "snapshot_text": snapshot.snapshot_text or "",
        "corpus_hash": corpus_hash,
        "source_doc_count": len(corpus),
        "snapshot_status": snapshot.build_status,
    }
def get_context_snapshot_status_impl(project_id: int, db: Session) -> dict:
    """查询快照状态，供前端/调试接口使用。"""
    snapshot = db.query(ProjectContextSnapshot).filter(ProjectContextSnapshot.project_id == project_id).first()
    if not snapshot:
        return {"exists": False, "snapshot_status": "missing"}
    docs = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.project_id == project_id)
        .order_by(KnowledgeDocument.id.asc())
        .all()
    )
    parts = [f"{doc.id}:{_doc_content_hash(doc)}" for doc in docs]
    current_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest() if parts else ""
    return {
        "exists": True,
        "snapshot_status": snapshot.build_status,
        "project_id": project_id,
        "corpus_hash": current_hash,
        "snapshot_hash": snapshot.corpus_hash,
        "source_doc_count": int(snapshot.source_doc_count or 0),
        "last_built_at": snapshot.last_built_at.isoformat() if snapshot.last_built_at else None,
        "last_used_at": snapshot.last_used_at.isoformat() if snapshot.last_used_at else None,
        "build_error": snapshot.build_error,
        "rebuild_reason": snapshot.rebuild_reason,
        "incremental_merge_count": int(snapshot.incremental_merge_count or 0),
        "is_stale": bool(snapshot.corpus_hash != current_hash),
    }
