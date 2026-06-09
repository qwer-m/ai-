from __future__ import annotations

import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[4]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.orm import Session

from core.cache_layer.chroma_client import describe_embedding_runtime
from core.db.database import SessionLocal
from core.settings.config import settings
from core.settings.config_manager import config_manager


def _mask_secret(value: str | None) -> str:
    if not value:
        return "not set"
    if len(value) <= 8:
        return "set"
    return f"set ({value[:4]}...{value[-4:]})"


def _print_active_db_config() -> None:
    print("[database active AI config]")
    db: Session = SessionLocal()
    try:
        active_config = config_manager.get_active_config(db)
        if not active_config:
            print("  status: not found")
            print("  effect: environment/default settings will be used")
            return

        print("  status: found")
        print(f"  id: {active_config.id}")
        print(f"  provider: {active_config.provider}")
        print(f"  model_name: {active_config.model_name}")
        print(f"  base_url_set: {bool(active_config.base_url)}")
        print(f"  api_key_encrypted: {bool(active_config.api_key)}")
        try:
            decrypted_key = config_manager.get_decrypted_api_key(active_config)
            print(f"  api_key_decryptable: true (length={len(decrypted_key)})")
        except Exception as exc:
            print(f"  api_key_decryptable: false ({exc})")
        print(f"  updated_at: {active_config.updated_at}")
    except Exception as exc:
        print(f"  status: query failed ({exc})")
    finally:
        db.close()


def _print_environment_config() -> None:
    print("[environment/default AI config]")
    print(f"  DASHSCOPE_API_KEY: {_mask_secret(settings.DASHSCOPE_API_KEY)}")
    print(f"  MODEL_NAME: {settings.MODEL_NAME}")
    print(f"  VL_MODEL_NAME: {settings.VL_MODEL_NAME}")
    print(f"  TURBO_MODEL_NAME: {settings.TURBO_MODEL_NAME}")


def _print_embedding_runtime() -> None:
    print("[embedding runtime]")
    runtime = describe_embedding_runtime()
    for key in (
        "configured_provider",
        "selected_provider",
        "model",
        "base_url_set",
        "base_url_local",
        "api_key_env",
        "api_key_set",
        "would_call_embedding_model",
        "would_call_cloud",
        "fallback_reason",
        "batch_size",
        "max_chars",
    ):
        print(f"  {key}: {runtime.get(key)}")
    print("  note: this diagnostic does not call the embedding model")


def check_active_config() -> None:
    print("=== AI configuration check ===")
    _print_active_db_config()
    print()
    _print_environment_config()
    print()
    _print_embedding_runtime()


if __name__ == "__main__":
    check_active_config()
