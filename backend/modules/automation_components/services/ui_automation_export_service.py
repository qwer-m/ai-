"""Export generated UI automation scripts as a standalone desktop project."""

from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


EXPORT_FOLDER_NAME = "ai ui自动化"


def _desktop_directory() -> Path:
    configured = os.environ.get("UI_AUTOMATION_EXPORT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    if os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0:
                return Path(buffer.value) / EXPORT_FOLDER_NAME
        except (AttributeError, OSError):
            pass

    return Path.home() / "Desktop" / EXPORT_FOLDER_NAME


def get_ui_automation_export_root() -> Path:
    """返回 UI 自动化独立测试包的统一根目录。"""
    return _desktop_directory()


def _write_managed(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _validate_script(script: str) -> str:
    cleaned = (script or "").strip()
    if not cleaned:
        raise ValueError("不能导出空的 UI 自动化脚本。")
    ast.parse(cleaned)
    return cleaned + "\n"


def _safe_case_name(task: str, automation_type: str) -> str:
    ascii_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", task).strip("-").lower()[:40]
    suffix = ascii_name or "case"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{automation_type}_{timestamp}_{suffix}.py"


def _ensure_scaffold(root: Path) -> None:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir(parents=True, exist_ok=True)

    runtime_source = Path(__file__).resolve().parents[2] / "testing" / "standalone" / "ui_hybrid_runtime.py"
    if not runtime_source.is_file():
        raise FileNotFoundError(f"独立 UI 混合定位运行时不存在：{runtime_source}")
    _write_managed(root / "runtime" / "__init__.py", "")
    _write_managed(root / "runtime" / "ui_hybrid_runtime.py", runtime_source.read_text(encoding="utf-8"))

    _write_managed(
        root / "requirements.txt",
        "-r requirements-web.txt\n-r requirements-app.txt\n",
    )
    _write_managed(root / "requirements-web.txt", "playwright>=1.40,<2\n")
    _write_managed(
        root / "requirements-app.txt",
        "Appium-Python-Client>=4,<6\n"
        "selenium>=4.20,<5\n"
        "opencv-python-headless>=4.10,<5\n"
        "numpy>=1.26,<3\n",
    )
    _write_managed(
        root / ".env.example",
        "# 复制为 .env 后填写真实测试环境参数；不要在脚本中硬编码凭据。\n"
        "UI_TARGET_URL=http://127.0.0.1:5173\n"
        "UI_HEADLESS=false\n"
        "UI_ARTIFACT_DIR=artifacts\n"
        "APPIUM_SERVER_URL=http://127.0.0.1:4723\n"
        "APPIUM_PLATFORM_NAME=Android\n"
        "APPIUM_DEVICE_NAME=\n"
        "APPIUM_UDID=\n"
        "APPIUM_APP_PACKAGE=\n"
        "APPIUM_APP_ACTIVITY=\n"
        "RESET_APP_DATA=false\n",
    )
    _write_managed(
        root / ".gitignore",
        ".env\n.venv/\n__pycache__/\n*.pyc\nartifacts/*\n!artifacts/.gitkeep\n",
    )
    _write_managed(root / "artifacts" / ".gitkeep", "")
    _write_managed(
        root / "install.ps1",
        "$ErrorActionPreference = 'Stop'\n"
        "$Root = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        "$Venv = Join-Path $Root '.venv'\n"
        "if (-not (Test-Path $Venv)) { python -m venv $Venv }\n"
        "$Python = Join-Path $Venv 'Scripts\\python.exe'\n"
        "$Manifest = Get-Content (Join-Path $Root 'manifest.json') -Raw | ConvertFrom-Json\n"
        "$Types = @($Manifest.cases | ForEach-Object { $_.automation_type } | Select-Object -Unique)\n"
        "if ($Types -contains 'web') {\n"
        "  & $Python -m pip install -r (Join-Path $Root 'requirements-web.txt')\n"
        "  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "  & $Python -m playwright install chromium\n"
        "  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "}\n"
        "if ($Types -contains 'app') {\n"
        "  & $Python -m pip install -r (Join-Path $Root 'requirements-app.txt')\n"
        "  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "}\n"
        "Write-Host 'Installation complete. Copy .env.example to .env and set real environment values.'\n",
    )
    _write_managed(
        root / "run.ps1",
        "param([string]$Script = '')\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$Root = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        "$EnvFile = Join-Path $Root '.env'\n"
        "if (Test-Path $EnvFile) {\n"
        "  Get-Content $EnvFile | ForEach-Object {\n"
        "    $line = $_.Trim()\n"
        "    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {\n"
        "      $name, $value = $line.Split('=', 2)\n"
        "      [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')\n"
        "    }\n"
        "  }\n"
        "}\n"
        "if (-not $Script) {\n"
        "  $manifest = Get-Content (Join-Path $Root 'manifest.json') -Raw | ConvertFrom-Json\n"
        "  $Script = $manifest.cases[-1].script\n"
        "}\n"
        "$ScriptPath = if ([IO.Path]::IsPathRooted($Script)) { $Script } else { Join-Path $Root $Script }\n"
        "$Python = Join-Path $Root '.venv\\Scripts\\python.exe'\n"
        "if (-not (Test-Path $Python)) { throw 'Dependencies are missing. Run .\\install.ps1 first.' }\n"
        "$env:PYTHONPATH = if ($env:PYTHONPATH) { \"$Root;$env:PYTHONPATH\" } else { $Root }\n"
        "Push-Location $Root\n"
        "try { & $Python $ScriptPath; exit $LASTEXITCODE } finally { Pop-Location }\n",
    )
    _write_managed(
        root / "README.md",
        "# AI UI 自动化独立工程\n\n"
        "该目录由测试开发平台自动导出，不依赖平台源码，可复制到其他 Windows 机器执行。"
        "脚本连接真实目标系统，不拦截或伪造项目接口。\n\n"
        "## 环境要求\n\n"
        "- Python 3.11 或更高版本。\n"
        "- Web 脚本由安装命令自动安装 Chromium。\n"
        "- App 脚本还需要目标机已有 Appium Server、对应平台驱动以及 Android SDK/ADB 或 Xcode。\n"
        "- `.venv` 不应跨机器复制；目录迁移后请在新机器重新运行安装命令。\n\n"
        "## App 混合定位\n\n"
        "原生控件优先使用 resource-id/accessibility-id；Cocos、Canvas 等原生层不可见控件使用 "
        "`assets/<资产组>/visual_assets.json` 中的命名模板。通用匹配、缩放、条件轮询和 W3C 点击位于 "
        "`runtime/ui_hybrid_runtime.py`，业务脚本不保存裸坐标。AI 只在采集模板和生成脚本时使用，回归执行不调用 AI 或平台 API。\n\n"
        "Appium 地址会通过真实 `/status` 自动识别根路径或 Appium 1 的 `/wd/hub`。多设备环境必须在 `.env` 设置 `APPIUM_UDID`。\n\n"
        "## 首次安装\n\n"
        "```powershell\n.\\install.ps1\nCopy-Item .env.example .env\n```\n\n"
        "安装程序根据 `manifest.json` 中的真实脚本类型按需安装 Web/App 依赖。"
        "编辑 `.env`，填写真实测试地址、设备和账号相关环境变量。\n\n"
        "## 执行\n\n"
        "```powershell\n# 默认执行最后一次生成的脚本\n.\\run.ps1\n\n"
        "# 指定脚本\n.\\run.ps1 -Script scripts\\web_xxx.py\n```\n\n"
        "执行证据写入 `artifacts/`。生成记录保存在 `manifest.json`。\n",
    )


