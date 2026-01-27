"""
知识库管理模块 (Knowledge Base Management)

此模块负责项目文档的存储、索引和检索，是 RAG (Retrieval-Augmented Generation) 的核心组件。
核心功能：
1. 文档存储：将需求文档、原型图描述、API 文档等存储到 MySQL。
2. 向量索引：调用 ChromaDB 对文档内容进行向量化，支持语义检索。
3. 智能摘要：对长文档自动生成摘要，优化上下文窗口使用。
4. 上下文检索：根据用户 Query，检索最相关的文档片段。
5. ID 管理：维护项目维度的连续文档 ID (project_specific_id)。

依赖：
- core.chroma_client: 向量数据库客户端。
- core.ai_client: 用于生成摘要。
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from core.models import KnowledgeDocument
from core.chroma_client import chroma_client
import hashlib
from typing import Optional

class KnowledgeBaseModule:
    """
    知识库核心逻辑封装类
    """
    def _ensure_summary(self, doc: KnowledgeDocument, db: Session, user_id: Optional[int] = None) -> str:
        """
        确保文档拥有摘要 (Ensure Document Summary)
        如果文档内容过长且没有摘要，调用 AI 生成摘要并保存。
        这有助于在构建 Prompt 时减少 Token 消耗。
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
                db=db
            )
            if summary and isinstance(summary, str) and not summary.startswith("Error") and not summary.startswith("Exception"):
                doc.summary = summary
                db.commit()
                db.refresh(doc)
                return summary
        except Exception:
            pass
        return content

    def calculate_hash(self, content: str) -> str:
        """
        计算内容的 SHA256 哈希值 (Calculate Content Hash)
        用于检测文档内容是否发生变化，防止重复存储。
        """
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def reindex_project_specific_ids(self, doc_type: str, project_id: int, db: Session):
        """
        重置特定项目下特定类型文档的 ID (Reindex Project Specific IDs)
        
        功能：
        将指定项目(project_id)下指定类型(doc_type)的所有文档，按创建时间(created_at)重新排序，
        并从 1 开始分配连续的 project_specific_id。
        
        目的：
        保证文档 ID 的连续性和可读性（如 REQ-1, REQ-2），避免因删除文档导致的 ID 空洞。
        """
        # Get all documents of this type for the project, ordered by created_at ascending
        # This ensures consistent and predictable ID assignment
        # (获取该项目下该类型的所有文档，按创建时间升序排列。这确保了 ID 分配的一致性和可预测性)
        remaining_docs = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.doc_type == doc_type,
            KnowledgeDocument.project_id == project_id
        ).order_by(KnowledgeDocument.created_at.asc()).all()
        
        # Reset project_specific_id to be consecutive, starting from 1
        # This ensures every operation results in reset IDs
        # (重置 project_specific_id 使其连续，从 1 开始。这确保每次操作后 ID 都会重置)
        for idx, remaining_doc in enumerate(remaining_docs, 1):
            remaining_doc.project_specific_id = idx
        
        db.commit()
        return len(remaining_docs)

    def check_duplicate(self, content: str, db: Session) -> bool:
        """
        检查内容是否重复 (Check Duplicate Content)
        通过比对哈希值快速判断内容是否已存在于数据库中。
        """
        content_hash = self.calculate_hash(content)
        exists = db.query(KnowledgeDocument).filter(KnowledgeDocument.content_hash == content_hash).first()
        return exists is not None

    def add_document(self, filename: str, content: str, doc_type: str, project_id: int, db: Session, force: bool = False, user_id: int = None):
        """
        添加文档到知识库 (Add Document)
        
        流程：
        1. 计算内容哈希，检查是否重复。
        2. 如果重复且未开启 force 模式，返回重复信息。
        3. 如果 force=True，则复用现有文档（幂等性）。
        4. 创建新文档记录，分配初始 project_specific_id。
        5. 计算 display_order 以确保新文档显示在列表末尾（逻辑底部）。
        6. 保存到数据库后，触发 reindex_project_specific_ids 修正 ID。
        7. 尝试生成摘要 (_ensure_summary)。
        8. 如果是文本类文档，同步添加到 ChromaDB 向量库。
        """
        content_hash = self.calculate_hash(content)

        existing = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.content_hash == content_hash,
            KnowledgeDocument.project_id == project_id
        ).first()

        if existing:
            if not force:
                return {"status": "duplicate", "existing_filename": existing.filename, "existing_doc_id": existing.id}
            else:
                # If force=True, we reuse the existing document (Idempotency)
                # We do NOT create a duplicate row for the same content
                # (如果 force=True，我们重用现有文档（幂等性）。我们不会为相同内容创建重复行)
                return existing

        # Create document with temporary project_specific_id
        # Calculate min display_order to append to the end (Bottom)
        # Since we sort by display_order DESC, the bottom is the minimum value.
        # (创建带有临时 project_specific_id 的文档。计算最小 display_order 以追加到末尾（底部）。因为我们按 display_order 倒序排列，底部是最小值。)
        min_order = db.query(func.min(KnowledgeDocument.display_order)).filter(
            KnowledgeDocument.project_id == project_id
        ).scalar()
        
        # If no docs, min_order is None. We can start at 0.0.
        # If docs exist, we go lower than the min.
        # (如果没有文档，min_order 为 None。我们可以从 0.0 开始。如果文档存在，我们取比最小值更小的值。)
        new_order = (min_order if min_order is not None else 0.0) - 1.0

        doc = KnowledgeDocument(
            filename=filename, 
            content=content, 
            doc_type=doc_type,
            content_hash=content_hash,
            project_id=project_id,
            project_specific_id=0,  # Temporary ID, will be updated in reindex (临时 ID，将在重索引时更新)
            user_id=user_id,
            display_order=new_order
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        
        # Reindex project_specific_id to be consecutive
        self.reindex_project_specific_ids(doc_type, project_id, db)
        
        # Refresh to get the updated project_specific_id
        db.refresh(doc)
        
        try:
            self._ensure_summary(doc, db, user_id)
        except Exception:
            pass

        # Add to ChromaDB for RAG
        # We only index 'requirement' and 'product_requirement' for now, or maybe all text docs?
        # Let's index everything that is text-heavy.
        if doc_type in ['requirement', 'product_requirement', 'incomplete', 'evaluation_report']:
             chroma_client.add_document(
                 doc_id=str(doc.id),
                 content=content,
                 metadata={
                     "project_id": project_id,
                     "doc_type": doc_type,
                     "filename": filename,
                     "doc_id": doc.id, # Store global ID in metadata for easier deletion
                     "user_id": user_id
                 }
             )

        return doc

    def get_documents_list(self, db: Session, project_id: int, search_term: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """
        获取文档列表 (Get Documents List)
        
        支持多条件筛选：
        - 项目 ID (必选)
        - 关键词搜索 (文件名)
        - 时间范围 (start_date, end_date)
        
        排序：
        优先按 display_order 倒序（自定义排序），其次按创建时间倒序。
        """
        query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
        
        if search_term:
            query = query.filter(KnowledgeDocument.filename.contains(search_term))
        
        if start_date:
            query = query.filter(KnowledgeDocument.created_at >= start_date)
        
        if end_date:
            query = query.filter(KnowledgeDocument.created_at <= end_date + ' 23:59:59') # Include end date fully
        
        # Order by display_order descending (Newest/Highest first), then created_at desc
        return query.order_by(
            KnowledgeDocument.display_order.desc(),
            KnowledgeDocument.created_at.desc()
        ).all()

    def update_relation(self, doc_id: int, source_doc_id: int, db: Session):
        """
        更新文档关联关系 (Update Document Relation)
        
        功能：
        设置文档的 source_doc_id，用于建立文档间的父子或引用关系（如 测试用例 -> 需求文档）。
        
        安全检查：
        - 确保源文档(source_doc)存在。
        - 确保两个文档属于同一个项目，防止跨项目错误关联。
        """
        # Try to find document by global id first, then by project_specific_id
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if not doc:
            doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_specific_id == doc_id).first()
        if not doc:
            return False
        
        # If source_doc_id is -1 or None, clear the relation
        if source_doc_id is None or source_doc_id == -1:
             doc.source_doc_id = None
        else:
            # For source_doc_id, use the global id directly (frontend now passes global_id)
            source_doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == source_doc_id).first()
            if source_doc:
                # Defensive check: ensure both documents belong to the same project
                if source_doc.project_id != doc.project_id:
                    # Clear invalid cross-project association
                    doc.source_doc_id = None
                else:
                    doc.source_doc_id = source_doc.id
            else:
                # Source document not found, clear the relation
                doc.source_doc_id = None
             
        db.commit()
        return True

    def clean_cross_project_associations(self, db: Session):
        """
        清理跨项目错误关联 (Clean Cross-Project Associations)
        
        维护任务：
        扫描数据库，查找所有 source_doc_id 指向不同项目文档的记录，并将其置空。
        用于修复历史遗留的脏数据。
        """
        # Find all documents with source_doc_id that point to documents in different projects
        # This is a one-time cleanup function to fix existing dirty data
        dirty_docs = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.source_doc_id != None
        ).all()
        
        cleaned_count = 0
        for doc in dirty_docs:
            source_doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc.source_doc_id).first()
            if source_doc and source_doc.project_id != doc.project_id:
                # Clear invalid cross-project association
                doc.source_doc_id = None
                cleaned_count += 1
        
        db.commit()
        return cleaned_count

    def get_relevant_context(self, query: str, project_id: int, limit: int = 5, db: Optional[Session] = None, user_id: Optional[int] = None) -> str:
        """
        获取相关上下文 (Get Relevant Context / RAG)
        
        核心 RAG 逻辑：
        1. 调用 ChromaDB 进行语义搜索，找到与 Query 最相关的文档片段。
        2. 如果提供了 DB Session，尝试获取文档摘要而非全文，以节省 Token。
        3. 格式化输出为 "--- Relevant Knowledge: [Filename] --- \n [Content]" 格式。
        """
        if not query:
            return ""
            
        try:
            results = chroma_client.search(
                query=query,
                n_results=limit,
                where={"project_id": project_id}
            )
            
            context = ""
            if results and results.get('documents') and len(results['documents']) > 0:
                # results['documents'] is a list of lists (one list per query)
                for i, doc_text in enumerate(results['documents'][0]):
                    meta = results['metadatas'][0][i] if results.get('metadatas') else {}
                    filename = meta.get('filename', 'Unknown')
                    doc_type = meta.get('doc_type', 'Unknown')
                    doc_id = meta.get('doc_id') if isinstance(meta, dict) else None
                    if db and doc_id:
                        try:
                            kb_doc = db.query(KnowledgeDocument).filter(
                                KnowledgeDocument.id == int(doc_id),
                                KnowledgeDocument.project_id == project_id
                            ).first()
                            if kb_doc:
                                doc_text = self._ensure_summary(kb_doc, db, user_id)
                        except Exception:
                            pass
                    context += f"""--- Relevant Knowledge: {filename} ({doc_type}) ---
{doc_text}

"""
            return context
        except Exception as e:
            print(f"RAG retrieval failed: {e}")
            return ""

    def get_all_context(self, db: Session, project_id: int, user_id: Optional[int] = None, max_docs: Optional[int] = 50) -> str:
        """
        获取全量上下文 (Get All Context)
        
        用于需要全量项目知识的场景（如初步分析）。
        注意：仅返回最近的 max_docs 篇文档，且优先使用摘要。
        """
        query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
        if max_docs:
            query = query.order_by(KnowledgeDocument.created_at.desc()).limit(max_docs)
        docs = query.all()
        context = ""
        for doc in docs:
            content_to_use = self._ensure_summary(doc, db, user_id)
            context += f"""--- Document: {doc.filename} ({doc.doc_type}) ---
{content_to_use}

"""
        return context
    
    def update_document(self, doc_id: int, filename: str, content: str, doc_type: str, db: Session):
        """
        更新文档 (Update Document)
        
        功能：
        1. 更新文档的基本信息（文件名、内容、类型）。
        2. 如果内容发生变化：
           - 重新计算哈希。
           - 清空旧摘要 (summary)，触发后续重新生成。
           - 同步更新 ChromaDB 中的向量数据。
        3. 如果文档类型发生变化，触发 reindex_project_specific_ids 维护 ID 连续性。
        """
        # Try to find document by global id first, then by project_specific_id
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if not doc:
            doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_specific_id == doc_id).first()
        if not doc:
            return None
        
        # Store original doc type for reindexing if needed
        original_doc_type = doc.doc_type
        project_id = doc.project_id
        
        # Calculate new hash if content changed
        content_changed = False
        if content != doc.content:
            content_hash = self.calculate_hash(content)
            doc.content = content
            doc.content_hash = content_hash
            content_changed = True
            doc.summary = None
        
        # Update other fields
        if filename:
            doc.filename = filename
        if doc_type:
            doc.doc_type = doc_type
        
        db.commit()
        
        # Reindex if doc_type changed or for the current doc_type
        if original_doc_type != doc_type:
            # Reindex both original and new doc types
            self.reindex_project_specific_ids(original_doc_type, project_id, db)
            self.reindex_project_specific_ids(doc_type, project_id, db)
        else:
            # Just reindex the current doc type
            self.reindex_project_specific_ids(doc_type, project_id, db)
        
        db.refresh(doc)
        
        try:
            self._ensure_summary(doc, db, getattr(doc, "user_id", None))
        except Exception:
            pass

        # Update ChromaDB if content changed or doc_type changed to/from indexable types
        # For simplicity, if content changed and it's a requirement, re-index.
        # Ideally we should delete old vector and add new one.
        if content_changed and doc.doc_type in ['requirement', 'product_requirement', 'incomplete']:
             # Delete old (by ID, assuming ID didn't change, but we used doc_id in metadata?)
             # Actually we used doc.id as ID in Chroma.
             chroma_client.delete_document(str(doc.id))
             chroma_client.add_document(
                 doc_id=str(doc.id),
                 content=content,
                 metadata={
                     "project_id": project_id,
                     "doc_type": doc.doc_type,
                     "filename": filename or doc.filename,
                     "doc_id": doc.id
                 }
             )

        return doc
    
    def delete_document(self, doc_id: int, db: Session):
        """
        删除文档 (Delete Document)
        
        流程：
        1. 查找文档。
        2. 解除所有关联到该文档的引用（将其他文档的 source_doc_id 置空）。
        3. 从数据库物理删除记录。
        4. 触发 reindex_project_specific_ids 填补 ID 空缺。
        5. 从 ChromaDB 删除对应的向量数据。
        """
        # Try to find document by project_specific_id first, then by global id
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_specific_id == doc_id).first()
        if not doc:
            doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if not doc:
            return False
        
        # Store the doc type and project id for reindexing
        doc_type = doc.doc_type
        project_id = doc.project_id
        doc_global_id = doc.id
        
        # Delete all linked documents
        linked_docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.source_doc_id == doc.id).all()
        for linked_doc in linked_docs:
            linked_doc.source_doc_id = None
        
        # Delete the document
        db.delete(doc)
        db.commit()
        
        # Reindex project_specific_id for remaining documents of the same type
        self.reindex_project_specific_ids(doc_type, project_id, db)

        # Delete from ChromaDB
        chroma_client.delete_document(str(doc_global_id))
        
        return True

    def move_document(self, project_id: int, doc_id: int, anchor_doc_id: int, position: str, db: Session):
        """
        移动文档位置 (Move Document / Drag and Drop)
        
        实现拖拽排序逻辑：
        - position: 'before' (上方) | 'after' (下方)
        - 列表按 display_order 倒序排列。
        - 'before': 新 display_order = (anchor + upper_neighbor) / 2
        - 'after': 新 display_order = (anchor + lower_neighbor) / 2
        """
        target_doc = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.project_id == project_id
        ).first()
        
        anchor_doc = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == anchor_doc_id,
            KnowledgeDocument.project_id == project_id
        ).first()
        
        if not target_doc or not anchor_doc:
            return False
            
        if target_doc.id == anchor_doc.id:
            return True

        # Calculate new order
        new_order = 0.0
        
        if position == 'before': # Visually above -> Higher Value
            # Find the document immediately above the anchor (next higher value)
            upper_neighbor = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.display_order > anchor_doc.display_order
            ).order_by(KnowledgeDocument.display_order.asc()).first()
            
            if upper_neighbor:
                new_order = (anchor_doc.display_order + upper_neighbor.display_order) / 2.0
            else:
                # Anchor is the top-most document
                new_order = anchor_doc.display_order + 10.0
                
        else: # 'after', Visually below -> Lower Value
            # Find the document immediately below the anchor (next lower value)
            lower_neighbor = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.display_order < anchor_doc.display_order
            ).order_by(KnowledgeDocument.display_order.desc()).first()
            
            if lower_neighbor:
                new_order = (anchor_doc.display_order + lower_neighbor.display_order) / 2.0
            else:
                # Anchor is the bottom-most document
                new_order = anchor_doc.display_order - 10.0

        target_doc.display_order = new_order
        db.commit()
        return True

    def reorder_documents(self, project_id: int, ordered_ids: list[int], db: Session):
        """
        批量重排序文档 (Batch Reorder Documents)
        
        功能：
        根据前端传入的 ID 顺序列表 (ordered_ids)，重新分配 display_order。
        策略：
        1. 获取这批文档当前的 display_order 集合，并降序排列。
        2. 按 ordered_ids 的顺序，依次分配这些 display_order 值。
        3. 这样可以保持整体的排序权重区间不变，只是交换了具体文档的位置。
        """
        if not ordered_ids:
            return True
            
        # 1. Fetch the documents corresponding to these IDs
        docs = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id.in_(ordered_ids),
            KnowledgeDocument.project_id == project_id
        ).all()
        
        if not docs:
            return True

        doc_map = {doc.id: doc for doc in docs}
        
        # 2. Get the current display_orders of these documents and sort them to create "slots"
        # Sort DESCENDING because we display in descending order.
        current_orders = sorted([doc.display_order for doc in docs], reverse=True)
        
        # Ensure values are strictly descending to avoid collisions
        for i in range(1, len(current_orders)):
            if current_orders[i] >= current_orders[i-1]:
                current_orders[i] = current_orders[i-1] - 1.0

        # 3. Assign the sorted orders to the IDs in the new sequence
        for i, doc_id in enumerate(ordered_ids):
            if doc_id in doc_map:
                doc_map[doc_id].display_order = current_orders[i]
        
        db.commit()
        return True

knowledge_base = KnowledgeBaseModule()
