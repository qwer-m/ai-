from types import SimpleNamespace

from modules.knowledge_base_components.document import document_ops
from modules.knowledge_base_components.document.document_index_service import (
    _clean_content_text,
)


class _FakeQuery:
    def __init__(self, doc):
        self._doc = doc

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._doc


def test_index_content_cleanup_removes_invisible_controls_but_preserves_layout() -> None:
    content = "模块标题\x01\n字段\t说明\x7f"

    assert _clean_content_text(content) == "模块标题 \n字段\t说明 "


class _FakeDB:
    def __init__(self, doc):
        self._doc = doc
        self.commit_count = 0

    def query(self, model):
        return _FakeQuery(self._doc)

    def commit(self):
        self.commit_count += 1

    def refresh(self, doc):
        return None


class _FakeModule:
    def __init__(self, summary_text):
        self.summary_text = summary_text
        self.reindex_calls = []

    def calculate_hash(self, content: str) -> str:
        return f"hash::{content}"

    def reindex_project_specific_ids(self, doc_type, project_id, db):
        self.reindex_calls.append((doc_type, project_id))

    def _ensure_summary(self, doc, db, user_id):
        doc.summary = self.summary_text
        return self.summary_text


class _FakeChroma:
    def __init__(self):
        self.delete_calls = []
        self.add_calls = []

    def delete_document(self, doc_id, *, raise_on_error=False):
        self.delete_calls.append(str(doc_id))

    def add_document(self, **kwargs):
        self.add_calls.append(kwargs)


def _build_doc():
    return SimpleNamespace(
        id=12,
        project_specific_id=3,
        project_id=7,
        filename="old.md",
        content="old content",
        content_hash="old_hash",
        doc_type="requirement",
        user_id=101,
        summary=None,
    )


def test_update_document_reindexes_raw_and_summary(monkeypatch):
    doc = _build_doc()
    db = _FakeDB(doc)
    module = _FakeModule(summary_text="summary text for updated content")
    fake_chroma = _FakeChroma()
    monkeypatch.setattr(document_ops, "chroma_client", fake_chroma)

    result = document_ops.update_document_impl(
        module=module,
        doc_id=12,
        filename="new.md",
        content="updated content",
        doc_type="requirement",
        db=db,
    )

    assert result is doc
    assert fake_chroma.delete_calls == ["12", "12_summary"]
    assert len(fake_chroma.add_calls) == 2

    raw_call = fake_chroma.add_calls[0]
    summary_call = fake_chroma.add_calls[1]

    assert raw_call["doc_id"] == "12"
    assert raw_call["chunks"]
    assert "updated content" in "".join(item["chunk_text"] for item in raw_call["chunks"])
    assert raw_call["metadata"]["doc_id"] == 12
    assert raw_call["metadata"]["is_summary"] is False
    assert raw_call["metadata"]["user_id"] == 101

    assert summary_call["doc_id"] == "12_summary"
    assert summary_call["chunks"]
    assert "summary text for updated content" in "".join(
        item["chunk_text"] for item in summary_call["chunks"]
    )
    assert summary_call["metadata"]["doc_id"] == 12
    assert summary_call["metadata"]["is_summary"] is True
    assert summary_call["metadata"]["user_id"] == 101
    assert summary_call["metadata"]["filename"] == "new.md (Summary)"


def test_update_document_skips_summary_index_when_same_as_content(monkeypatch):
    doc = _build_doc()
    db = _FakeDB(doc)
    module = _FakeModule(summary_text="updated content")
    fake_chroma = _FakeChroma()
    monkeypatch.setattr(document_ops, "chroma_client", fake_chroma)

    document_ops.update_document_impl(
        module=module,
        doc_id=12,
        filename="new.md",
        content="updated content",
        doc_type="requirement",
        db=db,
    )

    assert fake_chroma.delete_calls == ["12", "12_summary"]
    assert len(fake_chroma.add_calls) == 1
    assert fake_chroma.add_calls[0]["metadata"]["is_summary"] is False


def test_add_document_covers_same_identity_instead_of_creating_duplicate(monkeypatch):
    existing = _build_doc()
    existing.filename = "requirement.pdf"
    db = _FakeDB(existing)
    module = _FakeModule(summary_text="summary text for replacement content")
    fake_chroma = _FakeChroma()

    class _CoverRepo:
        def __init__(self, db):
            self.db = db
            self.added = db.added

        def find_latest_by_identity(self, **kwargs):
            assert kwargs == {
                "project_id": 7,
                "user_id": 101,
                "doc_type": "requirement",
                "filename": "requirement.pdf",
            }
            return self.db._doc

        def get_by_id(self, doc_id):
            return self.db._doc if doc_id == self.db._doc.id else None

        def commit(self):
            self.db.commit()

        def refresh(self, doc):
            self.db.refresh(doc)

        def add(self, doc):
            self.added.append(doc)

    db.added = []
    monkeypatch.setattr(document_ops, "KnowledgeDocumentRepository", _CoverRepo)
    monkeypatch.setattr(document_ops, "chroma_client", fake_chroma)

    result = document_ops.add_document_impl(
        module=module,
        filename="requirement.pdf",
        content="replacement content",
        doc_type="requirement",
        project_id=7,
        db=db,
        user_id=101,
    )

    assert result is existing
    assert db.added == []
    assert existing.id == 12
    assert existing.content == "replacement content"
    assert existing.content_hash == "hash::replacement content"
    assert fake_chroma.delete_calls == ["12", "12_summary"]
    assert fake_chroma.add_calls[0]["metadata"]["filename"] == "requirement.pdf"
    assert fake_chroma.add_calls[0]["metadata"]["doc_id"] == 12


def test_delete_document_uses_document_id(monkeypatch):
    target = SimpleNamespace(
        id=11,
        project_specific_id=6,
        project_id=8,
        doc_type="requirement",
        user_id=101,
    )
    class _DeleteRepo:
        def __init__(self, db):
            self.deleted = db.deleted

        def get_by_id(self, doc_id):
            return target if doc_id == 11 else None

        def list_linked_by_source(self, source_doc_id):
            return []

        def delete(self, doc):
            self.deleted.append(doc)

        def commit(self):
            return None

    class _DeleteDB:
        def __init__(self):
            self.deleted = []

    class _DeleteModule:
        def __init__(self):
            self.reindex_calls = []

        def reindex_project_specific_ids(self, doc_type, project_id, db):
            self.reindex_calls.append((doc_type, project_id))

    deleted_indexes = []
    monkeypatch.setattr(document_ops, "KnowledgeDocumentRepository", _DeleteRepo)
    monkeypatch.setattr(document_ops, "delete_document_indexes", lambda doc_id, client=None: deleted_indexes.append(doc_id))

    db = _DeleteDB()
    module = _DeleteModule()

    assert document_ops.delete_document_impl(module, 11, db) is True
    assert db.deleted == [target]
    assert deleted_indexes == [11]
    assert module.reindex_calls == [("requirement", 8)]

