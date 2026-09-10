"""启动服务前同步项目虚拟环境依赖；本文件只依赖 Python 标准库。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path


CRITICAL_IMPORTS = (
    "dotenv",
    "fastapi",
    "uvicorn",
    "celery",
    "playwright",
    "cv2",
    "appium",
    "selenium",
)

IMPORT_DISTRIBUTIONS = {
    "dotenv": "python-dotenv",
    "cv2": "opencv-python-headless",
    "appium": "Appium-Python-Client",
}


def _project_paths() -> tuple[Path, Path, Path, Path]:
    backend_dir = Path(__file__).resolve().parent
    root_dir = backend_dir.parent
    requirements_path = backend_dir / "requirements.txt"
    expected_venv = root_dir / ".venv"
    marker_path = expected_venv / ".ai_test_platform_requirements.sha256"
    return root_dir, requirements_path, expected_venv, marker_path


def _is_expected_virtualenv(expected_venv: Path) -> bool:
    try:
        executable = Path(sys.executable).resolve()
        expected = expected_venv.resolve()
        return os.path.commonpath((str(executable), str(expected))) == str(expected)
    except (OSError, ValueError):
        return False


def _fingerprint(requirements_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(requirements_path.read_bytes())
    digest.update(str(Path(sys.executable).resolve()).encode("utf-8"))
    digest.update(sys.version.encode("utf-8"))
    return digest.hexdigest()


def _requirement_lines(requirements_path: Path) -> list[str]:
    return [
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    ]


def _distribution_name(requirement_line: str) -> str | None:
    match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?", requirement_line)
    return match.group(1) if match else None


def _unsatisfied_requirements(requirements_path: Path) -> list[str]:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        Requirement = None  # type: ignore[assignment,misc]

    unsatisfied: list[str] = []
    for line in _requirement_lines(requirements_path):
        name = _distribution_name(line)
        if not name:
            continue
        try:
            installed_version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            unsatisfied.append(line)
            continue
        if Requirement is not None:
            parsed = Requirement(line)
            if parsed.marker is not None and not parsed.marker.evaluate():
                continue
            if parsed.specifier and not parsed.specifier.contains(installed_version, prereleases=True):
                unsatisfied.append(line)
    return unsatisfied


def _missing_critical_imports() -> list[str]:
    return [name for name in CRITICAL_IMPORTS if importlib.util.find_spec(name) is None]


def _requirements_for_missing_imports(requirements_path: Path, missing_imports: list[str]) -> list[str]:
    required_names = {IMPORT_DISTRIBUTIONS.get(name, name).lower() for name in missing_imports}
    return [
        line
        for line in _requirement_lines(requirements_path)
        if (_distribution_name(line) or "").lower() in required_names
    ]


def _marker_matches(marker_path: Path, expected: str) -> bool:
    try:
        return marker_path.read_text(encoding="utf-8").strip() == expected
    except OSError:
        return False


def _pip_check(root_dir: Path) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=root_dir,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        return True
    print("[ERROR] Python dependency consistency check failed:", flush=True)
    print((result.stdout or result.stderr).strip(), flush=True)
    return False


def ensure_dependencies(*, force: bool = False) -> bool:
    root_dir, requirements_path, expected_venv, marker_path = _project_paths()
    if not requirements_path.is_file():
        print(f"[ERROR] Requirements file not found: {requirements_path}", flush=True)
        return False
    if not _is_expected_virtualenv(expected_venv):
        print(f"[ERROR] Launcher must use the project virtualenv: {expected_venv}", flush=True)
        print(f"[ERROR] Current Python: {sys.executable}", flush=True)
        return False

    expected_fingerprint = _fingerprint(requirements_path)
    missing_imports = _missing_critical_imports()
    fingerprint_changed = not _marker_matches(marker_path, expected_fingerprint)
    if not force and not missing_imports and not fingerprint_changed:
        print("Python dependencies are up to date.", flush=True)
        return True

    reasons = []
    if force:
        reasons.append("forced")
    if missing_imports:
        reasons.append("missing imports: " + ", ".join(missing_imports))
    if fingerprint_changed:
        reasons.append("requirements.txt changed or has not been synchronized")
    print("Synchronizing Python dependencies (" + "; ".join(reasons) + ")...", flush=True)

    targets = _requirement_lines(requirements_path) if force else _unsatisfied_requirements(requirements_path)
    targets.extend(_requirements_for_missing_imports(requirements_path, missing_imports))
    targets = list(dict.fromkeys(targets))

    if targets:
        print("Installing: " + ", ".join(targets), flush=True)
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--only-binary=:all:",
            "--timeout=60",
            "--retries=2",
        ]
        command.extend(["-r", str(requirements_path)] if force else targets)
        try:
            result = subprocess.run(command, cwd=root_dir, timeout=600)
        except subprocess.TimeoutExpired:
            print("[ERROR] Dependency installation timed out after 600 seconds.", flush=True)
            return False
        if result.returncode != 0:
            print(f"[ERROR] Dependency installation failed with code {result.returncode}.", flush=True)
            return False

    missing_after_install = _missing_critical_imports()
    if missing_after_install:
        print("[ERROR] Critical imports are still missing: " + ", ".join(missing_after_install), flush=True)
        return False
    if not _pip_check(root_dir):
        return False

    marker_path.write_text(expected_fingerprint + "\n", encoding="utf-8")
    print("Python dependencies synchronized successfully.", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize launcher Python dependencies.")
    parser.add_argument("--force", action="store_true", help="Install the complete requirements file.")
    args = parser.parse_args()
    return 0 if ensure_dependencies(force=args.force) else 1


if __name__ == "__main__":
    raise SystemExit(main())
