from __future__ import annotations

import re
import tokenize
from pathlib import Path


SOURCE_ROOTS = (Path("backend"), Path("frontend"))
SOURCE_SUFFIXES = {
    ".bat",
    ".conf",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".part",
    ".ps1",
    ".py",
    ".sh",
    ".template",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
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
SUSPECT_MOJIBAKE_CODEPOINTS = frozenset(
    (
        0x6D93,
        0x95C2,
        0x93B4,
        0x701B,
        0x9352,
        0x951B,
        0x9286,
        0xE15F,
        0xFFFD,
    )
)
PRIVATE_USE_MIN = 0xE000
PRIVATE_USE_MAX = 0xF8FF
ALLOWED_CONTROL_CHARS = frozenset(("\n", "\r", "\t"))
CONVERTIBLE_UNICODE_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}")
PYTHON_STRING_PREFIX_CHARS = frozenset("rRuUbBfF")


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.name == "test_source_text_hygiene.py":
                continue
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            files.append(path)
    return files


def _python_string_prefix(token_text: str) -> str:
    prefix_end = 0
    while prefix_end < len(token_text) and token_text[prefix_end] in PYTHON_STRING_PREFIX_CHARS:
        prefix_end += 1
    return token_text[:prefix_end].lower()


def _is_raw_or_bytes_string(token_text: str) -> bool:
    prefix = _python_string_prefix(token_text)
    return "r" in prefix or "b" in prefix


def _is_convertible_unicode_escape(token_text: str, escape_start: int) -> bool:
    backslash_count = 0
    index = escape_start - 1
    while index >= 0 and token_text[index] == "\\":
        backslash_count += 1
        index -= 1
    return backslash_count % 2 == 0


def _unicode_escape_snippet(line: str, index: int, radius: int = 80) -> str:
    start = max(0, index - radius)
    end = min(len(line), index + radius + 1)
    snippet = line[start:end].encode("unicode_escape").decode("ascii")
    if start > 0:
        snippet = "..." + snippet
    if end < len(line):
        snippet += "..."
    return snippet


def _is_private_use_codepoint(codepoint: int) -> bool:
    return PRIVATE_USE_MIN <= codepoint <= PRIVATE_USE_MAX


def _is_disallowed_control_char(char: str) -> bool:
    if char in ALLOWED_CONTROL_CHARS:
        return False
    codepoint = ord(char)
    return codepoint <= 0x1F or 0x7F <= codepoint <= 0x9F


def _suspicious_text_reason(char: str) -> str | None:
    codepoint = ord(char)
    if codepoint in SUSPECT_MOJIBAKE_CODEPOINTS:
        return f"mojibake U+{codepoint:04X}"
    if _is_private_use_codepoint(codepoint):
        return f"private-use U+{codepoint:04X}"
    if _is_disallowed_control_char(char):
        return f"control U+{codepoint:04X}"
    return None


def test_source_files_do_not_contain_common_mojibake_or_control_chars() -> None:
    offenders: list[str] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            offenders.append(f"{path}: not valid utf-8")
            continue
        for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
            for column, char in enumerate(line):
                reason = _suspicious_text_reason(char)
                if reason is None:
                    continue
                snippet = _unicode_escape_snippet(line, column)
                offenders.append(f"{path}:{line_number}: {reason}: {snippet}")

    assert offenders == [], (
        "Found suspicious mojibake/control characters in source text:\n" + "\n".join(offenders)
    )


def test_python_non_raw_strings_do_not_contain_convertible_unicode_escapes() -> None:
    offenders: list[str] = []
    for path in _source_files():
        if path.suffix != ".py":
            continue
        try:
            with path.open("rb") as source:
                tokens = tokenize.tokenize(source.readline)
                for token in tokens:
                    if token.type != tokenize.STRING or _is_raw_or_bytes_string(token.string):
                        continue
                    for match in CONVERTIBLE_UNICODE_ESCAPE_RE.finditer(token.string):
                        if not _is_convertible_unicode_escape(token.string, match.start()):
                            continue
                        line_number = token.start[0] + token.string.count("\n", 0, match.start())
                        offenders.append(f"{path}:{line_number}: {match.group(0)}")
        except (SyntaxError, tokenize.TokenError, UnicodeDecodeError) as exc:
            offenders.append(f"{path}: unable to tokenize: {exc}")

    assert offenders == [], (
        "Found convertible \\uXXXX escapes in non-raw Python strings:\n" + "\n".join(offenders)
    )
