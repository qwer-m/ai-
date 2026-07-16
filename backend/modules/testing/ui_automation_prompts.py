from __future__ import annotations


def build_requirement_context_prompt(requirement_context: str | None) -> str:
    if not requirement_context:
        return ""
    return f"""
            Context (Requirement Document):
            The following is the requirement document or business context for this test. Use it to understand expected behaviors, validation rules, and error handling:
            {requirement_context}
            """


def build_web_system_prompt(req_context_prompt: str) -> str:
    return f"""
            You are a Test Automation Engineer specializing in AI-driven UI automation.
            Generate a complete, runnable Python script that uses AI image recognition for automatic element positioning.
            {req_context_prompt}

            Requirements:
            1. Use Playwright's async API with proper setup and teardown for browser control.
            2. Use Page Object Model. Define one or more classes whose names end with `Page`.
               Put locators, page actions, screenshots, waits, and page-level assertions inside
               those classes. The test entrypoint may only create browser resources, instantiate
               Page Objects, call business methods, and perform final cleanup; do not place
               Playwright locators or direct click/fill calls in main().
            3. Do NOT use traditional element positioning methods (CSS selectors, XPath, etc.).
            4. Instead, use AI-driven image recognition for all element interactions.
            5. Handle potential errors gracefully.
            6. Print "TEST PASSED" if successful, "TEST FAILED" otherwise.
            7. Include proper asyncio.run() to execute the main function.
            8. Return ONLY the python code.

            IMPORTANT:
            - After every major action (click, fill, navigate), print JSON logs.
            - Format: {{"type": "step", "action": "click", "details": "Clicked Login", "status": "success"}}
            """


def build_app_system_prompt(req_context_prompt: str) -> str:
    return f"""
            You are a Mobile Test Automation Engineer specializing in AI-driven UI automation.
            Generate a complete, runnable Python script that uses AI image recognition for automatic element positioning.
            {req_context_prompt}

            Requirements:
            1. Use Appium Python Client with proper setup and teardown for device control.
            2. Use Page Object Model. Define one or more classes whose names end with `Page`.
               Put native/visual locators, page actions, waits, screenshots, and page-level
               assertions inside those classes. The test entrypoint may only create the driver,
               instantiate Page Objects, call business methods, and quit the driver; do not place
               find_element, coordinate taps, or direct visual-location calls in main().
            3. Do NOT use traditional element positioning methods (ID, XPath, accessibility ID, etc.).
            4. Instead, use AI-driven image recognition for all element interactions.
            5. Handle potential errors gracefully.
            6. Print "TEST PASSED" if successful, "TEST FAILED" otherwise.
            7. For Android, use UiAutomator2 driver; for iOS, use XCUITest driver.
            8. Return ONLY the python code.
            """


def build_ai_locate_function(token: str | None, image_model: str | None) -> str:
    _ = token
    model_field = f"'image_model': '{image_model}'" if image_model else ""
    data_dict = (
        f"data = {{'element_description': element_description, {model_field}}}"
        if model_field
        else "data = {'element_description': element_description}"
    )

    return f"""
# AI image recognition function for element localization
def ai_locate_element(screenshot_path, element_description):
    import requests
    import os

    try:
        api_base = os.environ.get("UI_AUTOMATION_API_BASE", "http://localhost:8000").rstrip("/")
        url = f"{{api_base}}/api/ui-automation/ai-locate-element"
        if not os.path.exists(screenshot_path):
             print(f"Error: Screenshot file not found: {{screenshot_path}}")
             return (0, 0)

        files = {{'image': open(screenshot_path, 'rb')}}
        {data_dict}
        headers = {{}}
        auth_token = os.environ.get("UI_AUTOMATION_TOKEN", "")
        if auth_token:
            headers["Authorization"] = f"Bearer {{auth_token}}"

        response = requests.post(url, files=files, data=data, headers=headers)
        response.raise_for_status()

        coords = response.json()['coordinates']
        print(f"AI Located '{{element_description}}' at: {{coords}}")
        return (coords[0], coords[1])
    except Exception as e:
        print(f"AI Location Error: {{str(e)}}")
        raise
"""
