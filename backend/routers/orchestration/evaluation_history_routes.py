from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import Evaluation, KnowledgeDocument, TestGenerationComparison, User
from core.processing.file_processing import is_image_filename, parse_file_bytes, parse_image_bytes_with_fallback
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from routers.orchestration.evaluation_shared import (
    build_source_key,
    get_owned_project,
    is_attachment_ocr_ok,
    normalize_source_title,
    source_filename,
)

router = APIRouter()


def _normalize_metric_value(value: Any) -> Optional[float]:
    """将指标值标准化到 0~1 区间，无法解析时返回 None。"""
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            try:
                return max(0.0, min(1.0, float(text[:-1]) / 100.0))
            except ValueError:
                return None
        try:
            value = float(text)
        except ValueError:
            return None

    if not isinstance(value, (int, float)):
        return None

    number = float(value)
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return round(number, 6)


def _extract_first_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    """从文本中提取首个 JSON 对象，兼容 ```json 代码块。"""
    if not raw_text:
        return None

    text = raw_text.strip()
    block = re.search(r"```json\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if not block:
        block = re.search(r"```\s*([\s\S]*?)\s*```", text)
    if block and block.group(1):
        text = block.group(1).strip()

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[i:])
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _pick_metric_value(candidates: dict[str, Any], keys: list[str]) -> Optional[float]:
    for key in keys:
        if key in candidates:
            value = _normalize_metric_value(candidates.get(key))
            if value is not None:
                return value
    return None


def _extract_metrics_from_plain_text(raw_text: str) -> dict[str, Optional[float]]:
    """JSON 不可用时，尝试从纯文本中兜底提取关键指标。"""
    text = raw_text or ""
    patterns = {
        "precision": r"(?:precision|精准率|精确率)\s*[:：=]\s*([0-9]+(?:\.[0-9]+)?%?)",
        "recall": r"(?:recall|召回率)\s*[:：=]\s*([0-9]+(?:\.[0-9]+)?%?)",
        "f1_score": r"(?:f1(?:[_\s-]*score)?|f1分数|f1 分数)\s*[:：=]\s*([0-9]+(?:\.[0-9]+)?%?)",
        "semantic_similarity": r"(?:semantic[_\s-]*similarity|相似度|语义相似度)\s*[:：=]\s*([0-9]+(?:\.[0-9]+)?%?)",
    }
    result: dict[str, Optional[float]] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        result[key] = _normalize_metric_value(match.group(1)) if match else None
    return result


def _extract_quality_metrics(raw_text: str) -> dict[str, Optional[float]]:
    """
    兼容历史数据格式，尽可能抽取评估趋势图需要的四个指标。
    返回值统一包含四个 key，缺失时为 None。
    """
    fallback = _extract_metrics_from_plain_text(raw_text)
    payload = _extract_first_json_object(raw_text)
    if not payload:
        return fallback

    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    if not isinstance(metrics, dict):
        return fallback

    parsed = {
        "precision": _pick_metric_value(metrics, ["precision", "Precision"]),
        "recall": _pick_metric_value(metrics, ["recall", "Recall"]),
        "f1_score": _pick_metric_value(metrics, ["f1_score", "f1", "F1", "f1Score"]),
        "semantic_similarity": _pick_metric_value(
            metrics,
            ["semantic_similarity", "similarity", "semanticSimilarity", "语义相似度"],
        ),
    }

    # 单个字段未取到时，回退到文本正则兜底，避免旧数据图表断裂。
    for key, value in fallback.items():
        if parsed.get(key) is None and value is not None:
            parsed[key] = value
    return parsed


@router.get("/evaluation/history/{project_id}")
def get_evaluation_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    eval_items = (
        db.query(Evaluation)
        .filter(Evaluation.project_id == project_id, Evaluation.user_id == current_user.id)
        .order_by(desc(Evaluation.created_at), desc(Evaluation.id))
        .limit(30)
        .all()
    )
    compare_items = (
        db.query(TestGenerationComparison)
        .filter(
            TestGenerationComparison.project_id == project_id,
            TestGenerationComparison.user_id == current_user.id,
        )
        .order_by(desc(TestGenerationComparison.created_at), desc(TestGenerationComparison.id))
        .limit(30)
        .all()
    )

    history: list[dict[str, Any]] = []

    for item in eval_items:
        raw_result = item.evaluation_result or ""
        metrics = _extract_quality_metrics(raw_result)
        history.append(
            {
                "id": f"eval-{item.id}",
                "type": "evaluation",
                "created_at": item.created_at,
                "preview": raw_result[:200],
                "precision": metrics.get("precision"),
                "recall": metrics.get("recall"),
                "f1_score": metrics.get("f1_score"),
                "semantic_similarity": metrics.get("semantic_similarity"),
            }
        )

    for item in compare_items:
        raw_result = item.comparison_result or ""
        metrics = _extract_quality_metrics(raw_result)
        history.append(
            {
                "id": f"compare-{item.id}",
                "type": "comparison",
                "created_at": item.created_at,
                "preview": raw_result[:200],
                "precision": metrics.get("precision"),
                "recall": metrics.get("recall"),
                "f1_score": metrics.get("f1_score"),
                "semantic_similarity": metrics.get("semantic_similarity"),
            }
        )
    history.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)
    return {"history": history[:50]}


