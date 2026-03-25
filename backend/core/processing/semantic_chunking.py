from __future__ import annotations

import re


_PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n){2,}")
_SENTENCE_CAPTURE_RE = re.compile(r".*?(?:[。！？；;.!?]|$)", flags=re.S)


def _hard_split(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        return []
    return [text[i : i + max_chars].strip() for i in range(0, len(text), max_chars) if text[i : i + max_chars].strip()]


def _split_sentence_units(paragraph: str, max_chars: int) -> list[str]:
    sentence_like = [
        s.strip()
        for s in _SENTENCE_CAPTURE_RE.findall(paragraph or "")
        if s and s.strip()
    ]
    if not sentence_like:
        return _hard_split(paragraph or "", max_chars)

    units: list[str] = []
    for sentence in sentence_like:
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue
        units.extend(_hard_split(sentence, max_chars))
    return units


def split_semantic_text(text: str, max_chars: int, min_chars: int = 220) -> list[str]:
    """
    语义优先分块：
    1. 先按段落切，再按句号/问号/感叹号等句边界切；
    2. 在长度约束下聚合为 chunk；
    3. 极端长句再退化为硬切，确保不会超限。
    """
    raw = str(text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []

    safe_max = max(20, int(max_chars))
    safe_min = max(40, min(int(min_chars), safe_max))

    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(raw) if p and p.strip()]
    if not paragraphs:
        paragraphs = [raw]

    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_sentence_units(paragraph, safe_max))

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for unit in units:
        if not unit:
            continue
        sep = 1 if current else 0
        candidate_len = current_len + sep + len(unit)
        if current and candidate_len > safe_max:
            chunks.append("\n".join(current).strip())
            current = [unit]
            current_len = len(unit)
            continue
        current.append(unit)
        current_len = candidate_len

    if current:
        chunks.append("\n".join(current).strip())

    # 合并过短尾块，减少碎片。
    merged: list[str] = []
    for chunk in chunks:
        if (
            merged
            and len(chunk) < safe_min
            and len(merged[-1]) + 1 + len(chunk) <= safe_max
        ):
            merged[-1] = f"{merged[-1]}\n{chunk}".strip()
        else:
            merged.append(chunk)

    return [c for c in merged if c]


def semantic_head(text: str, max_chars: int) -> tuple[str, bool]:
    """
    在长度预算内保留“语义完整”的前缀，尽量避免硬截断到句子中间。
    返回：(结果文本, 是否发生截断)
    """
    raw = str(text or "").strip()
    if max_chars <= 0:
        return "", bool(raw)
    if len(raw) <= max_chars:
        return raw, False

    segments = split_semantic_text(
        raw,
        max_chars=max(20, min(int(max_chars), 1200)),
        min_chars=max(40, int(max_chars * 0.2)),
    )
    if not segments:
        clipped = raw[:max_chars].rstrip()
        return clipped, len(clipped) < len(raw)

    chosen: list[str] = []
    used = 0
    for seg in segments:
        sep = 2 if chosen else 0
        if used + sep + len(seg) <= max_chars:
            chosen.append(seg)
            used += sep + len(seg)
            continue
        break

    if not chosen:
        clipped = raw[:max_chars].rstrip()
        return clipped, len(clipped) < len(raw)

    clipped = "\n\n".join(chosen).strip()
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars].rstrip()
    return clipped, len(clipped) < len(raw)
