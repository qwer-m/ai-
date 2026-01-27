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
    """
    ChromaDB 客户端封装 (ChromaDB Client Wrapper)
    
    管理向量数据库的连接、集合创建和文档操作。
    支持自动切换 Embedding 提供商 (DashScope 或默认)。
    """
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
        """
        添加文档到向量库 (Add Document)
        
        自动对长文本进行分块，并注入 doc_id 到 metadata 中以便后续删除。
        
        Args:
            doc_id: 文档唯一标识 ID。
            content: 文档内容。
            metadata: 额外的元数据 (如 project_id, filename)。
        """
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
            
            # 确保 doc_id 存在于 metadata 中，用于删除
            base_metadata = metadata.copy() if metadata else {}
            base_metadata["doc_id"] = str(doc_id)
            
            metadatas = [base_metadata for _ in range(len(chunks))]
            
            self.collection.add(
                documents=chunks,
                metadatas=metadatas,
                ids=ids
            )
        except Exception as e:
            logger.error(f"ChromaDB add failed: {e}")

    def search(self, query: str, n_results: int = 5, where: dict = None):
        """
        检索相似文档 (Search)
        
        Args:
            query: 查询文本。
            n_results: 返回结果数量。
            where: 过滤条件 (如 {"project_id": 1})。
            
        Returns:
            list: 检索结果。
        """
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
        """
        删除文档 (Delete Document)
        
        根据 metadata 中的 doc_id 删除该文档的所有分块。
        
        Args:
            doc_id: 文档 ID。
        """
        if not self.collection:
            return
        try:
            # Delete all chunks for this doc_id (using where filter)
            self.collection.delete(
                where={"doc_id": str(doc_id)}
            )
        except Exception as e:
            logger.error(f"ChromaDB delete failed: {e}")

# Global instance
chroma_client = ChromaClient()
