from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.cache_layer.chroma_client import (
    DynamicEmbeddingFunction,
    EmbeddingProviderConfig,
    DashScopeEmbeddingFunction,
    build_embedding_provider_config,
    describe_embedding_runtime,
    select_embedding_function,
)
from schemas.automation.test_generation import TestComparisonRequest, TestGenRequest


def _run_python_with_backend_path(code: str, env_updates: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_updates)
    backend_path = str(Path.cwd() / "backend")
    env["PYTHONPATH"] = backend_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    timeout_seconds = float(env.get("DEPENDENCY_HYGIENE_SUBPROCESS_TIMEOUT", "30"))
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _loads_last_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_test_generation_request_is_not_collected_as_pytest_test_class() -> None:
    assert TestGenRequest.__test__ is False
    assert TestComparisonRequest.__test__ is False

    request = TestGenRequest(requirement="req", project_id=1)
    assert request.expected_count == 20


@pytest.mark.parametrize(
    ("module_path", "removed_parts_dir"),
    [
        (
            Path("backend/routers/orchestration/evaluation_history_routes_impl.py"),
            Path("backend/routers/orchestration/evaluation_history_routes_impl_parts"),
        ),
        (
            Path("backend/routers/system/common_impl.py"),
            Path("backend/routers/system/common_impl_parts"),
        ),
        (
            Path("backend/modules/domain/knowledge_base_impl.py"),
            Path("backend/modules/domain/knowledge_base_impl_parts"),
        ),
        (
            Path("backend/modules/orchestration_components/pipeline_runtime/agent_ops_impl.py"),
            Path("backend/modules/orchestration_components/pipeline_runtime/agent_ops_impl_parts"),
        ),
        (
            Path("backend/routers/system/config_routes_runtime_impl.py"),
            Path("backend/routers/system/config_routes_runtime_impl_parts"),
        ),
        (
            Path("backend/tests/rag/generation/test_hybrid_empty_guard.py"),
            Path("backend/tests/rag/generation/test_hybrid_empty_guard_impl_parts"),
        ),
        (
            Path("backend/routers/automation/test_generation_generate_routes_impl.py"),
            Path("backend/routers/automation/test_generation_generate_routes_impl_parts"),
        ),
        (
            Path("backend/modules/test_generation_components/legacy/context/hybrid_impl.py"),
            Path("backend/modules/test_generation_components/legacy/context/hybrid_impl_parts"),
        ),
        (
            Path("backend/core/ai/ai_client_impl.py"),
            Path("backend/core/ai/ai_client_impl_parts"),
        ),
        (
            Path("backend/modules/test_generation_components/legacy/json_generation_impl.py"),
            Path("backend/modules/test_generation_components/legacy/json_generation_impl_parts"),
        ),
        (
            Path("backend/modules/test_generation_components/postprocess/result_postprocess_streaming_impl.py"),
            Path("backend/modules/test_generation_components/postprocess/result_postprocess_streaming_impl_parts"),
        ),
    ],
)
def test_migrated_part_loader_modules_are_static_source(
    module_path: Path,
    removed_parts_dir: Path,
) -> None:
    text = module_path.read_text(encoding="utf-8")

    assert "exec(" + "compile(" not in text
    assert "_source_chunks" not in text
    assert not removed_parts_dir.exists()


def test_backend_source_tree_has_no_part_files() -> None:
    part_files = sorted(str(path) for path in Path("backend").rglob("*.part"))

    assert part_files == []


def test_backend_python_files_do_not_use_utf8_bom() -> None:
    bom_files = sorted(
        str(path)
        for path in Path("backend").rglob("*.py")
        if path.read_bytes().startswith(b"\xef\xbb\xbf")
    )

    assert bom_files == []


def test_testing_test_generation_facade_import_is_lightweight() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json
import sys

importlib.import_module("modules.testing.test_generation")

