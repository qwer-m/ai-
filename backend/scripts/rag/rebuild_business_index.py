"""
知识库历史文档批量重建（业务分块版）。

用途：
1. 对历史 knowledge_documents 重新执行业务分块；
2. 重写 Chroma metadata（doc_type/module/biz_key/chunk_index/chunk_total 等）；
3. 支持按 project/doc_type/doc_id 选择范围；
4. 支持 dry-run 与随机抽查报告。

示例：
python scripts/rag/rebuild_business_index.py --dry-run
python scripts/rag/rebuild_business_index.py --apply --project-id 2 --project-id 9
python scripts/rag/rebuild_business_index.py --apply --doc-type requirement,test_case --sample-per-project 3
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

# 中文注释：确保从任意工作目录执行时都能导入 backend 包。
CURRENT_FILE = Path(__file__).resolve()
BACKEND_ROOT = CURRENT_FILE.parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.cache_layer.chroma_client import chroma_client
from core.db.database import SessionLocal
from core.db.models import KnowledgeDocument
from core.processing.biz_key_extractor import extract_biz_key
from core.processing.business_chunking import BusinessChunkerDispatcher, Chunk
from modules.knowledge_base_components.document.document_ops import INDEXABLE_DOC_TYPES


def _parse_csv_list(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _normalize_doc_type(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    if key in {"testcase", "test_case"}:
        return "test_case"
    return key


def _build_doc_type_filter(raw_doc_types: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_doc_types:
        normalized = _normalize_doc_type(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _to_chroma_chunk_payloads(
    chunks: list[Chunk],
    *,
    default_module: str | None,
    default_biz_key: str,
) -> list[dict]:
    payloads: list[dict] = []
    for item in chunks:
        chunk_text = str(getattr(item, "text", "") or "").strip()
        if not chunk_text:
            continue
        module_value = str(getattr(item, "module", "") or "").strip() or default_module
        biz_key_value = str(getattr(item, "biz_key", "") or "").strip() or default_biz_key
        requirement_id = str(getattr(item, "requirement_id", "") or "").strip() or None
        test_case_id = str(getattr(item, "test_case_id", "") or "").strip() or None
        related_ids: list[str] = []
        if requirement_id:
            related_ids.append(requirement_id)
        if test_case_id:
            related_ids.append(test_case_id)
        payloads.append(
            {
                "chunk_text": chunk_text,
                "metadata": {
                    "module": module_value,
                    "biz_key": biz_key_value,
                    "requirement_id": requirement_id,
                    "test_case_id": test_case_id,
                    "related_ids": related_ids,
                },
            }
        )
    return payloads


def _chunk_document(dispatcher: BusinessChunkerDispatcher, doc: KnowledgeDocument, text: str) -> tuple[list[dict], str | None, str]:
    raw_chunks = dispatcher.chunk(str(doc.doc_type or ""), text)
    if not raw_chunks:
        raw_chunks = [Chunk(text=text)]

    module_hint = next((str(c.module).strip() for c in raw_chunks if getattr(c, "module", None)), None)
    module_hint = module_hint or None
    biz_key = extract_biz_key(text, module_hint or "")
    payloads = _to_chroma_chunk_payloads(
        raw_chunks,
        default_module=module_hint,
        default_biz_key=biz_key,
    )
    return payloads, module_hint, biz_key


def _base_metadata(
    *,
    doc: KnowledgeDocument,
    module_hint: str | None,
    biz_key: str,
    is_summary: bool,
) -> dict:
    return {
        "project_id": doc.project_id,
        "doc_id": doc.id,
        "doc_type": doc.doc_type,
        "module": module_hint,
        "biz_key": biz_key,
        "requirement_id": None,
        "test_case_id": None,
        "source_doc_name": doc.filename,
        "filename": f"{doc.filename} (Summary)" if is_summary else doc.filename,
        "user_id": doc.user_id,
        "is_summary": bool(is_summary),
    }


def _iter_documents(
    db: Session,
    *,
    project_ids: list[int],
    doc_ids: list[int],
    doc_types: list[str],
    limit: int,
    include_non_indexable: bool,
) -> list[KnowledgeDocument]:
    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.content.isnot(None))
    if project_ids:
        query = query.filter(KnowledgeDocument.project_id.in_(project_ids))
    if doc_ids:
        query = query.filter(KnowledgeDocument.id.in_(doc_ids))
    if doc_types:
        query = query.filter(KnowledgeDocument.doc_type.in_(doc_types))
    if not include_non_indexable:
        query = query.filter(KnowledgeDocument.doc_type.in_(INDEXABLE_DOC_TYPES))
    return query.order_by(KnowledgeDocument.project_id.asc(), KnowledgeDocument.id.asc()).limit(max(1, int(limit))).all()


def rebuild_documents(
    *,
    db: Session,
    docs: list[KnowledgeDocument],
    apply_changes: bool,
    rebuild_summary: bool,
) -> dict:
    dispatcher = BusinessChunkerDispatcher()
    stats = {
        "total_docs": len(docs),
        "processed_docs": 0,
        "skipped_empty": 0,
        "failed_docs": 0,
        "raw_chunk_total": 0,
        "summary_chunk_total": 0,
        "errors": [],
        "per_project_doc_count": defaultdict(int),
        "per_project_chunk_count": defaultdict(int),
    }

    for doc in docs:
        project_id = int(doc.project_id or 0)
        content = str(doc.content or "").strip()
        if not content:
            stats["skipped_empty"] += 1
            continue

        try:
            raw_chunks, module_hint, biz_key = _chunk_document(dispatcher, doc, content)
            stats["raw_chunk_total"] += len(raw_chunks)
            stats["per_project_doc_count"][project_id] += 1
            stats["per_project_chunk_count"][project_id] += len(raw_chunks)

            summary = str(doc.summary or "").strip()
            has_summary = bool(summary and summary != content and rebuild_summary)
            summary_chunks: list[dict] = []
            if has_summary:
                summary_chunks, _, _ = _chunk_document(dispatcher, doc, summary)
                stats["summary_chunk_total"] += len(summary_chunks)

            if apply_changes:
                chroma_client.delete_document(str(doc.id), raise_on_error=True)
                chroma_client.delete_document(f"{doc.id}_summary", raise_on_error=True)

                chroma_client.add_document(
                    doc_id=str(doc.id),
                    content=content,
                    metadata=_base_metadata(
                        doc=doc,
                        module_hint=module_hint,
                        biz_key=biz_key,
                        is_summary=False,
                    ),
                    chunks=raw_chunks,
                    raise_on_error=True,
                )

                if has_summary:
                    chroma_client.add_document(
                        doc_id=f"{doc.id}_summary",
                        content=summary,
                        metadata=_base_metadata(
                            doc=doc,
                            module_hint=module_hint,
                            biz_key=biz_key,
                            is_summary=True,
                        ),
                        chunks=summary_chunks,
                        raise_on_error=True,
                    )

            stats["processed_docs"] += 1
        except Exception as e:
            stats["failed_docs"] += 1
            stats["errors"].append({"doc_id": doc.id, "filename": doc.filename, "error": str(e)})

    return stats


def run_sample_check(*, db: Session, docs: list[KnowledgeDocument], sample_per_project: int) -> dict:
    """随机抽查：每个项目抽样若干文档，验证 chunk 拆分数量。"""
    dispatcher = BusinessChunkerDispatcher()
    by_project: dict[int, list[KnowledgeDocument]] = defaultdict(list)
    for doc in docs:
        by_project[int(doc.project_id or 0)].append(doc)

    random.seed(20260331)
    report = {"project_count": len(by_project), "samples": []}
    for project_id in sorted(by_project.keys()):
        doc_pool = by_project[project_id]
        sampled = random.sample(doc_pool, min(max(1, int(sample_per_project)), len(doc_pool)))
        for doc in sampled:
            content = str(doc.content or "").strip()
            chunks = dispatcher.chunk(str(doc.doc_type or ""), content) if content else []
            lengths = [len(str(item.text or "").strip()) for item in chunks]
            report["samples"].append(
                {
                    "project_id": project_id,
                    "doc_id": int(doc.id),
                    "doc_type": str(doc.doc_type or ""),
                    "filename": str(doc.filename or ""),
                    "chunk_count": len(chunks),
                    "chunk_lengths_head": lengths[:8],
                }
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="批量重建知识库业务分块索引")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认仅 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="仅计算不写入（默认行为）")
    parser.add_argument("--project-id", action="append", type=int, default=[], help="可重复指定项目ID")
    parser.add_argument("--doc-id", action="append", type=int, default=[], help="可重复指定文档ID")
    parser.add_argument("--doc-type", type=str, default="", help="逗号分隔，例如 requirement,test_case")
    parser.add_argument("--limit", type=int, default=10000, help="最多处理文档数")
    parser.add_argument("--no-summary", action="store_true", help="不重建 summary 索引")
    parser.add_argument("--include-non-indexable", action="store_true", help="包含非 INDEXABLE_DOC_TYPES 文档")
    parser.add_argument("--sample-per-project", type=int, default=2, help="每个项目随机抽查文档数")
    args = parser.parse_args()

    apply_changes = bool(args.apply)
    if args.dry_run:
        apply_changes = False

    doc_types = _build_doc_type_filter(_parse_csv_list(args.doc_type))
    db = SessionLocal()
    try:
        docs = _iter_documents(
            db,
            project_ids=list(args.project_id or []),
            doc_ids=list(args.doc_id or []),
            doc_types=doc_types,
            limit=int(args.limit),
            include_non_indexable=bool(args.include_non_indexable),
        )
        print(f"[INFO] 待处理文档数: {len(docs)} apply={apply_changes}")
        stats = rebuild_documents(
            db=db,
            docs=docs,
            apply_changes=apply_changes,
            rebuild_summary=(not bool(args.no_summary)),
        )
        print(
            "[SUMMARY] processed={processed_docs}/{total_docs} failed={failed_docs} "
            "raw_chunks={raw_chunk_total} summary_chunks={summary_chunk_total}".format(**stats)
        )
        print("[PROJECT_BREAKDOWN]")
        for project_id in sorted(stats["per_project_doc_count"].keys()):
            print(
                f"  - project={project_id} docs={stats['per_project_doc_count'][project_id]} "
                f"raw_chunks={stats['per_project_chunk_count'][project_id]}"
            )

        if stats["errors"]:
            print("[ERROR_SAMPLES]")
            for item in stats["errors"][:20]:
                print(f"  - doc_id={item['doc_id']} filename={item['filename']} err={item['error']}")

        sample_report = run_sample_check(db=db, docs=docs, sample_per_project=int(args.sample_per_project))
        print(f"[SAMPLE_CHECK] project_count={sample_report['project_count']} sample_count={len(sample_report['samples'])}")
        for sample in sample_report["samples"][:50]:
            print(
                f"  - project={sample['project_id']} doc_id={sample['doc_id']} doc_type={sample['doc_type']} "
                f"chunks={sample['chunk_count']} lens={sample['chunk_lengths_head']} filename={sample['filename']}"
            )

    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
