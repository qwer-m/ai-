from __future__ import annotations

import re
from typing import Optional


# 中文注释：模块名归一化映射，优先输出可过滤的稳定 token。
_MODULE_HINTS = {
    "机构": "org",
    "门店": "shop",
    "课程": "course",
    "订单": "order",
    "教材": "material",
    "用户": "user",
    "权限": "auth",
    "支付": "payment",
    "库存": "inventory",
    "测试": "test",
}

_ACTION_HINTS = {
    "关闭": "close",
    "停用": "disable",
    "禁用": "disable",
    "创建": "create",
    "新建": "create",
    "新增": "create",
    "修改": "update",
    "编辑": "update",
    "调整": "update",
    "切换": "switch",
    "删除": "delete",
    "绑定": "bind",
    "解绑": "unbind",
    "导入": "import",
    "导出": "export",
}

_ENTITY_HINTS = {
    "机构": "org",
    "门店": "shop",
    "课程": "course",
    "订单": "order",
    "教材": "material",
    "用户": "user",
    "权限": "permission",
    "账号": "account",
    "用例": "testcase",
    "需求": "requirement",
}


def _tokenize(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{1,}", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _normalize_module_token(module: Optional[str]) -> str:
    raw = str(module or "").strip()
    if not raw:
        return "general"
    for key, value in _MODULE_HINTS.items():
        if key in raw:
            return value
    # 中文注释：兜底把英文/数字 token 规整成可索引字符串。
    lowered = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    return lowered[:32] or "general"


def _find_first_hint(text: str, mapping: dict[str, str]) -> str:
    source = str(text or "")
    for key, value in mapping.items():
        if key in source:
            return value
    return ""


def extract_biz_key(text: str, module: str) -> str:
    """
    从文档文本中提取业务主键。

    规则：
    1. 优先从标题/首句中抽动作 + 实体；
    2. 输出格式：module*entity*action；
    3. 无法命中时，退化为 module + 前3个 token。
    """
    raw = str(text or "").strip()
    title_or_head = (raw.splitlines()[0] if raw else "")[:120]

    module_token = _normalize_module_token(module)
    action = _find_first_hint(title_or_head, _ACTION_HINTS) or _find_first_hint(raw[:300], _ACTION_HINTS)
    entity = _find_first_hint(title_or_head, _ENTITY_HINTS) or _find_first_hint(raw[:300], _ENTITY_HINTS)

    if action and entity:
        return f"{module_token}*{entity}*{action}"

    tokens = _tokenize(f"{title_or_head}\n{raw[:300]}", limit=6)
    fallback = "_".join(tokens[:3]) if tokens else "unknown"
    fallback = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", "_", fallback).strip("_") or "unknown"
    return f"{module_token}*{fallback}"
