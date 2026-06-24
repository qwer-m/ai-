import pytest

from modules.testing.ui_automation import inject_ai_locate_function, reject_model_error_script
from modules.testing.ui_automation_prompts import build_ai_locate_function


HELPER = "def ai_locate_element(screenshot_path, element_description):\n    return (1, 2)"


def test_inject_ai_locator_before_async_web_main() -> None:
    script = "import asyncio\n\nasync def main():\n    print('run')\n\nasyncio.run(main())"

    injected = inject_ai_locate_function(script, HELPER, automation_type="web")

    assert "def ai_locate_element" in injected
    assert injected.index("def ai_locate_element") < injected.index("async def main")


def test_inject_ai_locator_handles_app_main_with_arguments() -> None:
    script = "from appium import webdriver\n\ndef main(driver):\n    print(driver)\n"

    injected = inject_ai_locate_function(script, HELPER, automation_type="app")

    assert "def ai_locate_element" in injected
    assert injected.index("def ai_locate_element") < injected.index("def main(driver)")


def test_inject_ai_locator_before_main_guard_when_no_main_function() -> None:
    script = "print('prepare')\n\nif __name__ == '__main__':\n    print('run')"

    injected = inject_ai_locate_function(script, HELPER, automation_type="app")

    assert "def ai_locate_element" in injected
    assert injected.index("def ai_locate_element") < injected.index("if __name__")


def test_inject_ai_locator_prepends_top_level_script_without_main() -> None:
    script = "print('top level script')"

    injected = inject_ai_locate_function(script, HELPER, automation_type="app")

    assert injected.startswith("def ai_locate_element")
    assert "print('top level script')" in injected


def test_inject_ai_locator_rejects_empty_script() -> None:
    with pytest.raises(ValueError, match="empty"):
        inject_ai_locate_function("", HELPER, automation_type="web")


def test_reject_model_error_script_blocks_exception_text() -> None:
    with pytest.raises(ValueError, match="AI script generation failed"):
        reject_model_error_script("Exception occurred: The read operation timed out")


def test_ai_locator_helper_does_not_embed_bearer_token() -> None:
    helper = build_ai_locate_function(token="secret-token", image_model=None)

    assert "secret-token" not in helper
    assert "UI_AUTOMATION_TOKEN" in helper
