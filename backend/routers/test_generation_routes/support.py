from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.processing.file_processing import parse_file_content
from core.db.models import Project
from modules.domain.knowledge_base import knowledge_base
from modules.test_generation_components.repositories.history_repository import (
    TestGenerationHistoryRepository,
)


def get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    """
    校验项目归属。

    测试生成接口会写入日志和生成记录，必须先确保项目属于当前用户，
    避免跨项目误操作。
    """
    project = TestGenerationHistoryRepository(db).get_owned_project(project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_previous_generation_json(
    db: Session,
    project_id: int,
    user_id: int,
    requirement_text: str,
) -> Any:
    """
    查找同项目同用户最近一次生成结果。

    与历史逻辑保持一致：
    - 先按全文精确匹配。
    - 超长文本再尝试前缀匹配，兼容早期截断保存场景。
    """
    prev = (
        TestGenerationHistoryRepository(db).get_latest_generation_exact(
            project_id=project_id,
            user_id=user_id,
            requirement_text=requirement_text,
        )
    )

    if not prev and len(requirement_text) > 60000:
        prefix = requirement_text[:60000]
        prev = (
            TestGenerationHistoryRepository(db).get_latest_generation_by_prefix(
                project_id=project_id,
                user_id=user_id,
                prefix=prefix,
            )
        )

    prev_json = None
    if prev and prev.generated_result:
        try:
            prev_json = json.loads(prev.generated_result)
        except Exception:
            prev_json = {"raw": prev.generated_result}
    return prev_json


async def parse_requirement_content(
    file: UploadFile,
    doc_type: str,
    prototype_file: UploadFile | None = None,
) -> str:
    """
    解析上传文档内容，并在 incomplete 模式拼接原型分析文本。

    该函数只负责输入组装，不负责知识库入库和业务判断。
    """
    base_prompt = "OCR: Extract all text from this image."
    proto_prompt = (
        "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
        "Identify input fields, buttons, navigation menus, and any visual indicators of state."
    )
    content = await parse_file_content(file, base_prompt)
    if doc_type == "incomplete" and prototype_file is not None:
        proto_text = await parse_file_content(prototype_file, proto_prompt)
        content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"
    return content


def detect_duplicate_document(
    db: Session,
    *,
    filename: str,
    content: str,
    doc_type: str,
    project_id: int,
    force: bool,
    user_id: int,
) -> dict[str, Any] | None:
    """
    执行知识库重复检测并回填历史生成结果。

    返回值保持与原接口一致：
    - 非重复或检测失败：返回 None
    - 命中重复且未 force：返回 duplicate payload
    """
    try:
        kb_add = knowledge_base.add_document(
            filename,
            content,
            doc_type,
            project_id,
            db,
            force=force,
            user_id=user_id,
        )
        if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate" and not force:
            return {
                "duplicate": True,
                "filename": kb_add.get("existing_filename"),
                "previous_json": get_previous_generation_json(db, project_id, user_id, content),
            }
    except Exception:
        # 维持原语义：重复检测失败不阻断生成流程
        return None
    return None


def build_generation_qm(result: Any) -> dict[str, Any]:
    """
    计算生成质量指标（GEN_QM）。

    该指标用于前端概览和历史诊断，不参与生成主流程判定，
    因此保持容错策略，尽可能给出统计结果。
    """
    positive = 0
    negative = 0
    edge = 0
    pending = 0
    steps_count = 0
    steps_items = 0
    kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时"]
    kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符"]

    if isinstance(result, list):
        for item in result:
            desc = (item.get("description") or "") + " " + (item.get("expected_result") or "")
            is_neg = any(k in desc for k in kw_neg)
            is_edge = any(k in desc for k in kw_edge)
            if is_neg:
                negative += 1
            elif is_edge:
                edge += 1
            else:
                positive += 1

            steps = item.get("steps")
            if isinstance(steps, list):
                steps_count += len(steps)
                steps_items += 1
            elif isinstance(steps, str):
                lines = [s for s in steps.splitlines() if s.strip()]
                steps_count += len(lines)
                steps_items += 1

            if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                pending += 1

    avg_steps = steps_count / steps_items if steps_items else 0.0
    return {
        "positive": positive,
        "negative": negative,
        "edge": edge,
        "avg_steps": avg_steps,
        "pending": pending,
        "generated_count": len(result) if isinstance(result, list) else 0,
    }

