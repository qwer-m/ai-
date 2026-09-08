"""需求来源的不可变快照与历史读取边界，不通过当前文档反推旧运行指纹。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any


SOURCE_ARTIFACT_KEY = "requirement_source"


@dataclass(frozen=True)
class SourceSnapshot:
    kind: str
    content_hash: str
    document_id: int | None = None
    filename: str = ""
    doc_type: str = "inline_requirement"

    def __post_init__(self) -> None:
        if self.kind not in {"knowledge_document", "inline"}:
            raise ValueError("需求来源类型无效")
        if len(self.content_hash) != 64 or any(char not in "0123456789abcdef" for char in self.content_hash):
            raise ValueError("需求来源缺少有效的 SHA256 指纹")
        if self.kind == "knowledge_document" and (type(self.document_id) is not int or self.document_id < 1):
            raise ValueError("需求来源缺少有效文档编号")
        if self.kind == "inline" and self.document_id is not None:
            raise ValueError("直接输入的需求不能绑定文档编号")

    @property
    def key(self) -> str:
        prefix = "document" if self.kind == "knowledge_document" else "requirement"
        return f"{prefix}-sha256:{self.content_hash}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceSnapshot:
        return cls(
            kind=str(value.get("kind") or ""),
            content_hash=str(value.get("content_hash") or "").strip().lower(),
            document_id=value.get("document_id"),
            filename=str(value.get("filename") or ""),
            doc_type=str(value.get("doc_type") or ""),
        )

    @classmethod
    def from_document(cls, document: Any) -> SourceSnapshot:
        if document.doc_type not in {"requirement", "product_requirement", "incomplete"}:
            raise ValueError("文档类型不允许作为需求来源")
        if document.parse_status != "success":
            raise ValueError("需求文档尚未解析成功")
        return cls(
            kind="knowledge_document", document_id=int(document.id),
            content_hash=str(document.content_hash or "").strip().lower(),
            filename=str(document.filename or ""), doc_type=str(document.doc_type),
        )

    @classmethod
    def from_text(cls, requirement: str) -> SourceSnapshot:
        content = requirement.strip()
        if not content:
            raise ValueError("需求正文不能为空")
        return cls(kind="inline", content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest())


def historical_source_snapshot(input_payload: dict[str, Any], *candidates: Any) -> SourceSnapshot | None:
    """历史格式仅在此读取；缺少来源证据的文档运行不能参与复用或覆盖。"""
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return SourceSnapshot.from_dict(candidate)
    if input_payload.get("requirement_doc_id") is None:
        requirement = str(input_payload.get("requirement") or "").strip()
        if requirement:
            return SourceSnapshot.from_text(requirement)
    return None


def persisted_source_snapshot(run: Any) -> SourceSnapshot | None:
    artifacts = dict((run.run_context or {}).get("artifacts") or {})
    output_artifacts = dict((run.output_payload or {}).get("artifacts") or {})
    return historical_source_snapshot(
        dict(run.input_payload or {}),
        artifacts.get(SOURCE_ARTIFACT_KEY),
        dict(artifacts.get("requirement_evidence") or {}).get("source"),
        dict(dict(artifacts.get("test_generation") or {}).get("evidence") or {}).get("source"),
        dict(dict(output_artifacts.get("test_generation") or {}).get("evidence") or {}).get("source"),
    )


def assert_same_source(expected: SourceSnapshot, actual: SourceSnapshot) -> None:
    if expected.key != actual.key or expected.document_id != actual.document_id:
        raise ValueError("需求来源在创建运行后已变化，请基于当前文档重新生成，不能复用旧检查点")
