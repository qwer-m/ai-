from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from core.settings.config import settings
from modules.test_generation_components.legacy.json_generation_impl import (
    LegacyGenerationJsonMixin,
)
from modules.test_generation_components.legacy.stream.batch_flow_control import (
    resolve_stream_batch_plan,
)
from modules.test_generation_components.legacy.stream.generation import (
    LegacyGenerationStreamGenerationMixin,
)
from modules.test_generation_components.legacy.stream.prepare import (
    LegacyGenerationStreamPrepareMixin,
)
from routers.automation import test_generation_generate_routes_stream as stream_routes
from schemas.automation.test_generation import TestGenRequest


def _parameter_default(callable_obj, name: str):
    return inspect.signature(callable_obj).parameters[name].default


def test_generation_entrypoints_share_one_default_batch_size() -> None:
    expected_default = settings.TEST_GENERATION_BATCH_SIZE

    assert expected_default == 25
    assert TestGenRequest(requirement="真实需求", project_id=1).batch_size == expected_default
    assert (
        _parameter_default(
            LegacyGenerationStreamGenerationMixin.generate_test_cases_stream,
            "batch_size",
        )
        == expected_default
    )
    assert (
        _parameter_default(
            LegacyGenerationStreamPrepareMixin._stream_prepare_phase,
            "batch_size",
        )
        == expected_default
    )
    assert (
        _parameter_default(
            LegacyGenerationJsonMixin.generate_test_cases_json,
            "batch_size",
        )
        == expected_default
    )

    route_form = _parameter_default(stream_routes.generate_tests_stream, "batch_size")
    assert route_form.default == expected_default


def test_stream_route_forwards_requested_batch_size(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Generator:
        def generate_test_cases_stream(self, **kwargs):
            captured.update(kwargs)
            return iter(["[]"])

    monkeypatch.setattr(stream_routes, "test_generator", _Generator())
    monkeypatch.setattr(stream_routes, "get_owned_project", lambda *args, **kwargs: object())

    response = asyncio.run(
        stream_routes.generate_tests_stream(
            project_id=7,
            doc_type="requirement",
            compress=False,
            expected_count=62,
            batch_size=37,
            enable_sample_pool_feedback=True,
            force=False,
            append=False,
            current_biz_key="",
            only_current_biz=False,
            multi_pass=True,
            generation_mode="",
            requirement_text="真实业务需求",
            file=None,
            prototype_file=None,
            db=object(),
            current_user=SimpleNamespace(id=9),
        )
    )

    assert response.status_code == 200
    assert captured["expected_count"] == 62
    assert captured["batch_size"] == 37


def test_stream_batch_plan_defaults_to_25_and_keeps_last_batch_remainder() -> None:
    plan = resolve_stream_batch_plan(
        expected_count=62,
        batch_size=0,
        append=False,
        start_id=1,
        existing_unique_count=0,
    )

    assert plan["batch_size"] == 25
    assert plan["generation_target_count"] == 62
    assert plan["total_batches"] == 3
    assert [min(plan["batch_size"], 62 - offset) for offset in (0, 25, 50)] == [25, 25, 12]

    append_plan = resolve_stream_batch_plan(
        expected_count=42,
        batch_size=25,
        append=True,
        start_id=31,
        existing_unique_count=30,
    )
    assert append_plan["batch_size"] == 12
    assert append_plan["generation_target_count"] == 12
    assert append_plan["total_batches"] == 1

