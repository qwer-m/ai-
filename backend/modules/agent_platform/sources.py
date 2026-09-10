"""需求来源的不可变快照与历史读取边界，不通过当前文档反推旧运行指纹。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
from typing import TYPE_CHECKING, Any, NamedTuple

from .results import persisted_test_generation_result

if TYPE_CHECKING:
    from core.db.model_defs import AgentRun
    from .run_repository import AgentRunRepository


SOURCE_ARTIFACT_KEY = "requirement_source"
_SOURCE_PATHS = (
    ("run_context", "artifacts", SOURCE_ARTIFACT_KEY),
    ("run_context", "artifacts", "requirement_evidence", "source"),
    ("run_context", "artifacts", "test_generation", "evidence", "source"),
    ("output_payload", "artifacts", "test_generation", "evidence", "source"),
)


class RunSourceRecord(NamedTuple):
    """运行来源的轻量投影，不携带生成产物。"""

    run_id: int
    finished_at: datetime | None
    source_key: str | None
    status: str


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
    candidates = []
    for field, *path in _SOURCE_PATHS:
        value = getattr(run, field)
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        candidates.append(value)
    return historical_source_snapshot(
        dict(run.input_payload or {}), *candidates,
    )


def source_snapshot_columns(run_model: Any) -> list[Any]:
    """SQL 投影与内存读取共用来源路径，避免加载完整运行 JSON。"""
    columns = []
    for field, *path in _SOURCE_PATHS:
        column = getattr(run_model, field)
        for key in path:
            column = column[key]
        columns.append(column)
    return columns


def historical_run_source_key(input_payload: dict[str, Any], *candidates: Any) -> str | None:
    snapshot = historical_source_snapshot(input_payload, *candidates)
    if snapshot is not None:
        return snapshot.key
    if input_payload.get("requirement_doc_id") is None and not str(input_payload.get("requirement") or "").strip():
        return "workflow"
    return None


def latest_successful_run_for_source(
    repo: AgentRunRepository,
    *,
    project_id: int,
    user_id: int,
    workflow_key: str,
    requirement_doc_id: int,
) -> AgentRun | None:
    """按真实来源查找可复用结果，成功状态还必须对应已持久化的用例产物。"""
    from .definition_repository import AgentDefinitionRepository

    source = repo.resolve_source_snapshot(
        project_id=project_id, input_payload={"requirement_doc_id": requirement_doc_id},
    )
    if source is None:
        return None
    workflow_ids = AgentDefinitionRepository(repo.db).list_workflow_definition_ids(
        project_id=project_id, workflow_key=workflow_key,
    )
    if not workflow_ids:
        return None
    candidates = repo.list_run_sources(
        project_id=project_id, user_id=user_id,
        workflow_definition_ids=workflow_ids, statuses={"success"},
    )
    matching_ids = sorted(
        (row.run_id for row in candidates if row.source_key == source.key), reverse=True,
    )
    for run_id in matching_ids:
        run = repo.get_run(run_id=run_id)
        if run is None:
            continue
        artifact = persisted_test_generation_result(run)
        if isinstance(artifact, dict) and isinstance(artifact.get("test_cases"), list):
            return run
    return None


def assert_same_source(expected: SourceSnapshot, actual: SourceSnapshot) -> None:
    if expected.key != actual.key or expected.document_id != actual.document_id:
        raise ValueError("需求来源在创建运行后已变化，请基于当前文档重新生成，不能复用旧检查点")
