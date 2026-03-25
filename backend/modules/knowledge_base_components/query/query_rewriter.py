"""
查询改写模块（RAG 第二阶段检索治理）。

设计目标：
1. 保留原始问题作为兜底查询，避免改写失真导致漏召回。
2. 在低成本规则下补充 0~1 条改写查询，提升召回覆盖率。
3. 收紧泛化改写触发条件，降低小语料场景噪音。
"""

from __future__ import annotations

import re
from typing import List

# 常见停用词：用于降低无信息量词语对改写结果的干扰。
STOP_WORDS = {
    "怎么",
    "如何",
    "请问",
    "是否",
    "这个",
    "那个",
    "以及",
    "或者",
    "我们",
    "你们",
    "他们",
    "问题",
    "一个",
    "相关",
    "进行",
    "关于",
    "the",
    "and",
    "with",
    "for",
    "from",
    "that",
    "this",
}

# 仅当问题具备“规则/条件”意图时，才生成泛化改写，避免小语料噪音。
GENERALIZE_INTENT_WORDS = {"规则", "条件", "异常", "流程", "字段"}


def _normalize_text(text: str) -> str:
    """统一空白字符，减少同义 query 的文本差异。"""
    return re.sub(r"\s+", " ", (text or "").strip())


def _extract_keywords(text: str, limit: int = 8) -> list[str]:
    """
    提取关键词/实体片段。

    规则说明：
    1. 中文连续词（长度>=2）优先保留。
    2. 英文/数字 token（长度>=2）保留，用于接口名、字段名、状态码等场景。
    """
    pattern = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{1,}|\d{2,}")
    raw_tokens = pattern.findall(text or "")
    keywords: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        t = token.strip()
        if not t:
            continue
        if t.lower() in STOP_WORDS:
            continue
        if t in seen:
            continue
        seen.add(t)
        keywords.append(t)
        if len(keywords) >= limit:
            break
    return keywords


def _should_generate_generalized_rewrite(question: str) -> bool:
    """仅在问题包含规则类意图词时开启泛化改写。"""
    text = question or ""
    return any(word in text for word in GENERALIZE_INTENT_WORDS)


def rewrite_query(question: str, max_queries: int = 2) -> List[str]:
    """
    生成用于扩展召回范围的查询列表。

    返回约定：
    1. 第一项始终是原始 query（兜底）。
    2. 默认最多返回 2 条（原始 + 1 条改写），减少小语料噪音。

    兼容性说明：
    - 接口签名保持不变；旧调用方若不传参，仍能拿到“原始 query + 改写”。
    """
    normalized = _normalize_text(question)
    if not normalized:
        return []

    keywords = _extract_keywords(normalized, limit=10)
    candidates: list[str] = [normalized]

    # 改写1：关键词短语，适合提升实体召回。
    if keywords:
        candidates.append(_normalize_text(" ".join(keywords[:6])))

    # 改写2（收紧触发）：仅规则类问题才补充泛化尾巴。
    if len(keywords) >= 2 and _should_generate_generalized_rewrite(normalized):
        candidates.append(
            _normalize_text(f"{keywords[0]} {keywords[1]} 业务规则 字段约束 边界条件 异常处理")
        )

    deduped: list[str] = []
    seen_norm: set[str] = set()
    for q in candidates:
        nq = _normalize_text(q)
        if not nq or nq in seen_norm:
            continue
        seen_norm.add(nq)
        deduped.append(nq)

    limit = max(1, int(max_queries or 1))
    return deduped[:limit]
