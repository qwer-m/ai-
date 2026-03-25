import json
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument, Project


class RelationUpdateRequest(BaseModel):
    """更新测试用例与需求文档关联关系的请求体。"""

    doc_id: int
    source_doc_id: Optional[int] = None


class MoveDocumentRequest(BaseModel):
    """知识库拖拽排序请求体。"""

    project_id: int
    doc_id: int
    anchor_doc_id: int
    position: str


class RetrieveContextRequest(BaseModel):
    """检索治理调试请求体。"""

    project_id: int
    query: str
    limit: int = 5
    max_tokens: int = 1800
    debug: bool = False


def _to_iso(dt: Any) -> Optional[str]:
    """统一把时间字段转为可序列化的 ISO 字符串。"""
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _serialize_linked_doc(doc: KnowledgeDocument) -> dict:
    """序列化关联测试用例。"""
    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "content_preview": (doc.content or "")[:180],
    }


def _serialize_doc(
    doc: KnowledgeDocument,
    source_name_map: dict[int, str],
    linked_map: dict[int, list[dict]],
) -> dict:
    """序列化知识库文档。"""
    content = doc.content or ""
    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "created_at": _to_iso(doc.created_at),
        "file_size": len(content.encode("utf-8")),
        "source_doc_id": doc.source_doc_id,
        "source_doc_name": source_name_map.get(doc.source_doc_id),
        "linked_test_cases": linked_map.get(doc.id, []),
        "content_preview": content[:180],
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": doc.task_id,
        "retry_count": doc.retry_count,
    }


def _get_owned_project(project_id: int, user_id: int, db: Session) -> Optional[Project]:
    """校验项目归属。"""
    return db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()


def _get_owned_doc_by_id_or_project_specific_id(
    doc_id: int,
    user_id: int,
    db: Session,
) -> Optional[KnowledgeDocument]:
    """先按全局ID查询，查不到再按 project_specific_id 兜底。"""
    doc = (
        db.query(KnowledgeDocument)
        .join(Project, Project.id == KnowledgeDocument.project_id)
        .filter(KnowledgeDocument.id == doc_id, Project.user_id == user_id)
        .first()
    )
    if doc:
        return doc

    return (
        db.query(KnowledgeDocument)
        .join(Project, Project.id == KnowledgeDocument.project_id)
        .filter(KnowledgeDocument.project_specific_id == doc_id, Project.user_id == user_id)
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
        .first()
    )


def extract_error_text(err: Any) -> str:
    """从不同错误结构中提取可读文本。"""
    if err is None:
        return ""
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        for key in ["message", "detail", "error", "msg", "code"]:
            val = err.get(key)
            if val:
                return str(val)
        try:
            return json.dumps(err, ensure_ascii=False)
        except Exception:
            return str(err)
    return str(err)


def translate_error_text(text: str) -> str:
    """将常见英文错误关键词映射成中文。"""
    if not text:
        return "发生未知错误"
    if re.search(r"[\u4e00-\u9fff]", text):
        return text

    lower = text.lower()
    mapping = [
        ("timeout", "请求超时"),
        ("timed out", "请求超时"),
        ("failed to fetch", "网络请求失败"),
        ("networkerror", "网络请求失败"),
        ("ssl", "网络连接异常，请检查网络环境后重试"),
        ("unexpected_eof_while_reading", "网络连接被中断，请稍后重试"),
        ("econnrefused", "连接被拒绝"),
        ("connection refused", "连接被拒绝"),
        ("unauthorized", "未授权或登录已过期"),
        ("forbidden", "权限不足"),
        ("not found", "资源不存在"),
        ("bad request", "请求参数错误"),
        ("invalidparameter", "参数错误"),
        ("quotaexhausted", "额度已耗尽"),
        ("arrearage", "余额不足"),
        ("paymentrequired", "需要付费或余额不足"),
        ("rate limit", "请求过于频繁"),
        ("json", "响应解析失败"),
        ("parse", "响应解析失败"),
        ("500", "服务端异常"),
        ("502", "网关错误"),
        ("503", "服务暂不可用"),
        ("504", "网关超时"),
    ]
    for key, msg in mapping:
        if key in lower:
            return msg
    return "发生错误，请稍后重试"
