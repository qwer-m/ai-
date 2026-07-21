from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional


_MAX_KEY_PART_LENGTH = 48


def _normalize_key_part(value: object, *, fallback: str = "", max_length: int = _MAX_KEY_PART_LENGTH) -> str:
    """把任意语言的结构名称归一化为稳定键，不依赖业务词表。"""
    raw = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if not raw:
        return fallback
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", raw).strip("_")
    if not normalized:
        return fallback
    limit = max(16, int(max_length or _MAX_KEY_PART_LENGTH))
    if len(normalized) <= limit:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[: limit - 11].rstrip('_')}_{digest}"


def _first_content_heading(text: str) -> str:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 中文注释：只剥离通用编号/Markdown 标记，保留原始业务名称。
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^\d+(?:\.\d+)*\s*[.、):：-]?\s*", "", line)
        return line
    return ""


def _normalize_module_token(module: Optional[str]) -> str:
    return _normalize_key_part(module, fallback="general")


def extract_biz_key(text: str, module: str) -> str:
    """
    从文档结构中提取业务主键。

    规则：
    1. 有显式模块时直接使用模块名，保证同模块数据稳定聚合；
    2. 无显式模块时使用首个标题，避免所有未知中文模块落入 general；
    3. 仅做 Unicode/标点归一化，不猜测具体领域、动作或实体。
    """
    module_token = _normalize_module_token(module)
    if module_token != "general":
        return f"module*{module_token}"

    heading_token = _normalize_key_part(_first_content_heading(text), fallback="")
    if heading_token:
        return f"heading*{heading_token}"
    return "general"
