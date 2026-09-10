"""文档摘要生成与持久化，供文档写入及上下文检索共用。"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session
from core.db.model_defs import KnowledgeDocument


def ensure_document_summary(
    *,
    doc: KnowledgeDocument,
    db: Session,
    user_id: Optional[int],
) -> str:
    """为长文档生成可复用摘要，保持现有内容选择规则。"""
    if not doc:
        return ""
    if doc.summary and str(doc.summary).strip():
        return doc.summary

    content = doc.content or ""
    if len(content) < 12000:
        return content

    try:
        from core.ai.ai_client import get_client_for_user

        client = get_client_for_user(user_id, db)
        summary = client.compress_context(
            content,
            prompt=(
                "请忠实压缩以下文档，保留标题层级、关键实体、明确事实、"
                "先后关系、约束、数值和例外。不按任何下游任务改写事实，不补充原文未声明内容，输出纯文本。"
            ),
            db=db,
        )
        if (
            summary
            and isinstance(summary, str)
            and not summary.startswith("Error")
            and not summary.startswith("Exception")
        ):
            doc.summary = summary
            db.commit()
            db.refresh(doc)
            return summary
    except Exception:
        pass

    return content

