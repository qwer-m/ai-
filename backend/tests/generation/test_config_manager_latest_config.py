from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db.models import SystemConfig, User
from core.ai.ai_client_impl import get_client_for_user
from core.settings.config_manager import ConfigManager


def _make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(bind=engine)
    SystemConfig.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def test_create_config_replaces_existing_user_configs() -> None:
    db = _make_session()
    manager = ConfigManager()
    try:
        first = manager.create_config(
            db,
            provider="openai",
            model_name="old-model",
            api_key="",
            base_url="https://old.example/v1",
            user_id=1,
        )
        second = manager.create_config(
            db,
            provider="openai",
            model_name="latest-model",
            api_key="",
            base_url="https://latest.example/v1",
            user_id=1,
        )

        configs = db.query(SystemConfig).filter(SystemConfig.user_id == 1).all()
        assert len(configs) == 1
        assert configs[0].id == second.id
        assert configs[0].id != first.id
        assert configs[0].model_name == "latest-model"
        assert configs[0].base_url == "https://latest.example/v1"
        assert configs[0].is_active == 1
        assert configs[0].version == 2
    finally:
        db.close()


def test_user_active_config_does_not_fallback_to_global_config() -> None:
    db = _make_session()
    manager = ConfigManager()
    try:
        manager.create_config(
            db,
            provider="dashscope",
            model_name="global-old-model",
            api_key="",
            user_id=None,
        )

        assert manager.get_active_config(db, user_id=42) is None
    finally:
        db.close()


def test_missing_user_config_returns_unconfigured_ai_client() -> None:
    db = _make_session()
    try:
        client = get_client_for_user(42, db)

        assert client.provider is None
        assert client.generate_response("hi") == "Error: AI Provider not configured."
    finally:
        db.close()


def test_user_config_follow_main_ignores_stale_review_and_global_turbo_models() -> None:
    db = _make_session()
    manager = ConfigManager()
    try:
        manager.create_config(
            db,
            provider="openai",
            model_name="glm-5.1",
            api_key="",
            base_url="https://new-api.example/v1",
            user_id=1,
            metadata_info={
                "targets": {
                    "review": {
                        "follow_main": True,
                        "model_name": "deepseek-v4-flash",
                    },
                    "turbo": {
                        "follow_main": True,
                    },
                }
            },
        )

        client = get_client_for_user(1, db)

        assert client.review_model == ""
        assert client.turbo_model == ""
        assert client.select_model("req", "review") == "glm-5.1"
        assert client.select_model("req", "compression") == "glm-5.1"
    finally:
        db.close()


def test_user_config_independent_review_model_is_explicit_opt_in() -> None:
    db = _make_session()
    manager = ConfigManager()
    try:
        manager.create_config(
            db,
            provider="openai",
            model_name="glm-5.1",
            api_key="",
            base_url="https://new-api.example/v1",
            user_id=1,
            metadata_info={
                "targets": {
                    "review": {
                        "follow_main": False,
                        "model_name": "deepseek-v4-flash",
                    },
                }
            },
        )

        client = get_client_for_user(1, db)

        assert client.review_model == "deepseek-v4-flash"
        assert client.select_model("req", "review") == "deepseek-v4-flash"
    finally:
        db.close()
