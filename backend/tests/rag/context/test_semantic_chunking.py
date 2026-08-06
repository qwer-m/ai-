from core.processing.semantic_chunking import semantic_head, split_semantic_text
from pathlib import Path
import pytest

from core.cache_layer.chroma_client import ChromaClient, _is_chroma_store_corrupted


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def add(self, documents, metadatas, ids):
        self.calls.append(
            {
                "documents": list(documents),
                "metadatas": list(metadatas),
                "ids": list(ids),
            }
        )


class _FailQueryCollection:
    def query(self, query_texts, n_results, where):
        raise RuntimeError("Error loading hnsw index")


def test_split_semantic_text_prefers_sentence_boundaries():
    text = (
        "第一步，创建订单并校验参数。"
        "第二步，校验库存和额度上限。"
        "第三步，支付成功后更新状态并记录流水。"
    )
    chunks = split_semantic_text(text, max_chars=24, min_chars=8)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 24 for chunk in chunks)
    assert chunks[0].endswith("。")


def test_semantic_head_avoids_mid_sentence_cut():
    text = "规则A：额度不超过500。规则B：同一用户24小时内仅一次。规则C：超时自动关闭。"
    clipped, truncated = semantic_head(text, max_chars=26)

    assert truncated is True
    assert len(clipped) <= 26
    assert clipped.endswith("。")


def test_chroma_add_document_writes_explicit_business_chunks():
    client = object.__new__(ChromaClient)
    client.collection = _FakeCollection()
    client.add_document(
        doc_id="42",
        metadata={"project_id": 7},
        chunks=[
            {"chunk_text": "语义片段A。", "metadata": {"module": "开户"}},
            {"chunk_text": "语义片段B。", "metadata": {"module": "审批"}},
        ],
    )

    assert len(client.collection.calls) == 1
    payload = client.collection.calls[0]
    assert payload["documents"] == ["语义片段A。", "语义片段B。"]
    assert payload["ids"] == ["42_0", "42_1"]
    assert all(str(meta.get("doc_id")) == "42" for meta in payload["metadatas"])


def test_chroma_add_document_keeps_explicit_metadata_doc_id():
    client = object.__new__(ChromaClient)
    client.collection = _FakeCollection()
    client.add_document(
        doc_id="42_summary",
        metadata={"project_id": 7, "doc_id": 42, "is_summary": True},
        chunks=[{"chunk_text": "summary 片段。", "metadata": {}}],
    )

    payload = client.collection.calls[0]
    assert payload["ids"] == ["42_summary_0"]
    assert str(payload["metadatas"][0].get("doc_id")) == "42"
    assert bool(payload["metadatas"][0].get("is_summary")) is True


def test_chroma_corruption_detection_covers_hnsw_errors():
    assert _is_chroma_store_corrupted(Exception("database disk image is malformed")) is True
    assert _is_chroma_store_corrupted(Exception("Error loading hnsw index")) is True


def test_chroma_search_rejects_destructive_runtime_recovery():
    client = object.__new__(ChromaClient)
    client.client = object()
    client.collection = _FailQueryCollection()
    client.persist_path = Path(".")

    with pytest.raises(RuntimeError, match="Error loading hnsw index"):
        client.search(query="workflow case", n_results=3, where={"doc_type": "x"}, raise_on_error=True)
