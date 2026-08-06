import logging
import os
import json
import ipaddress
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse

import chromadb
import dashscope
import httpx
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


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using default=%s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Env %s=%r below minimum=%s; using minimum", name, raw, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("Env %s=%r above maximum=%s; using maximum", name, raw, maximum)
        return maximum
    return value


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROMA_PATH = BACKEND_ROOT / "chroma_db"
DEFAULT_EMBED_BATCH_SIZE = _env_int("DASHSCOPE_EMBED_BATCH_SIZE", 25, minimum=1)
DEFAULT_EMBED_MAX_CHARS = _env_int("DASHSCOPE_EMBED_MAX_CHARS", 2000, minimum=128, maximum=2048)
DEFAULT_HNSW_BATCH_SIZE = _env_int("CHROMA_HNSW_BATCH_SIZE", 100, minimum=2)
DEFAULT_HNSW_SYNC_THRESHOLD = _env_int(
    "CHROMA_HNSW_SYNC_THRESHOLD",
    100000 if os.name == "nt" else 1000,
    minimum=DEFAULT_HNSW_BATCH_SIZE,
)
_HNSW_LOAD_ERROR_SIGNALS = (
    "error loading hnsw index",
    "error creating hnsw segment reader",
    "error constructing hnsw segment reader",
    "error sending backfill request to compactor",
    # 中文注释：查询阶段常见损坏信号，表现为索引/ID 映射不一致。
    "error finding id",
    "collection expecting embedding with dimension",
    "embedding with dimension",
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


@dataclass(frozen=True)
class EmbeddingProviderConfig:
    provider: str = "local"
    model: str = ""
    api_key: str = ""
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url: str = ""
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE
    max_chars: int = DEFAULT_EMBED_MAX_CHARS
    timeout_seconds: float = 30.0


def _default_embedding_model(provider: str) -> str:
    normalized = _normalize_embedding_provider(provider)
    if normalized == "dashscope":
        return str(dashscope.TextEmbedding.Models.text_embedding_v1)
    if normalized in {"openai", "openai_compatible"}:
        return "text-embedding-3-small"
    return ""


def build_embedding_provider_config(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    text_model_config: dict | None = None,
) -> EmbeddingProviderConfig:
    text_config = dict(text_model_config or {})
    raw_provider = provider if provider is not None else getattr(settings, "EMBEDDING_PROVIDER", "local")
    normalized_provider = _normalize_embedding_provider(raw_provider)
    if normalized_provider == "follow_text":
        normalized_provider = _normalize_embedding_provider(text_config.get("provider") or "local")

    api_key_env = str(getattr(settings, "EMBEDDING_API_KEY_ENV", "") or "").strip()
    if normalized_provider == "local":
        api_key_env = ""
    elif not api_key_env:
        api_key_env = "DASHSCOPE_API_KEY" if normalized_provider in {"dashscope", "auto"} else "OPENAI_API_KEY"
    provider_fallback_key = settings.DASHSCOPE_API_KEY if normalized_provider in {"dashscope", "auto"} else ""
    if normalized_provider == "local":
        resolved_api_key = ""
    else:
        resolved_api_key = str(
            api_key
            if api_key is not None
            else (
                os.getenv(api_key_env)
                or text_config.get("api_key")
                or provider_fallback_key
                or ""
            )
        )
    resolved_api_key = resolved_api_key.strip()
    resolved_base_url = str(
        base_url
        if base_url is not None
        else (getattr(settings, "EMBEDDING_BASE_URL", "") or text_config.get("base_url") or "")
    ).strip()
    resolved_model = str(
        model
        if model is not None
        else (getattr(settings, "EMBEDDING_MODEL_NAME", "") or text_config.get("embedding_model") or "")
    ).strip()
    if not resolved_model:
        resolved_model = _default_embedding_model(normalized_provider)

    return EmbeddingProviderConfig(
        provider=normalized_provider,
        model=resolved_model,
        api_key=resolved_api_key,
        api_key_env=api_key_env,
        base_url=resolved_base_url,
        batch_size=max(1, int(DEFAULT_EMBED_BATCH_SIZE)),
        max_chars=max(128, int(DEFAULT_EMBED_MAX_CHARS)),
        timeout_seconds=max(1.0, float(getattr(settings, "EMBEDDING_TIMEOUT_SECONDS", 30.0) or 30.0)),
    )


class DashScopeEmbeddingProvider:
    def __init__(self, config: EmbeddingProviderConfig):
        self.config = config

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        total = len(texts)
        model_name = self.config.model or str(dashscope.TextEmbedding.Models.text_embedding_v1)
        for batch_index, batch in enumerate(_iter_batches(texts, self.config.batch_size), start=1):
            resp = dashscope.TextEmbedding.call(
                model=model_name,
                input=batch,
                api_key=self.config.api_key,
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


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, config: EmbeddingProviderConfig):
        self.config = config

    def _endpoint(self) -> str:
        base_url = str(self.config.base_url or "").strip().rstrip("/")
        if not base_url:
            raise ValueError("base_url is required for openai-compatible embeddings")
        if base_url.endswith("/embeddings"):
            return base_url
        if base_url.endswith("/responses") or base_url.endswith("/chat/completions"):
            base_url = base_url.rsplit("/", 1)[0]
        return f"{base_url}/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        embeddings: list[list[float]] = []
        with httpx.Client(timeout=float(self.config.timeout_seconds or 30.0)) as client:
            for batch in _iter_batches(texts, self.config.batch_size):
                response = client.post(
                    self._endpoint(),
                    headers=headers,
                    json={"model": self.config.model, "input": batch},
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list):
                    raise ValueError("OpenAI-compatible embedding response missing data list")
                rows = sorted(
                    [item for item in data if isinstance(item, dict)],
                    key=lambda item: int(item.get("index") or 0),
                )
                batch_embeddings = [item.get("embedding") for item in rows]
                if len(batch_embeddings) != len(batch) or not all(isinstance(item, list) for item in batch_embeddings):
                    raise ValueError("OpenAI-compatible embedding response size mismatch")
                embeddings.extend(batch_embeddings)
        return embeddings


class DynamicEmbeddingFunction(EmbeddingFunction):
    def __init__(self, config: EmbeddingProviderConfig):
        self.config = config
        if config.provider == "dashscope":
            self._provider = DashScopeEmbeddingProvider(config)
        elif config.provider in {"openai", "openai_compatible"}:
            self._provider = OpenAICompatibleEmbeddingProvider(config)
        else:
            raise ValueError(f"Unsupported dynamic embedding provider: {config.provider}")

    def name(self) -> str:
        return f"{self.config.provider}:{self.config.model or 'default'}"

    def get_config(self) -> dict:
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "api_key_env": self.config.api_key_env,
            "base_url": self.config.base_url,
            "batch_size": int(self.config.batch_size),
            "max_chars": int(self.config.max_chars),
            "timeout_seconds": float(self.config.timeout_seconds),
        }

    @staticmethod
    def build_from_config(config: dict) -> "DynamicEmbeddingFunction":
        DynamicEmbeddingFunction.validate_config(config)
        provider = str(config.get("provider") or "local").strip().lower()
        api_key_env = str(config.get("api_key_env") or "DASHSCOPE_API_KEY").strip()
        return DynamicEmbeddingFunction(
            build_embedding_provider_config(
                provider=provider,
                api_key=os.getenv(api_key_env) or "",
                base_url=str(config.get("base_url") or ""),
                model=str(config.get("model") or ""),
            )
        )

    @staticmethod
    def validate_config(config: dict) -> None:
        if not isinstance(config, dict):
            raise ValueError("DynamicEmbeddingFunction config must be a dict")
        provider = _normalize_embedding_provider(config.get("provider"))
        if provider not in {"dashscope", "openai", "openai_compatible"}:
            raise ValueError(f"Unsupported dynamic embedding provider: {provider}")

    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []
        try:
            texts = [str(item or "") for item in input]
            return self._provider.embed(texts)
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            raise e


