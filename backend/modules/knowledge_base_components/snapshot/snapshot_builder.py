"""context snapshot 构建基础能力：配置、预算、状态落库。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument, ProjectContextSnapshot
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.knowledge_base_components.snapshot.snapshot_chunking import (
    build_snapshot_text_with_budget,
    trim_text_head,
)


def _env_bool(key: str, default: bool) -> bool:
    """环境变量布尔解析。"""
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SnapshotBuildConfig:
    """快照构建阈值。"""

    max_docs: int = max(1, int(os.getenv("RAG_SNAPSHOT_MAX_DOCS", "200")))
    max_snapshot_chars: int = max(4000, int(os.getenv("RAG_SNAPSHOT_MAX_CHARS", "120000")))
    incremental_doc_threshold: int = max(
        1, int(os.getenv("RAG_SNAPSHOT_INCREMENTAL_DOC_THRESHOLD", "8"))
    )
    incremental_ratio_threshold: float = float(
        os.getenv("RAG_SNAPSHOT_INCREMENTAL_RATIO_THRESHOLD", "0.30")
    )
    full_rebuild_hours: int = max(1, int(os.getenv("RAG_SNAPSHOT_FULL_REBUILD_HOURS", "24")))
    max_incremental_merges: int = max(
        1, int(os.getenv("RAG_SNAPSHOT_MAX_INCREMENTAL_MERGES", "4"))
    )
    # 新增：输入保护与分段参数。
    input_soft_limit: int = max(4000, int(os.getenv("RAG_SNAPSHOT_INPUT_SOFT_LIMIT", "25000")))
    single_doc_max_chars: int = max(
        800, int(os.getenv("RAG_SNAPSHOT_SINGLE_DOC_MAX_CHARS", "12000"))
    )
    batch_max_docs: int = max(1, int(os.getenv("RAG_SNAPSHOT_BATCH_MAX_DOCS", "12")))
    final_merge_limit: int = max(
        4000, int(os.getenv("RAG_SNAPSHOT_FINAL_MERGE_LIMIT", "24000"))
    )
    async_prewarm_enabled: bool = _env_bool("RAG_SNAPSHOT_ASYNC_PREWARM", True)
    enqueue_cooldown_seconds: int = max(
        5, int(os.getenv("RAG_SNAPSHOT_ENQUEUE_COOLDOWN_SECONDS", "30"))
    )


SNAPSHOT_CONFIG = SnapshotBuildConfig()


def safe_json_loads(raw: Optional[str]) -> dict:
    """容错 JSON 解析。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def doc_content_hash(doc: KnowledgeDocument) -> str:
    """文档指纹：优先 content_hash。"""
    if doc.content_hash:
        return str(doc.content_hash)
    return hashlib.sha256((doc.content or "").encode("utf-8")).hexdigest()


def collect_project_docs(module, db: Session, project_id: int, user_id: Optional[int]) -> list[dict]:
    """收集语料，优先 summary。"""
    docs = KnowledgeDocumentRepository(db).list_project_docs_for_snapshot(
        project_id=project_id,
        max_docs=SNAPSHOT_CONFIG.max_docs,
    )
    corpus: list[dict] = []
    for doc in docs:
        text = (module._ensure_summary(doc, db, user_id) or "").strip()
        if not text:
            continue
        corpus.append(
            {
                "doc_id": int(doc.id),
                "filename": doc.filename or f"doc_{doc.id}",
                "text": text,
                "fingerprint": doc_content_hash(doc),
                # 中文注释：补充来源元信息，便于构建日志解释“有效文档数”由哪些类型构成。
                "doc_type": str(doc.doc_type or "unknown"),
                "owner_user_id": doc.user_id,
            }
        )
    return corpus


def build_corpus_hash(corpus: list[dict]) -> tuple[str, dict[str, str]]:
    """构建语料哈希 + 指纹映射。"""
    fingerprints = {str(item["doc_id"]): str(item["fingerprint"]) for item in corpus}
    parts = [f'{item["doc_id"]}:{item["fingerprint"]}' for item in corpus]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest(), fingerprints