forbidden = [
    "core.ai.ai_client",
    "core.db.database",
    "core.db.models",
    "core.cache_layer.cache",
    "modules.domain.knowledge_base",
    "modules.memory_fabric.contracts.memory_context",
    "modules.orchestration.context_orchestrator",
    "modules.testing.test_generation_components.legacy_generation_impl",
    "modules.test_generation_components.legacy_generation_impl",
    "modules.test_generation_components.legacy.json_generation_impl",
    "modules.test_generation_components.legacy.stream.generation",
    "modules.test_generation_components.legacy.stream.prepare",
    "modules.test_generation_components.legacy.stream.batches",
    "modules.test_generation_components.legacy.stream.persist",
]
print(json.dumps({"loaded": [name for name in forbidden if name in sys.modules]}))
""",
        {},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Successfully connected to MySQL" not in result.stdout + result.stderr
    assert "Redis connected:" not in result.stdout + result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload["loaded"] == []


def test_testing_test_generation_facade_resolves_legacy_from_canonical_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    facade = importlib.import_module("modules.testing.test_generation")
    canonical_target = "modules.test_generation_components.legacy_generation_impl"
    calls: list[str] = []

    class _FakeTestGenerationModule:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs
            self.status = "ready"

    class _FakeLegacyModule:
        TestGenerationModule = _FakeTestGenerationModule

    def _fake_import_module(name: str):
        calls.append(name)
        if name == canonical_target:
            return _FakeLegacyModule
        raise AssertionError(f"unexpected import target: {name}")

    monkeypatch.setattr(facade, "import_module", _fake_import_module)

    module = facade.TestGenerationModule("req", project_id=1)
    assert isinstance(module, _FakeTestGenerationModule)
    assert module.args == ("req",)
    assert module.kwargs == {"project_id": 1}

    lazy = facade.LazyTestGenerator()
    assert lazy.status == "ready"
    lazy.extra = "ok"
    assert lazy.extra == "ok"
    assert calls == [canonical_target, canonical_target]


def test_legacy_json_generation_impl_import_is_lightweight() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json
import sys

importlib.import_module("modules.test_generation_components.legacy.json_generation_impl")

forbidden = [
    "core.ai.ai_client",
    "core.db.models",
    "core.db.database",
    "core.cache_layer.cache",
    "modules.domain.stage25_switches",
    "modules.domain.knowledge_base",
    "modules.memory_fabric.contracts.memory_context",
    "modules.memory_fabric.runtime.diagnostics",
    "modules.memory_fabric.runtime.factory",
    "modules.test_generation_components.control.build_feedback_control_state",
    "modules.test_generation_components.prompting.structured_context",
    "modules.test_generation_components.legacy.stream.generation",
    "modules.test_generation_components.legacy.stream.prepare",
    "modules.test_generation_components.legacy.stream.batches",
    "modules.test_generation_components.legacy.stream.persist",
]
print(json.dumps({"loaded": [name for name in forbidden if name in sys.modules]}))
""",
        {},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Successfully connected to MySQL" not in result.stdout + result.stderr
    assert "Redis connected:" not in result.stdout + result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload["loaded"] == []


def test_legacy_stream_generation_imports_are_lightweight() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json
import sys

modules = [
    "modules.test_generation_components.legacy.stream.generation",
    "modules.test_generation_components.legacy.stream.prepare",
    "modules.test_generation_components.legacy.stream.batches",
    "modules.test_generation_components.legacy.stream.persist",
    "modules.testing.test_generation_components.legacy.stream.generation",
    "modules.testing.test_generation_components.legacy.stream.prepare",
    "modules.testing.test_generation_components.legacy.stream.batches",
    "modules.testing.test_generation_components.legacy.stream.persist",
]
for module in modules:
    importlib.import_module(module)