def _normalize_embedding_provider(raw_provider: str | None) -> str:
    provider = str(raw_provider or "local").strip().lower()
    if provider in {"local", "default", "chroma", "chroma_default"}:
        return "local"
    if provider in {"dashscope", "aliyun", "ali"}:
        return "dashscope"
    if provider in {"openai", "deepseek", "ollama"}:
        return "openai_compatible"
    if provider in {"openai_compatible", "openai-compatible", "compatible"}:
        return "openai_compatible"
    if provider in {"follow_text", "text", "model_router"}:
        return "follow_text"
    if provider == "auto":
        return "auto"
    logger.warning("Unknown EMBEDDING_PROVIDER=%s, falling back to local", raw_provider)
    return "local"


def _base_url_is_local(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "::1"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private)


def select_embedding_function(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    text_model_config: dict | None = None,
):
    config = build_embedding_provider_config(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        text_model_config=text_model_config,
    )
    selected_provider = config.provider
    if selected_provider == "auto":
        selected_provider = "dashscope" if config.api_key else "local"
    if selected_provider == "dashscope":
        if config.api_key:
            return DynamicEmbeddingFunction(config), "dashscope"
        logger.warning("EMBEDDING_PROVIDER=dashscope but DASHSCOPE_API_KEY is empty; falling back to local")
    if selected_provider == "openai_compatible":
        if config.base_url and config.model:
            return DynamicEmbeddingFunction(config), "openai_compatible"
        logger.warning("EMBEDDING_PROVIDER=%s requires EMBEDDING_BASE_URL and EMBEDDING_MODEL_NAME; falling back to local", selected_provider)
    return embedding_functions.DefaultEmbeddingFunction(), "local"


