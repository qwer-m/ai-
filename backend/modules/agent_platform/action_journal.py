"""串行自主节点的动作回执与领域检查点，共用现有节点和追加事件存储。"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterator

from sqlalchemy.orm import load_only
from core.db.model_defs import AgentNodeRun, AgentRun, AgentActionReceiptRecord, AgentValidatedCallRecord

from .lifecycle import assert_run_claim
from .run_repository import AgentRunRepository
from .serialization import DuplicateJSONKeyError, strict_json_loads
from .usage import sync_action_usage_projection
from . import checkpoint_storage as storage


_ACTION_CHECKPOINT_VERSION = storage.CHECKPOINT_VERSION

def action_state_hash(value: Any) -> str:
    """输入、参数和领域状态共用完整 JSON 身份，不对业务字段做特殊猜测。"""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()








class ActionInProgress(RuntimeError):
    """动作已开始但尚未提交，只有明确恢复入口才能重新执行。"""


class ActionStateConflict(RuntimeError):
    """动作身份或领域状态变化，禁止把旧结果写入新版本。"""


@dataclass(frozen=True)
class ActionReceipt:
    tool_call_id: str
    tool_key: str
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    replayed: bool = False


class AgentActionJournal:
    """沿用 Run fencing；小检查点、增量回执和不可变产物在同一事务提交。"""

    def __init__(self, repo: AgentRunRepository, *, run_id: int, node_run_id: int, expected_claim_token: str) -> None:
        self.repo, self.run_id, self.node_run_id = repo, run_id, node_run_id
        self.expected_claim_token = expected_claim_token
        self._run = None

    @contextmanager
    def _transaction(self) -> Iterator[AgentNodeRun]:
        db = self.repo.db
        if db.new or db.dirty or db.deleted:
            raise RuntimeError("动作日志不能提交调用方尚未提交的业务修改")
        try:
            with db.no_autoflush:
                run = self.repo.get_run_control_for_update(run_id=self.run_id)
                if run is None:
                    raise LookupError("动作日志对应的运行不存在")
                assert_run_claim(run, self.expected_claim_token, now=datetime.utcnow())
                node = db.query(AgentNodeRun).options(load_only(
                    AgentNodeRun.id, AgentNodeRun.run_id, AgentNodeRun.status,
                    AgentNodeRun.sdk_state, AgentNodeRun.input_payload,
                )).filter(AgentNodeRun.id == self.node_run_id, AgentNodeRun.run_id == self.run_id).populate_existing().with_for_update().first()
                if node is None or node.status != "running":
                    raise ActionStateConflict("动作日志对应的节点不处于执行状态")
                self._run = run
                yield node
                assert_run_claim(run, self.expected_claim_token, now=datetime.utcnow())
            db.commit()
        except storage.CheckpointStorageError as error:
            db.rollback()
            raise ActionStateConflict(str(error)) from error
        except BaseException:
            db.rollback()
            raise
        finally:
            self._run = None

    def _checkpoint(self, node, *, fork=False):
        checkpoint = storage.compact_checkpoint(node)
        if checkpoint is not None:
            storage.validated_branch(self.repo.db, node, checkpoint)
            if fork:
                previous_branch = checkpoint["journal_ref"]["branch_id"]
                checkpoint = storage.fork_checkpoint(self.repo.db, node, checkpoint)
                if checkpoint["journal_ref"]["branch_id"] != previous_branch:
                    # 即使命中既有回执提前返回，新节点也必须固定此次轻量分支引用。
                    node.sdk_state = {**dict(node.sdk_state or {}), "action_checkpoint": deepcopy(checkpoint)}
                    self.repo.db.add(node)
        return checkpoint

    @staticmethod
    def _receipt(value, *, replayed=False):
        return ActionReceipt(tool_call_id=value["tool_call_id"], tool_key=value["tool_key"], status=value["status"],
                             result=deepcopy(value.get("result")), error=deepcopy(value.get("error")), replayed=replayed)

    def _save(self, node, checkpoint):
        node.sdk_state = {**dict(node.sdk_state or {}), "action_checkpoint": deepcopy(checkpoint)}
        sync_action_usage_projection(self.repo.db, self._run, node)
        self.repo.db.add(node)

    def _event(self, node, event_type, receipt):
        summary = {key: deepcopy(value) for key, value in receipt.items() if key not in {"arguments", "result", "error"}}
        return self.repo.append_event(run_id=self.run_id, node_run_id=node.id, event_type=event_type, payload=summary).sequence

    def _receipt_records(self, node, checkpoint, *, tool_call_id=None):
        return storage.receipt_records(self.repo.db, node, checkpoint, tool_call_id=tool_call_id)

    def _unpack_receipt(self, node, record):
        return storage.unpack_record(storage.store_for_node(self.repo.db, node), record, ("arguments", "result", "error"))

    def metadata(self):
        with self._transaction() as node:
            return self._checkpoint(node)

    def load_state(self):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            return None if checkpoint is None else storage.hydrate_checkpoint_state(self.repo.db, node, checkpoint=checkpoint)

    def load(self):
        """明确的完整审计视图；模型请求和预算热路径不得调用。"""
        with self._transaction() as node:
            return storage.hydrate_action_checkpoint(self.repo.db, node, checkpoint=self._checkpoint(node))

    def receipts(self, *, include_payloads=False):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                return []
            values = sorted(self._receipt_records(node, checkpoint).values(), key=lambda value: value["started_sequence"])
            return [self._unpack_receipt(node, value) for value in values] if include_payloads else values

    def receipt_by_id(self, tool_call_id, *, include_payloads=True):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                return None
            record = self._receipt_records(node, checkpoint, tool_call_id=tool_call_id).get(tool_call_id)
            return self._unpack_receipt(node, record) if record is not None and include_payloads else record

    def active_receipt(self, *, include_payloads=False):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            active = (checkpoint or {}).get("projection", {}).get("current_action")
            if not active or active["status"] != "started":
                return None
            record = self._receipt_records(node, checkpoint, tool_call_id=active["tool_call_id"]).get(active["tool_call_id"])
            if record is None or record["status"] != "started":
                raise ActionStateConflict("当前行动投影与持久回执不一致")
            return self._unpack_receipt(node, record) if include_payloads else record

    def receipt_count(self):
        return len(self.receipts())

    def child_call_count(self, parent_tool_call_id=None):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                return 0
            if parent_tool_call_id is None:
                return checkpoint["projection"]["saved_child_call_count"]
            return sum(record["parent_tool_call_id"] == parent_tool_call_id
                       for record in storage.call_records(self.repo.db, node, checkpoint).values())

    def failure_projection(self, *, state_hash):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                return []
            records = sorted(self._receipt_records(node, checkpoint).values(), key=lambda value: value["started_sequence"], reverse=True)
            calls = storage.call_records(self.repo.db, node, checkpoint)
            seen, failures = set(), []
            for record in records:
                if record["status"] == "started":
                    continue
                identity = (record["tool_key"], record["arguments_hash"])
                if identity in seen:
                    continue
                seen.add(identity)
                if record["status"] != "failed" or record["pre_state_hash"] != state_hash:
                    continue
                value = self._unpack_receipt(node, record)
                raw = value["arguments"]["arguments_json"]
                try:
                    arguments = strict_json_loads(raw)
                except (json.JSONDecodeError, DuplicateJSONKeyError):
                    arguments = None
                counts = {}
                for call in calls.values():
                    if call["parent_tool_call_id"] == record["tool_call_id"]:
                        key = call["identity"]["agent_key"]
                        counts[key] = counts.get(key, 0) + 1
                failures.append({"tool_key": value["tool_key"], "tool_call_id": value["tool_call_id"],
                                 "arguments": arguments, "arguments_hash": value["arguments_hash"],
                                 "arguments_sha256": hashlib.sha256(raw.encode()).hexdigest(), "error": value["error"],
                                 "state_matches_failure": True, "validated_child_call_counts": counts})
                if len(failures) == 5:
                    break
            return list(reversed(failures))

    def init(self, *, state, input_hash, budget=None):
        if not isinstance(state, dict):
            raise ValueError("动作领域状态必须是对象")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is not None:
                if checkpoint["input_hash"] != input_hash:
                    raise ActionStateConflict("初始化输入与已持久化检查点不同")
                return checkpoint
            full_input = self.repo.read_node_input(node)
            if action_state_hash(full_input) != input_hash:
                raise ActionStateConflict("初始化动作检查点的输入身份不匹配")
            if node.input_payload.get(storage.INPUT_MARKER) != 1:
                storage.store_node_input(self.repo.db, node, full_input)
            store = storage.store_for_node(self.repo.db, node)
            state_ref = store.put(state)
            branch = storage.create_branch(self.repo.db, node, input_hash)
            checkpoint = {"version": _ACTION_CHECKPOINT_VERSION, "storage": storage.CHECKPOINT_STORAGE,
                          "input_hash": input_hash, "input_ref": deepcopy(node.input_payload["ref"]),
                          "state_hash": state_ref["sha256"], "state_ref": state_ref,
                          "journal_ref": {"branch_id": branch.id, "receipt_cursor": 0, "call_cursor": 0},
                          "budget": deepcopy(budget or {}),
                          "projection": {**storage.state_projection(state), "current_action": None,
                                         "last_started_sequence": 0, "saved_child_call_count": 0,
                                         "saved_child_counts": {}, "current_action_completed_child_count": 0}}
            self._save(node, checkpoint)
            return deepcopy(checkpoint)

    def bind_execution_identity(self, semantic_hash):
        if not isinstance(semantic_hash, str) or not semantic_hash.strip():
            raise ValueError("动作执行身份不能为空")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("动作检查点尚未初始化")
            identity = checkpoint.get("execution_semantic_hash")
            if identity is not None:
                if identity != semantic_hash:
                    raise ActionStateConflict("动作执行契约已变化，禁止混用旧检查点与新 Agent 配置")
                return checkpoint
            charged = checkpoint["budget"].get("charged", {})
            if not isinstance(charged, dict) or any(type(value) is not int or value < 0 for value in charged.values()):
                raise ActionStateConflict("已占用预算无效")
            if checkpoint["budget"] and not charged:
                raise ActionStateConflict("已占用预算缺失")
            if checkpoint["projection"]["last_started_sequence"] or any(charged.values()):
                raise ActionStateConflict("已有行动或请求的检查点缺少执行身份，不能恢复")
            checkpoint["execution_semantic_hash"] = semantic_hash
            self._save(node, checkpoint)
            return deepcopy(checkpoint)

    def _append_receipt(self, node, checkpoint, record):
        row = AgentActionReceiptRecord(branch_id=checkpoint["journal_ref"]["branch_id"],
                                       tool_call_id=record["tool_call_id"], record=record)
        self.repo.db.add(row)
        self.repo.db.flush()
        checkpoint["journal_ref"]["receipt_cursor"] = row.id
        checkpoint["projection"]["current_action"] = {key: record[key] for key in ("tool_key", "tool_call_id", "status", "started_sequence")}

    def begin(self, *, tool_call_id, tool_key, args, prehash, resume_started=False):
        if not tool_call_id or not tool_key or not isinstance(args, dict):
            raise ValueError("动作需要唯一调用 ID、工具名和对象参数")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("动作检查点尚未初始化")
            records = self._receipt_records(node, checkpoint)
            record = records.get(tool_call_id)
            arguments_hash = action_state_hash(args)
            if resume_started and record is None:
                raise ActionStateConflict("不能恢复没有开始回执的动作")
            if record is not None:
                if record["tool_key"] != tool_key or record["arguments_hash"] != arguments_hash:
                    raise ActionStateConflict("同一动作调用 ID 不能绑定不同工具或参数")
                if record["status"] != "started":
                    return self._receipt(self._unpack_receipt(node, record), replayed=True)
                if not resume_started:
                    raise ActionInProgress("动作尚未完成，必须通过明确恢复入口继续")
                if record["pre_state_hash"] != prehash:
                    raise ActionStateConflict("未完成动作的原始领域状态已改变")
            if checkpoint["state_hash"] != prehash:
                raise ActionStateConflict("动作基于的领域状态版本已过期")
            if any(value["status"] == "started" and key != tool_call_id for key, value in records.items()):
                raise ActionInProgress("串行节点仍有尚未完成的动作")
            if record is None:
                sequence = checkpoint["projection"]["last_started_sequence"] + 1
                value = {"tool_call_id": tool_call_id, "tool_key": tool_key, "arguments": deepcopy(args),
                         "arguments_hash": arguments_hash, "pre_state_hash": prehash, "status": "started", "started_sequence": sequence}
                record = storage.pack_record(storage.store_for_node(self.repo.db, node), value, ("arguments",))
                record["started_event_sequence"] = self._event(node, "agent_action_started", record)
                record["started_run_id"] = self.run_id
                self._append_receipt(node, checkpoint, record)
                checkpoint["projection"]["last_started_sequence"] = sequence
                checkpoint["projection"]["current_action_completed_child_count"] = 0
            else:
                self._event(node, "agent_action_resumed", record)
            self._save(node, checkpoint)
            return self._receipt(record)

    def complete(self, *, tool_call_id, nextstate, result, budget=None):
        if not isinstance(nextstate, dict) or not isinstance(result, dict):
            raise ValueError("动作新状态与结果必须是对象")
        return self._finish(tool_call_id=tool_call_id, nextstate=nextstate, result=result, error=None, budget=budget)

    def fail(self, *, tool_call_id, error, budget=None):
        if not isinstance(error, dict):
            raise ValueError("动作错误必须是对象")
        return self._finish(tool_call_id=tool_call_id, nextstate=None, result=None, error=error, budget=budget)

    def _finish(self, *, tool_call_id, nextstate, result, error, budget):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("动作缺少检查点")
            record = self._receipt_records(node, checkpoint, tool_call_id=tool_call_id).get(tool_call_id)
            if record is None:
                raise ActionStateConflict("动作缺少已经保存的开始回执")
            if record["status"] != "started":
                return self._receipt(self._unpack_receipt(node, record), replayed=True)
            if checkpoint["state_hash"] != record["pre_state_hash"]:
                raise ActionStateConflict("动作执行期间领域状态版本已变化")
            if nextstate is not None:
                checkpoint["state_ref"] = storage.store_for_node(self.repo.db, node).put(nextstate)
                checkpoint["state_hash"] = checkpoint["state_ref"]["sha256"]
                checkpoint["projection"].update(storage.state_projection(nextstate))
            record.update(status="failed" if error is not None else "completed", post_state_hash=checkpoint["state_hash"])
            store = storage.store_for_node(self.repo.db, node)
            if result is not None:
                record.update(storage.pack_record(store, {"result": result}, ("result",)))
            if error is not None:
                record.update(storage.pack_record(store, {"error": error}, ("error",)))
            self._append_receipt(node, checkpoint, record)
            if budget is not None:
                checkpoint["budget"] = deepcopy(budget)
            self._save(node, checkpoint)
            self._event(node, "agent_action_failed" if error is not None else "agent_action_completed", record)
            return self._receipt(self._unpack_receipt(node, record))

    def managed_parallelism(self, *, scope_key, scope):
        """读取执行控制头，不展开领域状态或历史专业产物。"""
        if scope_key != action_state_hash(scope):
            raise ActionStateConflict("受管并发作用域摘要不一致")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("受管并发控制缺少检查点")
            records = checkpoint.get("managed_parallelism", {})
            if not isinstance(records, dict):
                raise ActionStateConflict("受管并发控制目录无效")
            record = records.get(scope_key)
            if record is None:
                return None
            if not isinstance(record, dict) or record.get("scope") != scope or not isinstance(record.get("policy"), dict):
                raise ActionStateConflict("受管并发控制不属于当前真实模型作用域")
            return deepcopy(record["policy"])

    def save_managed_parallelism(self, *, scope_key, scope, policy, adjustment=None):
        """执行权校验与分支沿用现有事务；不改领域哈希或已用预算。"""
        if scope_key != action_state_hash(scope) or not isinstance(policy, dict):
            raise ActionStateConflict("受管并发控制的作用域或快照无效")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("受管并发控制缺少检查点")
            records = deepcopy(checkpoint.get("managed_parallelism", {}))
            existing = records.get(scope_key)
            if existing is not None and existing.get("scope") != scope:
                raise ActionStateConflict("同一并发作用域不能绑定不同模型")
            record = {"scope": deepcopy(scope), "policy": deepcopy(policy)}
            if existing == record:
                return
            records[scope_key] = record
            checkpoint["managed_parallelism"] = records
            self._save(node, checkpoint)
            if adjustment is not None:
                self.repo.append_event(run_id=self.run_id, node_run_id=self.node_run_id,
                    event_type="managed_concurrency_adjusted",
                    payload={"scope_key": scope_key, "scope": deepcopy(scope), **deepcopy(adjustment)})

    def save_budget(self, snapshot):
        if not isinstance(snapshot, dict):
            raise TypeError("预算快照必须是对象")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("动作检查点尚未初始化")
            checkpoint["budget"] = deepcopy(snapshot)
            self._save(node, checkpoint)
            return deepcopy(checkpoint)

    @staticmethod
    def _validated_call_key(checkpoint, identity):
        required = {"agent_key", "input_hash", "execution_semantic_hash", "contract_hash"}
        if (not isinstance(identity, dict) or set(identity) != required
                or any(not isinstance(value, str) or not value for value in identity.values())):
            raise ActionStateConflict("专业结果回执缺少完整输入与执行契约身份")
        if identity["execution_semantic_hash"] != checkpoint.get("execution_semantic_hash"):
            raise ActionStateConflict("专业结果与当前父执行契约不一致")
        return action_state_hash(identity)

    def validated_agent_call(self, identity):
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("专业结果缺少父检查点")
            key = self._validated_call_key(checkpoint, identity)
            record = storage.call_records(self.repo.db, node, checkpoint, call_key=key).get(key)
            if record is None:
                return None
            value = storage.unpack_record(storage.store_for_node(self.repo.db, node), record, ("raw_output", "output"))
            if (value.get("identity") != identity or value.get("status") != "validated"
                    or not isinstance(value.get("raw_output"), dict) or not isinstance(value.get("output"), dict)
                    or value["raw_output_hash"] != action_state_hash(value["raw_output"])
                    or value["output_hash"] != action_state_hash(value["output"])
                    or value.get("result_type") != value["output"].get("result_type", "output")):
                raise ActionStateConflict("专业结果的身份、内容或结果类型无效")
            return value

    def save_validated_agent_call(self, *, identity, raw_output, output):
        if not isinstance(raw_output, dict) or not isinstance(output, dict):
            raise TypeError("专业结果必须保存原始候选及完整验收产物")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("专业结果缺少父检查点")
            key = self._validated_call_key(checkpoint, identity)
            active = [value for value in self._receipt_records(node, checkpoint).values() if value["status"] == "started"]
            if len(active) != 1:
                raise ActionStateConflict("专业结果必须属于唯一正在执行的父行动")
            value = {"identity": deepcopy(identity), "status": "validated", "parent_tool_call_id": active[0]["tool_call_id"],
                     "raw_output": deepcopy(raw_output), "output": deepcopy(output), "result_type": output.get("result_type", "output")}
            record = storage.pack_record(storage.store_for_node(self.repo.db, node), value, ("raw_output", "output"))
            previous = storage.call_records(self.repo.db, node, checkpoint, call_key=key).get(key)
            if previous is not None:
                if any(previous.get(field) != record[field] for field in record if field != "parent_tool_call_id"):
                    raise ActionStateConflict("同一专业输入与契约已有不同的验收产物")
                return storage.unpack_record(storage.store_for_node(self.repo.db, node), previous, ("raw_output", "output"))
            row = AgentValidatedCallRecord(branch_id=checkpoint["journal_ref"]["branch_id"], call_key=key,
                                           agent_key=identity["agent_key"], parent_tool_call_id=record["parent_tool_call_id"], record=record)
            self.repo.db.add(row)
            self.repo.db.flush()
            checkpoint["journal_ref"]["call_cursor"] = row.id
            projection = checkpoint["projection"]
            projection["saved_child_call_count"] += 1
            projection["current_action_completed_child_count"] += 1
            projection["saved_child_counts"][identity["agent_key"]] = projection["saved_child_counts"].get(identity["agent_key"], 0) + 1
            self._save(node, checkpoint)
            self.repo.append_event(run_id=self.run_id, node_run_id=self.node_run_id, event_type="agent_specialist_result_saved", payload={
                **deepcopy(identity), "parent_tool_call_id": record["parent_tool_call_id"],
                "output_hash": record["output_hash"], "result_type": record["result_type"]})
            return {**value, "raw_output_hash": record["raw_output_hash"], "output_hash": record["output_hash"]}
