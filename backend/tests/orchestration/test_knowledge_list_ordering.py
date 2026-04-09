import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# Ensure backend package imports work when pytest runs from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2]))

from routers.system import common


class _FakeQuery:
    def __init__(self, docs):
        self._docs = list(docs)
        self._ordered = list(docs)
        self._offset = 0
        self._limit = None
        self.order_by_args = ()

    def filter(self, *args, **kwargs):
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

