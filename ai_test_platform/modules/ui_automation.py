from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import UIExecution
from core.utils import extract_code_block, run_temp_script
import subprocess
import os
import tempfile
from PIL import Image
import base64
import io
import json

class UIAutomationModule:
    def generate_ui_script(self, task_description: str, url: str, automation_type: str = "web", db: Session = None, user_id: int = None) -> str:
        """
        Generate UI automation script based on the automation type.
        """
        client = get_client_for_user(user_id, db)
        
        if automation_type == "web":
             # ... (keep prompt)
             system_prompt = """
            You are a Test Automation Engineer specializing in AI-driven UI automation.
            Generate a complete, runnable Python script that uses AI image recognition for automatic element positioning.
            
            Requirements:
            1. Use Playwright's async API with proper setup and teardown for browser control.
            2. Do NOT use traditional element positioning methods (CSS selectors, XPath, etc.).
            3. Instead, use AI-driven image recognition for all element interactions:
               - Take screenshots at key points during test execution
               - Use the provided ocr_from_screenshot function for text recognition
               - Implement visual matching for element identification and interaction
               - For click actions, use image recognition to determine coordinates and then click by coordinates
            4. Handle potential errors gracefully.
            5. Print "TEST PASSED" if successful, "TEST FAILED" otherwise.
            6. Include proper asyncio.run() to execute the main function.
            7. Return ONLY the python code. Do not wrap in markdown code blocks if possible, or I will strip them.
            
            Example structure:
            ```python
            from playwright.async_api import async_playwright
            import asyncio
            from PIL import Image
            import base64
            import io
            import os
            
            # Mock function for demonstration - in actual use, this would call AI image recognition
            def ai_find_element(screenshot_path, element_description):
                # Use AI to find element coordinates from screenshot
                # This is a placeholder - actual implementation would use AI image recognition
                # to analyze the screenshot and return (x, y) coordinates of the element
                return (100, 200)
            
            async def main():
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=False)
                    page = await browser.new_page()
                    try:
                        await page.goto('https://example.com')
                        
                        # Take screenshot for AI analysis
                        screenshot_path = "step1_screenshot.png"
                        await page.screenshot(path=screenshot_path)
                        
                        # Use AI to find and interact with elements
                        element_coords = ai_find_element(screenshot_path, "登录按钮")
                        await page.mouse.click(element_coords[0], element_coords[1])
                        
                        # Continue with AI-driven interactions
                        # ...
                        
                        print("TEST PASSED")
                    except Exception as e:
                        print(f"TEST FAILED: {e}")
                    finally:
                        await browser.close()
                        # Clean up screenshots
                        if os.path.exists(screenshot_path):
                            os.remove(screenshot_path)
            
            if __name__ == "__main__":
                asyncio.run(main())
            ```
            """
             prompt = f"Target URL: {url}\nTask: {task_description}"
        else:  # app automation
             # ... (keep prompt)
             system_prompt = """
            You are a Mobile Test Automation Engineer specializing in AI-driven UI automation.
            Generate a complete, runnable Python script that uses AI image recognition for automatic element positioning.
            
            Requirements:
            1. Use Appium Python Client with proper setup and teardown for device control.
            2. Do NOT use traditional element positioning methods (ID, XPath, accessibility ID, etc.).
            3. Instead, use AI-driven image recognition for all element interactions:
               - Take screenshots at key points during test execution
               - Use the provided ocr_from_screenshot function for text recognition
               - Implement visual matching for element identification and interaction
               - For click actions, use image recognition to determine coordinates and then click by coordinates
            4. Handle potential errors gracefully.
            5. Print "TEST PASSED" if successful, "TEST FAILED" otherwise.
            6. For Android, use UiAutomator2 driver; for iOS, use XCUITest driver.
            7. Use appropriate capabilities for the target platform.
            8. Return ONLY the python code. Do not wrap in markdown code blocks if possible, or I will strip them.
            
            Example structure:
            ```python
            from appium import webdriver
            from PIL import Image
            import base64
            import io
            import os
            
            # Mock function for demonstration - in actual use, this would call AI image recognition
            def ai_find_element(screenshot_path, element_description):
                # Use AI to find element coordinates from screenshot
                # This is a placeholder - actual implementation would use AI image recognition
                # to analyze the screenshot and return (x, y) coordinates of the element
                return (100, 200)
            
            def main():
                # Set desired capabilities
                capabilities = {
                    "platformName": "Android",
                    "deviceName": "emulator",
                    "appPackage": "com.example.app",
                    "appActivity": ".MainActivity",
                    "automationName": "UiAutomator2"
                }
                
                driver = webdriver.Remote("http://localhost:4723/wd/hub", capabilities)
                
                try:
                    # Take screenshot for AI analysis
                    screenshot_path = "app_screenshot.png"
                    driver.save_screenshot(screenshot_path)
                    
                    # Use AI to find and interact with elements
                    element_coords = ai_find_element(screenshot_path, "登录按钮")
                    driver.tap([(element_coords[0], element_coords[1])], 500)
                    
                    # Continue with AI-driven interactions
                    # ...
                    
                    print("TEST PASSED")
                except Exception as e:
                    print(f"TEST FAILED: {e}")
                finally:
                    driver.quit()
                    # Clean up screenshots
                    if os.path.exists(screenshot_path):
                        os.remove(screenshot_path)
            
            if __name__ == "__main__":
                main()
            ```
            """
             prompt = f"Target App: {url}\nTask: {task_description}"
        
        response = client.generate_response(prompt, system_prompt)
        
        return extract_code_block(response, "python")

    # ... execute_script already updated ...

    def ocr_from_screenshot(self, image_path: str, db: Session = None, user_id: int = None) -> str:
        """
        Perform OCR on a screenshot image using qwen-vl-plus.
        """
        try:
            client = get_client_for_user(user_id, db)
            # Use qwen-vl-plus for OCR via ai_client.analyze_image
            prompt = "请识别图片中的文字内容，包括中英文。"
            response = client.analyze_image(f"file://{image_path}", prompt, db=db)
            return response
        except Exception as e:
            return f"OCR Error: {str(e)}"
    
    def ai_locate_element(self, image_path: str, element_description: str, db: Session = None, user_id: int = None) -> tuple:
        """
        Use AI image recognition to locate element coordinates from screenshot.
        """
        try:
            client = get_client_for_user(user_id, db)
            # Use AI to analyze screenshot and find element coordinates
            prompt = f"Please analyze this screenshot and locate the element described as '{element_description}'. "
            prompt += "Return ONLY the coordinates of the element in the format [x, y] where x and y are integers representing the center point of the element. "
            prompt += "Do not include any other text or explanation. The coordinates should be relative to the screenshot dimensions."
            
            response = client.analyze_image(f"file://{image_path}", prompt, db=db)
            
            # Parse the response to get coordinates
            # Clean response by removing any extra text
            response = response.strip()
            if '[' in response and ']' in response:
                response = response[response.find('['):response.rfind(']')+1]
            
            # Convert to JSON
            coords = json.loads(response)
            return tuple(coords)
        except Exception as e:
            return f"AI Location Error: {str(e)}"
    
    def generate_ai_image_recognition_script(self, task_description: str, url: str, automation_type: str = "web", db: Session = None, user_id: int = None, token: str = None) -> str:
        """
        Generate AI-driven image recognition automation script with built-in ai_locate_element function.
        """
        # First generate the base script using the updated system prompt
        base_script = self.generate_ui_script(task_description, url, automation_type, db=db, user_id=user_id)
        
        # Add the ai_locate_element function to the script
        auth_header = f"'Authorization': 'Bearer {token}'" if token else ""
        headers_dict = f"headers = {{{auth_header}}}" if token else "headers = {}"
        
        ai_locate_function = f"""
# AI image recognition function for element localization
def ai_locate_element(screenshot_path, element_description):
    # Use AI to find element coordinates from screenshot
    import json
    import requests
    
    try:
        # Prepare the request to the local AI service
        url = "http://localhost:8000/api/ai-locate-element"
        files = {{'image': open(screenshot_path, 'rb')}}
        data = {{'element_description': element_description}}
        {headers_dict}
        
        response = requests.post(url, files=files, data=data, headers=headers)
        response.raise_for_status()
        
        # Parse response
        coords = response.json()['coordinates']
        return (coords[0], coords[1])
    except Exception as e:
        print(f"AI Location Error: {{str(e)}}")
        raise
        
"""
        
        # Insert the AI locate function into the script
        # For async scripts (Playwright), insert after imports but before main function
        if automation_type == "web":
            # Find the end of imports and start of main function
            if "async def main()" in base_script:
                parts = base_script.split("async def main()")
                return f"{parts[0]}{ai_locate_function}\nasync def main(){parts[1]}"
        else:
            # For sync scripts (Appium), insert after imports but before main function
            if "def main()" in base_script:
                parts = base_script.split("def main()")
                return f"{parts[0]}{ai_locate_function}\ndef main(){parts[1]}"
        
        # Fallback: return base script if insertion fails
        return base_script

ui_automator = UIAutomationModule()
