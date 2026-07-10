from __future__ import annotations

from routers.system.config_helpers import resolve_target_credentials


def test_independent_vision_model_can_reuse_main_openai_gateway_credentials() -> None:
    provider, api_key, base_url, follow_main = resolve_target_credentials(
        target_key="vision",
        follow_main=False,
        submitted_provider="openai",
        submitted_api_key=None,
        submitted_base_url=None,
        main_provider="openai",
        main_api_key="sk-main",
        main_base_url="https://new-api.example.com/v1",
        active_config=None,
    )

    assert provider == "openai"
    assert api_key == "sk-main"
    assert base_url == "https://new-api.example.com/v1"
    assert follow_main is False
