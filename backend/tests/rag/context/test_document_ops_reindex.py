from types import SimpleNamespace

from modules.knowledge_base_components import document_ops


class _FakeQuery:
    def __init__(self, doc):
        self._doc = doc

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._doc


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

    def delete_document(self, doc_id):
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
    assert raw_call["content"] == "updated content"
    assert raw_call["metadata"]["doc_id"] == 12
    assert raw_call["metadata"]["is_summary"] is False
    assert raw_call["metadata"]["user_id"] == 101

    assert summary_call["doc_id"] == "12_summary"
    assert summary_call["content"] == "summary text for updated content"
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

