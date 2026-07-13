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
            2. Do NOT use traditional element positioning methods (CSS selectors, XPath, etc.).
            3. Instead, use AI-driven image recognition for all element interactions.
            4. Handle potential errors gracefully.
            5. Print "TEST PASSED" if successful, "TEST FAILED" otherwise.
            6. Include proper asyncio.run() to execute the main function.
            7. Return ONLY the python code.

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
            2. Do NOT use traditional element positioning methods (ID, XPath, accessibility ID, etc.).
            3. Instead, use AI-driven image recognition for all element interactions.
            4. Handle potential errors gracefully.
            5. Print "TEST PASSED" if successful, "TEST FAILED" otherwise.
            6. For Android, use UiAutomator2 driver; for iOS, use XCUITest driver.
            7. Return ONLY the python code.
            """


def build_ai_locate_function(token: str | None, image_model: str | None) -> str:
    auth_header = f"'Authorization': 'Bearer {token}'" if token else ""
    headers_dict = f"headers = {{{auth_header}}}" if token else "headers = {}"
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
        url = "http://localhost:8000/api/ai-locate-element"
        if not os.path.exists(screenshot_path):
             print(f"Error: Screenshot file not found: {{screenshot_path}}")
             return (0, 0)

        files = {{'image': open(screenshot_path, 'rb')}}
        {data_dict}
        {headers_dict}

        response = requests.post(url, files=files, data=data, headers=headers)
        response.raise_for_status()

        coords = response.json()['coordinates']
        print(f"AI Located '{{element_description}}' at: {{coords}}")
        return (coords[0], coords[1])
    except Exception as e:
        print(f"AI Location Error: {{str(e)}}")
        raise
"""
