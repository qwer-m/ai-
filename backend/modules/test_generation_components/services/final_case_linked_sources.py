from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class FinalCaseSourceResolution:
    linked_docs: list[Any]
    linked_doc_ids: list[Any]
    linked_doc_ids_int: list[int]
    effective_final_cases: list[dict[str, Any]]


def resolve_final_case_sources(
    *,
    entry: Any,
    final_cases: list[dict[str, Any]] | None,
    final_case_doc_ids: list[int] | None,
    source_doc_ids: list[int] | None,
    include_linked_docs: bool,
    knowledge_repo: Any,
    find_linked_final_case_docs_fn: Callable[[Any], list[Any]],
    parse_test_cases_payload_fn: Callable[[Any], list[dict[str, Any]]],
) -> FinalCaseSourceResolution:
    linked_docs: list[Any] = []
    project_id = getattr(entry, "project_id", None)
    if final_case_doc_ids:
        linked_docs = [
            doc
            for doc in knowledge_repo.list_project_docs_by_ids(
                project_id=project_id,
                doc_ids=final_case_doc_ids,
            )
            if getattr(doc, "doc_type", None) == "test_case"
        ]
    elif source_doc_ids:
        linked_docs = knowledge_repo.list_linked_test_cases_for_sources(
            project_id=project_id,
            source_doc_ids=source_doc_ids,
        )
    elif include_linked_docs:
        linked_docs = find_linked_final_case_docs_fn(entry)

    linked_doc_cases: list[dict[str, Any]] = []
    for doc in linked_docs:
        linked_doc_cases.extend(parse_test_cases_payload_fn(getattr(doc, "content", None)))

    effective_final_cases: list[dict[str, Any]] = []
    if final_cases:
        effective_final_cases.extend(parse_test_cases_payload_fn(final_cases))
    effective_final_cases.extend(linked_doc_cases)

    linked_doc_ids = [getattr(doc, "id", None) for doc in linked_docs]
    linked_doc_ids_int = [
        int(doc_id)
        for doc_id in linked_doc_ids
        if doc_id is not None
    ]
    return FinalCaseSourceResolution(
        linked_docs=linked_docs,
        linked_doc_ids=linked_doc_ids,
        linked_doc_ids_int=linked_doc_ids_int,
        effective_final_cases=effective_final_cases,
    )
