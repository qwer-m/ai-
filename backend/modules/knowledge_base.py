"""
知识库模块（Knowledge Base Management）。

层级定位：
1. 位于 modules 层，负责知识文档管理与 RAG 上下文供给。
2. 对外保持统一门面，复杂实现拆分到 `knowledge_base_components`。
3. 不改变既有数据语义：MySQL 主数据、ChromaDB 检索、project_specific_id 连续性规则。
"""

import hashlib
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from core.models import KnowledgeDocument
from modules.knowledge_base_components.context_ops import (
    get_all_context_impl,
    get_relevant_context_impl,
)
from modules.knowledge_base_components.document_ops import (
    add_document_impl,
    delete_document_impl,
    move_document_impl,
    reorder_documents_impl,
    update_document_impl,
)


class KnowledgeBaseModule:
    """
    知识库核心门面。

    对外职责：
    1. 暴露文档管理、关系维护、上下文检索接口。
    2. 保持历史调用契约不变，便于 router/service 无感升级。
    """

    def _ensure_summary(
        self, doc: KnowledgeDocument, db: Session, user_id: Optional[int] = None
    ) -> str:
        """
        确保文档可用于低成本上下文拼接。

        设计原因：
        1. 长文直接注入上下文会显著抬高 token 成本。
        2. 仅在长文且摘要缺失时触发 AI 压缩，避免不必要的模型调用。
        """
        if not doc:
            return ""
        if doc.summary and str(doc.summary).strip():
            return doc.summary

        content = doc.content or ""
        if len(content) < 12000:
            return content

        try:
            from core.ai_client import get_client_for_user

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
            pass

        return content

    def calculate_hash(self, content: str) -> str:
        """计算内容哈希，用于重复文档判定。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def reindex_project_specific_ids(self, doc_type: str, project_id: int, db: Session):
        """
        维护项目内类型级连续编号。

        该编号用于前端展示和业务可读性，不替代全局主键。
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
        """按内容哈希检查是否已存在同内容文档。"""
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
        """
        新增文档门面。
        具体数据库/向量库协作在 `document_ops` 中实现，避免当前文件继续膨胀。
        """
        return add_document_impl(self, filename, content, doc_type, project_id, db, force, user_id)

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

        关键点：
        1. 搜索会命中当前文档名、关联子文档名、关联父文档名。
        2. 排序优先 `display_order DESC`，其次 `created_at DESC`。
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
        """
        更新文档关联关系（test_case -> requirement-like）。
        """
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
        """
        清理历史脏数据：跨项目 source_doc_id 关联。
        """
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
    ) -> str:
        """语义召回门面。"""
        return get_relevant_context_impl(self, query, project_id, limit, db, user_id)

    def get_all_context(
        self,
        db: Session,
        project_id: int,
        user_id: Optional[int] = None,
        max_docs: Optional[int] = 50,
    ) -> str:
        """全量上下文门面。"""
        return get_all_context_impl(self, db, project_id, user_id, max_docs)

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

