from __future__ import annotations

from types import SimpleNamespace

from modules.testing import priority_sample_pool_store as store


def test_remove_priority_sample_from_pool_deletes_only_matching_sample(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {
            "generation_id": 77,
            "samples": [
                {"sample_id": "sample-a", "pattern_summary": "keep A"},
                {"sample_id": "sample-b", "pattern_summary": "delete B"},
                {"sample_id": "sample-c", "pattern_summary": "keep C"},
            ],
        }

    def fake_upsert_priority_sample_pool(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(id=123)

    monkeypatch.setattr(store, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(store, "upsert_priority_sample_pool", fake_upsert_priority_sample_pool)

    doc = store.remove_priority_sample_from_pool(
        db=object(),
        project_id=1,
        user_id=2,
        generation_id=None,
        sample_id="sample-b",
    )

    assert doc is not None
    assert doc.id == 123
    assert captured["project_id"] == 1
    assert captured["user_id"] == 2
    assert captured["generation_id"] == 77
    assert captured["samples"] == [
        {"sample_id": "sample-a", "pattern_summary": "keep A"},
        {"sample_id": "sample-c", "pattern_summary": "keep C"},
    ]


def test_remove_priority_sample_from_pool_returns_none_when_sample_missing(monkeypatch) -> None:
    called = {"upsert": 0}

    def fake_load_priority_sample_pool(**_: object) -> dict[str, object]:
        return {"generation_id": 77, "samples": [{"sample_id": "sample-a"}]}

    def fake_upsert_priority_sample_pool(**_: object) -> SimpleNamespace:
        called["upsert"] += 1
        return SimpleNamespace(id=123)

    monkeypatch.setattr(store, "load_priority_sample_pool", fake_load_priority_sample_pool)
    monkeypatch.setattr(store, "upsert_priority_sample_pool", fake_upsert_priority_sample_pool)

    doc = store.remove_priority_sample_from_pool(
        db=object(),
        project_id=1,
        user_id=2,
        generation_id=None,
        sample_id="missing-sample",
    )

    assert doc is None
    assert called["upsert"] == 0

