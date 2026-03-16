import logging
import os
import shutil
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

import chromadb
import dashscope
from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.config import Settings
from chromadb.utils import embedding_functions

from core.config import settings

# 关闭 Chroma 遥测，避免本地开发环境出现无关 telemetry 报错
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_SERVER_NO_ANALYTICS", "True")
os.environ.setdefault(
    "CHROMA_PRODUCT_TELEMETRY_IMPL", "core.chroma_telemetry.NoOpProductTelemetryClient"
)
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "core.chroma_telemetry.NoOpProductTelemetryClient")

# 模块级日志器：统一输出向量库相关日志
logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROMA_PATH = BACKEND_ROOT / "chroma_db"


def _resolve_persist_path(persist_path: str | None) -> Path:
    """
    统一解析向量库目录，避免不同启动目录导致读写到不同库。

    优先级：
    1. 显式传参 persist_path
    2. 环境变量 CHROMA_PERSIST_PATH
    3. backend/chroma_db（固定绝对路径）
    """
    raw = (persist_path or os.getenv("CHROMA_PERSIST_PATH") or "").strip()
    if not raw:
        return DEFAULT_CHROMA_PATH

    path = Path(raw)
    if not path.is_absolute():
        path = (BACKEND_ROOT / path).resolve()
    return path


def _is_chroma_store_corrupted(error: Exception) -> bool:
    """判断是否是本地持久化文件损坏导致的初始化失败。"""
    message = str(error).lower()
    return "file is not a database" in message or "database disk image is malformed" in message


def _backup_corrupted_store(path: Path) -> Path | None:
    """
    备份损坏目录后重建，避免服务直接不可用。

    设计取舍：优先保证服务可恢复，再保留损坏现场用于排查。
    """
    if not path.exists():
        return None

    backup = path.with_name(f"{path.name}_corrupt_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
    shutil.move(str(path), str(backup))
    return backup


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

            logger.error("DashScope Embedding Error: %s", resp)
            # 这里直接抛错，交给上层重试或降级处理
            raise Exception(f"DashScope Embedding Error: {resp.message}")
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            raise e


class ChromaClient:
    """
    ChromaDB 客户端封装。

    负责：
    1. 初始化持久化向量库
    2. 选择向量化函数（DashScope / 默认）
    3. 文档入库、检索、删除
    """

    def __init__(self, persist_path: str | None = None):
        self.client = None
        self.collection = None
        self.persist_path = _resolve_persist_path(persist_path)

        try:
            self._init_client()
            logger.info("ChromaDB initialized at %s", self.persist_path)
        except Exception as e:
            if _is_chroma_store_corrupted(e):
                try:
                    backup_path = _backup_corrupted_store(self.persist_path)
                    logger.error(
                        "Chroma 持久化目录疑似损坏，已备份为 %s，准备重建。原始错误: %s",
                        backup_path,
                        e,
                    )
                    self._init_client()
                    logger.info("ChromaDB recovered at %s", self.persist_path)
                    return
                except Exception as recover_error:
                    logger.error("ChromaDB recover failed: %s", recover_error)

            logger.error("Failed to initialize ChromaDB: %s", e)

    def _init_client(self) -> None:
        """初始化客户端和 collection。"""
        self.persist_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.persist_path),
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

    def add_document(
        self,
        doc_id: str,
        content: str,
        metadata: dict | None = None,
        raise_on_error: bool = False,
    ):
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
            logger.error("Failed to add document to ChromaDB: %s", e)
            if raise_on_error:
                raise

    def search(
        self,
        query: str,
        n_results: int = 5,
        where: dict | None = None,
        raise_on_error: bool = False,
    ):
        """语义检索：按 query 返回最相关的 n 条内容。"""
        if not self.collection:
            return {}

        try:
            return self.collection.query(query_texts=[query], n_results=n_results, where=where)
        except Exception as e:
            logger.error("ChromaDB search failed: %s", e)
            if raise_on_error:
                raise
            return {}

    def delete_document(self, doc_id: str, raise_on_error: bool = False):
        """按 metadata.doc_id 删除该文档在向量库中的所有分块。"""
        if not self.collection:
            return

        try:
            self.collection.delete(where={"doc_id": str(doc_id)})
        except Exception as e:
            logger.error("Failed to delete document from ChromaDB: %s", e)
            if raise_on_error:
                raise


# 全局单例：业务层直接导入使用
chroma_client = ChromaClient()
