from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.processing.file_processing import parse_file_content_with_meta
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


@dataclass
class RequirementContentBlock:
    role: str
    filename: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        ocr = self.meta.get("ocr") if isinstance(self.meta.get("ocr"), dict) else {}
        return {
            "role": self.role,
            "filename": self.filename,
            "parse_strategy": self.meta.get("parse_strategy", ""),
            "is_image": bool(self.meta.get("is_image")),
            "size": int(self.meta.get("size") or 0),
            "text_length": len(self.text or ""),
            "ocr_source": ocr.get("ocr_source", ""),
            "cloud_fallback": bool(ocr.get("cloud_fallback", False)),
            "ocr_error": ocr.get("error", "") or ocr.get("local_ocr_error", ""),
        }


@dataclass
class RequirementParseArtifact:
    blocks: list[RequirementContentBlock]
    alignments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def content(self) -> str:
        return self.to_prompt_text()

    def to_prompt_text(self) -> str:
        main_blocks = [block for block in self.blocks if block.role == "main_requirement"]
        other_blocks = [block for block in self.blocks if block.role != "main_requirement"]
        parts: list[str] = []
        parts.extend(block.text for block in main_blocks if (block.text or "").strip())
        for block in other_blocks:
            label = "Prototype Analysis" if block.role == "prototype" else f"Attachment: {block.filename}"
            if (block.text or "").strip():
                parts.append(f"[{label}]\n{block.text}")

        evidence_lines = self._evidence_lines()
        if evidence_lines:
            parts.append("[Parsed Requirement Evidence]\n" + "\n".join(evidence_lines))
        if self.alignments:
            alignment_lines = []
            for item in self.alignments[:8]:
                alignment_lines.append(
                    "- {role}:{filename} -> requirement score={score:.2f}; requirement=\"{requirement}\"; evidence=\"{evidence}\"".format(
                        role=item.get("role", ""),
                        filename=item.get("filename", ""),
                        score=float(item.get("score") or 0.0),
                        requirement=_compact_one_line(item.get("requirement", ""), 120),
                        evidence=_compact_one_line(item.get("evidence", ""), 120),
                    )
                )
            parts.append("[Multimodal Evidence Alignment]\n" + "\n".join(alignment_lines))
        return "\n\n".join(part for part in parts if str(part or "").strip())

    def to_meta(self) -> dict[str, Any]:
        return {
            "blocks": [block.to_meta() for block in self.blocks],
            "alignment_count": len(self.alignments),
            "alignments": self.alignments[:8],
        }

    def _evidence_lines(self) -> list[str]:
        lines: list[str] = []
        for block in self.blocks:
            meta = block.to_meta()
            suffix = ""
            if meta["is_image"]:
                suffix = (
                    f", ocr_source={meta['ocr_source'] or 'unknown'}, "
                    f"cloud_fallback={str(meta['cloud_fallback']).lower()}"
                )
                if meta["ocr_error"]:
                    suffix += f", ocr_error={_compact_one_line(meta['ocr_error'], 80)}"
            lines.append(
                f"- {meta['role']}: filename={meta['filename']}, strategy={meta['parse_strategy']}, "
                f"chars={meta['text_length']}{suffix}"
            )
        return lines


def _compact_one_line(value: Any, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _split_requirement_sections(text: str, limit: int = 80) -> list[str]:
    chunks: list[str] = []
    for raw in re.split(r"[\r\n\x01]+", str(text or "")):
        value = raw.strip()
        if len(value) < 8:
            continue
        if len(value) <= 260:
            chunks.append(value)
            continue
        for index in range(0, len(value), 220):
            part = value[index : index + 260].strip()
            if len(part) >= 8:
                chunks.append(part)
        if len(chunks) >= limit:
            break
    return chunks[:limit]


def _evidence_tokens(text: str) -> set[str]:
    lowered = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    compact = re.sub(r"\s+", "", lowered)
    cjk_chars = [ch for ch in compact if chr(0x4E00) <= ch <= chr(0x9FFF)]
    for index in range(0, max(0, len(cjk_chars) - 1)):
        tokens.add("".join(cjk_chars[index : index + 2]))
    return {item for item in tokens if len(item) >= 2}


def _align_blocks_to_requirement(blocks: list[RequirementContentBlock]) -> list[dict[str, Any]]:
    main_text = "\n".join(block.text for block in blocks if block.role == "main_requirement")
    sections = _split_requirement_sections(main_text)
    section_tokens = [(section, _evidence_tokens(section)) for section in sections]
    if not section_tokens:
        return []

    alignments: list[dict[str, Any]] = []
    for block in blocks:
        if block.role == "main_requirement":
            continue
        for evidence in _split_requirement_sections(block.text, limit=24):
            evidence_token_set = _evidence_tokens(evidence)
            if not evidence_token_set:
                continue
            best_section = ""
            best_score = 0.0
            for section, tokens in section_tokens:
                if not tokens:
                    continue
                overlap = len(tokens & evidence_token_set)
                score = overlap / max(1, min(len(tokens), len(evidence_token_set)))
                if score > best_score:
                    best_score = score
                    best_section = section
            if best_score >= 0.08 and best_section:
                alignments.append(
                    {
                        "role": block.role,
                        "filename": block.filename,
                        "score": round(float(best_score), 4),
                        "requirement": _compact_one_line(best_section, 180),
                        "evidence": _compact_one_line(evidence, 180),
                    }
                )
    alignments.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return alignments[:12]


async def parse_requirement_artifact(
    file: UploadFile,
    doc_type: str,
    prototype_file: UploadFile | None = None,
    *,
    db: Session | None = None,
    user_id: int | None = None,
) -> RequirementParseArtifact:
    base_prompt = "OCR: Extract all text from this image."
    proto_prompt = (
        "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
        "Identify input fields, buttons, navigation menus, and any visual indicators of state."
    )
    content, meta = await parse_file_content_with_meta(file, base_prompt, db=db, user_id=user_id)
    blocks = [
        RequirementContentBlock(
            role="main_requirement",
            filename=file.filename or "uploaded_file",
            text=content,
            meta=meta,
        )
    ]
    if doc_type == "incomplete" and prototype_file is not None:
        proto_text, proto_meta = await parse_file_content_with_meta(
            prototype_file,
            proto_prompt,
            db=db,
            user_id=user_id,
        )
        blocks.append(
            RequirementContentBlock(
                role="prototype",
                filename=prototype_file.filename or "prototype_file",
                text=proto_text,
                meta=proto_meta,
            )
        )
    return RequirementParseArtifact(blocks=blocks, alignments=_align_blocks_to_requirement(blocks))


async def parse_requirement_for_generation(
    file: UploadFile,
    doc_type: str,
    prototype_file: UploadFile | None = None,
    *,
    db: Session | None = None,
    user_id: int | None = None,
    project_id: int | None = None,
    source: str = "",
) -> tuple[str, dict[str, Any]]:
    artifact = await parse_requirement_artifact(
        file,
        doc_type,
        prototype_file,
        db=db,
        user_id=user_id,
    )
    diag = {
        "kind": "requirement_parse",
        "source": str(source or ""),
        "doc_type": str(doc_type or ""),
        "project_id": int(project_id or 0),
        **artifact.to_meta(),
    }
    return artifact.content, diag


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

