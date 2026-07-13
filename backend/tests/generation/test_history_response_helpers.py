from __future__ import annotations

from types import SimpleNamespace

from modules.test_generation_components.services import history_service as history_service_module
from modules.test_generation_components.services.history_response_helpers import (
    build_history_comparison,
    build_priority_sample_pool_mutation_response,
    build_priority_sample_pool_response,
    cap_priority_sample_pool_samples,
    has_history_comparison,
)
from modules.test_generation_components.services.history_service import (
    TestGenerationHistoryService,
)


class _OwnedRepo:
    def get_owned_project(self, *, project_id: int, user_id: int) -> object:
        return object()


def test_history_comparison_prefers_artifact_payload_with_metadata() -> None:
    matched = SimpleNamespace(
        id=7,
        generated_test_case="unrelated generated case",
        modified_test_case="matched modified",
        comparison_result="matched comparison",
        source_filename="matched.xlsx",
        created_at="old",
    )
    artifact = {
        "modified_test_case": "artifact modified",
        "comparison_result": "artifact comparison",
        "source_filename": "artifact.xlsx",
        "updated_at": "2026-07-02T10:00:00",
        "artifact_doc_id": 42,
        "source_file_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "source_file_size": 2048,
        "ocr": {"page_count": 1},
    }

    comparison = build_history_comparison(
        generated_result="generated result",
        matched=matched,
        artifact=artifact,
    )

    assert comparison == {
        "id": None,
        "modified_test_case": "artifact modified",
        "comparison_result": "artifact comparison",
        "source_filename": "artifact.xlsx",
        "created_at": "2026-07-02T10:00:00",
        "artifact_doc_id": 42,
        "source_file_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "source_file_size": 2048,
        "ocr": {"page_count": 1},
    }
    assert has_history_comparison(
        generated_result="generated result",
        matched=matched,
        artifact=artifact,
    )


def test_history_comparison_rejects_short_fuzzy_match_without_artifact() -> None:
    matched = SimpleNamespace(
        id=8,
        generated_test_case="case A plus extra text",
        modified_test_case="manual modified",
        comparison_result="manual comparison",
        source_filename=None,
        created_at="old",
    )

    assert (
        build_history_comparison(
            generated_result="case A",
            matched=matched,
            artifact=None,
        )
        is None
    )
    assert not has_history_comparison(
        generated_result="case A",
        matched=matched,
        artifact=None,
    )


def test_history_list_comparison_flag_only_needs_matched_generated_case() -> None:
    matched = SimpleNamespace(generated_test_case="case A")

    assert has_history_comparison(
        generated_result="case A",
        matched=matched,
        artifact=None,
    )


def test_priority_sample_pool_response_helpers_keep_legacy_shapes() -> None:
    payload = {
        "generation_id": 11,
        "samples": [{"sample_id": "s1"}],
        "patterns": "invalid",
        "signals": [{"signal_id": "sig1"}],
        "learning_events": "invalid",
        "updated_at": "now",
        "artifact_doc_id": 31,
    }

    read_response = build_priority_sample_pool_response(project_id=3, payload=payload)
    mutation_response = build_priority_sample_pool_mutation_response(
        project_id=3,
        payload={**payload, "samples": "invalid"},
        doc=SimpleNamespace(id=32),
    )

    assert read_response == {
        "project_id": 3,
        "generation_id": 11,
        "samples": [{"sample_id": "s1"}],
        "patterns": [],
        "signals": [{"signal_id": "sig1"}],
        "learning_events": [],
        "updated_at": "now",
        "artifact_doc_id": 31,
    }
    assert mutation_response == {
        "project_id": 3,
        "generation_id": 11,
        "samples": [],
        "updated_at": "now",
        "artifact_doc_id": 32,
    }


def test_service_save_priority_sample_pool_caps_samples_and_reloads_response(monkeypatch) -> None:
    service = TestGenerationHistoryService(db=object())
    service.repo = _OwnedRepo()
    captured: dict[str, object] = {}

    def fake_upsert_priority_sample_pool(**kwargs: object) -> SimpleNamespace:
        samples = kwargs["samples"]
        assert isinstance(samples, list)
        captured["sample_count"] = len(samples)
        captured["last_sample"] = samples[-1]
        return SimpleNamespace(id=77)

    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 12,
            "samples": [{"sample_id": "persisted"}],
            "updated_at": "reloaded",
        }

    monkeypatch.setattr(
        history_service_module,
        "upsert_priority_sample_pool",
        fake_upsert_priority_sample_pool,
    )
    monkeypatch.setattr(
        history_service_module,
        "load_priority_sample_pool",
        fake_load_priority_sample_pool,
    )

    status, response = service.save_priority_sample_pool(
        project_id=3,
        user_id=4,
        generation_id=12,
        samples=[{"sample_id": f"s{i}"} for i in range(5001)],
    )

    assert cap_priority_sample_pool_samples([1, 2, 3], max_items=2) == [1, 2]
    assert captured == {"sample_count": 5000, "last_sample": {"sample_id": "s4999"}}
    assert status == "ok"
    assert response == {
        "project_id": 3,
        "generation_id": 12,
        "samples": [{"sample_id": "persisted"}],
        "updated_at": "reloaded",
        "artifact_doc_id": 77,
    }
