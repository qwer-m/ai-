from types import SimpleNamespace
import hashlib

from modules.knowledge_base_components.document import offline_parse


class _FakeRepo:
    def __init__(self, db):
        self.db = db

    def get_by_id(self, doc_id):
        return self.db.docs.get(doc_id)

    def find_latest_by_identity(
        self,
        *,
        project_id,
        user_id,
        doc_type,
        filename,
        exclude_doc_id=None,
    ):
        for doc in self.db.docs.values():
            if exclude_doc_id is not None and doc.id == exclude_doc_id:
                continue
            if (
                doc.project_id == project_id
                and doc.user_id == user_id
                and doc.doc_type == doc_type
                and doc.filename == filename
            ):
                return doc
        return None

    def find_duplicate_by_hash(self, **kwargs):
        return None

    def commit(self):
        self.db.commit_count += 1

    def rollback(self):
        self.db.rollback_count += 1

    def refresh(self, doc):
        return None

    def delete(self, doc):
        self.db.deleted.append(doc)
        self.db.docs.pop(doc.id, None)


class _FakeDB:
    def __init__(self, docs):
        self.docs = {doc.id: doc for doc in docs}
        self.deleted = []
        self.commit_count = 0
        self.rollback_count = 0


class _FakeModule:
    def __init__(self):
        self.reindex_calls = []

    def calculate_hash(self, content):
        return f"hash::{content}"

    def reindex_project_specific_ids(self, doc_type, project_id, db):
        self.reindex_calls.append((doc_type, project_id))


def _doc(doc_id, *, project_specific_id, content, status="success"):
    return SimpleNamespace(
        id=doc_id,
        project_specific_id=project_specific_id,
        project_id=8,
        user_id=1,
        filename="讲错题接入AI.pdf",
        content=content,
        content_hash=f"hash::{content}" if content else None,
        doc_type="requirement",
        summary=None,
        parse_status=status,
        parse_error=None,
        parsed_at=None,
        task_id=None,
        retry_count=0,
    )


def test_offline_parse_keeps_same_filename_documents_independent(monkeypatch, tmp_path):
    existing = _doc(11, project_specific_id=6, content="旧内容")
    pending = _doc(230, project_specific_id=20, content="", status="pending")
    db = _FakeDB([existing, pending])
    module = _FakeModule()
    indexed = []
    cleaned = []

    upload_file = tmp_path / "upload.pdf"
    upload_file.write_bytes(b"not used")

    monkeypatch.setattr(offline_parse, "KnowledgeDocumentRepository", _FakeRepo)
    monkeypatch.setattr(offline_parse, "parse_file_path", lambda file_path, **kwargs: "新内容")
    monkeypatch.setattr(
        offline_parse,
        "prepare_document_assets",
        lambda **kwargs: {"manifest": {}, "document_text": ""},
    )
    monkeypatch.setattr(offline_parse, "validate_parsed_content", lambda content: None)
    monkeypatch.setattr(offline_parse, "cleanup_offline_file", lambda file_path: cleaned.append(file_path))
    monkeypatch.setattr(offline_parse, "is_vector_store_ready", lambda: True)
    def fake_reindex(doc):
        indexed.append(doc.id)
        return {"indexed_raw": True, "indexed_summary": False}

    monkeypatch.setattr(offline_parse, "reindex_document_from_persisted_content", fake_reindex)

    result = offline_parse.parse_document_offline_impl(
        module,
        doc_id=pending.id,
        file_path=str(upload_file),
        db=db,
        user_id=1,
        task_id="task-1",
    )

    assert result == {"status": "success", "document_id": pending.id}
    assert existing.content == "旧内容"
    assert existing.content_hash == "hash::旧内容"
    assert pending.content == "新内容"
    assert pending.content_hash == hashlib.sha256(b"not used").hexdigest()
    assert pending.parse_status == "success"
    assert db.docs == {existing.id: existing, pending.id: pending}
    assert db.deleted == []
    assert indexed == [pending.id]
    assert module.reindex_calls == []
    assert cleaned == [str(upload_file)]