def describe_embedding_runtime(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    text_model_config: dict | None = None,
) -> dict:
    config = build_embedding_provider_config(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        text_model_config=text_model_config,
    )
    selected_provider = config.provider
    fallback_reason = ""
    if selected_provider == "auto":
        selected_provider = "dashscope" if config.api_key else "local"
    if selected_provider == "dashscope" and not config.api_key:
        fallback_reason = "dashscope_api_key_missing"
        selected_provider = "local"
    if selected_provider == "openai_compatible" and not (config.base_url and config.model):
        fallback_reason = "openai_compatible_base_url_or_model_missing"
        selected_provider = "local"
    would_call_embedding_model = selected_provider in {"dashscope", "openai_compatible"}
    base_url_local = _base_url_is_local(config.base_url)
    would_call_cloud = selected_provider == "dashscope" or (
        selected_provider == "openai_compatible" and not base_url_local
    )
    return {
        "configured_provider": config.provider,
        "selected_provider": selected_provider,
        "model": config.model,
        "base_url_set": bool(config.base_url),
        "base_url_local": base_url_local,
        "api_key_env": config.api_key_env,
        "api_key_set": bool(config.api_key),
        "would_call_embedding_model": would_call_embedding_model,
        "would_call_cloud": would_call_cloud,
        "fallback_reason": fallback_reason,
        "batch_size": int(config.batch_size),
        "max_chars": int(config.max_chars),
    }


class ChromaClient:
    """
    ChromaDB 客户端封装。

    负责：
    1. 初始化持久化向量库
        2. 选择向量化函数（本地 / DashScope / OpenAI-compatible）
    3. 文档入库、检索、删除
    """

    def __init__(self, persist_path: str | None = None):
        self.client = None
        self.collection = None
        self.embedding_fn = None
        self.persist_path = _resolve_persist_path(persist_path)
        try:
            self._init_client()
            logger.info("ChromaDB initialized at %s", self.persist_path)
        except Exception as e:
            if _is_chroma_store_corrupted(e):
                logger.error(
                    "Chroma 持久化目录疑似损坏，已停止自动重置。"
                    "请从 MySQL 持久化文档执行完整索引重建后再恢复服务。原始错误: %s",
                    e,
                )

            logger.error("Failed to initialize ChromaDB: %s", e)

    def _try_runtime_recover(self, *, operation: str, error: Exception) -> bool:
        """损坏时只报告失败，禁止在业务请求内删除或切换活动索引库。"""
        if _is_chroma_store_corrupted(error):
            logger.error(
                "ChromaDB %s 检测到索引损坏，已拒绝自动删除 collection；"
                "请执行完整重建。err=%s",
                operation,
                error,
            )
        return False

    def _run_with_recover(self, operation: str, func, *, raise_on_error: bool = False, default=None):
        """执行 Chroma 操作；损坏时显式失败，不在请求内破坏或重置索引。"""
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

        self.embedding_fn, embedding_provider = select_embedding_function()
        if embedding_provider == "dashscope":
            logger.info("Using DashScope Embedding Function")
        elif embedding_provider == "openai_compatible":
            logger.info("Using OpenAI-compatible Embedding Function")
        else:
            logger.info("Using Default Embedding Function (provider=local; may require local model download)")

        self.collection = self.client.get_or_create_collection(
            name="knowledge_base",
            embedding_function=self.embedding_fn,
            # Windows 下 chroma-hnswlib 1.5.x 的本地快照可能只写 metadata
            # 而缺少二进制段；提高同步阈值后由 SQLite 日志在启动时重放。
            metadata={
                "hnsw:batch_size": DEFAULT_HNSW_BATCH_SIZE,
                "hnsw:sync_threshold": DEFAULT_HNSW_SYNC_THRESHOLD,
            },
        )

    def add_document(
        self,
        doc_id: str,
        metadata: dict,
        chunks: list[dict],
        raise_on_error: bool = False,
    ):
        """
        将文档写入向量库。

        写入已由上游按业务类型生成的分块，metadata 内强制写入 doc_id，
        便于后续按文档删除。
        """
        if not self.collection:
            return
        if not chunks:
            raise ValueError(f"Chroma 写入分块不能为空：doc_id={doc_id}")

        def _do_add():
            chunk_payloads: list[tuple[str, dict]] = []
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

            base_metadata = metadata.copy()
            # 摘要索引沿用原文 doc_id，索引自身仍使用独立前缀。
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
