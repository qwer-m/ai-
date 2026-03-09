import os
import logging
from http import HTTPStatus

import chromadb
import dashscope
from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.config import Settings
from chromadb.utils import embedding_functions

from core.config import settings

# 关闭 Chroma 遥测，避免本地开发环境出现无关 telemetry 报错
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_SERVER_NO_ANALYTICS", "True")
os.environ.setdefault("CHROMA_PRODUCT_TELEMETRY_IMPL", "core.chroma_telemetry.NoOpProductTelemetryClient")
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "core.chroma_telemetry.NoOpProductTelemetryClient")

# 模块级日志器：统一输出向量库相关日志
logger = logging.getLogger(__name__)


class DashScopeEmbeddingFunction(EmbeddingFunction):
    """基于 DashScope 的向量化函数封装。"""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def __call__(self, input: Documents) -> Embeddings:
        """把文本列表转换为向量列表。"""
        if not input:
            return []

        try:
            # 调用 DashScope 文本向量接口
            resp = dashscope.TextEmbedding.call(
                model=dashscope.TextEmbedding.Models.text_embedding_v1,
                input=input,
                api_key=self.api_key,
            )
            if resp.status_code == HTTPStatus.OK:
                return [item["embedding"] for item in resp.output["embeddings"]]

            logger.error(f"DashScope Embedding Error: {resp}")
            # 这里直接抛错，交给上层重试或降级处理
            raise Exception(f"DashScope Embedding Error: {resp.message}")
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise e


class ChromaClient:
    """
    ChromaDB 客户端封装。

    负责：
    1. 初始化持久化向量库
    2. 选择向量化函数（DashScope / 默认）
    3. 文档入库、检索、删除
    """

    def __init__(self, persist_path: str = "./chroma_db"):
        try:
            self.client = chromadb.PersistentClient(
                path=persist_path,
                settings=Settings(
                    anonymized_telemetry=False,
                    chroma_product_telemetry_impl="core.chroma_telemetry.NoOpProductTelemetryClient",
                    chroma_telemetry_impl="core.chroma_telemetry.NoOpProductTelemetryClient",
                ),
            )

            # 有 DashScope Key 时优先使用云端向量；否则用默认本地向量函数
            if settings.DASHSCOPE_API_KEY:
                self.embedding_fn = DashScopeEmbeddingFunction(settings.DASHSCOPE_API_KEY)
                logger.info("Using DashScope Embedding Function")
            else:
                self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
                logger.info("Using Default Embedding Function (Warning: Requires download)")

            self.collection = self.client.get_or_create_collection(
                name="knowledge_base",
                embedding_function=self.embedding_fn,
            )
            logger.info(f"ChromaDB initialized at {persist_path}")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            self.client = None
            self.collection = None

    def add_document(self, doc_id: str, content: str, metadata: dict | None = None):
        """
        将文档写入向量库。

        当前采用“按字符长度分块”的轻量策略：
        - 每块 2000 字符
        - 每块一个向量ID（doc_id_序号）
        - metadata 内强制写入 doc_id，便于后续按文档删除
        """
        if not self.collection:
            return

        try:
            # 这里按字符分块是实用方案：实现简单、稳定性高
            max_chars = 2000
            chunks = [content[i : i + max_chars] for i in range(0, len(content), max_chars)]
            ids = [f"{doc_id}_{i}" for i in range(len(chunks))]

            base_metadata = metadata.copy() if metadata else {}
            base_metadata["doc_id"] = str(doc_id)
            metadatas = [base_metadata for _ in range(len(chunks))]

            self.collection.add(documents=chunks, metadatas=metadatas, ids=ids)
        except Exception as e:
            logger.error(f"Failed to add document to ChromaDB: {e}")

    def search(self, query: str, n_results: int = 5, where: dict | None = None):
        """语义检索：按 query 返回最相关的 n 条内容。"""
        if not self.collection:
            return {}

        try:
            return self.collection.query(query_texts=[query], n_results=n_results, where=where)
        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return {}

    def delete_document(self, doc_id: str):
        """按 metadata.doc_id 删除该文档在向量库中的所有分块。"""
        if not self.collection:
            return

        try:
            self.collection.delete(where={"doc_id": str(doc_id)})
        except Exception as e:
            logger.error(f"Failed to delete document from ChromaDB: {e}")


# 全局单例：业务层直接导入使用
chroma_client = ChromaClient()
