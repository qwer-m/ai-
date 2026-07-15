import json
from pathlib import Path

from modules.automation_components.services.ui_automation_export_service import export_standalone_ui_script


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


def test_export_creates_self_contained_project(tmp_path: Path, monkeypatch):
    export_root = tmp_path / "ai ui自动化"
    monkeypatch.setenv("UI_AUTOMATION_EXPORT_DIR", str(export_root))

    result = export_standalone_ui_script(
        script=VALID_WEB_SCRIPT,
        task="访问真实测试开发平台首页",
        target="http://127.0.0.1:5173",
        automation_type="web",
        project_id=1,
    )

    script_path = Path(result["script_path"])
    assert script_path.is_file()
    assert script_path.parent == export_root / "scripts"
    assert (export_root / "requirements.txt").is_file()
    assert (export_root / "requirements-web.txt").is_file()
    assert (export_root / "requirements-app.txt").is_file()
    assert (export_root / "install.ps1").is_file()
    assert (export_root / "run.ps1").is_file()
    assert (export_root / ".env.example").is_file()
    assert (export_root / "README.md").is_file()
    assert (export_root / "runtime" / "__init__.py").is_file()
    assert (export_root / "runtime" / "ui_hybrid_runtime.py").is_file()

    env_example = (export_root / ".env.example").read_text(encoding="utf-8")
    assert "APPIUM_UDID=" in env_example
    assert "RESET_APP_DATA=false" in env_example

    manifest = json.loads((export_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cases"][-1]["target"] == "http://127.0.0.1:5173"
    assert manifest["cases"][-1]["script"] == script_path.relative_to(export_root).as_posix()
