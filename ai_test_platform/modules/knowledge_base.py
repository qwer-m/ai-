from sqlalchemy.orm import Session
from sqlalchemy import func
from core.models import KnowledgeDocument
from core.chroma_client import chroma_client
import hashlib
from typing import Optional

class KnowledgeBaseModule:
    def calculate_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def reindex_project_specific_ids(self, doc_type: str, project_id: int, db: Session):
        """Reindex project_specific_id for documents of the same type and project to be consecutive"""
        # Get all documents of this type for the project, ordered by created_at ascending
        # This ensures consistent and predictable ID assignment
        remaining_docs = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.doc_type == doc_type,
            KnowledgeDocument.project_id == project_id
        ).order_by(KnowledgeDocument.created_at.asc()).all()
        
        # Reset project_specific_id to be consecutive, starting from 1
        # This ensures every operation results in reset IDs
        for idx, remaining_doc in enumerate(remaining_docs, 1):
            remaining_doc.project_specific_id = idx
        
        db.commit()
        return len(remaining_docs)

    def check_duplicate(self, content: str, db: Session) -> bool:
        content_hash = self.calculate_hash(content)
        exists = db.query(KnowledgeDocument).filter(KnowledgeDocument.content_hash == content_hash).first()
        return exists is not None

    def add_document(self, filename: str, content: str, doc_type: str, project_id: int, db: Session, force: bool = False, user_id: int = None):
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
                return existing

        # Create document with temporary project_specific_id
        doc = KnowledgeDocument(
            filename=filename, 
            content=content, 
            doc_type=doc_type,
            content_hash=content_hash,
            project_id=project_id,
            project_specific_id=0,  # Temporary ID, will be updated in reindex
            user_id=user_id
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        
        # Reindex project_specific_id to be consecutive
        self.reindex_project_specific_ids(doc_type, project_id, db)
        
        # Refresh to get the updated project_specific_id
        db.refresh(doc)

        # Add to ChromaDB for RAG
        # We only index 'requirement' and 'product_requirement' for now, or maybe all text docs?
        # Let's index everything that is text-heavy.
        if doc_type in ['requirement', 'product_requirement', 'incomplete']:
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
        query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
        
        if search_term:
            query = query.filter(KnowledgeDocument.filename.contains(search_term))
        
        if start_date:
            query = query.filter(KnowledgeDocument.created_at >= start_date)
        
        if end_date:
            query = query.filter(KnowledgeDocument.created_at <= end_date + ' 23:59:59') # Include end date fully
        
        # Order by document type, then project_specific_id ascending
        return query.order_by(
            KnowledgeDocument.doc_type,
            KnowledgeDocument.project_specific_id.asc()
        ).all()

    def update_relation(self, doc_id: int, source_doc_id: int, db: Session):
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

    def get_relevant_context(self, query: str, project_id: int, limit: int = 5) -> str:
        """
        Retrieve relevant context using semantic search via ChromaDB.
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
                    context += f"""--- Relevant Knowledge: {filename} ({doc_type}) ---
{doc_text}

"""
            return context
        except Exception as e:
            print(f"RAG retrieval failed: {e}")
            return ""

    def get_all_context(self, db: Session, project_id: int) -> str:
        docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id).all()
        context = ""
        for doc in docs:
            # Use summary if available (Context Compression)
            content_to_use = doc.summary if doc.summary else doc.content
            context += f"""--- Document: {doc.filename} ({doc.doc_type}) ---
{content_to_use}

"""
        return context
    
    def update_document(self, doc_id: int, filename: str, content: str, doc_type: str, db: Session):
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

knowledge_base = KnowledgeBaseModule()
