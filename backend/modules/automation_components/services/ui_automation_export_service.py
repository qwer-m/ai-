"""将平台生成的 UI 自动化操作导出为桌面独立 Page Object 项目。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


_INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_AI_LOCATOR_WRAPPER = ast.parse(
    """
def ai_locate_element(screenshot_path, element_description):
    from runtime.ai_visual_runtime import locate_element
    return locate_element(screenshot_path, element_description)
"""
).body[0]


class UIAutomationExportService:
    """按平台项目隔离导出，不向当前源码仓库写入运行产物。"""

    def __init__(self, root_dir: str | Path | None = None):
        configured = root_dir or os.environ.get("UI_AUTOMATION_EXPORT_ROOT")
        self.root_dir = Path(configured) if configured else Path.home() / "Desktop" / "ai ui自动化"

    @staticmethod
    def _safe_name(value: str, fallback: str) -> str:
        cleaned = _INVALID_WINDOWS_NAME.sub("_", (value or "").strip()).rstrip(". ")
        return cleaned[:80] or fallback

    @staticmethod
    def _operation_slug(operation_name: str) -> str:
        ascii_name = re.sub(r"[^a-zA-Z0-9_]+", "_", operation_name.strip()).strip("_").lower()
        if ascii_name:
            return ascii_name[:60]
        digest = hashlib.sha1(operation_name.encode("utf-8")).hexdigest()[:12]
        return f"operation_{digest}"

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)

    @classmethod
    def _write_json(cls, path: Path, payload: Any) -> None:
        cls._write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    @classmethod
    def _align_script_project_root(cls, script_path: Path, script_relative: Path) -> None:
        """脚本移入子目录后，同步 ROOT/PROJECT_ROOT 对项目根目录的 parents 索引。"""
        if not script_path.is_file():
            return
        content = script_path.read_text(encoding="utf-8")
        parent_index = len(script_relative.parts) - 1
        pattern = re.compile(
            r"(?P<prefix>\b(?:ROOT|PROJECT_ROOT)\s*=\s*Path\(__file__\)\.resolve\(\)\.parents\[)\d+(?P<suffix>\])"
        )
        updated = pattern.sub(lambda match: f"{match.group('prefix')}{parent_index}{match.group('suffix')}", content)
        if updated != content:
            cls._write_text(script_path, updated)

    def _project_dir(self, *, project_id: int, project_name: str) -> Path:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        base_name = self._safe_name(project_name, f"project_{project_id}")
        candidate = self.root_dir / base_name
        marker = candidate / ".project.json"
        if not candidate.exists():
            return candidate
        marker_data = self._read_json(marker, {})
        if int(marker_data.get("project_id") or 0) == project_id:
            return candidate
        return self.root_dir / f"{base_name}_{project_id}"

    def resolve_operation(
        self,
        *,
        project_id: int,
        project_name: str,
        operation_name: str,
    ) -> dict[str, Any] | None:
        """解析已存在的独立 Page Object 操作，不重新拆分编排脚本。"""
        project_dir = self._project_dir(project_id=project_id, project_name=project_name)
        marker = self._read_json(project_dir / ".project.json", {})
        if int(marker.get("project_id") or 0) != project_id:
            return None
        manifest_path = project_dir / "manifest.json"
        manifest = self._read_json(manifest_path, {})
        operation = next(
            (
                item
                for item in (manifest.get("operations") or [])
                if str(item.get("name") or "").strip() == operation_name.strip()
            ),
            None,
        )
        if not operation:
            return None
        script_path = project_dir / str(operation.get("script") or "")
        page_paths = [project_dir / str(path) for path in (operation.get("page_objects") or [])]
        if not script_path.is_file() or not page_paths or not all(path.is_file() for path in page_paths):
            raise ValueError(f"桌面操作“{operation_name}”的脚本或 Page Object 文件不完整")
        return {
            "project_id": project_id,
            "project_name": project_name,
            "root_dir": str(project_dir),
            "script_path": str(script_path),
            "page_paths": [str(path) for path in page_paths],
            "manifest_path": str(manifest_path),
            "operation_slug": str(operation.get("slug") or ""),
        }

    def prepare_operation_for_execution(
        self,
        *,
        project_id: int,
        project_name: str,
        operation_name: str,
        description: str,
        steps: Iterable[str] | None,
        script: str,
        automation_type: str,
        target: str,
    ) -> dict[str, Any]:
        """支持模型单文件输出和已拆分的 Page Object 编排脚本两种真实来源。"""
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:
            raise ValueError(f"自动化脚本语法无效：{exc}") from exc
        if any(isinstance(node, ast.ClassDef) for node in tree.body):
            return self.export_operation(
                project_id=project_id,
                project_name=project_name,
                operation_name=operation_name,
                description=description,
                steps=steps,
                script=script,
                automation_type=automation_type,
                target=target,
            )
        resolved = self.resolve_operation(
            project_id=project_id,
            project_name=project_name,
            operation_name=operation_name,
        )
        if resolved:
            return resolved
        raise ValueError(
            f"操作“{operation_name}”是编排脚本，但桌面项目中没有对应的 Page Object 文件，请重新转化该操作"
        )

    @staticmethod
    def _case_parent_chain(case: Any, cases_by_id: dict[int, Any]) -> list[Any]:
        chain: list[Any] = []
        parent_id = getattr(case, "parent_id", None)
        visited: set[int] = set()
        while parent_id:
            if int(parent_id) in visited:
                raise ValueError("自动化目录存在循环引用")
            visited.add(int(parent_id))
            parent = cases_by_id.get(int(parent_id))
            if parent is None or str(getattr(parent, "type", "")) != "folder":
                raise ValueError("自动化目录的父节点不存在或不是文件夹")
            chain.append(parent)
            parent_id = getattr(parent, "parent_id", None)
        chain.reverse()
        return chain

    def sync_project_hierarchy(
        self,
        *,
        project_id: int,
        project_name: str,
        cases: Iterable[Any],
    ) -> dict[int, str]:
        """将平台树形层级映射为桌面工程 scripts 目录。

        Page Object 保持在 pages 中，因为它们可能被多个操作共享；只移动可独立运行的编排脚本。
        """
        project_dir = self._project_dir(project_id=project_id, project_name=project_name)
        marker = self._read_json(project_dir / ".project.json", {})
        if int(marker.get("project_id") or 0) != project_id:
            return {}
        manifest_path = project_dir / "manifest.json"
        manifest = self._read_json(manifest_path, {})
        operations = list(manifest.get("operations") or [])
        case_rows = [case for case in cases if getattr(case, "id", None)]
        cases_by_id = {int(case.id): case for case in case_rows}

        folder_entries: list[dict[str, Any]] = []
        folder_paths: dict[int, Path] = {}
        for case in case_rows:
            if str(getattr(case, "type", "")) != "folder":
                continue
            chain = [*self._case_parent_chain(case, cases_by_id), case]
            relative = Path("scripts")
            names: list[str] = []
            ids: list[int] = []
            for folder in chain:
                relative /= self._safe_name(str(folder.name), f"folder_{folder.id}")
                names.append(str(folder.name))
                ids.append(int(folder.id))
            folder_paths[int(case.id)] = relative
            folder_entries.append({"id": int(case.id), "name": str(case.name), "parent_id": case.parent_id, "path": relative.as_posix()})
            current = project_dir / relative
            current.mkdir(parents=True, exist_ok=True)
            self._write_text(current / "__init__.py", "")

        moved: dict[int, str] = {}
        old_directories: set[Path] = set()
        for case in case_rows:
            if str(getattr(case, "type", "")) != "file":
                continue
            case_id = int(case.id)
            automation_type = str(getattr(case, "automation_type", "") or "")
            operation = next((item for item in operations if int(item.get("test_case_id") or 0) == case_id), None)
            if operation is None:
                operation = next(
                    (
                        item
                        for item in operations
                        if str(item.get("name") or "") == str(case.name)
                        and str(item.get("automation_type") or "") == automation_type
                    ),
                    None,
                )
            if operation is None:
                continue

            chain = self._case_parent_chain(case, cases_by_id)
            script_parent = Path("scripts")
            for folder in chain:
                script_parent /= self._safe_name(str(folder.name), f"folder_{folder.id}")
            old_relative = Path(str(operation.get("script") or f"scripts/{operation.get('slug') or case_id}.py"))
            filename = old_relative.name or f"{operation.get('slug') or case_id}.py"
            new_relative = script_parent / filename
            old_path = project_dir / old_relative
            new_path = project_dir / new_relative
            new_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_text(new_path.parent / "__init__.py", "")
            if old_path != new_path and old_path.is_file():
                old_directories.add(old_path.parent)
                old_path.replace(new_path)
            self._align_script_project_root(new_path, new_relative)
            operation["script"] = new_relative.as_posix()
            operation["test_case_id"] = case_id
            operation["hierarchy_ids"] = [int(folder.id) for folder in chain]
            operation["hierarchy"] = [str(folder.name) for folder in chain]
            moved[case_id] = str(new_path)

        scripts_root = (project_dir / "scripts").resolve()
        for directory in sorted(old_directories, key=lambda item: len(item.parts), reverse=True):
            current = directory.resolve()
            while current != scripts_root and scripts_root in current.parents and current.is_dir():
                children = list(current.iterdir())
                removable = children == [current / "__init__.py"] or not children
                if not removable:
                    break
                if children:
                    children[0].unlink()
                current.rmdir()
                current = current.parent

        manifest["version"] = max(3, int(manifest.get("version") or 0))
        manifest["folders"] = sorted(folder_entries, key=lambda item: (item["path"], item["id"]))
        manifest["operations"] = operations
        self._write_json(manifest_path, manifest)
        return moved

    def get_project_code_paths(
        self,
        *,
        project_id: int,
        project_name: str,
        cases: Iterable[Any],
    ) -> dict[int, str]:
        """返回平台节点对应的桌面真实代码路径。"""
        project_dir = self._project_dir(project_id=project_id, project_name=project_name)
        manifest = self._read_json(project_dir / "manifest.json", {})
        operations = list(manifest.get("operations") or [])
        case_rows = [case for case in cases if getattr(case, "id", None)]
        cases_by_id = {int(case.id): case for case in case_rows}
        result: dict[int, str] = {}
        for case in case_rows:
            case_id = int(case.id)
            chain = self._case_parent_chain(case, cases_by_id)
            if str(getattr(case, "type", "")) == "folder":
                relative = Path("scripts")
                for folder in [*chain, case]:
                    relative /= self._safe_name(str(folder.name), f"folder_{folder.id}")
                result[case_id] = str(project_dir / relative)
                continue
            operation = next((item for item in operations if int(item.get("test_case_id") or 0) == case_id), None)
            if operation is None:
                operation = next(
                    (
                        item
                        for item in operations
                        if str(item.get("name") or "") == str(case.name)
                        and str(item.get("automation_type") or "") == str(getattr(case, "automation_type", "") or "")
                    ),
                    None,
                )
            if operation and operation.get("script"):
                result[case_id] = str(project_dir / str(operation["script"]))
        return result

    @staticmethod
    def normalize_steps(task: str, steps: Iterable[str] | None) -> list[str]:
        provided = [str(step).strip() for step in (steps or []) if str(step).strip()]
        if provided:
            return provided
        lines = []
        for raw in (task or "").splitlines():
            line = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", raw).strip()
            if line:
                lines.append(line)
        return lines or ([task.strip()] if task.strip() else [])

    @staticmethod
    def _replace_ai_locator(node: ast.AST) -> ast.AST:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "ai_locate_element":
            return ast.fix_missing_locations(ast.parse(ast.unparse(_AI_LOCATOR_WRAPPER)).body[0])
        return node

    @classmethod
    def _split_page_object(cls, script: str, module_slug: str) -> tuple[str, str, list[str]]:
        tree = ast.parse(script)
        page_classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        if not page_classes:
            raise ValueError("生成脚本没有顶层 Page Object 类，无法导出为独立项目")
        class_names = [node.name for node in page_classes]

        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
        helpers = [
            cls._replace_ai_locator(node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "main"
        ]
        page_tree = ast.Module(body=[*imports, *assignments, *helpers, *page_classes], type_ignores=[])

        script_nodes = [node for node in tree.body if not isinstance(node, ast.ClassDef)]
        script_nodes = [cls._replace_ai_locator(node) for node in script_nodes]
        import_pages = ast.ImportFrom(
            module=f"pages.{module_slug}_pages",
            names=[ast.alias(name=name) for name in class_names],
            level=0,
        )
        insertion = 0
        if (
            script_nodes
            and isinstance(script_nodes[0], ast.Expr)
            and isinstance(script_nodes[0].value, ast.Constant)
            and isinstance(script_nodes[0].value.value, str)
        ):
            insertion = 1
        while insertion < len(script_nodes):
            node = script_nodes[insertion]
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                insertion += 1
                continue
            break
        script_nodes.insert(insertion, import_pages)
        script_tree = ast.Module(body=script_nodes, type_ignores=[])

        return (
            ast.unparse(ast.fix_missing_locations(script_tree)).rstrip() + "\n",
            ast.unparse(ast.fix_missing_locations(page_tree)).rstrip() + "\n",
            class_names,
        )

    @staticmethod
    def _runtime_source() -> str:
        return '''"""独立 AI 视觉定位运行时，使用 OpenAI 兼容视觉接口。"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path

