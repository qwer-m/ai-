"""UI automation module."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image
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


_VISION_COORDINATE_MAX = 1000


def scale_normalized_coordinates(image_path: str, coordinates: list | tuple) -> tuple[int, int]:
    """将视觉模型的 0-1000 归一化坐标转换为原始截图像素。"""
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        raise ValueError("AI locator returned invalid normalized coordinates")
    x = float(coordinates[0])
    y = float(coordinates[1])
    if not 0 <= x <= _VISION_COORDINATE_MAX or not 0 <= y <= _VISION_COORDINATE_MAX:
        raise ValueError(
            f"AI locator coordinates must be between 0 and {_VISION_COORDINATE_MAX}: {(x, y)}"
        )
    with Image.open(image_path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError(f"Screenshot has invalid dimensions: {(width, height)}")
    pixel_x = round(x * max(0, width - 1) / _VISION_COORDINATE_MAX)
    pixel_y = round(y * max(0, height - 1) / _VISION_COORDINATE_MAX)
    return pixel_x, pixel_y


def resolve_android_device_id() -> str:
    """从显式环境变量或真实 ADB 在线设备中解析唯一设备。"""
    configured = str(
        os.environ.get("ANDROID_DEVICE_ID")
        or os.environ.get("APPIUM_DEVICE_ID")
        or ""
    ).strip()
    if configured:
        return configured
    adb = os.environ.get("ADB_PATH", "adb")
    result = subprocess.run(
        [adb, "devices"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ADB device detection failed: {result.stderr.strip()}")
    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if len(devices) != 1:
        raise RuntimeError(
            f"UI automation requires exactly one online Android device or ANDROID_DEVICE_ID; found {devices}"
        )
    return devices[0]


def prepare_android_device(device_id: str) -> dict[str, Any]:
    """在执行脚本前唤醒真实设备并解除无密码锁屏。"""
    adb = os.environ.get("ADB_PATH", "adb")
    commands = (
        [adb, "-s", device_id, "shell", "input", "keyevent", "224"],
        [adb, "-s", device_id, "shell", "wm", "dismiss-keyguard"],
    )
    for command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Android device preparation failed: {result.stderr.strip()}")
    return {"device_id": device_id, "awake": True, "keyguard_dismissed": True}


def capture_android_screenshot(device_id: str, execution_id: int | None) -> str:
    """从真实 Android 设备抓取最终截图，运行产物写入系统临时目录。"""
    adb = os.environ.get("ADB_PATH", "adb")
    result = subprocess.run(
        [adb, "-s", device_id, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Android final screenshot failed: {error or 'empty screenshot'}")
    artifact_dir = Path(tempfile.gettempdir()) / "ai-ui-automation" / f"execution_{execution_id or 'adhoc'}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifact_dir / "final.png"
    screenshot_path.write_bytes(result.stdout)
    return str(screenshot_path)


def verify_ui_result(
    image_path: str,
    task_description: str,
    *,
    db: Session,
    user_id: int,
    image_model: str | None = None,
    expected_app: str | None = None,
) -> dict[str, Any]:
    """让视觉模型仅依据最终真实截图判断自然语言任务是否完成。"""
    client = get_client_for_user(user_id, db)
    prompt = (
        "Judge whether this final UI screenshot visibly proves that the following task completed successfully: "
        f"{task_description}. "
        f"The required foreground application is '{expected_app or 'the target application'}'. "
        "An Android launcher or device home screen showing only the application icon is always a failure; "
        "it does not prove that the application is open or that the business task completed. "
        "Fail when the screen is black, locked, still loading, covered by an unresolved blocking dialog, "
        "or does not show the expected final state. "
        "Set passed=true only when visible content directly proves the expected business result, not merely "
        "because no error is visible. Return ONLY JSON in the form "
        "{\"passed\": true, \"reason\": \"visible evidence\"}."
    )
    response = client.analyze_image(f"file://{image_path}", prompt, db=db, model=image_model).strip()
    if "{" in response and "}" in response:
        response = response[response.find("{") : response.rfind("}") + 1]
    payload = json.loads(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
        raise ValueError("AI semantic verification returned invalid JSON")
    return {
        "passed": bool(payload["passed"]),
        "reason": str(payload.get("reason") or "").strip(),
        "screenshot_path": image_path,
    }


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


def validate_page_object_model(script: str, automation_type: str | None = None) -> str:
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
        "tap_visual_element",
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
            if isinstance(node.func, ast.Name) and node.func.id == "tap_visual_element":
                raise ValueError("Page Object violation: main() directly calls tap_visual_element().")

    if automation_type == "app":
        required_runtime_markers = {
            "tap_visual_element(": "visual tap runtime",
            "ANDROID_DEVICE_ID": "Android device environment",
            "APPIUM_SERVER_URL": "Appium server environment",
            "APPIUM_NEW_COMMAND_TIMEOUT": "Appium session timeout environment",
            "TEST PASSED": "page-level pass assertion",
            "screenshot": "final screenshot evidence",
        }
        missing = [label for marker, label in required_runtime_markers.items() if marker not in cleaned]
        if missing:
            raise ValueError(
                "Generated app UI script is missing required runtime contracts: " + ", ".join(missing)
            )
        if "AppiumBy.IMAGE" in cleaned or "'-image'" in cleaned or '"-image"' in cleaned:
            raise ValueError("Generated app UI script must use semantic visual location, not template image matching.")
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

        response = client.generate_response(
            prompt,
            system_prompt,
            task_type="ui_automation",
            response_mode="text",
            max_tokens=int(os.environ.get("UI_AUTOMATION_AI_MAX_TOKENS", "6000")),
            request_timeout_seconds=float(
                os.environ.get("UI_AUTOMATION_AI_TIMEOUT_SECONDS", "360")
            ),
            reasoning_effort="low",
            disable_thinking=True,
        )
        return validate_page_object_model(
            extract_code_block(response, "python"),
            automation_type=automation_type,
        )

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
        image_model: str = None,
        require_semantic_verification: bool = False,
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
            device_readiness = None
            device_id = None
            if automation_type == "app":
                device_id = resolve_android_device_id()
                device_readiness = prepare_android_device(device_id)
            script_env = {
                "UI_AUTOMATION_API_BASE": "http://localhost:8000",
                "APPIUM_SERVER_URL": os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723"),
                "APPIUM_NEW_COMMAND_TIMEOUT": os.environ.get("APPIUM_NEW_COMMAND_TIMEOUT", "900"),
            }
            if device_id:
                script_env["ANDROID_DEVICE_ID"] = device_id
                script_env["APPIUM_DEVICE_ID"] = device_id
            if auth_token:
                script_env["UI_AUTOMATION_TOKEN"] = auth_token
            if script_path:
                stdout, stderr, returncode = run_script_file(script_path, timeout=300, env=script_env)
            else:
                stdout, stderr, returncode = run_temp_script(script, timeout=300, env=script_env)
            status = "success" if returncode == 0 else "failed"
            if "TEST FAILED" in stdout or "TEST FAILED" in stderr:
                status = "failed"
            reported_pass = "TEST PASSED" in stdout

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

            final_screenshot = None
            foreground_app = None
            if automation_type == "app" and device_id:
                final_screenshot = capture_android_screenshot(device_id, execution.id if db else None)
                screenshot_paths.append(final_screenshot)
                foreground_app = self.get_current_app_info(device_id)

            semantic_verification = None
            if require_semantic_verification:
                if status != "success":
                    semantic_verification = {
                        "passed": False,
                        "reason": "脚本进程执行失败，未进入语义验收",
                        "screenshot_path": final_screenshot,
                    }
                elif not reported_pass:
                    status = "failed"
                    semantic_verification = {
                        "passed": False,
                        "reason": "脚本未输出 TEST PASSED，页面级断言没有完成",
                        "screenshot_path": final_screenshot,
                    }
                elif not final_screenshot:
                    status = "failed"
                    semantic_verification = {
                        "passed": False,
                        "reason": "没有获取到真实设备最终截图",
                        "screenshot_path": None,
                    }
                else:
                    expected_package = str(url or "").split("/", 1)[0].strip()
                    actual_package = str((foreground_app or {}).get("package") or "").strip()
                    if automation_type == "app" and expected_package and actual_package != expected_package:
                        semantic_verification = {
                            "passed": False,
                            "reason": (
                                f"目标应用未处于前台：expected={expected_package}, "
                                f"actual={actual_package or 'unknown'}"
                            ),
                            "screenshot_path": final_screenshot,
                        }
                    else:
                        semantic_verification = verify_ui_result(
                            final_screenshot,
                            task_description,
                            db=db,
                            user_id=user_id,
                            image_model=image_model,
                            expected_app=expected_package or None,
                        )
                    if not semantic_verification["passed"]:
                        status = "failed"

            result_text = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
            if semantic_verification is not None:
                result_text += "\n\nSEMANTIC_VERIFICATION:\n" + json.dumps(
                    semantic_verification,
                    ensure_ascii=False,
                )

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
                "device_readiness": device_readiness,
                "foreground_app": foreground_app,
                "semantic_verification": semantic_verification,
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
            prompt += "Use a normalized coordinate system where the top-left is [0, 0] and the bottom-right is [1000, 1000]. "
            prompt += "Return ONLY the center point in the format [x, y], using integers from 0 to 1000. "
            prompt += "Do not include any other text or explanation."

            response = client.analyze_image(f"file://{image_path}", prompt, db=db, model=image_model)

            response = response.strip()
            if "[" in response and "]" in response:
                response = response[response.find("[") : response.rfind("]") + 1]

            coords = json.loads(response)
            return scale_normalized_coordinates(image_path, coords)
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