@router.get("/evaluation/latest-supplement/{project_id}")
def get_latest_supplement(
    project_id: int,
    source_key: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    query = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.project_id == project_id,
        KnowledgeDocument.user_id == current_user.id,
        KnowledgeDocument.doc_type == "evaluation_report",
    )
    normalized_key = build_source_key(source_key or "") if source_key else None
    if normalized_key:
        query = query.filter(KnowledgeDocument.filename.like(f"evaluation_report_{normalized_key}_%"))

    doc = query.order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id)).first()
    if not doc:
        return {"found": False}
    return {
        "found": True,
        "doc_id": doc.id,
        "supplement": doc.content or "",
        "source_key": normalized_key,
    }


@router.post("/evaluation/save-knowledge")
async def save_evaluation_knowledge(
    project_id: int = Form(...),
    defect_analysis: str = Form(""),
    user_supplement: str = Form(""),
    doc_id: Optional[int] = Form(None),
    source_key: str = Form(""),
    source_title: str = Form(""),
    generation_id: Optional[int] = Form(None),
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)
    client = get_client_for_user(current_user.id, db)
    ocr_model = client.vl_model if client else ""
    normalized_source_title = normalize_source_title(source_title)
    normalized_source_key = build_source_key(source_key or normalized_source_title)

    attachments: list[str] = []
    attachment_details: list[dict[str, Any]] = []
    for upload in files or []:
        upload_bytes = await upload.read()
        if is_image_filename(upload.filename or ""):
            parsed, ocr_meta = parse_image_bytes_with_fallback(
                filename=upload.filename or "",
                content_bytes=upload_bytes,
                db=db,
                user_id=current_user.id,
            )
        else:
            parsed = parse_file_bytes(
                filename=upload.filename or "",
                content_bytes=upload_bytes,
                db=db,
                user_id=current_user.id,
            )
            ocr_meta = {"ocr_source": "not_image", "cloud_fallback": False, "error": ""}
        attachments.append(f"## Attachment: {upload.filename}\n{parsed}")
        attachment_details.append(
            {
                "filename": upload.filename or "",
                "extracted_text": parsed or "",
                "extracted_length": len(parsed or ""),
                "ocr_model": ocr_model,
                "ocr_ok": is_attachment_ocr_ok(parsed or ""),
                "ocr_source": ocr_meta.get("ocr_source", "unknown"),
                "local_ocr_error": ocr_meta.get("local_ocr_error", ""),
                "cloud_fallback": bool(ocr_meta.get("cloud_fallback", False)),
                "ocr_error": ocr_meta.get("error", ""),
            }
        )

    sections = [
        "# Evaluation Knowledge",
        "## Source",
        f"- source_key: {normalized_source_key}",
        f"- source_title: {normalized_source_title}",
        f"- generation_id: {generation_id if generation_id is not None else '(none)'}",
        "## Defect Analysis",
        defect_analysis or "(empty)",
        "## User Supplement",
        user_supplement or "(empty)",
    ]
    if attachments:
        sections.append("## Attachments")
        sections.extend(attachments)
    content = "\n\n".join(sections)
    filename = source_filename(normalized_source_key)

    replaced_previous = False
    previous_doc_id: Optional[int] = None
    if doc_id:
        doc = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.user_id == current_user.id,
        ).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Knowledge document not found")
        replaced_previous = True
        previous_doc_id = doc.id
        doc.filename = filename
        doc.content = content
        doc.doc_type = "evaluation_report"
        db.commit()
        db.refresh(doc)
    else:
        existing = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.user_id == current_user.id,
                KnowledgeDocument.doc_type == "evaluation_report",
                KnowledgeDocument.filename.like(f"evaluation_report_{normalized_source_key}_%"),
            )
            .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
            .first()
        )
        if existing:
            replaced_previous = True
            previous_doc_id = existing.id
            existing.filename = filename
            existing.content = content
            existing.doc_type = "evaluation_report"
            db.commit()
            db.refresh(existing)
            doc = existing
        else:
            from modules.domain.knowledge_base import knowledge_base

            created = knowledge_base.add_document(
                filename,
                content,
                "evaluation_report",
                project_id,
                db,
                force=True,
                user_id=current_user.id,
            )
            if isinstance(created, dict):
                raise HTTPException(status_code=409, detail=created)
            doc = created

    log_workflow_trace(
        db,
        project_id,
        current_user.id,
        WorkflowKind.EVALUATION,
        WorkflowStage.LEARN,
        {
            "action": "save_evaluation_knowledge",
            "doc_id": doc.id,
            "attachments": len(attachments),
            "content_length": len(content),
        },
    )
    ocr_ok_count = sum(1 for item in attachment_details if item.get("ocr_ok"))
    ocr_failed_files = [item.get("filename") or "" for item in attachment_details if not item.get("ocr_ok")]
    ocr_all_ok = bool(attachment_details) and ocr_ok_count == len(attachment_details)
    persisted_attachment_count = sum(
        1 for item in attachment_details if (item.get("extracted_text") or "") in (doc.content or "")
    )

    return {
        "success": True,
        "result": {
            "id": doc.id,
            "filename": doc.filename,
            "source_key": normalized_source_key,
            "source_title": normalized_source_title,
            "generation_id": generation_id,
            "replaced_previous": replaced_previous,
            "previous_doc_id": previous_doc_id,
            "ocr_model": ocr_model,
            "ocr_summary": {
                "total": len(attachment_details),
                "ok": ocr_ok_count,
                "all_ok": ocr_all_ok,
                "failed_files": ocr_failed_files,
            },
            "persist_summary": {
                "content_length": len(doc.content or ""),
                "attachments_embedded": persisted_attachment_count,
                "attachments_expected": len(attachment_details),
                "verified": persisted_attachment_count == len(attachment_details),
            },
            "attachment_details": attachment_details,
            "saved_content_preview": (content or "")[:1000],
        },
    }
