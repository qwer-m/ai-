from __future__ import annotations

from types import SimpleNamespace

from modules.test_generation_components.services.final_case_linked_sources import (
    resolve_final_case_sources,
)


class _KnowledgeRepo:
    def __init__(self) -> None:
        self.by_ids = [
            SimpleNamespace(id=101, doc_type="test_case", content=[{"id": "TC-DOC"}]),
            SimpleNamespace(id=102, doc_type="requirement", content=[{"id": "REQ"}]),
        ]
        self.linked = [
            SimpleNamespace(id=201, doc_type="test_case", content=[{"id": "TC-LINKED"}])
        ]

    def list_project_docs_by_ids(self, *, project_id: int, doc_ids: list[int]):
        assert project_id == 11
        assert doc_ids == [101, 102]
        return self.by_ids

    def list_linked_test_cases_for_sources(self, *, project_id: int, source_doc_ids: list[int]):
        assert project_id == 11
        assert source_doc_ids == [501]
        return self.linked


def _parse(payload):
    return list(payload or [])


def test_resolve_final_case_sources_filters_explicit_test_case_docs() -> None:
    result = resolve_final_case_sources(
        entry=SimpleNamespace(project_id=11),
        final_cases=[{"id": "TC-MANUAL"}],
        final_case_doc_ids=[101, 102],
        source_doc_ids=None,
        include_linked_docs=True,
        knowledge_repo=_KnowledgeRepo(),
        find_linked_final_case_docs_fn=lambda entry: [
            SimpleNamespace(id=999, doc_type="test_case", content=[{"id": "TC-AUTO"}])
        ],
        parse_test_cases_payload_fn=_parse,
    )

    assert result.linked_doc_ids == [101]
    assert result.linked_doc_ids_int == [101]
    assert result.effective_final_cases == [{"id": "TC-MANUAL"}, {"id": "TC-DOC"}]


def test_resolve_final_case_sources_uses_source_doc_links() -> None:
    result = resolve_final_case_sources(
        entry=SimpleNamespace(project_id=11),
        final_cases=None,
        final_case_doc_ids=None,
        source_doc_ids=[501],
        include_linked_docs=True,
        knowledge_repo=_KnowledgeRepo(),
        find_linked_final_case_docs_fn=lambda entry: [
            SimpleNamespace(id=999, doc_type="test_case", content=[{"id": "TC-AUTO"}])
        ],
        parse_test_cases_payload_fn=_parse,
    )

    assert result.linked_doc_ids == [201]
    assert result.effective_final_cases == [{"id": "TC-LINKED"}]
