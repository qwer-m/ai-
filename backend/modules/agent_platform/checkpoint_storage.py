"""内容寻址分块产物与带游标的增量日志，沿用原 Run/Node 事务所有权。"""

from __future__ import annotations

from copy import deepcopy
from collections import Counter
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from core.db import db_utc_now
from core.db.model_defs import (
    AgentArtifactBlob, AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord,
    AgentNodeRun, AgentRun,
)


CHECKPOINT_VERSION = 3
CHECKPOINT_STORAGE = "artifact-journal-v1"
INPUT_MARKER = "__agent_artifact_input__"
_ENCODING = "json-tree-v1"
_CHUNK_BYTES = 1024
_CHUNK_ITEMS = 16


class CheckpointStorageError(ValueError):
    """引用、作用域或不可变内容校验失败，不允许回退到其他状态来源。"""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _scope(db: Session, node: AgentNodeRun) -> tuple[int, int, str, str | None]:
    row = db.execute(select(
        AgentRun.user_id, AgentRun.project_id,
        AgentRun.run_context["logical_task_id"].as_string(),
        AgentRun.run_context["artifacts"]["requirement_source"]["content_hash"].as_string(),
    ).where(AgentRun.id == node.run_id)).first()
    if row is None or not isinstance(row[2], str) or not row[2]:
        raise CheckpointStorageError("产物缺少真实运行或冻结的逻辑任务身份")
    return int(row[0]), int(row[1]), row[2], row[3]


