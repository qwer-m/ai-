from __future__ import annotations

from pathlib import Path


SOURCE_ROOTS = (Path("backend"), Path("frontend"))
SOURCE_SUFFIXES = {".py", ".part", ".ts", ".tsx", ".js", ".jsx", ".md", ".json"}
IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "tmp",
    "venv",
}
MOJIBAKE_ESCAPE_MARKERS = (
    r"\ufffd",
    r"\u20ac?",
    r"\u9286?",
    r"\u951b",
    r"\u9286",
    r"\ue15f",
    r"\ue1",
)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if path.name == "test_source_text_hygiene.py":
                continue
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            files.append(path)
    return files


def test_source_files_do_not_contain_common_mojibake_markers() -> None:
    offenders: list[str] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            offenders.append(f"{path}: not valid utf-8")
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            escaped = line.encode("unicode_escape").decode("ascii")
            has_private_use = any(0xE000 <= ord(char) <= 0xF8FF for char in line)
            has_marker = any(marker in escaped for marker in MOJIBAKE_ESCAPE_MARKERS)
            if has_private_use or has_marker:
                offenders.append(f"{path}:{line_number}: {escaped[:180]}")

    assert offenders == []
