from __future__ import annotations

import re
import unicodedata

from .coverage_strategy import stopwords

_STOPWORDS = stopwords()

_OCR_CHAR_TRANSLATION = str.maketrans(
    {
        "⾼": "高",
        "⾸": "首",
        "⻚": "页",
        "⽂": "文",
        "⽣": "生",
        "⼊": "入",
        "⼝": "口",
        "⼆": "二",
        "⼀": "一",
        "⽬": "目",
        "⽤": "用",
        "⼾": "户",
        "⽀": "支",
        "⻓": "长",
        "⽅": "方",
        "⻅": "见",
        "⽇": "日",
        "⾃": "自",
        "⼒": "力",
        "⾄": "至",
        "⼼": "心",
        "⼯": "工",
        "⽆": "无",
        "⽹": "网",
    }
)

def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.translate(_OCR_CHAR_TRANSLATION)
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", normalized)
    normalized = normalized.replace("　", " ")
    return normalized

def _tokenize(text: str, limit: int = 18) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", _normalize_text(text))
    output: list[str] = []
    seen: set[str] = set()
    expanded: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            for idx in range(0, len(token) - 1):
                expanded.append(token[idx : idx + 2])
        else:
            expanded.append(token)
    for token in expanded:
        key = token.lower()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        output.append(token)
        if len(output) >= max(6, int(limit)):
            break
    return output

def _extract_rule_id(text: str) -> str | None:
    match = re.search(r"\bREQ[-_\s]?\d+\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0).upper().replace(" ", "")

__all__ = ["_STOPWORDS", "_normalize_text", "_tokenize", "_extract_rule_id"]