import requests


def locate_element(screenshot_path: str, element_description: str) -> tuple[int, int]:
    api_key = os.environ.get("UI_VISION_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = (os.environ.get("UI_VISION_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("UI_VISION_MODEL", "gpt-4.1-mini")
    if not api_key:
        raise RuntimeError("未配置 UI_VISION_API_KEY，无法执行独立 AI 视觉定位")

    image_path = Path(screenshot_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"截图不存在：{image_path}")
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        f"定位图片中的目标元素：{element_description}。"
        "只返回元素中心点坐标 JSON，例如 {\\"x\\": 120, \\"y\\": 300}。"
    )
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ]}],
            "temperature": 0,
        },
        timeout=90,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    match = re.search(r"\\{[^{}]*\\}", content)
    if match:
        coords = json.loads(match.group(0))
        return int(coords["x"]), int(coords["y"])
    numbers = re.findall(r"-?\\d+", content)
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    raise RuntimeError(f"视觉模型未返回有效坐标：{content}")
'''

    @staticmethod
    def _run_script_source() -> str:
        return '''param(
    [Parameter(Mandatory = $true)]
    [string]$Operation
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Manifest = Get-Content -LiteralPath (Join-Path $Root "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$Entry = $Manifest.operations | Where-Object { $_.name -eq $Operation -or $_.slug -eq $Operation } | Select-Object -First 1
if (-not $Entry) { throw "未找到自动化操作：$Operation" }
$ProjectPython = Join-Path $Root ".venv\\Scripts\\python.exe"
$Python = if (Test-Path -LiteralPath $ProjectPython) { $ProjectPython } else { (Get-Command python -ErrorAction Stop).Source }
$env:PYTHONPATH = $Root
$env:UI_AUTOMATION_PROJECT_ROOT = $Root
& $Python (Join-Path $Root $Entry.script)
exit $LASTEXITCODE
'''

    @staticmethod
    def _install_script_source() -> str:
        return '''$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath (Join-Path $Root ".venv\\Scripts\\python.exe"))) {
    & $Python -m venv (Join-Path $Root ".venv")
}
$ProjectPython = Join-Path $Root ".venv\\Scripts\\python.exe"
& $ProjectPython -m pip install --upgrade pip
& $ProjectPython -m pip install -r (Join-Path $Root "requirements.txt")
'''

    def export_operation(
        self,
        *,
        project_id: int,
        project_name: str,
        operation_name: str,
        description: str,
        steps: Iterable[str] | None,
        script: str,
        automation_type: str,
        target: str,
    ) -> dict[str, Any]:
        name = (operation_name or "").strip()
        if not name:
            raise ValueError("自动化操作名称不能为空")
        normalized_steps = self.normalize_steps(description, steps)
        slug = self._operation_slug(name)
        project_dir = self._project_dir(project_id=project_id, project_name=project_name)
        script_content, page_content, class_names = self._split_page_object(script, slug)

        for relative in ("scripts", "pages", "runtime", "assets", "artifacts"):
            (project_dir / relative).mkdir(parents=True, exist_ok=True)
        for package in ("scripts/__init__.py", "pages/__init__.py", "runtime/__init__.py"):
            path = project_dir / package
            if not path.exists():
                self._write_text(path, "")

        script_rel = f"scripts/{slug}.py"
        page_rel = f"pages/{slug}_pages.py"
        self._write_text(project_dir / script_rel, script_content)
        self._write_text(project_dir / page_rel, page_content)
        self._write_text(project_dir / "runtime/ai_visual_runtime.py", self._runtime_source())
        self._write_text(project_dir / "run.ps1", self._run_script_source())
        self._write_text(project_dir / "install.ps1", self._install_script_source())
        manifest_path = project_dir / "manifest.json"
        manifest = self._read_json(manifest_path, {})
        existing_types = {
            str(item.get("automation_type") or "")
            for item in (manifest.get("operations") or [])
        }
        existing_types.add(automation_type)
        requirements = ["requests>=2.31,<3"]
        if "app" in existing_types:
            requirements.append("Appium-Python-Client>=2.11,<6")
        if "web" in existing_types:
            requirements.append("playwright>=1.40,<2")
        self._write_text(project_dir / "requirements.txt", "\n".join(requirements) + "\n")
        self._write_text(
            project_dir / ".env.example",
            "UI_VISION_API_KEY=\nUI_VISION_BASE_URL=https://api.openai.com/v1\nUI_VISION_MODEL=gpt-4.1-mini\nAPPIUM_SERVER_URL=http://127.0.0.1:4723\n",
        )
        self._write_text(
            project_dir / "README.md",
            f"# {project_name} UI 自动化\n\n该目录由平台按项目独立生成，源码、依赖、资源和运行产物均与平台仓库隔离。\n\n"
            "## 首次安装\n\n```powershell\n.\\install.ps1\n```\n\n"
            f"## 运行操作\n\n```powershell\n.\\run.ps1 -Operation \"{name}\"\n```\n",
        )

        marker = {
            "project_id": project_id,
            "project_name": project_name,
            "platform_source": "AI测试平台",
        }
        self._write_json(project_dir / ".project.json", marker)
        operations = list(manifest.get("operations") or [])
        entry = {
            "slug": slug,
            "name": name,
            "description": description,
            "steps": normalized_steps,
            "automation_type": automation_type,
            "target": target,
            "architecture": "page_object",
            "page_objects": [page_rel],
            "page_classes": class_names,
            "script": script_rel,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        operations = [item for item in operations if item.get("slug") != slug]
        operations.append(entry)
        self._write_json(
            manifest_path,
            {"version": 2, "project": marker, "operations": operations},
        )
        return {
            "project_id": project_id,
            "project_name": project_name,
            "root_dir": str(project_dir),
            "script_path": str(project_dir / script_rel),
            "page_paths": [str(project_dir / page_rel)],
            "manifest_path": str(manifest_path),
            "operation_slug": slug,
        }
