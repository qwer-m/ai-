"""
UI 自动化测试模块 (UI Automation Module)

此模块负责生成和执行基于 AI 视觉识别的 UI 自动化测试脚本。
核心理念：
摒弃传统的基于 DOM/控件树 (XPath, CSS Selector, ID) 的定位方式，
采用 "截图 + AI 视觉识别" 的方式来定位元素并进行交互。

主要功能：
1. 脚本生成：利用 LLM 生成 Playwright (Web) 或 Appium (App) 的 Python 测试脚本。
2. 视觉定位：提供 API 接口，供测试脚本回调，通过 AI 分析截图返回元素坐标。
3. 设备信息：通过 ADB 获取 Android 设备当前运行的 App 信息。
4. OCR 识别：辅助识别屏幕文字。

依赖：
- core.ai_client: 用于脚本生成和图像分析。
- Playwright / Appium: 生成的脚本所使用的底层驱动。
"""

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
import re

class UIAutomationModule:
    """
    UI 自动化核心逻辑类
    """
    def get_current_app_info(self, device_id: str = None) -> dict:
        """
        获取当前前台 App 信息 (Get Current App Info)
        
        通过 ADB 命令获取 Android 设备当前显示的 Activity 信息。
        主要用于 App 自动化测试时，自动填充 "Target App" 信息。
        
        逻辑：
        1. 优先尝试 `dumpsys window displays` (更准确获取焦点窗口)。
        2. 降级尝试 `dumpsys activity activities` (兼容旧版安卓)。
        3. 解析输出中的 package 和 activity 名称。
        """
        try:
            cmd = ["adb"]
            if device_id:
                cmd.extend(["-s", device_id])
            cmd.extend(["shell", "dumpsys", "window", "displays"])
            
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            output = result.stdout
            
            # Try to find mCurrentFocus first (more accurate for current input focus)
            # Format: mCurrentFocus=Window{... u0 com.package/com.package.Activity}
            match = re.search(r'mCurrentFocus=Window\{.*?\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)\}', output)
            
            if not match:
                # Fallback to mFocusedApp
                # Format: mFocusedApp=AppWindowToken{... token=Token{... ActivityRecord{... u0 com.package/com.package.Activity ...}}}
                match = re.search(r'mFocusedApp=.*ActivityRecord\{.*?\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)', output)
                
            if match:
                package = match.group(1)
                activity = match.group(2)
                return {"package": package, "activity": activity, "full_activity": f"{package}/{activity}"}
            
            # If dumpsys window fails, try dumpsys activity (older android or different output)
            cmd = ["adb"]
            if device_id:
                cmd.extend(["-s", device_id])
            cmd.extend(["shell", "dumpsys", "activity", "activities"])
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            output = result.stdout
            
            match = re.search(r'mResumedActivity: ActivityRecord\{.*?\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)', output)
            if match:
                package = match.group(1)
                activity = match.group(2)
                return {"package": package, "activity": activity, "full_activity": f"{package}/{activity}"}
                
            return {"error": "Could not determine current app info"}
            
        except Exception as e:
            return {"error": f"Failed to get app info: {str(e)}"}

    def generate_ui_script(self, task_description: str, url: str, automation_type: str = "web", db: Session = None, user_id: int = None, requirement_context: str = None) -> str:
        """
        生成 UI 自动化脚本 (Generate UI Script)
        
        根据用户描述的任务和目标 URL/App，构建 Prompt 让 LLM 生成 Python 脚本。
        
        特点：
        - 注入需求上下文 (Requirement Context) 以增强生成的准确性。
        - 强制要求使用 "AI 视觉定位" 而非传统定位器。
        - 区分 Web (Playwright) 和 App (Appium) 两种不同的 Prompt 模板。
        """
        client = get_client_for_user(user_id, db)
        
        req_context_prompt = ""
        if requirement_context:
            req_context_prompt = f"""
            Context (Requirement Document):
            The following is the requirement document or business context for this test. Use it to understand expected behaviors, validation rules, and error handling:
            {requirement_context}
            """
        
        if automation_type == "web":
             # ... (keep prompt)
             system_prompt = f"""
            You are a Test Automation Engineer specializing in AI-driven UI automation.
            Generate a complete, runnable Python script that uses AI image recognition for automatic element positioning.
            {req_context_prompt}
            
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
             system_prompt = f"""
            You are a Mobile Test Automation Engineer specializing in AI-driven UI automation.
            Generate a complete, runnable Python script that uses AI image recognition for automatic element positioning.
            {req_context_prompt}
            
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

    def execute_script(self, script: str, url: str, task_description: str, automation_type: str = "web", db: Session = None, project_id: int = None, user_id: int = None) -> dict:
        """
        执行自动化脚本 (Execute Script)
        
        1. 记录执行开始到数据库 (UIExecution)。
        2. 将脚本写入临时文件并执行 (run_temp_script)。
        3. 捕获标准输出和错误输出。
        4. 更新数据库中的执行结果。
        5. 返回执行状态和日志。
        """
        # Create execution record
        execution = UIExecution(
            project_id=project_id,
            user_id=user_id,
            url=url if automation_type == "web" else None,
            app_info=url if automation_type == "app" else None, # url field reused for app info/package in request
            task_description=task_description,
            automation_type=automation_type,
            generated_script=script,
            status="running"
        )
        if db:
            db.add(execution)
            db.commit()
            db.refresh(execution)
        
        # Execute
        try:
            # Determine timeout based on complexity? Default to 60s
            stdout, stderr, returncode = run_temp_script(script, timeout=120)
            
            status = "success" if returncode == 0 else "failed"
            # Check for "TEST FAILED" in output even if return code is 0
            if "TEST FAILED" in stdout or "TEST FAILED" in stderr:
                status = "failed"
                
            result_text = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
            
            # Update record
            if db:
                execution.status = status
                execution.execution_result = result_text
                db.commit()
                
            return {
                "status": status,
                "stdout": stdout,
                "stderr": stderr,
                "execution_id": execution.id if db else None
            }
            
        except Exception as e:
            error_msg = f"System Error during execution: {str(e)}"
            if db:
                execution.status = "failed"
                execution.execution_result = error_msg
                db.commit()
            return {
                "status": "failed",
                "error": error_msg,
                "execution_id": execution.id if db else None
            }

    def ocr_from_screenshot(self, image_path: str, db: Session = None, user_id: int = None) -> str:
        """
        屏幕截图 OCR 识别 (OCR from Screenshot)
        
        调用多模态大模型 (qwen-vl-plus) 识别图片中的所有文字内容。
        用于验证测试结果或获取屏幕信息。
        """
        try:
            client = get_client_for_user(user_id, db)
            # Use qwen-vl-plus for OCR via ai_client.analyze_image
            prompt = "请识别图片中的文字内容，包括中英文。"
            response = client.analyze_image(f"file://{image_path}", prompt, db=db)
            return response
        except Exception as e:
            return f"OCR Error: {str(e)}"
    
    def ai_locate_element(self, image_path: str, element_description: str, db: Session = None, user_id: int = None, image_model: str = None) -> tuple:
        """
        AI 视觉元素定位 (AI Locate Element)
        
        核心功能：
        输入一张截图和一段元素描述（如"登录按钮"），
        利用多模态大模型分析图片，返回该元素的中心点坐标 [x, y]。
        
        用于支持自动化脚本中的点击操作，无需依赖传统的控件树定位。
        """
        try:
            client = get_client_for_user(user_id, db)
            # Use AI to analyze screenshot and find element coordinates
            prompt = f"Please analyze this screenshot and locate the element described as '{element_description}'. "
            prompt += "Return ONLY the coordinates of the element in the format [x, y] where x and y are integers representing the center point of the element. "
            prompt += "Do not include any other text or explanation. The coordinates should be relative to the screenshot dimensions."
            
            response = client.analyze_image(f"file://{image_path}", prompt, db=db, model=image_model)
            
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
    
    def generate_ai_image_recognition_script(self, task_description: str, url: str, automation_type: str = "web", db: Session = None, user_id: int = None, token: str = None, image_model: str = None, requirement_context: str = None) -> str:
        """
        生成带有 AI 视觉能力的自动化脚本 (Generate AI-Driven Script)
        
        这是一个高级包装函数，它不仅生成基础的测试脚本，
        还会将 `ai_locate_element` 函数及其依赖（requests调用本地API）注入到脚本中。
        
        注入逻辑：
        1. 生成基础脚本 (generate_ui_script)。
        2. 构建包含本地 API 调用逻辑的 Python 函数字符串。
        3. 将该函数插入到脚本的 Imports 之后、Main 函数之前。
        4. 确保脚本可以直接运行，且能回调后端服务进行图像识别。
        """
        # First generate the base script using the updated system prompt
        base_script = self.generate_ui_script(task_description, url, automation_type, db=db, user_id=user_id, requirement_context=requirement_context)
        
        # Add the ai_locate_element function to the script
        auth_header = f"'Authorization': 'Bearer {token}'" if token else ""
        headers_dict = f"headers = {{{auth_header}}}" if token else "headers = {}"
        
        # Inject image_model if provided
        model_field = f"'image_model': '{image_model}'" if image_model else ""
        data_dict = f"data = {{'element_description': element_description, {model_field}}}" if model_field else "data = {'element_description': element_description}"

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
        {data_dict}
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