class ArtifactStore:
    """大 JSON 子树批量去重；每个小标量不建行，读取始终验证内容与租户。"""

    def __init__(self, db: Session, *, user_id: int, project_id: int) -> None:
        self.db, self.user_id, self.project_id = db, int(user_id), int(project_id)
        self.new_rows = self.new_bytes = 0

    def _id(self, digest: str) -> str:
        return hashlib.sha256(f"{_ENCODING}:{self.user_id}:{self.project_id}:{digest}".encode()).hexdigest()

    def _ref(self, digest: str) -> dict[str, str]:
        return {"id": self._id(digest), "sha256": digest}

    def put(self, value: Any) -> dict[str, str]:
        planned: dict[str, dict[str, Any]] = {}

        def plan(item: Any) -> dict[str, str]:
            encoded = canonical_bytes(item)
            digest = hashlib.sha256(encoded).hexdigest()
            ref = self._ref(digest)
            if ref["id"] in planned:
                return ref
            def child(part: Any) -> dict[str, Any]:
                return {"ref": plan(part)} if len(canonical_bytes(part)) >= _CHUNK_BYTES else {"value": deepcopy(part)}
            if len(encoded) < _CHUNK_BYTES or not isinstance(item, (dict, list)):
                payload = {"type": "inline", "value": deepcopy(item)}
            elif len(item) > _CHUNK_ITEMS:
                if isinstance(item, dict):
                    keys = sorted(item)
                    parts = [{key: item[key] for key in keys[offset:offset + _CHUNK_ITEMS]}
                             for offset in range(0, len(keys), _CHUNK_ITEMS)]
                    kind = "object_chunks"
                else:
                    parts = [item[offset:offset + _CHUNK_ITEMS] for offset in range(0, len(item), _CHUNK_ITEMS)]
                    kind = "array_chunks"
                payload = {"type": kind, "entries": [{"ref": plan(part)} for part in parts]}
            elif isinstance(item, dict):
                payload = {"type": "object", "entries": [[key, child(item[key])] for key in sorted(item)]}
            else:
                payload = {"type": "array", "entries": [child(part) for part in item]}
            planned[ref["id"]] = {
                "id": ref["id"], "user_id": self.user_id, "project_id": self.project_id,
                "sha256": digest, "encoding": _ENCODING, "payload": payload,
                "payload_hash": content_hash(payload), "logical_bytes": len(encoded),
                "stored_bytes": len(canonical_bytes(payload)), "created_at": db_utc_now(),
            }
            return ref

        root = plan(value)
        ids = list(planned)
        existing: dict[str, tuple] = {}
        for offset in range(0, len(ids), 500):
            rows = self.db.execute(select(
                AgentArtifactBlob.id, AgentArtifactBlob.user_id, AgentArtifactBlob.project_id,
                AgentArtifactBlob.sha256, AgentArtifactBlob.encoding, AgentArtifactBlob.payload_hash,
            ).where(AgentArtifactBlob.id.in_(ids[offset:offset + 500]))).all()
            existing.update({row.id: row for row in rows})
        missing = [planned[key] for key in ids if key not in existing]
        dialect = self.db.get_bind().dialect.name
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert
            factory = lambda rows: insert(AgentArtifactBlob).values(rows).on_duplicate_key_update(id=AgentArtifactBlob.id)
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
            factory = lambda rows: insert(AgentArtifactBlob).values(rows).on_conflict_do_nothing(index_elements=["id"])
        else:
            raise CheckpointStorageError("产物存储尚未支持当前数据库的原子去重语义")
        for offset in range(0, len(missing), 250):
            self.db.execute(factory(missing[offset:offset + 250]))
        for offset in range(0, len(ids), 500):
            for row in self.db.execute(select(
                AgentArtifactBlob.id, AgentArtifactBlob.user_id, AgentArtifactBlob.project_id,
                AgentArtifactBlob.sha256, AgentArtifactBlob.encoding, AgentArtifactBlob.payload_hash,
            ).where(AgentArtifactBlob.id.in_(ids[offset:offset + 500]))):
                expected = planned[row.id]
                if (row.user_id != self.user_id or row.project_id != self.project_id
                        or row.sha256 != expected["sha256"] or row.encoding != _ENCODING
                        or row.payload_hash != expected["payload_hash"]):
                    raise CheckpointStorageError("已存在产物的租户、内容或编码与不可变身份不一致")
        self.new_rows += len(missing)
        self.new_bytes += sum(row["stored_bytes"] for row in missing)
        return root

    def get(self, ref: dict[str, Any]) -> Any:
        def validate_ref(reference):
            if (not isinstance(reference, dict) or set(reference) != {"id", "sha256"}
                    or not isinstance(reference["sha256"], str) or len(reference["sha256"]) != 64
                    or reference["id"] != self._id(reference["sha256"])):
                raise CheckpointStorageError("产物引用无效或不属于当前租户")
        validate_ref(ref)
        rows: dict[str, dict[str, Any]] = {}
        pending = {ref["id"]: ref}
        while pending:
            current = pending
            pending = {}
            ids = list(current)
            found = set()
            for offset in range(0, len(ids), 500):
                for row in self.db.execute(select(AgentArtifactBlob).where(AgentArtifactBlob.id.in_(ids[offset:offset + 500])).execution_options(populate_existing=True)).scalars():
                    expected = current[row.id]
                    if (row.user_id != self.user_id or row.project_id != self.project_id
                            or row.sha256 != expected["sha256"] or row.encoding != _ENCODING
                            or row.payload_hash != content_hash(row.payload)):
                        raise CheckpointStorageError("产物正文、摘要或租户已被改变")
                    rows[row.id] = row.payload
                    found.add(row.id)
                    payload = row.payload
                    if not isinstance(payload, dict) or payload.get("type") not in {"inline", "object", "array", "object_chunks", "array_chunks"}:
                        raise CheckpointStorageError("产物块编码无效")
                    entries = payload.get("entries", [])
                    if payload["type"] == "object":
                        entries = [entry[1] for entry in entries]
                    for entry in entries:
                        if isinstance(entry, dict) and set(entry) == {"ref"}:
                            child = entry["ref"]
                            validate_ref(child)
                            if child["id"] not in rows and child["id"] not in current:
                                pending[child["id"]] = child
            if found != set(ids):
                raise CheckpointStorageError("产物引用缺失，禁止混入其他运行或猜测内容")
        decoded: dict[str, Any] = {}
        active = set()
        def expand(reference):
            identity = reference["id"]
            if identity in decoded:
                return decoded[identity]
            if identity in active:
                raise CheckpointStorageError("产物引用存在循环")
            active.add(identity)
            payload = rows[identity]
            def child(entry):
                if set(entry) == {"ref"}:
                    return expand(entry["ref"])
                if set(entry) == {"value"}:
                    return entry["value"]
                raise CheckpointStorageError("产物子块结构无效")
            if payload["type"] == "inline":
                value = payload["value"]
            elif payload["type"] == "object":
                entries = payload["entries"]
                if len({entry[0] for entry in entries}) != len(entries):
                    raise CheckpointStorageError("产物对象存在重复字段")
                value = {key: child(item) for key, item in entries}
            elif payload["type"] == "array":
                value = [child(item) for item in payload["entries"]]
            elif payload["type"] == "array_chunks":
                parts = [child(item) for item in payload["entries"]]
                if any(not isinstance(part, list) for part in parts):
                    raise CheckpointStorageError("数组分页产物不是数组")
                value = [item for part in parts for item in part]
            else:
                parts = [child(item) for item in payload["entries"]]
                if any(not isinstance(part, dict) for part in parts):
                    raise CheckpointStorageError("对象分页产物不是对象")
                value = {}
                for part in parts:
                    if value.keys() & part.keys():
                        raise CheckpointStorageError("对象分页存在重复字段")
                    value.update(part)
            if content_hash(value) != reference["sha256"]:
                raise CheckpointStorageError("产物展开后的完整内容摘要不一致")
            active.remove(identity)
            decoded[identity] = value
            return value

        def materialize(value):
            # 展开缓存只在本次 get 内共享；交付时每个出现位置各自拥有可变容器。
            # 不使用 deepcopy 的 memo，防止相同内容引用在不同路径上形成可变别名。
            if isinstance(value, dict):
                return {key: materialize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [materialize(item) for item in value]
            return value

        return materialize(expand(ref))


def store_for_node(db: Session, node: AgentNodeRun) -> ArtifactStore:
    user_id, project_id, _, _ = _scope(db, node)
    return ArtifactStore(db, user_id=user_id, project_id=project_id)


def store_node_input(db: Session, node: AgentNodeRun, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise CheckpointStorageError("完整节点输入必须是对象")
    node.input_payload = {INPUT_MARKER: 1, "ref": store_for_node(db, node).put(payload)}


def hydrate_node_input(db: Session, node: AgentNodeRun) -> dict[str, Any]:
    packed = node.input_payload
    if not isinstance(packed, dict) or set(packed) != {INPUT_MARKER, "ref"} or packed[INPUT_MARKER] != 1:
        raise CheckpointStorageError("完整节点输入缺少明确的产物引用")
    value = store_for_node(db, node).get(packed["ref"])
    if not isinstance(value, dict):
        raise CheckpointStorageError("完整节点输入产物不是对象")
    return value


def compact_checkpoint(node: AgentNodeRun) -> dict[str, Any] | None:
    value = (node.sdk_state or {}).get("action_checkpoint")
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("version") != CHECKPOINT_VERSION
            or value.get("storage") != CHECKPOINT_STORAGE or any(key in value for key in ("state", "receipts", "validated_agent_calls"))
            or not isinstance(value.get("budget"), dict) or not isinstance(value.get("projection"), dict)):
        raise CheckpointStorageError("检查点协议不受支持；当前运行仅接受 artifact-journal-v1 的 v3 检查点")
    if value.get("input_hash") != value.get("input_ref", {}).get("sha256") or value.get("state_hash") != value.get("state_ref", {}).get("sha256"):
        raise CheckpointStorageError("检查点内容引用与完整摘要不一致")
    packed = node.input_payload or {}
    if packed.get(INPUT_MARKER) != 1 or packed.get("ref") != value["input_ref"]:
        raise CheckpointStorageError("检查点与当前完整节点输入引用不一致")
    return deepcopy(value)


def validated_branch(db: Session, node: AgentNodeRun, checkpoint: dict[str, Any]) -> AgentJournalBranch:
    user_id, project_id, logical_id, source_hash = _scope(db, node)
    reference = checkpoint["journal_ref"]
    branch = db.get(AgentJournalBranch, reference["branch_id"], populate_existing=True)
    if (branch is None or branch.user_id != user_id or branch.project_id != project_id
            or branch.logical_task_id != logical_id or branch.source_hash != source_hash
            or branch.input_hash != checkpoint["input_hash"]):
        raise CheckpointStorageError("行动日志分支不属于当前租户、来源或逻辑任务")
    if any(type(reference.get(key)) is not int or reference[key] < 0 for key in ("receipt_cursor", "call_cursor")):
        raise CheckpointStorageError("行动日志读取游标无效")
    return branch


def create_branch(db: Session, node: AgentNodeRun, input_hash: str, *, parent=None, parent_ref=None) -> AgentJournalBranch:
    user_id, project_id, logical_id, source_hash = _scope(db, node)
    branch = AgentJournalBranch(
        id=str(uuid4()), user_id=user_id, project_id=project_id, logical_task_id=logical_id,
        source_hash=source_hash, input_hash=input_hash, owner_node_id=node.id,
        parent_id=parent.id if parent is not None else None,
        parent_receipt_cursor=parent_ref["receipt_cursor"] if parent_ref else 0,
        parent_call_cursor=parent_ref["call_cursor"] if parent_ref else 0,
    )
    db.add(branch)
    db.flush()
    return branch


def fork_checkpoint(db: Session, node: AgentNodeRun, checkpoint: dict[str, Any]) -> dict[str, Any]:
    branch = validated_branch(db, node, checkpoint)
    if branch.owner_node_id == node.id:
        return checkpoint
    fork = create_branch(db, node, checkpoint["input_hash"], parent=branch, parent_ref=checkpoint["journal_ref"])
    checkpoint["journal_ref"] = {"branch_id": fork.id, "receipt_cursor": 0, "call_cursor": 0}
    return checkpoint


def journal_chain(db: Session, node: AgentNodeRun, checkpoint: dict[str, Any]):
    current = validated_branch(db, node, checkpoint)
    receipt_cursor, call_cursor = (checkpoint["journal_ref"][key] for key in ("receipt_cursor", "call_cursor"))
    chain, seen = [], set()
    while current is not None:
        if current.id in seen:
            raise CheckpointStorageError("行动日志分支存在循环")
        seen.add(current.id)
        chain.append((current.id, receipt_cursor, call_cursor))
        if current.parent_id is None:
            break
        receipt_cursor, call_cursor = current.parent_receipt_cursor, current.parent_call_cursor
        parent = db.get(AgentJournalBranch, current.parent_id, populate_existing=True)
        if (parent is None or (parent.user_id, parent.project_id, parent.logical_task_id, parent.source_hash, parent.input_hash)
                != (current.user_id, current.project_id, current.logical_task_id, current.source_hash, current.input_hash)):
            raise CheckpointStorageError("行动日志父分支的所有者或来源不一致")
        current = parent
    return list(reversed(chain))


def receipt_records(db: Session, node: AgentNodeRun, checkpoint: dict[str, Any], *, tool_call_id=None) -> dict[str, dict[str, Any]]:
    result = {}
    for branch_id, cursor, _ in journal_chain(db, node, checkpoint):
        query = select(AgentActionReceiptRecord).where(AgentActionReceiptRecord.branch_id == branch_id, AgentActionReceiptRecord.id <= cursor)
        if tool_call_id is not None:
            query = query.where(AgentActionReceiptRecord.tool_call_id == tool_call_id)
        for row in db.execute(query.order_by(AgentActionReceiptRecord.id)).scalars():
            result[row.tool_call_id] = deepcopy(row.record)
    return result


def call_records(db: Session, node: AgentNodeRun, checkpoint: dict[str, Any], *, call_key=None) -> dict[str, dict[str, Any]]:
    result = {}
    for branch_id, _, cursor in journal_chain(db, node, checkpoint):
        query = select(AgentValidatedCallRecord).where(AgentValidatedCallRecord.branch_id == branch_id, AgentValidatedCallRecord.id <= cursor)
        if call_key is not None:
            query = query.where(AgentValidatedCallRecord.call_key == call_key)
        for row in db.execute(query.order_by(AgentValidatedCallRecord.id)).scalars():
            if row.call_key in result and result[row.call_key] != row.record:
                raise CheckpointStorageError("同一专业调用身份出现不同的不可变结果")
            result[row.call_key] = deepcopy(row.record)
    return result


def pack_record(store: ArtifactStore, record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = deepcopy(record)
    for key in fields:
        if key in result:
            value = result.pop(key)
            result[key + "_ref"] = store.put(value)
            result[key + "_hash"] = content_hash(value)
            if key == "error" and isinstance(value, dict) and isinstance(value.get("error_type"), str):
                result["error_type"] = value["error_type"]
    return result


def unpack_record(store: ArtifactStore, record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = deepcopy(record)
    for key in fields:
        reference = result.pop(key + "_ref", None)
        if reference is not None:
            result[key] = store.get(reference)
    return result


def context_retrieval_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """只计算真实 manifest 的小型统计，指标读取不展开领域状态。"""
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise CheckpointStorageError("上下文检索轨迹必须是对象数组")
    return {
        "retrieval_count": len(records),
        "retrieval_status_counts": dict(sorted(Counter(str(item.get("status") or "unknown") for item in records).items())),
        "manifest_status_counts": dict(sorted(Counter(str((item.get("manifest") or {}).get("status") or "unknown") for item in records).items())),
        "selected_source_count": sum(len((item.get("manifest") or {}).get("selected") or []) for item in records),
        "unavailable_source_kinds": sorted({str(kind) for item in records
                                           for kind in (item.get("manifest") or {}).get("unavailable_source_kinds", [])}),
    }


def latest_context_checkpoint_nodes(nodes) -> list[AgentNodeRun]:
    """每个持久节点只采用最后一次尝试，避免把继承历史重复计入检索次数。"""
    latest = {}
    for node in nodes:
        if (getattr(node, "sdk_state", None) or {}).get("action_checkpoint") is None:
            continue
        previous = latest.get(node.node_key)
        if previous is None or (node.attempt, node.id or 0) > (previous.attempt, previous.id or 0):
            latest[node.node_key] = node
    return sorted(latest.values(), key=lambda node: node.id or 0)


def run_context_retrieval_metrics(run: AgentRun, nodes) -> dict[str, Any]:
    """持久任务只读最新投影；普通任务只读已落盘产物，不切换权威来源。"""
    checkpoints = latest_context_checkpoint_nodes(nodes)
    if not checkpoints:
        return {"retrieval_evidence_status": "available", **context_retrieval_summary(
            ((run.run_context or {}).get("artifacts") or {}).get("context_retrievals", []),
        )}
    summaries = []
    for node in checkpoints:
        header = node.sdk_state["action_checkpoint"]
        summary = (header.get("projection") or {}).get("context_retrieval_summary") if isinstance(header, dict) else None
        if (not isinstance(header, dict) or header.get("version") != CHECKPOINT_VERSION
                or header.get("storage") != CHECKPOINT_STORAGE or not isinstance(summary, dict)):
            return {"retrieval_evidence_status": "unavailable", "retrieval_count": None,
                    "retrieval_status_counts": {}, "manifest_status_counts": {},
                    "selected_source_count": None, "unavailable_source_kinds": []}
        summaries.append(summary)
    statuses, manifests = Counter(), Counter()
    for summary in summaries:
        statuses.update(summary["retrieval_status_counts"])
        manifests.update(summary["manifest_status_counts"])
    return {
        "retrieval_evidence_status": "available",
        "retrieval_count": sum(summary["retrieval_count"] for summary in summaries),
        "retrieval_status_counts": dict(sorted(statuses.items())),
        "manifest_status_counts": dict(sorted(manifests.items())),
        "selected_source_count": sum(summary["selected_source_count"] for summary in summaries),
        "unavailable_source_kinds": sorted({kind for summary in summaries for kind in summary["unavailable_source_kinds"]}),
    }


def read_run_context_retrievals(db: Session, run: AgentRun) -> list[dict[str, Any]]:
    """反馈明确需要原 manifest 时，沿当前运行的检查点引用显式读取。"""
    nodes = list(db.execute(select(AgentNodeRun).where(AgentNodeRun.run_id == run.id)).scalars())
    checkpoints = latest_context_checkpoint_nodes(nodes)
    if not checkpoints:
        records = ((run.run_context or {}).get("artifacts") or {}).get("context_retrievals", [])
    else:
        records = []
        for node in checkpoints:
            # hydrate_checkpoint_state 统一验证输入、来源、租户、日志分支及完整内容指纹。
            state = hydrate_checkpoint_state(db, node)
            history = state.get("context_retrievals", [])
            if not isinstance(history, list):
                raise CheckpointStorageError("持久上下文检索轨迹必须是数组")
            records.extend(history)
    context_retrieval_summary(records)
    return deepcopy(records)


def state_projection(state: dict[str, Any]) -> dict[str, Any]:
    """领域负责进度语义，存储只复制薄投影，不再理解生成的内部阶段。"""
    projection = state.get("projection", {})
    if not isinstance(projection, dict):
        raise CheckpointStorageError("领域投影必须是对象")
    return {**deepcopy(projection),
            "context_retrieval_summary": context_retrieval_summary(state.get("context_retrievals", []))}


def checkpoint_projection(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    return deepcopy((checkpoint or {}).get("projection") or {})


def _provided_checkpoint(node: AgentNodeRun, checkpoint) -> dict[str, Any] | None:
    current = compact_checkpoint(node)
    if checkpoint is not None and checkpoint != current:
        raise CheckpointStorageError("提供的检查点不是当前节点版本，禁止替换引用或恢复旧状态")
    return current


def hydrate_action_checkpoint(db: Session, node: AgentNodeRun, *, checkpoint=None) -> dict[str, Any] | None:
    compact = _provided_checkpoint(node, checkpoint)
    if compact is None:
        return None
    validated_branch(db, node, compact)
    store = store_for_node(db, node)
    state = hydrate_checkpoint_state(db, node, checkpoint=compact)
    return {**compact, "state": state,
            "receipts": {key: unpack_record(store, value, ("arguments", "result", "error")) for key, value in receipt_records(db, node, compact).items()},
            "validated_agent_calls": {key: unpack_record(store, value, ("raw_output", "output")) for key, value in call_records(db, node, compact).items()}}


def hydrate_checkpoint_state(db: Session, node: AgentNodeRun, *, checkpoint=None) -> dict[str, Any]:
    compact = _provided_checkpoint(node, checkpoint)
    if compact is None:
        raise CheckpointStorageError("领域状态缺少紧凑检查点")
    validated_branch(db, node, compact)
    state = store_for_node(db, node).get(compact["state_ref"])
    if not isinstance(state, dict) or content_hash(state) != compact["state_hash"]:
        raise CheckpointStorageError("领域状态类型或完整摘要无效")
    return state


def replace_checkpoint_state(db: Session, run: AgentRun, node: AgentNodeRun, state: dict[str, Any], *, checkpoint=None) -> dict[str, Any]:
    if run.id != node.run_id or object_session(run) is not db or object_session(node) is not db:
        raise CheckpointStorageError("领域更新必须属于同一运行与当前数据库事务")
    compact = _provided_checkpoint(node, checkpoint)
    if compact is None:
        raise CheckpointStorageError("领域更新缺少紧凑检查点")
    compact = fork_checkpoint(db, node, compact)
    store = store_for_node(db, node)
    compact["state_ref"] = store.put(state)
    compact["state_hash"] = compact["state_ref"]["sha256"]
    compact["projection"] = {**compact["projection"], **state_projection(state)}
    node.sdk_state = {**dict(node.sdk_state or {}), "action_checkpoint": compact}
    db.add(node)
    return compact