forbidden = [
    "core.ai.ai_client",
    "core.db.models",
    "core.db.database",
    "core.cache_layer.cache",
    "modules.domain.stage25_switches",
    "modules.domain.knowledge_base",
    "modules.memory_fabric.contracts.memory_context",
    "modules.memory_fabric.runtime.diagnostics",
    "modules.memory_fabric.runtime.factory",
    "modules.test_generation_components.control.build_feedback_control_state",
    "modules.test_generation_components.prompting.structured_context",
    "modules.test_generation_components.postprocess.result_postprocess",
]
print(json.dumps({"loaded": [name for name in forbidden if name in sys.modules]}))
""",
        {},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Successfully connected to MySQL" not in result.stdout + result.stderr
    assert "Redis connected:" not in result.stdout + result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload["loaded"] == []


def test_legacy_generation_impl_import_is_lightweight() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json
import sys

modules = [
    "modules.test_generation_components.legacy_generation_impl",
    "modules.testing.test_generation_components.legacy_generation_impl",
]
for module in modules:
    importlib.import_module(module)

forbidden = [
    "core.ai.ai_client",
    "core.db.models",
    "core.db.database",
    "core.cache_layer.cache",
    "modules.domain.knowledge_base",
    "modules.domain.stage25_switches",
    "modules.memory_fabric.contracts.memory_context",
    "modules.memory_fabric.runtime.factory",
]
print(json.dumps({"loaded": [name for name in forbidden if name in sys.modules]}))
""",
        {},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Successfully connected to MySQL" not in result.stdout + result.stderr
    assert "Redis connected:" not in result.stdout + result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload["loaded"] == []


def test_stream_quality_gate_summary_does_not_load_heavy_runtime() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json
import sys

persist = importlib.import_module("modules.test_generation_components.legacy.stream.persist")
summary = persist.summarize_case_quality_gate([
    {
        "id": "TC-001",
        "priority_final": "P0",
        "expected_result": "Save succeeds and status becomes active",
    }
])

forbidden = [
    "core.ai.ai_client",
    "core.db.models",
    "core.db.database",
    "core.cache_layer.cache",
]
print(json.dumps({
    "passed": summary.get("passed"),
    "loaded": [name for name in forbidden if name in sys.modules],
}))
""",
        {},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Successfully connected to MySQL" not in result.stdout + result.stderr
    assert "Redis connected:" not in result.stdout + result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload["passed"] is True
    assert payload["loaded"] == []


def test_rag_generation_tests_do_not_use_star_import_wrappers() -> None:
    wrappers = sorted(
        str(path)
        for path in Path("backend/tests/rag/generation").glob("test_*.py")
        if " import *" in path.read_text(encoding="utf-8")
    )

    assert wrappers == []


def test_runtime_source_tree_has_no_exec_compile_loaders() -> None:
    current_file = Path(__file__).resolve()
    offenders: list[str] = []
    needle = "exec(" + "compile("

    for path in Path("backend").rglob("*.py"):
        if path.resolve() == current_file:
            continue
        text = path.read_text(encoding="utf-8")
        if needle in text:
            offenders.append(str(path))

    assert offenders == []


def test_test_generation_components_use_relative_internal_imports() -> None:
    root = Path("backend/modules/test_generation_components")
    allowed: set[Path] = set()
    offenders: list[str] = []
    needles = (
        "from modules.test_generation_components",
        "import modules.test_generation_components",
    )

    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            offenders.append(str(path))

    assert offenders == []


def test_test_generation_components_do_not_import_through_testing_compat_path() -> None:
    root = Path("backend/modules/test_generation_components")
    offenders: list[str] = []
    needles = (
        "from modules.testing.test_generation_components",
        "import modules.testing.test_generation_components",
    )

    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            offenders.append(str(path))

    assert offenders == []


def test_testing_compat_package_reuses_canonical_test_generation_modules() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json

pairs = [
    (
        "modules.test_generation_components.prompting.structured_context",
        "modules.testing.test_generation_components.prompting.structured_context",
    ),
    (
        "modules.test_generation_components.control.feedback_control_state",
        "modules.testing.test_generation_components.control.feedback_control_state",
    ),
    (
        "modules.test_generation_components.legacy.json_generation",
        "modules.testing.test_generation_components.legacy.json_generation",
    ),
    (
        "modules.test_generation_components.legacy.context.hybrid",
        "modules.testing.test_generation_components.legacy.context.hybrid",
    ),
]
print(json.dumps({
    left + "|" + right: importlib.import_module(left) is importlib.import_module(right)
    for left, right in pairs
}, sort_keys=True))
""",
        {},
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload
    assert all(payload.values())


