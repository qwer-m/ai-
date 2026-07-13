from __future__ import annotations

import hashlib
from types import SimpleNamespace

import modules.knowledge_base_components.snapshot.snapshot_builder as snapshot_builder


class _FingerprintRepo:
    def __init__(self, db):  # noqa: ANN001
        self.db = db

    def list_project_doc_snapshot_fingerprints(self, *, project_id: int, max_docs: int):  # noqa: ARG002
        return [
            SimpleNamespace(
                id=1,
                filename="long_requirement.pdf",
                content_hash="hash-from-upload",
                doc_type="requirement",
                user_id=9,
                summary_length=0,
                content_length=50000,
            ),
            SimpleNamespace(
                id=2,
                filename="legacy.txt",
                content_hash="",
                doc_type="product_requirement",
                user_id=None,
                summary_length=0,
                content_length=12,
            ),
            SimpleNamespace(
                id=3,
                filename="empty.txt",
                content_hash="empty-hash",
                doc_type="requirement",
                user_id=None,
                summary_length=0,
                content_length=0,
            ),
        ]

    def list_project_doc_contents_by_ids(self, *, project_id: int, doc_ids):  # noqa: ARG002, ANN001
        self.db["content_lookup_ids"] = [int(item) for item in doc_ids]
        return [SimpleNamespace(id=2, content="legacy body")]


def test_collect_project_doc_fingerprints_uses_hash_metadata_before_content(monkeypatch) -> None:
    db = {}
    monkeypatch.setattr(snapshot_builder, "KnowledgeDocumentRepository", _FingerprintRepo)

    corpus = snapshot_builder.collect_project_doc_fingerprints(db, project_id=2, user_id=9)

    assert db["content_lookup_ids"] == [2]
    assert [item["doc_id"] for item in corpus] == [1, 2]
    assert corpus[0]["fingerprint"] == "hash-from-upload"
    assert corpus[0]["text"] == ""
    assert corpus[0]["owner_user_id"] == 9
    assert corpus[1]["fingerprint"] == hashlib.sha256(b"legacy body").hexdigest()
