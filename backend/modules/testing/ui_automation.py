"""UI automation module."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import UIExecution
from core.processing.utils import extract_code_block, run_temp_script
from modules.testing.ui_automation_prompts import (
    build_ai_locate_function,
    build_app_system_prompt,
    build_requirement_context_prompt,
    build_web_system_prompt,
)


class UIAutomationModule:
    def get_current_app_info(self, device_id: str = None) -> dict:
        try:
            cmd = ["adb"]
            if device_id:
                cmd.extend(["-s", device_id])
            cmd.extend(["shell", "dumpsys", "window", "displays"])

            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            output = result.stdout

            match = re.search(r"mCurrentFocus=Window\{.*?\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)\}", output)
            if not match:
                match = re.search(r"mFocusedApp=.*ActivityRecord\{.*?\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)", output)

            if match:
                package = match.group(1)
                activity = match.group(2)
                return {"package": package, "activity": activity, "full_activity": f"{package}/{activity}"}

            cmd = ["adb"]
            if device_id:
                cmd.extend(["-s", device_id])
            cmd.extend(["shell", "dumpsys", "activity", "activities"])
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            output = result.stdout

            match = re.search(r"mResumedActivity: ActivityRecord\{.*?\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)", output)
            if match:
                package = match.group(1)
                activity = match.group(2)
                return {"package": package, "activity": activity, "full_activity": f"{package}/{activity}"}

            return {"error": "Could not determine current app info"}
        except Exception as e:
            return {"error": f"Failed to get app info: {str(e)}"}

    def generate_ui_script(
        self,
        task_description: str,
        url: str,
        automation_type: str = "web",
        db: Session = None,
        user_id: int = None,
        requirement_context: str = None,
    ) -> str:
        client = get_client_for_user(user_id, db)

        req_context_prompt = build_requirement_context_prompt(requirement_context)
        if automation_type == "web":
            system_prompt = build_web_system_prompt(req_context_prompt)
            prompt = f"Target URL: {url}\nTask: {task_description}"
        else:
            system_prompt = build_app_system_prompt(req_context_prompt)
            prompt = f"Target App: {url}\nTask: {task_description}"

        response = client.generate_response(prompt, system_prompt)
        return extract_code_block(response, "python")

    def execute_script(
        self,
        script: str,
        url: str,
        task_description: str,
        automation_type: str = "web",
        db: Session = None,
        project_id: int = None,
        user_id: int = None,
        test_case_id: int = None,
    ) -> dict:
        execution = UIExecution(
            project_id=project_id,
            user_id=user_id,
            test_case_id=test_case_id,
            url=url if automation_type == "web" else None,
            app_info=url if automation_type == "app" else None,
            task_description=task_description,
            automation_type=automation_type,
            generated_script=script,
            status="running",
        )
        if db:
            db.add(execution)
            db.commit()
            db.refresh(execution)

        try:
            stdout, stderr, returncode = run_temp_script(script, timeout=300)
            status = "success" if returncode == 0 else "failed"
            if "TEST FAILED" in stdout or "TEST FAILED" in stderr:
                status = "failed"

            screenshot_paths = []
            try:
                for line in stdout.split("\n"):
                    if '"type": "screenshot"' in line or '"screenshot":' in line:
                        try:
                            json_str = line
                            if "{" in line:
                                json_str = line[line.find("{") : line.rfind("}") + 1]
                            log_entry = json.loads(json_str)
                            if log_entry.get("type") == "screenshot" and log_entry.get("path"):
                                screenshot_paths.append(log_entry.get("path"))
                            elif log_entry.get("screenshot"):
                                screenshot_paths.append(log_entry.get("screenshot"))
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error parsing screenshots: {e}")

            result_text = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"

            if db:
                execution.status = status
                execution.execution_result = result_text
                execution.screenshot_paths = screenshot_paths
                db.commit()

                if status in {"success", "failed"}:
                    try:
                        from modules.testing.evaluation import evaluator

                        eval_result = evaluator.evaluate_ui_automation(
                            script,
                            result_text,
                            db=db,
                            project_id=project_id,
                            user_id=user_id,
                        )
                        execution.evaluation_result = eval_result

                        score = 5.0
                        if "Score: " in eval_result:
                            try:
                                score_part = eval_result.split("Score: ")[1].split("/")[0].strip()
                                score = float(score_part)
                            except Exception:
                                pass
                        execution.quality_score = score
                        db.commit()
                    except Exception as ev_e:
                        print(f"Auto-evaluation failed: {ev_e}")

            return {
                "status": status,
                "stdout": stdout,
                "stderr": stderr,
                "execution_id": execution.id if db else None,
                "screenshot_paths": screenshot_paths,
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
                "execution_id": execution.id if db else None,
            }

    def ocr_from_screenshot(self, image_path: str, db: Session = None, user_id: int = None) -> str:
        try:
            client = get_client_for_user(user_id, db)
            prompt = "请识别图片中的文字内容，包括中英文。"
            response = client.analyze_image(f"file://{image_path}", prompt, db=db)
            return response
        except Exception as e:
            return f"OCR Error: {str(e)}"

    def ai_locate_element(
        self,
        image_path: str,
        element_description: str,
        db: Session = None,
        user_id: int = None,
        image_model: str = None,
    ) -> tuple:
        try:
            client = get_client_for_user(user_id, db)
            prompt = f"Please analyze this screenshot and locate the element described as '{element_description}'. "
            prompt += "Return ONLY the coordinates of the element in the format [x, y] where x and y are integers representing the center point of the element. "
            prompt += "Do not include any other text or explanation. The coordinates should be relative to the screenshot dimensions."

            response = client.analyze_image(f"file://{image_path}", prompt, db=db, model=image_model)

            response = response.strip()
            if "[" in response and "]" in response:
                response = response[response.find("[") : response.rfind("]") + 1]

            coords = json.loads(response)
            return tuple(coords)
        except Exception as e:
            return f"AI Location Error: {str(e)}"

    def generate_ai_image_recognition_script(
        self,
        task_description: str,
        url: str,
        automation_type: str = "web",
        db: Session = None,
        user_id: int = None,
        token: str = None,
        image_model: str = None,
        requirement_context: str = None,
    ) -> str:
        base_script = self.generate_ui_script(
            task_description,
            url,
            automation_type,
            db=db,
            user_id=user_id,
            requirement_context=requirement_context,
        )

        ai_locate_function = build_ai_locate_function(token=token, image_model=image_model)

        if automation_type == "web":
            if "async def main()" in base_script:
                parts = base_script.split("async def main()")
                return f"{parts[0]}{ai_locate_function}\nasync def main(){parts[1]}"
        else:
            if "def main()" in base_script:
                parts = base_script.split("def main()")
                return f"{parts[0]}{ai_locate_function}\ndef main(){parts[1]}"

        raise ValueError("Failed to inject AI location function into the generated script.")


ui_automator = UIAutomationModule()
