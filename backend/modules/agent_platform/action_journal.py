"""串行自主节点的动作回执与领域检查点，共用现有节点和追加事件存储。"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterator

from core.db.model_defs import AgentNodeRun, AgentRun

from .lifecycle import assert_run_claim
from .run_repository import AgentRunRepository
from .serialization import DuplicateJSONKeyError, strict_json_loads
from .usage import sync_action_usage


_ACTION_CHECKPOINT_VERSION = 2

def action_state_hash(value: Any) -> str:
    """输入、参数和领域状态共用完整 JSON 身份，不对业务字段做特殊猜测。"""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ordered_action_receipts(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    """只按已持久化的真实启动事件序号排序，拒绝没有可靠顺序的旧检查点。"""
    if (not isinstance(checkpoint, dict) or checkpoint.get("version") != _ACTION_CHECKPOINT_VERSION
            or not isinstance(checkpoint.get("receipts"), dict)):
        raise ActionStateConflict("动作检查点缺少受支持的有序回执契约")
    sequences = set()
    receipts = []
    for call_id, receipt in checkpoint["receipts"].items():
        sequence = receipt.get("started_sequence") if isinstance(receipt, dict) else None
        if (not isinstance(receipt, dict) or receipt.get("tool_call_id") != call_id
                or type(sequence) is not int or sequence <= 0 or sequence in sequences):
            raise ActionStateConflict("动作回执缺少唯一且有效的启动事件序号")
        sequences.add(sequence)
        receipts.append(receipt)
    return sorted(receipts, key=lambda receipt: receipt["started_sequence"])


def project_action_failures(checkpoint: dict[str, Any], *, state: dict[str, Any]) -> list[dict[str, Any]]:
    """当前观察仅保留同一领域版本的失败；旧状态和后续已成功的错误留在审计回执。"""
    state_hash = action_state_hash(state)
    seen: set[tuple[str, str]] = set()
    failures = []
    calls = list(checkpoint.get("validated_agent_calls", {}).values())
    for receipt in reversed(ordered_action_receipts(checkpoint)):
        if receipt["status"] == "started":
            continue
        identity = (receipt["tool_key"], receipt["arguments_hash"])
        if identity in seen:
            continue
        seen.add(identity)
        if receipt["status"] != "failed" or receipt["pre_state_hash"] != state_hash:
            continue
        child_counts: dict[str, int] = {}
        for call in calls:
            if call["parent_tool_call_id"] == receipt["tool_call_id"]:
                key = call["identity"]["agent_key"]
                child_counts[key] = child_counts.get(key, 0) + 1
        raw_arguments = receipt["arguments"]["arguments_json"]
        try:
            arguments = strict_json_loads(raw_arguments)
        except (json.JSONDecodeError, DuplicateJSONKeyError):
            # 已拒绝的参数仍保留于原回执；观察只保留诊断，不能猜测结构或覆盖重复字段。
            arguments = None
        failures.append({
            "tool_key": receipt["tool_key"], "tool_call_id": receipt["tool_call_id"],
            "arguments": arguments,
            "arguments_hash": receipt["arguments_hash"],
            "arguments_sha256": hashlib.sha256(raw_arguments.encode("utf-8")).hexdigest(),
            "error": deepcopy(receipt["error"]),
            "state_matches_failure": receipt["pre_state_hash"] == state_hash,
            "validated_child_call_counts": child_counts,
        })
        if len(failures) == 5:
            break
    return list(reversed(failures))


def interrupted_transport_action(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    """仅恢复最后一次网络中断且领域状态未变化的行动，保留原始完整参数。"""
    receipts = ordered_action_receipts(checkpoint)
    if not receipts:
        return None
    receipt = receipts[-1]
    if (receipt["status"] != "failed"
            or receipt.get("error", {}).get("error_type") not in {"APITimeoutError", "APIConnectionError"}
            or receipt["pre_state_hash"] != checkpoint["state_hash"]
            or checkpoint["state_hash"] != action_state_hash(checkpoint["state"])
            or any(item["status"] == "started" for item in receipts)):
        return None
    return deepcopy(receipt)


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
    """每次方法调用自提交短事务；调用方不能携带尚未提交的业务 ORM 修改。"""

    def __init__(
        self, repo: AgentRunRepository, *, run_id: int, node_run_id: int,
        expected_claim_token: str,
    ) -> None:
        self.repo = repo
        self.run_id = run_id
        self.node_run_id = node_run_id
        self.expected_claim_token = expected_claim_token

    @contextmanager
    def _transaction(self) -> Iterator[AgentNodeRun]:
        db = self.repo.db
        if db.new or db.dirty or db.deleted:
            raise RuntimeError("动作日志不能提交调用方尚未提交的业务修改")
        try:
            with db.no_autoflush:
                run = self.repo.get_run_for_update(run_id=self.run_id)
                if run is None:
                    raise LookupError("动作日志对应的运行不存在")
                assert_run_claim(run, self.expected_claim_token, now=datetime.utcnow())
                node = db.query(AgentNodeRun).filter(
                    AgentNodeRun.id == self.node_run_id, AgentNodeRun.run_id == self.run_id,
                ).populate_existing().with_for_update().first()
                if node is None or node.status != "running":
                    raise ActionStateConflict("动作日志对应的节点不处于执行状态")
                yield node
                # 准入与提交都校验；事务内部只处理本地 JSON，不跨模型或外部调用持锁。
                assert_run_claim(run, self.expected_claim_token, now=datetime.utcnow())
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def _checkpoint(self, node: AgentNodeRun) -> dict[str, Any] | None:
        value = (node.sdk_state or {}).get("action_checkpoint")
        if value is None:
            return None
        checkpoint = deepcopy(value)
        if (
            not isinstance(checkpoint, dict) or checkpoint.get("version") != _ACTION_CHECKPOINT_VERSION
            or not isinstance(checkpoint.get("state"), dict)
            or not isinstance(checkpoint.get("receipts"), dict)
            or not isinstance(checkpoint.get("budget"), dict)
        ):
            raise ActionStateConflict("动作检查点结构无效")
        ordered_action_receipts(checkpoint)
        if checkpoint.get("state_hash") != action_state_hash(checkpoint["state"]):
            raise ActionStateConflict("动作检查点的领域状态摘要不一致")
        if checkpoint.get("input_hash") != action_state_hash(self.repo.read_node_input(node)):
            raise ActionStateConflict("动作检查点与当前节点输入不一致")
        return checkpoint

    @staticmethod
    def _receipt(value: dict[str, Any], *, replayed: bool = False) -> ActionReceipt:
        return ActionReceipt(
            tool_call_id=value["tool_call_id"], tool_key=value["tool_key"], status=value["status"],
            result=deepcopy(value.get("result")), error=deepcopy(value.get("error")), replayed=replayed,
        )

    def _save(self, node: AgentNodeRun, checkpoint: dict[str, Any]) -> None:
        node.sdk_state = {**dict(node.sdk_state or {}), "action_checkpoint": deepcopy(checkpoint)}
        # _transaction 已锁住同一 Run；事件写入可能 flush，故必须在保存入口同步入账。
        run = self.repo.db.get(AgentRun, self.run_id)
        if run is None:
            raise LookupError("动作日志对应的运行不存在")
        sync_action_usage(run, node)
        self.repo.db.add(node)

    def _event(self, node: AgentNodeRun, event_type: str, receipt: dict[str, Any]) -> int:
        # 领域状态与原始参数只在检查点各保存一份，事件保存小回执供增量展示。
        summary = {key: deepcopy(value) for key, value in receipt.items() if key != "arguments"}
        event = self.repo.append_event(
            run_id=self.run_id, node_run_id=node.id, event_type=event_type, payload=summary,
        )
        return event.sequence

    def load(self) -> dict[str, Any] | None:
        with self._transaction() as node:
            return self._checkpoint(node)

    def bind_execution_identity(self, semantic_hash: str) -> dict[str, Any]:
        """首次请求前固定真实执行契约；已有行动的旧检查点不能补写未知身份。"""

        if not isinstance(semantic_hash, str) or not semantic_hash.strip():
            raise ValueError("动作执行身份不能为空")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("动作检查点尚未初始化")
            identity = checkpoint.get("execution_semantic_hash")
            if identity is not None:
                if identity != semantic_hash:
                    raise ActionStateConflict("动作执行契约已变化，禁止混用旧检查点与新 Agent 配置")
                return checkpoint
            budget = checkpoint["budget"]
            charged = budget.get("charged", {})
            if not isinstance(charged, dict) or any(
                type(value) is not int or value < 0 for value in charged.values()
            ):
                raise ActionStateConflict("动作检查点的已占用预算无效")
            if budget and not charged:
                raise ActionStateConflict("动作检查点的已占用预算缺失")
            if checkpoint["receipts"] or any(charged.values()):
                raise ActionStateConflict("已有行动或请求的检查点缺少执行身份，不能恢复")
            checkpoint["execution_semantic_hash"] = semantic_hash
            self._save(node, checkpoint)
            return deepcopy(checkpoint)

    def init(
        self, *, state: dict[str, Any], input_hash: str, budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise ValueError("动作领域状态必须是对象")
        with self._transaction() as node:
            if input_hash != action_state_hash(self.repo.read_node_input(node)):
                raise ActionStateConflict("初始化动作检查点的输入身份不匹配")
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                checkpoint = {
                    "version": _ACTION_CHECKPOINT_VERSION, "input_hash": input_hash, "state": deepcopy(state),
                    "state_hash": action_state_hash(state), "budget": deepcopy(budget or {}),
                    "receipts": {},
                }
                self._save(node, checkpoint)
            return deepcopy(checkpoint)

    def begin(
        self, *, tool_call_id: str, tool_key: str, args: dict[str, Any], prehash: str,
        resume_started: bool = False,
    ) -> ActionReceipt:
        if not tool_call_id or not tool_key or not isinstance(args, dict):
            raise ValueError("动作需要唯一调用 ID、工具名和对象参数")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("动作检查点尚未初始化")
            receipt = checkpoint["receipts"].get(tool_call_id)
            arguments_hash = action_state_hash(args)
            if resume_started and receipt is None:
                raise ActionStateConflict("不能恢复尚未持久化开始回执的动作")
            if receipt is not None:
                if receipt["tool_key"] != tool_key or receipt["arguments_hash"] != arguments_hash:
                    raise ActionStateConflict("同一动作调用 ID 不能绑定不同工具或参数")
                if receipt["status"] != "started":
                    return self._receipt(receipt, replayed=True)
                if not resume_started:
                    raise ActionInProgress("动作尚未完成，必须通过明确恢复入口继续")
                if receipt["pre_state_hash"] != prehash:
                    raise ActionStateConflict("未完成动作的原始领域状态已改变")
            if checkpoint["state_hash"] != prehash:
                raise ActionStateConflict("动作基于的领域状态版本已过期")
            if any(
                value["status"] == "started" and key != tool_call_id
                for key, value in checkpoint["receipts"].items()
            ):
                raise ActionInProgress("串行节点仍有尚未完成的动作")
            if receipt is None:
                receipt = {
                    "tool_call_id": tool_call_id, "tool_key": tool_key,
                    "arguments": deepcopy(args), "arguments_hash": arguments_hash,
                    "pre_state_hash": prehash, "status": "started",
                }
                # 事件与检查点处于同一事务；使用仓库已经分配的顺序，不依赖 JSON 对象成员序。
                receipt["started_sequence"] = self._event(node, "agent_action_started", receipt)
                checkpoint["receipts"][tool_call_id] = receipt
                self._save(node, checkpoint)
            else:
                self._event(node, "agent_action_resumed", receipt)
            return self._receipt(receipt)

    def complete(
        self, *, tool_call_id: str, nextstate: dict[str, Any], result: dict[str, Any],
        budget: dict[str, Any] | None = None,
    ) -> ActionReceipt:
        if not isinstance(nextstate, dict) or not isinstance(result, dict):
            raise ValueError("动作新状态与结果摘要必须是对象")
        return self._finish(
            tool_call_id=tool_call_id, nextstate=nextstate, result=result, error=None, budget=budget,
        )

    def fail(
        self, *, tool_call_id: str, error: dict[str, Any], budget: dict[str, Any] | None = None,
    ) -> ActionReceipt:
        if not isinstance(error, dict):
            raise ValueError("动作错误摘要必须是对象")
        return self._finish(
            tool_call_id=tool_call_id, nextstate=None, result=None, error=error, budget=budget,
        )

    def _finish(
        self, *, tool_call_id: str, nextstate: dict[str, Any] | None,
        result: dict[str, Any] | None, error: dict[str, Any] | None, budget: dict[str, Any] | None,
    ) -> ActionReceipt:
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None or tool_call_id not in checkpoint["receipts"]:
                raise ActionStateConflict("动作缺少已经持久化的开始回执")
            receipt = checkpoint["receipts"][tool_call_id]
            if receipt["status"] != "started":
                return self._receipt(receipt, replayed=True)
            if checkpoint["state_hash"] != receipt["pre_state_hash"]:
                raise ActionStateConflict("动作执行期间领域状态版本已变化")
            if nextstate is not None:
                checkpoint["state"] = deepcopy(nextstate)
                checkpoint["state_hash"] = action_state_hash(nextstate)
            receipt.update(
                status="failed" if error is not None else "completed",
                post_state_hash=checkpoint["state_hash"],
            )
            if result is not None:
                receipt["result"] = deepcopy(result)
            if error is not None:
                receipt["error"] = deepcopy(error)
            if budget is not None:
                checkpoint["budget"] = deepcopy(budget)
            self._save(node, checkpoint)
            self._event(node, "agent_action_failed" if error is not None else "agent_action_completed", receipt)
            return self._receipt(receipt)

    def save_budget(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("动作检查点尚未初始化")
            checkpoint["budget"] = deepcopy(snapshot)
            self._save(node, checkpoint)
            return deepcopy(checkpoint)

    @staticmethod
    def _validated_call_key(checkpoint: dict[str, Any], identity: dict[str, Any]) -> str:
        required = {"agent_key", "input_hash", "execution_semantic_hash", "contract_hash"}
        if (not isinstance(identity, dict) or set(identity) != required
                or any(not isinstance(value, str) or not value for value in identity.values())):
            raise ActionStateConflict("专业结果回执缺少完整输入与执行契约身份")
        if identity["execution_semantic_hash"] != checkpoint.get("execution_semantic_hash"):
            raise ActionStateConflict("专业结果回执与当前已绑定的父执行契约不一致")
        return action_state_hash(identity)

    def validated_agent_call(self, identity: dict[str, Any]) -> dict[str, Any] | None:
        """仅读取已完整验收的专业结果；调用方仍须用当前完整契约复验原始候选。"""
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("专业结果回执缺少父行动检查点")
            key = self._validated_call_key(checkpoint, identity)
            records = checkpoint.get("validated_agent_calls", {})
            if not isinstance(records, dict):
                raise ActionStateConflict("专业结果回执目录无效")
            record = records.get(key)
            if record is None:
                return None
            if (not isinstance(record, dict) or record.get("identity") != identity
                    or record.get("status") != "validated"
                    or not isinstance(record.get("raw_output"), dict)
                    or not isinstance(record.get("output"), dict)
                    or record.get("raw_output_hash") != action_state_hash(record["raw_output"])
                    or record.get("output_hash") != action_state_hash(record["output"])
                    or record.get("result_type") != record["output"].get("result_type", "output")):
                raise ActionStateConflict("专业结果回执的身份、结果类型或内容指纹无效")
            return deepcopy(record)

    def save_validated_agent_call(
        self, *, identity: dict[str, Any], raw_output: dict[str, Any], output: dict[str, Any],
    ) -> dict[str, Any]:
        """逐专业调用短事务保存，不改变父行动领域状态、成功状态或已计费预算。"""
        if not isinstance(raw_output, dict) or not isinstance(output, dict):
            raise TypeError("专业结果必须保存原始候选及完整验收产物")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("专业结果回执缺少父行动检查点")
            key = self._validated_call_key(checkpoint, identity)
            active = [item for item in checkpoint["receipts"].values() if item["status"] == "started"]
            if len(active) != 1:
                raise ActionStateConflict("专业结果必须归属于唯一正在执行的父行动")
            record = {
                "identity": deepcopy(identity), "status": "validated", "parent_tool_call_id": active[0]["tool_call_id"],
                "raw_output": deepcopy(raw_output), "output": deepcopy(output),
                "raw_output_hash": action_state_hash(raw_output), "output_hash": action_state_hash(output),
                "result_type": output.get("result_type", "output"),
            }
            records = checkpoint.setdefault("validated_agent_calls", {})
            if not isinstance(records, dict):
                raise ActionStateConflict("专业结果回执目录无效")
            previous = records.get(key)
            if previous is not None:
                if any(previous.get(field) != record[field] for field in record if field != "parent_tool_call_id"):
                    raise ActionStateConflict("同一专业输入与契约已有不同的验收产物，不能覆盖或重新抽取")
                return deepcopy(previous)
            records[key] = record
            self._save(node, checkpoint)
            self.repo.append_event(run_id=self.run_id, node_run_id=self.node_run_id,
                event_type="agent_specialist_result_saved", payload={
                    **deepcopy(identity), "parent_tool_call_id": record["parent_tool_call_id"],
                    "output_hash": record["output_hash"], "result_type": record["result_type"],
                })
            return deepcopy(record)