def test_testing_compat_package_aliases_when_historical_path_imports_first() -> None:
    result = _run_python_with_backend_path(
        """
import importlib
import json

pairs = [
    (
        "modules.test_generation_components.legacy.stream.generation",
        "modules.testing.test_generation_components.legacy.stream.generation",
    ),
    (
        "modules.test_generation_components.legacy.stream.prepare",
        "modules.testing.test_generation_components.legacy.stream.prepare",
    ),
    (
        "modules.test_generation_components.legacy.stream.batches",
        "modules.testing.test_generation_components.legacy.stream.batches",
    ),
    (
        "modules.test_generation_components.legacy.stream.persist",
        "modules.testing.test_generation_components.legacy.stream.persist",
    ),
    (
        "modules.test_generation_components.postprocess.result_postprocess",
        "modules.testing.test_generation_components.postprocess.result_postprocess",
    ),
]
payload = {}
for canonical, historical in pairs:
    historical_mod = importlib.import_module(historical)
    canonical_mod = importlib.import_module(canonical)
    payload[canonical + "|" + historical] = canonical_mod is historical_mod
print(json.dumps(payload, sort_keys=True))
""",
        {},
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload
    assert all(payload.values())


def test_dashscope_embedding_function_exposes_chroma_config_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-key")

    embedding_fn = DashScopeEmbeddingFunction("direct-secret-key")
    config = embedding_fn.get_config()
    serialized = json.dumps(config, sort_keys=True)

    assert embedding_fn.name() == "dashscope-text-embedding-v1"
    assert config["api_key_env"] == "DASHSCOPE_API_KEY"
    assert "direct-secret-key" not in serialized

    rebuilt = DashScopeEmbeddingFunction.build_from_config(config)
    assert rebuilt.api_key == "unit-test-key"


def test_dashscope_embedding_function_rejects_invalid_config() -> None:
    with pytest.raises(ValueError):
        DashScopeEmbeddingFunction.validate_config([])


def test_settings_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from core.settings.config import settings
print(json.dumps({
    "embedding_timeout": settings.EMBEDDING_TIMEOUT_SECONDS,
    "max_tokens": settings.MAX_TOKENS,
    "redis_port": settings.REDIS_PORT,
    "state_coverage": settings.EXECUTION_PLAN_MIN_STATE_FIELD_COVERAGE,
}))
""",
        {
            "EMBEDDING_TIMEOUT_SECONDS": "bad",
            "MAX_TOKENS": "bad",
            "REDIS_PORT": "70000",
            "EXECUTION_PLAN_MIN_STATE_FIELD_COVERAGE": "-0.1",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "embedding_timeout": 30.0,
        "max_tokens": 10000,
        "redis_port": 65535,
        "state_coverage": 0.0,
    }


def test_chroma_embedding_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from core.cache_layer import chroma_client
print(json.dumps({
    "batch_size": chroma_client.DEFAULT_EMBED_BATCH_SIZE,
    "max_chars": chroma_client.DEFAULT_EMBED_MAX_CHARS,
}))
""",
        {
            "DASHSCOPE_EMBED_BATCH_SIZE": "bad",
            "DASHSCOPE_EMBED_MAX_CHARS": "999999",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {"batch_size": 25, "max_chars": 2048}


def test_redis_pool_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from core.cache_layer import redis_pool
print(json.dumps({
    "port": redis_pool.REDIS_PORT,
    "db": redis_pool.REDIS_DB,
}))
""",
        {
            "REDIS_PORT": "bad",
            "REDIS_DB": "-1",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {"port": 6379, "db": 0}


def test_redis_configuration_preserves_remote_db_and_password_for_celery() -> None:
    result = _run_python_with_backend_path(
        """
import json
import celery_config
from core.cache_layer import redis_pool
print(json.dumps({
    "broker_url": celery_config.celery_app.conf.broker_url,
    "result_backend": celery_config.celery_app.conf.result_backend,
    "pool_host": redis_pool.REDIS_HOST,
    "pool_port": redis_pool.REDIS_PORT,
    "pool_db": redis_pool.REDIS_DB,
    "pool_password_set": bool(redis_pool.REDIS_PASSWORD),
}))
""",
        {
            "REDIS_HOST": "redis.example.test",
            "REDIS_PORT": "6380",
            "REDIS_DB": "2",
            "REDIS_PASSWORD": "pa ss/word",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "broker_url": "redis://:pa%20ss%2Fword@redis.example.test:6380/2",
        "result_backend": "redis://:pa%20ss%2Fword@redis.example.test:6380/2",
        "pool_host": "redis.example.test",
        "pool_port": 6380,
        "pool_db": 2,
        "pool_password_set": True,
    }


def test_redis_url_overrides_split_redis_configuration() -> None:
    redis_url = "redis://:secret@example.redis.test:6381/3"
    result = _run_python_with_backend_path(
        """
import json
import celery_config
from core.cache_layer import redis_pool
print(json.dumps({
    "broker_url": celery_config.celery_app.conf.broker_url,
    "result_backend": celery_config.celery_app.conf.result_backend,
    "redis_url": redis_pool.REDIS_URL,
}))
""",
        {
            "REDIS_URL": redis_url,
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6379",
            "REDIS_DB": "0",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "broker_url": redis_url,
        "result_backend": redis_url,
        "redis_url": redis_url,
    }


def test_feedback_control_import_tolerates_invalid_priority_pool_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
import importlib
mod = importlib.import_module("modules.test_generation_components.control.build_feedback_control_state")
print(json.dumps({
    "top_k": mod._MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    "cluster_cap": mod._MAX_PRIORITY_POOL_CLUSTER_CAP,
    "positive_min": mod._PRIORITY_POOL_MIN_POSITIVE_TOP_K,
    "negative_max": mod._PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    "confidence": mod._MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
}))
""",
        {
            "TESTGEN_PRIORITY_POOL_RETRIEVAL_TOP_K": "bad",
            "TESTGEN_PRIORITY_POOL_CLUSTER_CAP": "999",
            "TESTGEN_PRIORITY_POOL_MIN_POSITIVE_TOP_K": "bad",
            "TESTGEN_PRIORITY_POOL_MAX_NEGATIVE_TOP_K": "-3",
            "TESTGEN_PRIORITY_POOL_MIN_PATTERN_CONFIDENCE": "1.7",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "top_k": 5,
        "cluster_cap": 5,
        "positive_min": 2,
        "negative_max": 0,
        "confidence": 1.0,
    }


def test_hybrid_context_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from modules.test_generation_components.context.hybrid_context_builder import HYBRID_CONFIG
print(json.dumps({
    "snapshot_max_tokens": HYBRID_CONFIG.snapshot_max_tokens,
    "rag_max_tokens": HYBRID_CONFIG.rag_max_tokens,
    "total_max_tokens": HYBRID_CONFIG.total_max_tokens,
    "rag_top_k": HYBRID_CONFIG.rag_top_k,
    "snapshot_insufficient_tokens": HYBRID_CONFIG.snapshot_insufficient_tokens,
}))
""",
        {
            "RAG_HYBRID_SNAPSHOT_MAX_TOKENS": "bad",
            "RAG_HYBRID_RAG_MAX_TOKENS": "100",
            "RAG_HYBRID_TOTAL_MAX_TOKENS": "bad",
            "RAG_HYBRID_RAG_TOP_K": "999",
            "RAG_HYBRID_SNAPSHOT_MIN_TOKENS": "-10",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "snapshot_max_tokens": 2000,
        "rag_max_tokens": 300,
        "total_max_tokens": 3200,
        "rag_top_k": 5,
        "snapshot_insufficient_tokens": 200,
    }


def test_retrieval_retry_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from modules.knowledge_base_components.retrieval.retrieval_retry import STABILITY_CONFIG
print(json.dumps({
    "max_retrieve_attempts": STABILITY_CONFIG.max_retrieve_attempts,
    "retry_backoff_ms": STABILITY_CONFIG.retry_backoff_ms,
    "low_rel_filter_enabled": STABILITY_CONFIG.low_rel_filter_enabled,
    "low_rel_top1_threshold": STABILITY_CONFIG.low_rel_top1_threshold,
    "low_rel_topk_avg_threshold": STABILITY_CONFIG.low_rel_topk_avg_threshold,
    "low_rel_topk": STABILITY_CONFIG.low_rel_topk,
}))
""",
        {
            "RAG_RETRIEVE_MAX_ATTEMPTS": "bad",
            "RAG_RETRY_BACKOFF_MS": "1",
            "RAG_LOW_REL_FILTER_ENABLED": "false",
            "RAG_LOW_REL_TOP1_THRESHOLD": "bad",
            "RAG_LOW_REL_TOPK_AVG_THRESHOLD": "1.8",
            "RAG_LOW_REL_TOPK": "-2",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "max_retrieve_attempts": 2,
        "retry_backoff_ms": 50,
        "low_rel_filter_enabled": False,
        "low_rel_top1_threshold": 0.85,
        "low_rel_topk_avg_threshold": 1.0,
        "low_rel_topk": 1,
    }


def test_snapshot_builder_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from modules.knowledge_base_components.snapshot.snapshot_builder import SNAPSHOT_CONFIG
print(json.dumps({
    "max_docs": SNAPSHOT_CONFIG.max_docs,
    "max_snapshot_chars": SNAPSHOT_CONFIG.max_snapshot_chars,
    "incremental_doc_threshold": SNAPSHOT_CONFIG.incremental_doc_threshold,
    "incremental_ratio_threshold": SNAPSHOT_CONFIG.incremental_ratio_threshold,
    "full_rebuild_hours": SNAPSHOT_CONFIG.full_rebuild_hours,
    "max_incremental_merges": SNAPSHOT_CONFIG.max_incremental_merges,
    "input_soft_limit": SNAPSHOT_CONFIG.input_soft_limit,
    "single_doc_max_chars": SNAPSHOT_CONFIG.single_doc_max_chars,
    "batch_max_docs": SNAPSHOT_CONFIG.batch_max_docs,
    "final_merge_limit": SNAPSHOT_CONFIG.final_merge_limit,
    "enqueue_cooldown_seconds": SNAPSHOT_CONFIG.enqueue_cooldown_seconds,
}))
""",
        {
            "RAG_SNAPSHOT_MAX_DOCS": "bad",
            "RAG_SNAPSHOT_MAX_CHARS": "1",
            "RAG_SNAPSHOT_INCREMENTAL_DOC_THRESHOLD": "-10",
            "RAG_SNAPSHOT_INCREMENTAL_RATIO_THRESHOLD": "2.0",
            "RAG_SNAPSHOT_FULL_REBUILD_HOURS": "bad",
            "RAG_SNAPSHOT_MAX_INCREMENTAL_MERGES": "0",
            "RAG_SNAPSHOT_INPUT_SOFT_LIMIT": "100",
            "RAG_SNAPSHOT_SINGLE_DOC_MAX_CHARS": "bad",
            "RAG_SNAPSHOT_BATCH_MAX_DOCS": "-5",
            "RAG_SNAPSHOT_FINAL_MERGE_LIMIT": "100",
            "RAG_SNAPSHOT_ENQUEUE_COOLDOWN_SECONDS": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "max_docs": 200,
        "max_snapshot_chars": 4000,
        "incremental_doc_threshold": 1,
        "incremental_ratio_threshold": 1.0,
        "full_rebuild_hours": 24,
        "max_incremental_merges": 1,
        "input_soft_limit": 4000,
        "single_doc_max_chars": 12000,
        "batch_max_docs": 1,
        "final_merge_limit": 4000,
        "enqueue_cooldown_seconds": 5,
    }


def test_context_guard_imports_tolerate_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from modules.test_generation_components.context.hybrid_guard import HYBRID_EMPTY_GUARD_CONFIG
from modules.test_generation_components.context.snapshot_wait_gate import SNAPSHOT_WAIT_GATE_CONFIG
print(json.dumps({
    "snapshot_wait_timeout": SNAPSHOT_WAIT_GATE_CONFIG.wait_timeout_sec,
    "snapshot_poll_interval": SNAPSHOT_WAIT_GATE_CONFIG.poll_interval_ms,
    "hybrid_retry_timeout": HYBRID_EMPTY_GUARD_CONFIG.sync_snapshot_retry_timeout_sec,
}))
""",
        {
            "RAG_GENERATION_SNAPSHOT_WAIT_TIMEOUT_SEC": "bad",
            "RAG_GENERATION_SNAPSHOT_POLL_INTERVAL_MS": "1",
            "RAG_SYNC_SNAPSHOT_RETRY_TIMEOUT_SEC": "bad",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "snapshot_wait_timeout": 30,
        "snapshot_poll_interval": 100,
        "hybrid_retry_timeout": 8,
    }


def test_stage25_switch_import_tolerates_invalid_numeric_env_values() -> None:
    result = _run_python_with_backend_path(
        """
import json
from modules.domain.stage25_switches import STAGE25_SWITCHES
print(json.dumps({
    "fidelity_min_retention": STAGE25_SWITCHES.fidelity_min_retention,
    "retrieval_profile_topk": STAGE25_SWITCHES.retrieval_profile_topk,
}))
""",
        {
            "RAG_STAGE25_FIDELITY_MIN_RETENTION": "bad",
            "RAG_STAGE25_RETRIEVAL_PROFILE_TOPK": "bad",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = _loads_last_stdout_json(result)
    assert payload == {
        "fidelity_min_retention": 0.7,
        "retrieval_profile_topk": 10,
    }


def test_embedding_provider_defaults_to_local_even_when_dashscope_key_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "core.cache_layer.chroma_client.embedding_functions.DefaultEmbeddingFunction",
        lambda: sentinel,
    )

    embedding_fn, provider = select_embedding_function(provider="local", api_key="unit-test-key")

    assert provider == "local"
    assert embedding_fn is sentinel


def test_local_embedding_provider_does_not_keep_cloud_api_key() -> None:
    config = build_embedding_provider_config(provider="local", api_key="unused-secret")

    assert config.provider == "local"
    assert config.api_key_env == ""
    assert config.api_key == ""


def test_embedding_provider_uses_dashscope_only_when_explicitly_selected() -> None:
    embedding_fn, provider = select_embedding_function(provider="dashscope", api_key="unit-test-key")

    assert provider == "dashscope"
    assert isinstance(embedding_fn, DashScopeEmbeddingFunction)
    assert embedding_fn.api_key == "unit-test-key"


def test_embedding_provider_dashscope_without_key_falls_back_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "core.cache_layer.chroma_client.embedding_functions.DefaultEmbeddingFunction",
        lambda: sentinel,
    )

    embedding_fn, provider = select_embedding_function(provider="dashscope", api_key="")

    assert provider == "local"
    assert embedding_fn is sentinel


def test_embedding_provider_can_follow_text_model_params_for_openai_compatible() -> None:
    embedding_fn, provider = select_embedding_function(
        provider="follow_text",
        text_model_config={
            "provider": "openai",
            "api_key": "text-model-key",
            "base_url": "https://example.test/v1",
            "embedding_model": "text-embedding-test",
        },
    )

    assert provider == "openai_compatible"
    assert isinstance(embedding_fn, DynamicEmbeddingFunction)
    assert embedding_fn.config.model == "text-embedding-test"
    assert embedding_fn.config.base_url == "https://example.test/v1"
    assert "text-model-key" not in json.dumps(embedding_fn.get_config(), sort_keys=True)


def test_openai_compatible_embedding_config_uses_openai_key_env_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.cache_layer.chroma_client.settings.EMBEDDING_API_KEY_ENV", "")

    config = build_embedding_provider_config(
        provider="openai",
        api_key="secret-key",
        base_url="https://example.test/v1",
        model="text-embedding-test",
    )

    assert config.provider == "openai_compatible"
    assert config.api_key_env == "OPENAI_API_KEY"


def test_openai_compatible_embedding_config_does_not_reuse_dashscope_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.cache_layer.chroma_client.settings.EMBEDDING_API_KEY_ENV", "")
    monkeypatch.setattr("core.cache_layer.chroma_client.settings.DASHSCOPE_API_KEY", "dashscope-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config = build_embedding_provider_config(
        provider="openai",
        base_url="https://example.test/v1",
        model="text-embedding-test",
    )

    assert config.provider == "openai_compatible"
    assert config.api_key_env == "OPENAI_API_KEY"
    assert config.api_key == ""


def test_embedding_runtime_description_marks_local_as_non_cloud() -> None:
    runtime = describe_embedding_runtime(provider="local", api_key="unused-secret")

    assert runtime["configured_provider"] == "local"
    assert runtime["selected_provider"] == "local"
    assert runtime["would_call_cloud"] is False
    assert runtime["would_call_embedding_model"] is False
    assert runtime["base_url_local"] is False
    assert runtime["api_key_env"] == ""
    assert runtime["api_key_set"] is False
    assert "unused-secret" not in json.dumps(runtime, sort_keys=True)


def test_embedding_runtime_description_marks_explicit_dashscope_as_cloud() -> None:
    runtime = describe_embedding_runtime(provider="dashscope", api_key="unit-test-key")

    assert runtime["configured_provider"] == "dashscope"
    assert runtime["selected_provider"] == "dashscope"
    assert runtime["api_key_env"] == "DASHSCOPE_API_KEY"
    assert runtime["would_call_cloud"] is True
    assert runtime["would_call_embedding_model"] is True
    assert runtime["fallback_reason"] == ""


def test_embedding_runtime_description_explains_dashscope_key_fallback() -> None:
    runtime = describe_embedding_runtime(provider="dashscope", api_key="")

    assert runtime["configured_provider"] == "dashscope"
    assert runtime["selected_provider"] == "local"
    assert runtime["would_call_cloud"] is False
    assert runtime["would_call_embedding_model"] is False
    assert runtime["fallback_reason"] == "dashscope_api_key_missing"


def test_embedding_runtime_description_explains_openai_compatible_fallback() -> None:
    runtime = describe_embedding_runtime(
        provider="openai",
        api_key="unit-test-key",
        base_url="",
        model="text-embedding-test",
    )

    assert runtime["configured_provider"] == "openai_compatible"
    assert runtime["selected_provider"] == "local"
    assert runtime["would_call_cloud"] is False
    assert runtime["would_call_embedding_model"] is False
    assert runtime["fallback_reason"] == "openai_compatible_base_url_or_model_missing"


def test_embedding_runtime_description_marks_openai_compatible_as_cloud() -> None:
    runtime = describe_embedding_runtime(
        provider="openai",
        api_key="unit-test-key",
        base_url="https://example.test/v1",
        model="text-embedding-test",
    )

    assert runtime["configured_provider"] == "openai_compatible"
    assert runtime["selected_provider"] == "openai_compatible"
    assert runtime["api_key_env"] == "OPENAI_API_KEY"
    assert runtime["base_url_set"] is True
    assert runtime["base_url_local"] is False
    assert runtime["would_call_embedding_model"] is True
    assert runtime["would_call_cloud"] is True
    assert runtime["fallback_reason"] == ""
    assert "unit-test-key" not in json.dumps(runtime, sort_keys=True)


def test_embedding_runtime_description_marks_local_openai_compatible_as_non_cloud() -> None:
    runtime = describe_embedding_runtime(
        provider="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="nomic-embed-text",
    )

    assert runtime["configured_provider"] == "openai_compatible"
    assert runtime["selected_provider"] == "openai_compatible"
    assert runtime["base_url_local"] is True
    assert runtime["would_call_embedding_model"] is True
    assert runtime["would_call_cloud"] is False


def test_openai_compatible_dynamic_embedding_calls_embedding_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                    {"index": 1, "embedding": [0.3, 0.4]},
                ]
            }

    class _Client:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return _Response()

    monkeypatch.setattr("core.cache_layer.chroma_client.httpx.Client", _Client)

    embedding_fn = DynamicEmbeddingFunction(
        EmbeddingProviderConfig(
            provider="openai_compatible",
            model="text-embedding-test",
            api_key="secret-key",
            base_url="https://example.test/v1",
            batch_size=2,
            timeout_seconds=12.0,
        )
    )

    result = embedding_fn(["alpha", "beta"])
    assert [list(row) for row in result] == [[0.1, 0.2], [0.3, 0.4]]
    assert calls == [
        {
            "url": "https://example.test/v1/embeddings",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer secret-key"},
            "json": {"model": "text-embedding-test", "input": ["alpha", "beta"]},
            "timeout": 12.0,
        }
    ]
