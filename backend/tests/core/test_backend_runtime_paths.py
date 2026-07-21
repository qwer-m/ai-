from pathlib import Path

from core.ai import prompt_loader as prompt_loader_module
from core.cache_layer import chroma_client as chroma_client_module
from modules.knowledge_base_components.document import offline_parse


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_bindings_use_resolved_paths():
    assert prompt_loader_module.prompt_loader._base_path == (
        prompt_loader_module._resolve_prompt_base_path()
    )
    assert chroma_client_module.chroma_client.persist_path == (
        chroma_client_module._resolve_persist_path(None)
    )
    assert offline_parse.OFFLINE_UPLOAD_DIR == offline_parse._resolve_offline_upload_dir()


def test_prompt_path_defaults_to_real_backend_prompts(monkeypatch):
    monkeypatch.delenv("PROMPT_BASE_PATH", raising=False)

    resolved = prompt_loader_module._resolve_prompt_base_path()

    assert resolved == BACKEND_ROOT / "prompts"
    assert resolved.is_dir()


def test_prompt_path_allows_explicit_environment_override(monkeypatch):
    monkeypatch.setenv("PROMPT_BASE_PATH", "runtime/custom_prompts")

    assert prompt_loader_module._resolve_prompt_base_path() == (
        BACKEND_ROOT / "runtime" / "custom_prompts"
    ).resolve()


def test_chroma_path_defaults_to_real_backend_store(monkeypatch):
    monkeypatch.delenv("CHROMA_PERSIST_PATH", raising=False)

    resolved = chroma_client_module._resolve_persist_path(None)

    assert resolved == BACKEND_ROOT / "chroma_db"
    assert resolved.is_dir()


def test_chroma_path_keeps_explicit_environment_override(monkeypatch):
    monkeypatch.setenv("CHROMA_PERSIST_PATH", "runtime/custom_chroma")

    assert chroma_client_module._resolve_persist_path(None) == (
        BACKEND_ROOT / "runtime" / "custom_chroma"
    ).resolve()


def test_offline_upload_path_defaults_to_backend_runtime(monkeypatch):
    monkeypatch.delenv("OFFLINE_UPLOAD_DIR", raising=False)

    resolved = offline_parse._resolve_offline_upload_dir()

    assert resolved == BACKEND_ROOT / "runtime" / "knowledge_uploads"
    assert resolved.parent == BACKEND_ROOT / "runtime"
    assert resolved.parent.is_dir()


def test_offline_upload_path_allows_explicit_environment_override(monkeypatch):
    monkeypatch.setenv("OFFLINE_UPLOAD_DIR", "runtime/custom_uploads")

    assert offline_parse._resolve_offline_upload_dir() == (
        BACKEND_ROOT / "runtime" / "custom_uploads"
    ).resolve()
