import pytest

from modules.testing.ui_automation import validate_page_object_model
from modules.testing.ui_automation_prompts import build_app_system_prompt, build_web_system_prompt


VALID_POM_SCRIPT = '''
class LoginPage:
    def __init__(self, page):
        self.page = page

    async def open(self):
        await self.page.goto("/login")

    async def submit(self):
        await self.page.get_by_role("button", name="Login").click()


async def main():
    page = object()
    login_page = LoginPage(page)
    await login_page.open()
    await login_page.submit()
'''


INVALID_DIRECT_INTERACTION_SCRIPT = '''
class LoginPage:
    def __init__(self, page):
        self.page = page

    async def open(self):
        await self.page.goto("/login")


async def main():
    page = object()
    await page.get_by_role("button", name="Login").click()
'''


def test_prompts_require_page_object_model():
    assert "Page Object Model" in build_web_system_prompt("")
    assert "Page Object Model" in build_app_system_prompt("")
    assert "names end with `Page`" in build_web_system_prompt("")


def test_page_object_validator_accepts_business_method_orchestration():
    assert validate_page_object_model(VALID_POM_SCRIPT) == VALID_POM_SCRIPT.strip()


def test_page_object_validator_rejects_direct_entrypoint_interaction():
    with pytest.raises(ValueError, match=r"main\(\) directly calls (click|get_by_role)"):
        validate_page_object_model(INVALID_DIRECT_INTERACTION_SCRIPT)
