"""
知识库模块门面（Knowledge Base Facade）。

职责：
1. 对外暴露知识库相关能力，保持路由层调用方式稳定。
2. 把具体实现拆分到 knowledge_base_components，降低单文件复杂度。
3. 兼容历史同步流程，同时支持新增的离线解析入队流程。
"""

from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.context.context_ops import (
    get_all_context_impl,
    get_relevant_context_impl,
)
from modules.knowledge_base_components.context.context_snapshot import (
    enqueue_context_snapshot_rebuild_impl,
    get_context_snapshot_status_impl,
    get_or_build_context_snapshot_impl,
)
from modules.knowledge_base_components.document.document_ops import (
    add_document_impl,
    delete_document_impl,
    move_document_impl,
    reorder_documents_impl,
    update_document_impl,
)
from modules.knowledge_base_components.document.offline_parse import (
    bind_parse_task_impl,
    cleanup_offline_file,
    create_pending_document_impl,
    mark_parse_failed_impl,
    mark_parse_retry_impl,
    parse_document_offline_impl,
    queue_document_parse_impl,
    save_upload_file_for_offline_parse,
)


class KnowledgeBaseModule:
    """知识库统一门面。"""

    def _ensure_summary(
        self, doc: KnowledgeDocument, db: Session, user_id: Optional[int] = None
    ) -> str:
        """
        为长文档生成可复用摘要。

        业务目的：在不损失关键信息的前提下，降低后续检索和上下文拼接成本。
        """
        if not doc:
            return ""
        if doc.summary and str(doc.summary).strip():
            return doc.summary

        content = doc.content or ""
        if len(content) < 12000:
            return content

        try:
            from core.ai.ai_client import get_client_for_user

            client = get_client_for_user(user_id, db)
            summary = client.compress_context(
                content,
                prompt="请将以下文档压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
                db=db,
            )
            if (
                summary
                and isinstance(summary, str)
                and not summary.startswith("Error")
                and not summary.startswith("Exception")
            ):
                doc.summary = summary
                db.commit()
                db.refresh(doc)
                return summary
        except Exception:
            # 摘要失败时回退到原文，不阻断主流程。
            pass

        return content

    def calculate_hash(self, content: str) -> str:
        """计算内容哈希，用于去重判定。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def reindex_project_specific_ids(self, doc_type: str, project_id: int, db: Session):
        """
        维护项目内同类型文档的连续编号。

        该编号用于业务展示，不替代全局主键。
        """
        remaining_docs = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.doc_type == doc_type,
                KnowledgeDocument.project_id == project_id,
            )
            .order_by(KnowledgeDocument.created_at.asc())
            .all()
        )

        for idx, remaining_doc in enumerate(remaining_docs, 1):
            remaining_doc.project_specific_id = idx

        db.commit()
        return len(remaining_docs)

    def check_duplicate(self, content: str, db: Session) -> bool:
        """按内容哈希检查是否存在重复文档。"""
        content_hash = self.calculate_hash(content)
        exists = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.content_hash == content_hash)
            .first()
        )
        return exists is not None

    def add_document(
        self,
        filename: str,
        content: str,
        doc_type: str,
        project_id: int,
        db: Session,
        force: bool = False,
        user_id: int = None,
    ):
        """同步新增文档门面（保留历史能力）。"""
        return add_document_impl(self, filename, content, doc_type, project_id, db, force, user_id)

    async def enqueue_document_for_offline_parse(
        self,
        file: UploadFile,
        project_id: int,
        doc_type: str,
        db: Session,
        force: bool = False,
        user_id: Optional[int] = None,
    ) -> dict:
        """
        上传入队门面。

        这里收敛“文件落盘 + 建 pending 记录 + Celery 入队”三步编排，
        让路由层只负责参数和权限校验。
        """
        file_path = await save_upload_file_for_offline_parse(file)
        doc = create_pending_document_impl(
            self,
            filename=file.filename or "untitled",
            doc_type=doc_type,
            project_id=project_id,
            db=db,
            user_id=user_id,
        )
        try:
            task = queue_document_parse_impl(
                doc_id=doc.id,
                file_path=file_path,
                force=force,
                user_id=user_id,
            )
            bind_parse_task_impl(doc.id, task.id, db)
            db.refresh(doc)
            return {"document": doc, "task_id": task.id}
        except Exception as e:
            # 入队失败时显式落 failed，并清理临时文件，避免出现 pending 脏数据。
            cleanup_offline_file(file_path)
            self.mark_document_parse_failed(
                doc_id=doc.id,
                error=e,
                db=db,
                retry_count=doc.retry_count,
            )
            raise

    def parse_document_offline(
        self,
        doc_id: int,
        file_path: str,
        db: Session,
        force: bool = False,
        user_id: Optional[int] = None,
        task_id: Optional[str] = None,
        retry_count: int = 0,
    ) -> dict:
        """离线解析门面，供 Celery 任务执行实际解析与索引。"""
        return parse_document_offline_impl(
            self,
            doc_id=doc_id,
            file_path=file_path,
            db=db,
            force=force,
            user_id=user_id,
            task_id=task_id,
            retry_count=retry_count,
        )

    def mark_document_parse_retry(
        self,
        doc_id: int,
        retry_count: int,
        error: Exception,
        db: Session,
        task_id: Optional[str] = None,
    ) -> None:
        """记录重试中的失败信息，便于前端状态轮询。"""
        mark_parse_retry_impl(
            doc_id=doc_id,
            retry_count=retry_count,
            error=error,
            db=db,
            task_id=task_id,
        )

    def mark_document_parse_failed(
        self,
        doc_id: int,
        error: Exception,
        db: Session,
        task_id: Optional[str] = None,
        retry_count: Optional[int] = None,
    ) -> None:
        """记录最终失败状态，确保失败可见且可追踪。"""
        mark_parse_failed_impl(
            doc_id=doc_id,
            error=error,
            db=db,
            task_id=task_id,
            retry_count=retry_count,
        )

    def get_documents_list(
        self,
        db: Session,
        project_id: int,
        search_term: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """
        获取文档列表并支持联动搜索。

        搜索会覆盖：当前文档名、关联子文档名、关联父文档名。
        """
        query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)

        if search_term:
            linked_doc = aliased(KnowledgeDocument)
            source_doc = aliased(KnowledgeDocument)
            query = (
                query.outerjoin(linked_doc, linked_doc.source_doc_id == KnowledgeDocument.id)
                .outerjoin(source_doc, source_doc.id == KnowledgeDocument.source_doc_id)
                .filter(
                    or_(
                        KnowledgeDocument.filename.contains(search_term),
                        linked_doc.filename.contains(search_term),
                        source_doc.filename.contains(search_term),
                    )
                )
                .distinct()
            )

        if start_date:
            query = query.filter(KnowledgeDocument.created_at >= start_date)

        if end_date:
            query = query.filter(KnowledgeDocument.created_at <= end_date + " 23:59:59")

        return query.order_by(
            KnowledgeDocument.display_order.desc(),
            KnowledgeDocument.created_at.desc(),
        ).all()

    def update_relation(self, doc_id: int, source_doc_id: Optional[int], db: Session):
        """更新 test_case -> requirement-like 的关联关系。"""
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if not doc:
            return False, "Document not found"

        if doc.doc_type != "test_case":
            return False, "Only test_case documents can be linked"

        if source_doc_id is None or source_doc_id == -1:
            doc.source_doc_id = None
            db.commit()
            return True, None

        source_doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == source_doc_id).first()
        if not source_doc:
            return False, "Source document not found"

        if source_doc.project_id != doc.project_id:
            return False, "Source document must be in the same project"

        if source_doc.doc_type not in ["requirement", "product_requirement", "incomplete"]:
            return False, "Source document must be requirement-like"

        doc.source_doc_id = source_doc.id
        db.commit()
        return True, None

    def clean_cross_project_associations(self, db: Session):
        """清理历史跨项目 source_doc_id 脏数据。"""
        dirty_docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.source_doc_id != None).all()

        cleaned_count = 0
        for doc in dirty_docs:
            source_doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc.source_doc_id).first()
            if source_doc and source_doc.project_id != doc.project_id:
                doc.source_doc_id = None
                cleaned_count += 1

        db.commit()
        return cleaned_count

    def get_relevant_context(
        self,
        query: str,
        project_id: int,
        limit: int = 5,
        db: Optional[Session] = None,
        user_id: Optional[int] = None,
        debug: bool = False,
        max_tokens: int = 1800,
        retrieval_options: Optional[dict] = None,
    ) -> str | dict:
        """
        语义召回门面（兼容旧接口）。

        - debug=False：返回字符串上下文（历史行为）。
        - debug=True：返回包含检索治理调试信息的结构化结果。
        """
        return get_relevant_context_impl(
            self,
            query,
            project_id,
            limit,
            db,
            user_id,
            debug=debug,
            max_tokens=max_tokens,
            retrieval_options=retrieval_options,
        )

    def get_all_context(
        self,
        db: Session,
        project_id: int,
        user_id: Optional[int] = None,
        max_docs: Optional[int] = 50,
    ) -> str:
        """全量上下文门面。"""
        return get_all_context_impl(self, db, project_id, user_id, max_docs)

    def get_or_build_context_snapshot(
        self,
        project_id: int,
        db: Session,
        user_id: Optional[int] = None,
        force_rebuild: bool = False,
        prefer_async_rebuild: bool = False,
    ) -> dict:
        """
        获取或构建项目级上下文快照。

        设计目标：
        1. 优先复用快照，减少在线全量压缩。
        2. 构建失败时不抛出致命异常，由上层继续 fallback 到 RAG。
        """
        return get_or_build_context_snapshot_impl(
            self,
            project_id=project_id,
            db=db,
            user_id=user_id,
            force_rebuild=force_rebuild,
            prefer_async_rebuild=prefer_async_rebuild,
        )

    def get_context_snapshot_status(self, project_id: int, db: Session) -> dict:
        """查询项目级上下文快照状态。"""
        return get_context_snapshot_status_impl(project_id=project_id, db=db)

    def enqueue_context_snapshot_rebuild(
        self,
        project_id: int,
        db: Session,
        user_id: Optional[int] = None,
        force_rebuild: bool = False,
        rebuild_reason_hint: Optional[str] = None,
    ) -> dict:
        """手动触发项目级快照异步重建。"""
        return enqueue_context_snapshot_rebuild_impl(
            project_id=project_id,
            db=db,
            user_id=user_id,
            force_rebuild=force_rebuild,
            rebuild_reason_hint=rebuild_reason_hint,
        )

    def update_document(
        self, doc_id: int, filename: str, content: str, doc_type: str, db: Session
    ):
        """更新文档门面。"""
        return update_document_impl(self, doc_id, filename, content, doc_type, db)

    def delete_document(self, doc_id: int, db: Session):
        """删除文档门面。"""
        return delete_document_impl(self, doc_id, db)

    def move_document(
        self, project_id: int, doc_id: int, anchor_doc_id: int, position: str, db: Session
    ):
        """拖拽排序门面。"""
        return move_document_impl(project_id, doc_id, anchor_doc_id, position, db)

    def reorder_documents(self, project_id: int, ordered_ids: list[int], db: Session):
        """批量重排门面。"""
        return reorder_documents_impl(project_id, ordered_ids, db)


knowledge_base = KnowledgeBaseModule()
