from __future__ import annotations

from types import SimpleNamespace

from modules.test_generation_components.services.final_case_quality_ledger_lookup import (
    find_generation_quality_ledger,
    parse_gen_diag_payload,
)


class _Column:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def like(self, pattern: str):
        return ("like", self.name, pattern)

    def desc(self):
        return ("desc", self.name)


class _LogEntry:
    id = _Column("id")
    project_id = _Column("project_id")
    message = _Column("message")


class _Query:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def filter(self, *_args):
        self.calls.append("filter")
        return self

    def order_by(self, *_args):
        self.calls.append("order_by")
        return self

    def limit(self, value: int):
        self.calls.append(f"limit:{value}")
        return self

    def all(self):
        self.calls.append("all")
        return self.rows


class _Db:
    def __init__(self, rows) -> None:
        self.query_obj = _Query(rows)

    def query(self, model):
        assert model is _LogEntry
        return self.query_obj


def test_parse_gen_diag_payload_accepts_embedded_json() -> None:
    payload = parse_gen_diag_payload(
        'prefix GEN_DIAG: {"kind": "generation_quality_ledger", "generation_id": 42}'
    )

    assert payload == {"kind": "generation_quality_ledger", "generation_id": 42}
    assert parse_gen_diag_payload("missing marker") == {}
    assert parse_gen_diag_payload("GEN_DIAG: not json") == {}


def test_find_generation_quality_ledger_matches_entry_generation_id() -> None:
    db = _Db(
        [
            SimpleNamespace(
                message='GEN_DIAG: {"kind": "generation_quality_ledger", "generation_id": 41}'
            ),
            SimpleNamespace(
                message='GEN_DIAG: {"kind": "generation_quality_ledger", "generation_id": 42, "score": 0.91}'
            ),
        ]
    )

    payload = find_generation_quality_ledger(
        db=db,
        log_entry_model=_LogEntry,
        entry=SimpleNamespace(id=42, project_id=11),
    )

    assert payload["generation_id"] == 42
    assert payload["score"] == 0.91
    assert db.query_obj.calls == ["filter", "filter", "order_by", "limit:40", "all"]
