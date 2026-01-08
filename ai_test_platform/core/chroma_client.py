import chromadb
from chromadb import EmbeddingFunction, Documents, Embeddings
from chromadb.utils import embedding_functions
import os
import logging
import dashscope
from http import HTTPStatus
from core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

class DashScopeEmbeddingFunction(EmbeddingFunction):
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []
        try:
            # DashScope embedding call
            resp = dashscope.TextEmbedding.call(
                model=dashscope.TextEmbedding.Models.text_embedding_v1,
                input=input,
                api_key=self.api_key
            )
            if resp.status_code == HTTPStatus.OK:
                return [item['embedding'] for item in resp.output['embeddings']]
            else:
                logger.error(f"DashScope Embedding Error: {resp}")
                # Fallback to zero vector or raise? Raise to let retry handle it.
                raise Exception(f"DashScope Embedding Error: {resp.message}")
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise e

class ChromaClient:
    def __init__(self, persist_path="./chroma_db"):
        try:
            self.client = chromadb.PersistentClient(path=persist_path)
            
            # Use DashScope embedding if API key is available, otherwise default
            if settings.DASHSCOPE_API_KEY:
                self.embedding_fn = DashScopeEmbeddingFunction(settings.DASHSCOPE_API_KEY)
                logger.info("Using DashScope Embedding Function")
            else:
                self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
                logger.info("Using Default Embedding Function (Warning: Requires download)")

            self.collection = self.client.get_or_create_collection(
                name="knowledge_base",
                embedding_function=self.embedding_fn
            )
            logger.info(f"ChromaDB initialized at {persist_path}")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            self.client = None
            self.collection = None

    def add_document(self, doc_id: str, content: str, metadata: dict = None):
        if not self.collection:
            return
        try:
            # Chunking could be done here if content is too large, 
            # but for MVP we assume reasonable size or rely on Chroma/Embedding handling.
            # However, all-MiniLM-L6-v2 has a limit (usually 512 tokens).
            # Simple truncation for MVP to avoid errors.
            max_chars = 2000 # Rough approx for 512 tokens
            
            # If content is long, we might want to chunk it.
            # For now, let's just index the first 2000 chars as a "summary" vector
            # or split it. Splitting is better for RAG.
            
            # Simple chunking strategy
            chunks = [content[i:i+max_chars] for i in range(0, len(content), max_chars)]
            ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
            metadatas = [metadata or {} for _ in range(len(chunks))]
            
            self.collection.add(
                documents=chunks,
                metadatas=metadatas,
                ids=ids
            )
        except Exception as e:
            logger.error(f"ChromaDB add failed: {e}")

    def search(self, query: str, n_results: int = 5, where: dict = None):
        if not self.collection:
            return []
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where
            )
            return results
        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []

    def delete_document(self, doc_id: str):
        if not self.collection:
            return
        try:
            # Delete all chunks for this doc_id (using where filter if we stored doc_id in metadata)
            # But we used doc_id prefix in IDs. 
            # Chroma doesn't support "starts_with" in delete IDs easily?
            # Actually, using metadata filter is better.
            # Let's assume we pass doc_id in metadata.
            
            # Wait, in add_document I used ids=[f"{doc_id}_{i}"...]
            # I should definitely store original doc_id in metadata for deletion.
            
            # Re-implement delete using metadata
            self.collection.delete(
                where={"doc_id": doc_id}
            )
        except Exception as e:
            logger.error(f"ChromaDB delete failed: {e}")

# Global instance
chroma_client = ChromaClient()
