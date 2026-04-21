import logging
import os
import shutil
import json
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

import chromadb
import dashscope
from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.config import Settings
from chromadb.utils import embedding_functions

from core.settings.config import settings
from core.processing.semantic_chunking import split_semantic_text

# 关闭 Chroma 遥测，避免本地开发环境出现无关 telemetry 报错
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_SERVER_NO_ANALYTICS", "True")
os.environ.setdefault(
    "CHROMA_PRODUCT_TELEMETRY_IMPL", "core.cache_layer.chroma_telemetry.NoOpProductTelemetryClient"
)
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "core.cache_layer.chroma_telemetry.NoOpProductTelemetryClient")

# 模块级日志器：统一输出向量库相关日志
logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROMA_PATH = BACKEND_ROOT / "chroma_db"
DEFAULT_EMBED_BATCH_SIZE = max(1, int(os.getenv("DASHSCOPE_EMBED_BATCH_SIZE", "25")))
DEFAULT_EMBED_MAX_CHARS = max(128, min(2048, int(os.getenv("DASHSCOPE_EMBED_MAX_CHARS", "2000"))))
_RUNTIME_AUTO_RECOVER = str(os.getenv("CHROMA_RUNTIME_AUTO_RECOVER", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_HNSW_LOAD_ERROR_SIGNALS = (
    "error loading hnsw index",
    "error creating hnsw segment reader",
    "error constructing hnsw segment reader",
    "error sending backfill request to compactor",
    # 中文注释：查询阶段常见损坏信号，表现为索引/ID 映射不一致。
    "error finding id",
)


def _normalize_metadata_value(value):
    """把 metadata 值归一化为 Chroma 支持的标量类型。"""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _sanitize_metadata(metadata: dict) -> dict:
    """过滤/归一化 metadata，避免 None 或复杂类型导致入库失败。"""
    clean: dict = {}
    for key, value in (metadata or {}).items():
        normalized = _normalize_metadata_value(value)
        if normalized is None:
            continue
        clean[key] = normalized
    return clean


def _iter_batches(items: list, batch_size: int):
    """按固定大小分批迭代。"""
    size = max(1, int(batch_size))
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _split_text_for_embedding_limit(text: str, max_chars: int = DEFAULT_EMBED_MAX_CHARS) -> list[str]:
    """
    保障单条 embedding 输入不超过长度上限。

    先尝试语义切分；若仍超长则按字符硬切，避免 DashScope 报参数长度错误。
    """
    normalized = str(text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    semantic_parts = split_semantic_text(
        text=normalized,
        max_chars=max_chars,
        min_chars=max(80, int(max_chars * 0.2)),
    )
    result: list[str] = []
    for part in semantic_parts:
        chunk = str(part or "").strip()
        if not chunk:
            continue
        if len(chunk) <= max_chars:
            result.append(chunk)
            continue
        # 中文注释：兜底硬切，确保每一段都满足 API 最大长度限制。
        for i in range(0, len(chunk), max_chars):
            piece = chunk[i : i + max_chars].strip()
            if piece:
                result.append(piece)

    if result:
        return result

    return [normalized[i : i + max_chars] for i in range(0, len(normalized), max_chars) if normalized[i : i + max_chars].strip()]


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
    return (
        "file is not a database" in message
        or "database disk image is malformed" in message
        or any(signal in message for signal in _HNSW_LOAD_ERROR_SIGNALS)
    )


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
            texts = [str(item or "") for item in input]
            embeddings: list[list[float]] = []
            total = len(texts)

            for batch_index, batch in enumerate(_iter_batches(texts, DEFAULT_EMBED_BATCH_SIZE), start=1):
                resp = dashscope.TextEmbedding.call(
                    model=dashscope.TextEmbedding.Models.text_embedding_v1,
                    input=batch,
                    api_key=self.api_key,
                )
                if resp.status_code != HTTPStatus.OK:
                    logger.error(
                        "DashScope Embedding Error on batch=%s size=%s/%s: %s",
                        batch_index,
                        len(batch),
                        total,
                        resp,
                    )
                    raise Exception(f"DashScope Embedding Error: {resp.message}")

                batch_embeddings = [item["embedding"] for item in resp.output["embeddings"]]
                if len(batch_embeddings) != len(batch):
                    raise Exception(
                        f"DashScope Embedding Error: batch size mismatch, expected={len(batch)} actual={len(batch_embeddings)}"
                    )
                embeddings.extend(batch_embeddings)

            return embeddings
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
        self.embedding_fn = None
        self.persist_path = _resolve_persist_path(persist_path)
        self._runtime_recovering = False
        self._runtime_auto_recover = bool(_RUNTIME_AUTO_RECOVER)

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

    def _try_runtime_recover(self, *, operation: str, error: Exception) -> bool:
        """运行时遇到索引损坏错误时，尝试备份并重建。"""
        if not self._runtime_auto_recover:
            return False
        if self._runtime_recovering:
            return False
        if not _is_chroma_store_corrupted(error):
            return False

        self._runtime_recovering = True
        try:
            # 先尝试只重建 collection，避免直接动底层 sqlite 文件导致锁冲突。
            embedding_fn = getattr(self, "embedding_fn", None)
            if self.client is not None and embedding_fn is not None:
                try:
                    self.client.delete_collection(name="knowledge_base")
                except Exception:
                    pass
                try:
                    self.collection = self.client.get_or_create_collection(
                        name="knowledge_base",
                        embedding_function=embedding_fn,
                    )
                    logger.info("ChromaDB runtime recovered by collection reset during %s", operation)
                    return True
                except Exception as reset_error:
                    logger.error("Chroma collection reset failed during %s: %s", operation, reset_error)

            try:
                backup_path = _backup_corrupted_store(self.persist_path)
                logger.error(
                    "Chroma runtime store error during %s; backed up %s, reinitializing. err=%s",
                    operation,
                    backup_path,
                    error,
                )
                self._init_client()
                logger.info("ChromaDB runtime recovered at %s", self.persist_path)
                return True
            except PermissionError:
                fresh_path = self.persist_path.with_name(
                    f"{self.persist_path.name}_rebuild_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                )
                logger.error(
                    "Chroma backup blocked by file lock during %s; switching to fresh path %s",
                    operation,
                    fresh_path,
                )
                self.persist_path = fresh_path
                self._init_client()
                logger.info("ChromaDB runtime recovered on fresh path %s", self.persist_path)
                return True
        except Exception as recover_error:
            logger.error("ChromaDB runtime recover failed during %s: %s", operation, recover_error)
            return False
        finally:
            self._runtime_recovering = False

    def _run_with_recover(self, operation: str, func, *, raise_on_error: bool = False, default=None):
        """执行 Chroma 操作，遇到 HNSW/索引损坏时自动自愈并重试一次。"""
        try:
            return func()
        except Exception as first_error:
            recovered = self._try_runtime_recover(operation=operation, error=first_error)
            if recovered:
                try:
                    return func()
                except Exception as retry_error:
                    logger.error("ChromaDB %s failed after runtime recover: %s", operation, retry_error)
                    if raise_on_error:
                        raise
                    return default
            logger.error("ChromaDB %s failed: %s", operation, first_error)
            if raise_on_error:
                raise
            return default

    def _init_client(self) -> None:
        """初始化客户端和 collection。"""
        self.persist_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.persist_path),
            settings=Settings(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl="core.cache_layer.chroma_telemetry.NoOpProductTelemetryClient",
                chroma_telemetry_impl="core.cache_layer.chroma_telemetry.NoOpProductTelemetryClient",
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
        chunks: list[dict] | None = None,
        raise_on_error: bool = False,
    ):
        """
        将文档写入向量库。

        分块写入策略：
        - 若传入 chunks：直接使用业务分块结果（每项可携带 chunk 级 metadata）；
        - 若未传入 chunks：退回语义分块兜底（向后兼容旧调用）；
        - metadata 内强制写入 doc_id，便于后续按文档删除。
        """
        if not self.collection:
            return

        def _do_add():
            chunk_payloads: list[tuple[str, dict]] = []
            if chunks:
                for item in chunks:
                    if isinstance(item, dict):
                        text = str(item.get("chunk_text") or item.get("text") or "").strip()
                        chunk_meta = item.get("metadata") or {}
                        if not isinstance(chunk_meta, dict):
                            chunk_meta = {}
                    else:
                        text = str(item or "").strip()
                        chunk_meta = {}
                    if not text:
                        continue
                    chunk_payloads.append((text, dict(chunk_meta)))
            else:
                max_chars = 2000
                semantic_chunks = split_semantic_text(
                    text=content or "",
                    max_chars=max_chars,
                    min_chars=max(120, int(max_chars * 0.2)),
                )
                if not semantic_chunks and content:
                    semantic_chunks = [str(content)[:max_chars]]
                chunk_payloads = [(chunk_text, {}) for chunk_text in semantic_chunks if str(chunk_text or "").strip()]

            if not chunk_payloads:
                return

            normalized_payloads: list[tuple[str, dict]] = []
            for chunk_text, chunk_meta in chunk_payloads:
                # 中文注释：保证每个 chunk 都满足 embedding 最大长度限制。
                parts = _split_text_for_embedding_limit(chunk_text, max_chars=DEFAULT_EMBED_MAX_CHARS)
                if len(parts) <= 1:
                    if parts:
                        normalized_payloads.append((parts[0], dict(chunk_meta or {})))
                    continue

                for part_index, part_text in enumerate(parts):
                    extended_meta = dict(chunk_meta or {})
                    extended_meta.setdefault("chunk_part_index", part_index)
                    extended_meta.setdefault("chunk_part_total", len(parts))
                    normalized_payloads.append((part_text, extended_meta))

            chunk_payloads = normalized_payloads
            if not chunk_payloads:
                return

            ids = [f"{doc_id}_{i}" for i in range(len(chunk_payloads))]

            base_metadata = metadata.copy() if metadata else {}
            # 兼容双索引：优先保留显式传入的“原文 doc_id”，避免被 summary 索引前缀覆盖。
            base_metadata["doc_id"] = str(base_metadata.get("doc_id") or doc_id)
            base_metadata = _sanitize_metadata(base_metadata)
            chunk_total = len(chunk_payloads)

            documents: list[str] = []
            metadatas: list[dict] = []
            for idx, (chunk_text, chunk_meta) in enumerate(chunk_payloads):
                merged = dict(base_metadata)
                merged.update(chunk_meta or {})
                merged["doc_id"] = str(merged.get("doc_id") or base_metadata["doc_id"])
                merged.setdefault("chunk_index", idx)
                merged.setdefault("chunk_total", chunk_total)
                merged.setdefault("source_doc_name", merged.get("filename"))
                # 中文注释：新元数据字段默认允许为空，保证历史调用不报错。
                merged.setdefault("module", None)
                merged.setdefault("biz_key", None)
                merged.setdefault("requirement_id", None)
                merged.setdefault("test_case_id", None)
                merged = _sanitize_metadata(merged)
                documents.append(chunk_text)
                metadatas.append(merged)

            self.collection.add(documents=documents, metadatas=metadatas, ids=ids)
            logger.debug("chroma_add_document doc_id=%s chunk_total=%s", doc_id, chunk_total)
            return None

        self._run_with_recover(
            "add_document",
            _do_add,
            raise_on_error=raise_on_error,
            default=None,
        )

    def search_by_metadata(
        self,
        where: dict,
        n_results: int = 5,
        raise_on_error: bool = False,
    ):
        """按 metadata 条件检索（用于关系扩召等场景）。"""
        if not self.collection:
            return {}

        def _do_search():
            # 中文注释：关系扩召需要“纯 metadata 匹配”，这里使用 get 避免 query 语义噪音。
            return self.collection.get(where=where, include=["documents", "metadatas"], limit=max(1, int(n_results)))
        return self._run_with_recover(
            "search_by_metadata",
            _do_search,
            raise_on_error=raise_on_error,
            default={},
        )

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

        def _do_search():
            return self.collection.query(query_texts=[query], n_results=n_results, where=where)
        return self._run_with_recover(
            "search",
            _do_search,
            raise_on_error=raise_on_error,
            default={},
        )

    def delete_document(self, doc_id: str, raise_on_error: bool = False):
        """按 metadata.doc_id 删除该文档在向量库中的所有分块。"""
        if not self.collection:
            return

        def _do_delete():
            self.collection.delete(where={"doc_id": str(doc_id)})
            return None
        self._run_with_recover(
            "delete_document",
            _do_delete,
            raise_on_error=raise_on_error,
            default=None,
        )


# 全局单例：业务层直接导入使用
chroma_client = ChromaClient()