def compress_with_client(client, text: str, prompt: str, db: Session) -> tuple[bool, str, str]:
    """统一压缩调用。"""
    try:
        result = client.compress_context(text, prompt=prompt, db=db)
        if (
            result
            and isinstance(result, str)
            and not result.startswith("Error")
            and not result.startswith("Exception")
        ):
            return True, result, ""
        return False, "", (result or "compression_returned_empty")
    except Exception as e:
        return False, "", str(e)


def build_snapshot_text(client, db: Session, sources: list[dict], batch_prompt: str, merge_prompt: str) -> dict:
    """调用分段构建工具，输出文本与可观测指标。"""

    def _compress(text: str, prompt: str) -> tuple[bool, str, str]:
        return compress_with_client(client, text, prompt, db)

    return build_snapshot_text_with_budget(
        sources=sources,
        compress_fn=_compress,
        batch_prompt=batch_prompt,
        merge_prompt=merge_prompt,
        input_soft_limit=SNAPSHOT_CONFIG.input_soft_limit,
        single_doc_limit=SNAPSHOT_CONFIG.single_doc_max_chars,
        batch_max_docs=SNAPSHOT_CONFIG.batch_max_docs,
        final_merge_limit=SNAPSHOT_CONFIG.final_merge_limit,
    )


def merge_incremental_snapshot(existing: str, delta: str, max_chars: int) -> tuple[str, dict]:
    """受控合并旧快照与增量摘要，避免超长失败。"""
    old_text, delta_text = (existing or "").strip(), (delta or "").strip()
    if not old_text:
        merged, trimmed = trim_text_head(delta_text, max_chars)
        return merged, {"merge_mode": "delta_only", "merge_trimmed": bool(trimmed)}
    if not delta_text:
        merged, trimmed = trim_text_head(old_text, max_chars)
        return merged, {"merge_mode": "old_only", "merge_trimmed": bool(trimmed)}
    if delta_text in old_text:
        merged, trimmed = trim_text_head(old_text, max_chars)
        return merged, {"merge_mode": "delta_duplicate_skip", "merge_trimmed": bool(trimmed)}

    merged = f"{old_text}\n\n[增量知识补充]\n{delta_text}".strip()
    if len(merged) <= max_chars:
        return merged, {"merge_mode": "append", "merge_trimmed": False}

    # 超长时按 7:3 预算保留旧快照和新增信息。
    old_budget = max(1000, int(max_chars * 0.7))
    delta_budget = max(600, max_chars - old_budget - 20)
    old_cut, _ = trim_text_head(old_text, old_budget)
    delta_cut, _ = trim_text_head(delta_text, delta_budget)
    merged = f"{old_cut}\n\n[增量知识补充]\n{delta_cut}".strip()
    merged, _ = trim_text_head(merged, max_chars)
    return merged, {"merge_mode": "append_trimmed", "merge_trimmed": True}


def decide_rebuild_mode(
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


def mark_snapshot_failed(
    snapshot: ProjectContextSnapshot,
    build_error: str,
    rebuild_reason: str,
    db: Session,
    observability: Optional[dict] = None,
) -> None:
    """失败统一落库，写入结构化错误信息。"""
    payload = {"error": str(build_error or "unknown"), "observability": observability or {}}
    try:
        serialized = json.dumps(payload, ensure_ascii=False)
    except Exception:
        serialized = str(build_error or "unknown")
    snapshot.build_status = "failed"
    snapshot.build_error = serialized[:2000]
    snapshot.rebuild_reason = rebuild_reason
    db.commit()


def estimate_source_stats(docs: list[KnowledgeDocument]) -> dict:
    """估算状态接口展示的输入规模。"""
    estimated_total_chars = 0
    estimated_effective_docs = 0
    estimated_truncated_docs = 0
    for doc in docs:
        text = (doc.summary or doc.content or "").strip()
        if not text:
            continue
        estimated_effective_docs += 1
        estimated_total_chars += len(text)
        if len(text) > SNAPSHOT_CONFIG.single_doc_max_chars:
            estimated_truncated_docs += 1
    return {
        "estimated_source_total_chars": estimated_total_chars,
        "estimated_source_effective_doc_count": estimated_effective_docs,
        "estimated_truncated_doc_count": estimated_truncated_docs,
    }
