from __future__ import annotations

from types import SimpleNamespace

from modules.test_generation_components.services.final_case_sample_pool_persistence import (
    merge_sample_pool_samples,
    persist_sample_pool_samples,
)


def test_merge_sample_pool_samples_keeps_latest_when_capped() -> None:
    merged = merge_sample_pool_samples(
        {"samples": [{"id": "old-1"}, {"id": "old-2"}]},
        [{"id": "new-1"}, {"id": "new-2"}],
        max_pool_samples=3,
    )

    assert merged == [{"id": "old-2"}, {"id": "new-1"}, {"id": "new-2"}]


def test_persist_sample_pool_samples_runs_hook_before_reload() -> None:
    calls: list[str] = []
    saved: dict[str, object] = {}

    def load_priority_sample_pool_fn(**kwargs):
        calls.append("load")
        assert kwargs["project_id"] == 11
        assert kwargs["user_id"] == 7
        if "samples" in saved:
            return {"samples": saved["samples"], "updated_at": "after-hook"}
        return {"samples": [{"id": "old-1"}, {"id": "old-2"}], "updated_at": "before"}

    def upsert_priority_sample_pool_fn(**kwargs):
        calls.append("upsert")
        saved["samples"] = kwargs["samples"]
        assert kwargs["generation_id"] == 99
        return SimpleNamespace(id=123)

    result = persist_sample_pool_samples(
        db=object(),
        project_id=11,
        user_id=7,
        generation_id=99,
        samples=[{"id": "new-1"}, {"id": "new-2"}],
        max_pool_samples=3,
        load_priority_sample_pool_fn=load_priority_sample_pool_fn,
        upsert_priority_sample_pool_fn=upsert_priority_sample_pool_fn,
        after_upsert_fn=lambda: calls.append("hook"),
    )

    assert calls == ["load", "upsert", "hook", "load"]
    assert saved["samples"] == [{"id": "old-2"}, {"id": "new-1"}, {"id": "new-2"}]
    assert result.doc.id == 123
    assert result.sample_count == 3
    assert result.updated_at == "after-hook"