def export_standalone_ui_script(
    *,
    script: str,
    task: str,
    target: str,
    automation_type: str,
    project_id: int,
    visual_asset_group: str | None = None,
) -> dict[str, Any]:
    """Write one generated script and update the standalone project manifest."""
    root = get_ui_automation_export_root()
    _ensure_scaffold(root)

    normalized_type = "app" if automation_type == "app" else "web"
    filename = _safe_case_name(task, normalized_type)
    relative_script = Path("scripts") / filename
    script_path = root / relative_script
    script_path.write_text(_validate_script(script), encoding="utf-8")

    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] = {"version": 1, "cases": []}
    if manifest_path.exists():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("cases"), list):
            manifest = loaded

    entry = {
        "script": relative_script.as_posix(),
        "task": task,
        "target": target,
        "automation_type": normalized_type,
        "project_id": project_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if normalized_type == "app":
        entry["locator_strategy"] = "hybrid" if "runtime.ui_hybrid_runtime" in script else "native"
    if visual_asset_group:
        visual_manifest = Path("assets") / visual_asset_group / "visual_assets.json"
        if not (root / visual_manifest).is_file():
            raise FileNotFoundError(f"指定的视觉资产清单不存在：{root / visual_manifest}")
        entry["visual_asset_group"] = visual_asset_group
        entry["visual_asset_manifest"] = visual_manifest.as_posix()
        entry["runtime"] = "runtime/ui_hybrid_runtime.py"
    manifest["cases"].append(entry)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "root_dir": str(root),
        "script_path": str(script_path),
        "manifest_path": str(manifest_path),
        "install_command": ".\\install.ps1",
        "run_command": ".\\run.ps1",
    }
