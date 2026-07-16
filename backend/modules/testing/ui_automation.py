"""UI automation module."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from typing import Any

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import UIExecution
from core.processing.utils import extract_code_block, run_script_file, run_temp_script
from modules.testing.ui_automation_prompts import (
    build_ai_locate_function,
    build_app_system_prompt,
    build_requirement_context_prompt,
    build_web_system_prompt,
)


def inject_ai_locate_function(base_script: str, ai_locate_function: str, automation_type: str = "web") -> str:
    """Insert the AI locator helper without assuming an exact main() signature."""
    script = (base_script or "").strip()
    helper = (ai_locate_function or "").strip()
    if not script:
        raise ValueError("Generated UI automation script is empty.")
    if not helper or "def ai_locate_element" in script:
        return script

    lines = script.splitlines()

    try:
        tree = ast.parse(script)
        for node in tree.body:
            is_supported_main = (
                automation_type == "web"
                and isinstance(node, ast.AsyncFunctionDef)
                and node.name == "main"
            ) or (
                automation_type != "web"
                and isinstance(node, ast.FunctionDef)
                and node.name == "main"
            )
            if is_supported_main:
                insert_at = max(0, node.lineno - 1)
                return "\n".join(lines[:insert_at] + ["", helper, ""] + lines[insert_at:])
    except SyntaxError:
        pass

    main_pattern = r"(?m)^(async\s+def\s+main\s*\(|def\s+main\s*\()"
    main_match = re.search(main_pattern, script)
    if main_match:
        return f"{script[:main_match.start()].rstrip()}\n\n{helper}\n\n{script[main_match.start():].lstrip()}"

    guard_match = re.search(r"(?m)^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", script)
    if guard_match:
        return f"{script[:guard_match.start()].rstrip()}\n\n{helper}\n\n{script[guard_match.start():].lstrip()}"

    return f"{helper}\n\n{script}"


def reject_model_error_script(script: str) -> str:
    """Fail fast when the model client returned an error string instead of code."""
    cleaned = (script or "").strip()
    if not cleaned:
        raise ValueError("Generated UI automation script is empty.")
    first_line = cleaned.splitlines()[0].strip()
    error_prefixes = (
        "Error:",
        "Exception:",
        "Exception occurred:",
        "Traceback ",
        "Traceback:",
    )
    if first_line.startswith(error_prefixes) or "Exception occurred:" in cleaned[:300]:
        raise ValueError(f"AI script generation failed: {first_line[:200]}")
    return cleaned


def validate_page_object_model(script: str) -> str:
    """Ensure generated UI code keeps locators and interactions out of the test entrypoint."""
    cleaned = reject_model_error_script(script)
    try:
        tree = ast.parse(cleaned)
    except SyntaxError as exc:
        raise ValueError(f"Generated UI automation script has invalid Python syntax: {exc}") from exc

    page_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Page")
    ]
    if not page_classes:
        raise ValueError("Generated UI automation script must use Page Object Model with at least one *Page class.")

    public_methods = [
        child
        for page_class in page_classes
        for child in page_class.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_")
    ]
    if not public_methods:
        raise ValueError("Page Object classes must expose page-level business methods.")

    entrypoints = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    ]
    if not entrypoints:
        raise ValueError("Generated UI automation script must define main().")

    forbidden_entrypoint_calls = {
        "click",
        "fill",
        "tap",
        "find_element",
        "find_elements",
        "get_by_role",
        "get_by_label",
        "get_by_text",
        "locator",
        "ai_locate_element",
    }
    for entrypoint in entrypoints:
        for node in ast.walk(entrypoint):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_entrypoint_calls:
                raise ValueError(
                    f"Page Object violation: main() directly calls {node.func.attr}(); move it into a *Page class."
                )
            if isinstance(node.func, ast.Name) and node.func.id == "ai_locate_element":
                raise ValueError("Page Object violation: main() directly calls ai_locate_element().")
    return cleaned


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
        return validate_page_object_model(extract_code_block(response, "python"))

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
        auth_token: str = None,
        script_path: str = None,
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
            script_env = {
                "UI_AUTOMATION_API_BASE": "http://localhost:8000",
            }
            if auth_token:
                script_env["UI_AUTOMATION_TOKEN"] = auth_token
            if script_path:
                stdout, stderr, returncode = run_script_file(script_path, timeout=300, env=script_env)
            else:
                stdout, stderr, returncode = run_temp_script(script, timeout=300, env=script_env)
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
        return inject_ai_locate_function(base_script, ai_locate_function, automation_type=automation_type)


ui_automator = UIAutomationModule()
