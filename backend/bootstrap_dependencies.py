"""在启动服务前同步项目虚拟环境依赖。仅依赖 Python 标准库。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
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
        return os.path.commonpath((str(executable), str(expected_venv.resolve()))) == str(expected_venv.resolve())
    except (OSError, ValueError):
        return False


def _fingerprint(requirements_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(requirements_path.read_bytes())
    digest.update(str(Path(sys.executable).resolve()).encode("utf-8"))
    digest.update(sys.version.encode("utf-8"))
    return digest.hexdigest()


def _missing_critical_imports() -> list[str]:
    return [name for name in CRITICAL_IMPORTS if importlib.util.find_spec(name) is None]


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
    print("[ERROR] Python dependency consistency check failed:")
    print((result.stdout or result.stderr).strip())
    return False


def ensure_dependencies(*, force: bool = False) -> bool:
    root_dir, requirements_path, expected_venv, marker_path = _project_paths()
    if not requirements_path.is_file():
        print(f"[ERROR] Requirements file not found: {requirements_path}")
        return False
    if not _is_expected_virtualenv(expected_venv):
        print(f"[ERROR] Launcher must use the project virtualenv: {expected_venv}")
        print(f"[ERROR] Current Python: {sys.executable}")
        return False

    expected_fingerprint = _fingerprint(requirements_path)
    missing = _missing_critical_imports()
    needs_install = force or bool(missing) or not _marker_matches(marker_path, expected_fingerprint)
    if not needs_install:
        print("Python dependencies are up to date.")
        return True

    reasons = []
    if force:
        reasons.append("forced")
    if missing:
        reasons.append("missing imports: " + ", ".join(missing))
    if not _marker_matches(marker_path, expected_fingerprint):
        reasons.append("requirements.txt changed or has not been synchronized")
    print("Synchronizing Python dependencies (" + "; ".join(reasons) + ")...")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)],
        cwd=root_dir,
    )
    if result.returncode != 0:
        print(f"[ERROR] Dependency installation failed with code {result.returncode}.")
        return False

    missing_after_install = _missing_critical_imports()
    if missing_after_install:
        print("[ERROR] Critical imports are still missing: " + ", ".join(missing_after_install))
        return False
    if not _pip_check(root_dir):
        return False

    marker_path.write_text(expected_fingerprint + "\n", encoding="utf-8")
    print("Python dependencies synchronized successfully.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize launcher Python dependencies.")
    parser.add_argument("--force", action="store_true", help="Run pip install even when the fingerprint matches.")
    args = parser.parse_args()
    return 0 if ensure_dependencies(force=args.force) else 1


if __name__ == "__main__":
    raise SystemExit(main())
