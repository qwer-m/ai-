from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from core.db.model_defs import SystemConfig, User
from core.ai.ai_client import get_client_for_user
from core.settings.config_manager import ConfigManager


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        User.__table__.create(bind=engine)
        SystemConfig.__table__.create(bind=engine)
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def test_create_config_replaces_existing_user_configs(db: Session) -> None:
    manager = ConfigManager()
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


def test_user_active_config_does_not_fallback_to_global_config(db: Session) -> None:
    manager = ConfigManager()
    manager.create_config(
        db,
        provider="dashscope",
        model_name="global-old-model",
        api_key="",
        user_id=None,
    )

    assert manager.get_active_config(db, user_id=42) is None


def test_missing_user_config_returns_unconfigured_ai_client(db: Session) -> None:
    client = get_client_for_user(42, db)

    assert client.provider is None
    assert client.generate_response("hi") == "Error: AI Provider not configured."


def test_user_config_follow_main_reuses_credentials_with_independent_review_model(db: Session) -> None:
    manager = ConfigManager()
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

    assert client.review_model == "deepseek-v4-flash"
    assert client.review_provider is not None
    assert client.review_provider.model == "deepseek-v4-flash"
    assert client.review_provider.base_url == "https://new-api.example/v1"
    assert client.turbo_model == ""
    assert client.select_model("req", "review") == "deepseek-v4-flash"
    assert client.select_model("req", "compression") == "glm-5.1"


def test_user_config_independent_review_model_is_explicit_opt_in(db: Session) -> None:
    manager = ConfigManager()
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
