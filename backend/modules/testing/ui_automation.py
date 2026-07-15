"""UI automation module."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import UIExecution
from core.processing.utils import extract_code_block, run_temp_script
from modules.testing.ui_automation_prompts import (
    build_app_system_prompt,
    build_requirement_context_prompt,
    build_web_system_prompt,
)
from modules.testing.ui_runtime_context import collect_ui_runtime_context


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


def extract_generated_ui_script(response: str) -> str:
    """Extract Python from plain, fenced, or structured model responses."""
    raw = (response or "").strip()
    if not raw:
        return ""

    candidates = [raw]
    fenced = re.search(r"```(?:python|py|json)?\s*(.*?)```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(extract_code_block(raw, "python"))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            for field in ("script", "code"):
                if isinstance(payload.get(field), str):
                    return extract_code_block(payload[field], "python")

    if fenced and fenced.group(1).lstrip().startswith(("import ", "from ", "#")):
        return fenced.group(1).strip()
    return extract_code_block(raw, "python")


UI_READY_HELPER = '''
async def wait_for_ui_ready(page, timeout=15000):
    """Wait until rendered interactive controls are effectively visible through ancestors."""
    await page.wait_for_function(
        """
        () => {
          if (document.readyState === 'loading') return false;
          const selector = 'input, button, a, select, textarea, [role], [aria-label], [data-testid]';
          const candidates = Array.from(document.querySelectorAll(selector)).filter((node) => {
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          });
          if (!candidates.length) return false;
          return candidates.every((node) => {
            let opacity = 1;
            for (let current = node; current instanceof Element; current = current.parentElement) {
              const style = window.getComputedStyle(current);
              if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') {
                return false;
              }
              opacity *= Number.parseFloat(style.opacity || '1');
            }
            return opacity >= 0.95;
          });
        }
        """,
        timeout=timeout,
    )
'''.strip()


def inject_ui_ready_helper(script: str) -> str:
    """Embed and invoke the deterministic render-readiness helper in a web script."""
    cleaned = (script or "").strip()
    tree = ast.parse(cleaned)

    ready_call_exists = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "wait_for_ui_ready"
        for node in ast.walk(tree)
    )
    if not ready_call_exists:
        lines = cleaned.splitlines()
        goto_awaits = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "goto"
            and isinstance(node.value.func.value, ast.Name)
        ]
        for node in sorted(goto_awaits, key=lambda item: item.end_lineno or item.lineno, reverse=True):
            line_index = node.end_lineno or node.lineno
            source_line = lines[node.lineno - 1]
            indentation = source_line[: len(source_line) - len(source_line.lstrip())]
            page_name = node.value.func.value.id
            lines.insert(line_index, f"{indentation}await wait_for_ui_ready({page_name})")
        cleaned = "\n".join(lines)

    if "async def wait_for_ui_ready(" in cleaned:
        return cleaned

    tree = ast.parse(cleaned)
    insert_line = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            insert_line = node.end_lineno or node.lineno
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            insert_line = node.end_lineno or node.lineno
            continue
        break
    lines = cleaned.splitlines()
    return "\n".join(lines[:insert_line] + ["", UI_READY_HELPER, ""] + lines[insert_line:]).strip()


def validate_standalone_script(script: str, automation_type: str) -> str:
    """Reject generated code that cannot run independently and deterministically."""
    cleaned = reject_model_error_script(script)
    try:
        tree = ast.parse(cleaned)
    except SyntaxError as exc:
        raise ValueError(f"AI 生成的 UI 自动化脚本存在语法错误: {exc}") from exc

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    forbidden_fragments = {
        "UI_AUTOMATION_API_BASE": "依赖测试开发平台 API",
        "/api/ui-automation/ai-locate-element": "依赖平台视觉定位接口",
        "ai_locate_element(": "依赖运行时 AI 坐标定位",
        "wait_for_timeout(": "使用固定时间等待",
        "time.sleep(": "使用固定时间等待",
        ".route(": "拦截真实应用网络请求",
        "route_from_har(": "回放 HAR 而非访问真实服务",
        '\"action\": \"skip\"': "把目标步骤静默跳过",
        "'action': 'skip'": "把目标步骤静默跳过",
    }
    problems = [reason for fragment, reason in forbidden_fragments.items() if fragment in cleaned]
    if imported_modules.intersection({"core", "modules", "routers"}):
        problems.append("导入了当前平台源码")

    normalized_type = "app" if automation_type == "app" else "web"
    if normalized_type == "web":
        if "playwright" not in imported_modules:
            problems.append("未使用 Playwright")
        if "expect(" not in cleaned:
            problems.append("未使用 Playwright expect 进行业务断言")
        ready_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "wait_for_ui_ready"
        ]
        if not ready_calls:
            problems.append("导航后未等待界面完成真实渲染")
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_by_role"
            ):
                continue
            name_keyword = next((item for item in node.keywords if item.arg == "name"), None)
            exact_keyword = next((item for item in node.keywords if item.arg == "exact"), None)
            if (
                name_keyword is not None
                and isinstance(name_keyword.value, ast.Constant)
                and isinstance(name_keyword.value.value, str)
                and not (
                    exact_keyword is not None
                    and isinstance(exact_keyword.value, ast.Constant)
                    and exact_keyword.value.value is True
                )
            ):
                problems.append("固定可访问名称的 role 定位缺少 exact=True")
                break
        for required in ("UI_TARGET_URL", "UI_HEADLESS", "UI_ARTIFACT_DIR"):
            if required not in cleaned:
                problems.append(f"未从环境变量读取 {required}")
    else:
        uses_hybrid_runtime = "runtime.ui_hybrid_runtime" in cleaned
        if "appium" not in imported_modules and not uses_hybrid_runtime:
            problems.append("未使用 Appium 或独立混合定位运行时")
        if uses_hybrid_runtime:
            for symbol in ("VisualAssetCatalog", "HybridAppSession", "create_android_driver"):
                if symbol not in cleaned:
                    problems.append(f"混合定位脚本缺少 {symbol}")
            if "sys.path.insert" not in cleaned:
                problems.append("混合定位脚本未把独立测试包根目录加入模块搜索路径")
        if ".move_to_location(" in cleaned or ".tap(" in cleaned:
            problems.append("业务脚本使用了裸坐标点击，必须通过命名视觉资产定位")
        for required in ("APPIUM_SERVER_URL", "UI_ARTIFACT_DIR"):
            if required not in cleaned:
                problems.append(f"未从环境变量读取 {required}")

    for required_text in ("TEST PASSED", "TEST FAILED", "__main__"):
        if required_text not in cleaned:
            problems.append(f"缺少运行契约: {required_text}")

    if problems:
        details = "；".join(dict.fromkeys(problems))
        raise ValueError(f"AI 生成的脚本不满足独立稳定执行约束：{details}")
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
        device_id: str = None,
        visual_asset_group: str = None,
    ) -> str:
        client = get_client_for_user(user_id, db)

        req_context_prompt = build_requirement_context_prompt(requirement_context)
        if automation_type == "web":
            system_prompt = build_web_system_prompt(req_context_prompt)
            prompt = f"Target URL: {url}\nTask: {task_description}"
        else:
            system_prompt = build_app_system_prompt(req_context_prompt)
            prompt = f"Target App: {url}\nTask: {task_description}"

        runtime_context = collect_ui_runtime_context(
            url,
            automation_type,
            device_id=device_id,
            visual_asset_group=visual_asset_group,
        )
        if automation_type == "app":
            observed = json.loads(runtime_context)
            if observed.get("render_engine") == "cocos" and not observed.get("visual_asset_catalogs"):
                suffix = (
                    f"指定的视觉资产组不存在：{visual_asset_group}。"
                    if visual_asset_group
                    else "当前没有唯一可用的视觉资产组。"
                )
                raise ValueError(
                    "检测到 Cocos 页面，但没有可用于确定性执行的视觉资产。"
                    f"{suffix}请先从真实设备截图采集模板，再生成脚本。"
                )
        prompt += (
            "\n\nObserved real UI structure collected immediately before generation. "
            "Use these exact roles, labels, accessible names, placeholders, resource ids, and test ids. "
            "Do not invent translated labels or selectors that are absent from this observation:\n"
            f"{runtime_context}"
        )

        validation_error: ValueError | None = None
        response = ""
        for attempt in range(3):
            current_prompt = prompt
            if attempt and validation_error is not None:
                current_prompt += (
                    "\n\nYour previous response was rejected by the executable-script validator. "
                    "Generate a complete replacement script, not a patch or explanation.\n"
                    f"Validation error: {validation_error}\n"
                    f"Rejected response: {response[:4000]}"
                )
            response = client.generate_response(current_prompt, system_prompt)
            script = extract_generated_ui_script(response)
            if automation_type == "web":
                script = inject_ui_ready_helper(script)
            try:
                return validate_standalone_script(script, automation_type)
            except ValueError as exc:
                validation_error = exc

        raise validation_error or ValueError("AI 生成 UI 自动化脚本失败。")

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
        working_directory: str = None,
        execution_env: dict[str, str] | None = None,
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
            _ = auth_token
            execution_key = str(execution.id) if db and execution.id else datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            bundle_root = Path(working_directory).resolve() if working_directory else None
            artifact_dir = (
                bundle_root / "artifacts" / f"execution_{execution_key}"
                if bundle_root
                else Path(os.environ.get("UI_ARTIFACT_DIR", "artifacts")).resolve() / f"execution_{execution_key}"
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            script_env = {"UI_ARTIFACT_DIR": str(artifact_dir), **(execution_env or {})}

            if script_path:
                resolved_script = Path(script_path).resolve()
                if not resolved_script.is_file():
                    raise FileNotFoundError(f"待执行 UI 自动化脚本不存在：{resolved_script}")
                if bundle_root is None:
                    bundle_root = resolved_script.parent.parent
                existing_pythonpath = os.environ.get("PYTHONPATH", "")
                script_env["PYTHONPATH"] = str(bundle_root) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
                completed = subprocess.run(
                    [sys.executable, str(resolved_script)],
                    cwd=str(bundle_root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    env={**os.environ, **script_env},
                )
                stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
            else:
                stdout, stderr, returncode = run_temp_script(script, timeout=300, env=script_env)

            passed_marker = "TEST PASSED" in stdout
            failed_marker = "TEST FAILED" in stdout or "TEST FAILED" in stderr
            status = "success" if returncode == 0 and passed_marker and not failed_marker else "failed"

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
                                screenshot_paths.append(str(Path(log_entry.get("path")).expanduser().resolve()))
                            elif log_entry.get("screenshot"):
                                screenshot_paths.append(str(Path(log_entry.get("screenshot")).expanduser().resolve()))
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
                "artifact_dir": str(artifact_dir),
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

    def generate_executable_script(
        self,
        task_description: str,
        url: str,
        automation_type: str = "web",
        db: Session = None,
        user_id: int = None,
        requirement_context: str = None,
        device_id: str = None,
        visual_asset_group: str = None,
    ) -> str:
        return self.generate_ui_script(
            task_description,
            url,
            automation_type,
            db=db,
            user_id=user_id,
            requirement_context=requirement_context,
            device_id=device_id,
            visual_asset_group=visual_asset_group,
        )


ui_automator = UIAutomationModule()
