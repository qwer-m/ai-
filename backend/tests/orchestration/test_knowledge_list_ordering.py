import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# Ensure backend package imports work when pytest runs from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2]))

from routers.system import common
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    OPTIONAL_USER_VISIBLE_DOC_TYPES,
    USER_MANAGED_DOC_TYPES,
    KnowledgeDocumentRepository,
)


class _FakeQuery:
    def __init__(self, docs):
        self._docs = list(docs)
        self._ordered = list(docs)
        self._offset = 0
        self._limit = None
        self.order_by_args = ()
        self.filter_args = []

    def filter(self, *args, **kwargs):
        self.filter_args.append(args)
        return self

    def count(self):
        return len(self._docs)

    def order_by(self, *args, **kwargs):
        self.order_by_args = args
        first_arg = str(args[0]) if args else ""
        if "display_order" in first_arg:
            self._ordered = sorted(
                self._docs,
                key=lambda d: (-float(getattr(d, "display_order", 0.0) or 0.0), d.created_at, d.id),
            )
        else:
            self._ordered = sorted(self._docs, key=lambda d: (d.created_at, d.id))
        return self

    def offset(self, n):
        self._offset = max(0, int(n))
        return self

    def limit(self, n):
        self._limit = max(0, int(n))
        return self

    def all(self):
        rows = self._ordered[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class _FakeDB:
    def __init__(self, query):
        self._query = query

    def query(self, model):
        return self._query


def test_list_knowledge_orders_by_display_order(monkeypatch):
    docs = [
        SimpleNamespace(id=1, display_order=0.0, created_at=datetime(2026, 1, 1, 10, 0, 0)),
        SimpleNamespace(id=2, display_order=10.0, created_at=datetime(2026, 1, 1, 11, 0, 0)),
    ]
    query = _FakeQuery(docs)
    db = _FakeDB(query)
    current_user = SimpleNamespace(id=1001)

    monkeypatch.setattr(common, "_get_owned_project", lambda project_id, user_id, db: object())
    monkeypatch.setattr(common, "build_knowledge_list_related_maps", lambda db, pid, documents: ({}, {}))
    monkeypatch.setattr(
        common,
        "_serialize_doc",
        lambda doc, source_name_map, linked_map: {"global_id": doc.id},
    )
    monkeypatch.setattr(
        common,
        "build_knowledge_list_response",
        lambda serialized_docs, page, page_size, total, total_pages: {
            "documents": serialized_docs,
            "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages},
        },
    )

    result = common.list_knowledge(
        project_id=45,
        page=1,
        page_size=8,
        db=db,
        current_user=current_user,
    )

    assert [item["global_id"] for item in result["documents"]] == [2, 1]
    assert query.order_by_args
    assert "display_order" in str(query.order_by_args[0])


def _doc_type_in_values(query: _FakeQuery) -> tuple[str, ...]:
    for args in query.filter_args:
        for expr in args:
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            if getattr(left, "name", None) == "doc_type" and hasattr(right, "value"):
                value = getattr(right, "value")
                if isinstance(value, (list, tuple)):
                    return tuple(str(item) for item in value)
    return ()


def test_paginated_knowledge_list_hides_internal_artifacts_by_default():
    query = _FakeQuery([])
    db = _FakeDB(query)

    KnowledgeDocumentRepository(db).list_project_documents_paginated(
        project_id=45,
        page=1,
        page_size=8,
    )

    assert set(_doc_type_in_values(query)) == set(USER_MANAGED_DOC_TYPES)


def test_paginated_knowledge_list_can_include_user_visible_evaluation_reports():
    query = _FakeQuery([])
    db = _FakeDB(query)

    KnowledgeDocumentRepository(db).list_project_documents_paginated(
        project_id=45,
        page=1,
        page_size=8,
        include_evaluation_reports=True,
    )

    assert set(_doc_type_in_values(query)) == set(USER_MANAGED_DOC_TYPES + OPTIONAL_USER_VISIBLE_DOC_TYPES)

