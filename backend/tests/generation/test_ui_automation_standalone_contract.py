import json

import pytest

from modules.testing.ui_automation import (
    extract_generated_ui_script,
    inject_ui_ready_helper,
    validate_standalone_script,
)
from modules.testing.ui_automation_prompts import build_app_system_prompt, build_web_system_prompt


VALID_WEB_SCRIPT = '''
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright, expect

async def main():
    target = os.environ.get("UI_TARGET_URL", "http://127.0.0.1:5173")
    headless = os.environ.get("UI_HEADLESS", "false").lower() == "true"
    artifacts = Path(os.environ.get("UI_ARTIFACT_DIR", "artifacts"))
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless)
            page = await browser.new_page()
            await page.goto(target)
            await wait_for_ui_ready(page)
            await expect(page.locator("body")).to_be_visible()
            await page.screenshot(path=artifacts / "result.png")
            await browser.close()
        print("TEST PASSED")
    except Exception:
        print("TEST FAILED")
        raise

if __name__ == "__main__":
    asyncio.run(main())
'''


VALID_HYBRID_APP_SCRIPT = '''
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.ui_hybrid_runtime import HybridAppSession, VisualAssetCatalog, create_android_driver

def main():
    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    artifacts = Path(os.environ.get("UI_ARTIFACT_DIR", "artifacts"))
    catalog = VisualAssetCatalog(ROOT / "assets" / "case" / "visual_assets.json")
    driver = create_android_driver(package="example", activity="example.Activity", udid="device", server_url=server)
    try:
        session = HybridAppSession(driver, catalog, artifacts)
        session.visual_tap("login")
        session.assert_visual("home")
        print("TEST PASSED")
    except Exception:
        print("TEST FAILED")
        raise
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
'''


def test_web_prompt_requires_deterministic_standalone_execution():
    prompt = build_web_system_prompt("")

    assert "MUST NOT call any platform API" in prompt
    assert "role/name" in prompt
    assert "Do not intercept or mock" in prompt
    assert "wait_for_timeout" in prompt


def test_app_prompt_requires_native_stable_locators():
    prompt = build_app_system_prompt("")

    assert "accessibility id" in prompt
    assert "resource id" in prompt
    assert "Do not mock application APIs" in prompt
    assert "HybridAppSession" in prompt
    assert "Never invent resource ids or asset names" in prompt


def test_standalone_validator_accepts_named_visual_asset_runtime():
    assert validate_standalone_script(VALID_HYBRID_APP_SCRIPT, "app") == VALID_HYBRID_APP_SCRIPT.strip()


def test_standalone_validator_accepts_playwright_contract():
    script = inject_ui_ready_helper(VALID_WEB_SCRIPT)

    assert validate_standalone_script(script, "web") == script


def test_compiler_injects_render_wait_after_navigation():
    source = VALID_WEB_SCRIPT.replace("            await wait_for_ui_ready(page)\n", "")

    compiled = inject_ui_ready_helper(source)

    assert "async def wait_for_ui_ready(" in compiled
    assert "await page.goto(target)\n            await wait_for_ui_ready(page)" in compiled


def test_extract_generated_script_accepts_structured_model_response():
    response = json.dumps({"script": VALID_WEB_SCRIPT}, ensure_ascii=False)

    assert extract_generated_ui_script(response) == VALID_WEB_SCRIPT.strip()


@pytest.mark.parametrize("field", ["script", "code"])
def test_extract_generated_script_accepts_json_fence_and_known_code_fields(field):
    response = f"```json\n{json.dumps({field: VALID_WEB_SCRIPT}, ensure_ascii=False)}\n```"

    assert extract_generated_ui_script(response) == VALID_WEB_SCRIPT.strip()


@pytest.mark.parametrize(
    "forbidden",
    [
        "UI_AUTOMATION_API_BASE = 'http://localhost:8000'",
        "time.sleep(5)",
        "await page.wait_for_timeout(5000)",
        "await page.route('**/api/**')",
    ],
)
def test_standalone_validator_rejects_platform_dependency_or_flaky_wait(forbidden):
    script = VALID_WEB_SCRIPT.replace("async def main():", f"async def main():\n    {forbidden}")

    with pytest.raises(ValueError, match="不满足独立稳定执行约束"):
        validate_standalone_script(script, "web")
