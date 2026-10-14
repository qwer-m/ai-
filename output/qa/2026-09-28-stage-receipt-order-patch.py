"""准备候选文件并以真实事件回放顺序；不覆盖生产、不调用模型、不访问数据库。"""

from __future__ import annotations

from copy import deepcopy
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
QA = ROOT / "output" / "qa"
STAGED = QA / "2026-09-28-receipt-order-staged"
sys.path.insert(0, str(BACKEND))

from modules.agent_platform.action_journal import action_state_hash, project_action_failures


def replace_once(source, old, new):
    assert source.count(old) == 1, old[:100]
    return source.replace(old, new, 1)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def stage():
    STAGED.mkdir(exist_ok=False)
    files = {name: BACKEND / "modules" / "agent_platform" / name for name in ("action_journal.py", "sdk_adapter.py")}
    original_bytes = {name: path.read_bytes() for name, path in files.items()}
    originals = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    journal = originals["action_journal.py"]
    journal = replace_once(journal, '\n\ndef action_state_hash(value: Any) -> str:',
                           '\n\n_ACTION_CHECKPOINT_VERSION = 2\n\ndef action_state_hash(value: Any) -> str:')
    helper = '''def ordered_action_receipts(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
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


'''
    journal = replace_once(journal, 'def project_action_failures(', helper + 'def project_action_failures(')
    journal = replace_once(journal, 'for receipt in reversed(list(checkpoint["receipts"].values())):',
                          'for receipt in reversed(ordered_action_receipts(checkpoint)):')
    journal = replace_once(journal, 'checkpoint.get("version") != 1', 'checkpoint.get("version") != _ACTION_CHECKPOINT_VERSION')
    journal = replace_once(journal, '        if checkpoint.get("state_hash") != action_state_hash(checkpoint["state"]):',
                          '        ordered_action_receipts(checkpoint)\n        if checkpoint.get("state_hash") != action_state_hash(checkpoint["state"]):')
    journal = replace_once(journal, 'def _event(self, node: AgentNodeRun, event_type: str, receipt: dict[str, Any]) -> None:',
                          'def _event(self, node: AgentNodeRun, event_type: str, receipt: dict[str, Any]) -> int:')
    journal = replace_once(journal,
        '        self.repo.append_event(\n            run_id=self.run_id, node_run_id=node.id, event_type=event_type, payload=summary,\n        )',
        '        event = self.repo.append_event(\n            run_id=self.run_id, node_run_id=node.id, event_type=event_type, payload=summary,\n        )\n        return event.sequence')
    journal = replace_once(journal, '"version": 1, "input_hash": input_hash, "state": deepcopy(state),',
                          '"version": _ACTION_CHECKPOINT_VERSION, "input_hash": input_hash, "state": deepcopy(state),')
    journal = replace_once(journal,
        '                checkpoint["receipts"][tool_call_id] = receipt\n                self._save(node, checkpoint)\n            self._event(node, "agent_action_resumed" if resume_started else "agent_action_started", receipt)',
        '                # 事件与检查点处于同一事务；使用仓库已经分配的顺序，不依赖 JSON 对象成员序。\n'
        '                receipt["started_sequence"] = self._event(node, "agent_action_started", receipt)\n'
        '                checkpoint["receipts"][tool_call_id] = receipt\n                self._save(node, checkpoint)\n'
        '            else:\n                self._event(node, "agent_action_resumed", receipt)')
    sdk = originals["sdk_adapter.py"]
    sdk = replace_once(sdk, 'from .autonomous_tools import AutonomousToolLoop',
                       'from .autonomous_tools import AutonomousToolLoop\nfrom .action_journal import ordered_action_receipts')
    sdk = replace_once(sdk, 'reversed(list(saved_checkpoint["receipts"].values()))',
                       'reversed(ordered_action_receipts(saved_checkpoint))')
    sdk = replace_once(sdk, '            receipts = list(checkpoint["receipts"].values())',
                       '            receipts = ordered_action_receipts(checkpoint)')
    candidates = {"action_journal.py": journal, "sdk_adapter.py": sdk}
    patch = []
    for name, content in candidates.items():
        compile(content, str(STAGED / name), "exec")
        (STAGED / name).write_text(content, encoding="utf-8")
        target = f"backend/modules/agent_platform/{name}"
        patch.extend(difflib.unified_diff(originals[name].splitlines(keepends=True), content.splitlines(keepends=True),
                                         fromfile=f"a/{target}", tofile=f"b/{target}"))
    (STAGED / "receipt-order.patch").write_text("".join(patch), encoding="utf-8")
    manifest = {"status": "staged_not_applied", "base_sha256": {name: sha(value) for name, value in original_bytes.items()},
                "candidate_sha256": {name: sha((STAGED / name).read_bytes()) for name in candidates},
                "requires_explicit_root_apply_after_running_run_is_terminal": True,
                "checkpoint_version": 2, "historical_checkpoint_auto_migration": False}
    (STAGED / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return files, original_bytes


def verify(files, original_bytes):
    path = STAGED / "action_journal.py"
    spec = importlib.util.spec_from_file_location("modules.agent_platform._receipt_order_staged", path)
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = candidate
    spec.loader.exec_module(candidate)
    audit_path = QA / "2026-09-28-receipt-order-event-audit.json"
    checkpoint_path = QA / "2026-09-28-crm-workbench-run39-checkpoint.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    real = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run = next(item for item in audit["runs"] if item["run_id"] == 39)
    node = next(item for item in run["nodes"] if item["node_id"] == 986)
    assert action_state_hash(real) == node["checkpoint_sha256"]
    derived = deepcopy(real)
    derived["version"] = 2
    bindings = []
    for cid, receipt in derived["receipts"].items():
        starts = [event for event in run["events"] if event["tool_call_id"] == cid and event["event_type"] == "agent_action_started"]
        assert len(starts) == 1
        event = starts[0]
        assert event["node_run_id"] == node["node_id"]
        assert all(receipt[key] == event[key] for key in ("tool_key", "arguments_hash", "pre_state_hash"))
        receipt["started_sequence"] = event["sequence"]
        bindings.append({"tool_call_id": cid, "started_event_id": event["id"], "started_sequence": event["sequence"]})
    actual = candidate.project_action_failures(derived, state=derived["state"])
    assert actual == node["event_order_failure_projection"]
    assert len(actual) == 1 and actual[0]["tool_call_id"] == "call_00_RniWnkfNr9VCQb7NJaFZ6371"
    reverse_members = {**derived, "receipts": dict(reversed(list(derived["receipts"].items())))}
    assert candidate.project_action_failures(reverse_members, state=derived["state"]) == actual
    sorted_members = {**derived, "receipts": dict(sorted(derived["receipts"].items()))}
    assert candidate.project_action_failures(sorted_members, state=derived["state"]) == actual
    rejected = []
    # 异常样例仅由真实回执的协议元数据删除/错型/重复派生，不增加业务事实或模型结果。
    invalid = [("original_version_1", real)]
    cid = next(iter(derived["receipts"]))
    missing = deepcopy(derived); missing["receipts"][cid].pop("started_sequence")
    invalid.append(("missing_started_sequence", missing))
    boolean = deepcopy(derived); boolean["receipts"][cid]["started_sequence"] = True
    invalid.append(("boolean_started_sequence", boolean))
    duplicate = deepcopy(derived)
    ids = list(duplicate["receipts"])
    duplicate["receipts"][ids[1]]["started_sequence"] = duplicate["receipts"][ids[0]]["started_sequence"]
    invalid.append(("duplicate_started_sequence", duplicate))
    for label, checkpoint in invalid:
        try:
            candidate.ordered_action_receipts(checkpoint)
        except candidate.ActionStateConflict as error:
            rejected.append({"case": label, "error": str(error)})
        else:
            raise AssertionError(label)
    assert all(path.read_bytes() == original_bytes[name] for name, path in files.items())
    return {"status": "passed", "production_applied": False, "model_requests_sent": 0, "database_writes": 0,
        "source_run_id": 39, "source_node_id": 986, "real_event_bindings": bindings,
        "historical_usage": "仅离线给真实回执关联已存在的启动事件sequence，构造候选函数输入；不是线上迁移、恢复或兼容兜底",
        "source_sha256": {str(p): sha(p.read_bytes()) for p in (audit_path, checkpoint_path)},
        "old_projection": project_action_failures(real, state=real["state"]), "candidate_projection": actual,
        "json_member_order_invariance": True, "strict_rejections": rejected,
        "transaction_write_path_executed": False,
        "write_path_limit": "只检查现有append_event返回真实sequence以及同一事务内保存关系；未用mock或数据库写入替代实际新动作验证",
        "production_unchanged": True}


if __name__ == "__main__":
    files, original_bytes = stage()
    report = verify(files, original_bytes)
    (STAGED / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "staged": str(STAGED), "real_receipts_bound": len(report["real_event_bindings"]),
                      "candidate_visible_failures": len(report["candidate_projection"]), "production_applied": False}, ensure_ascii=False))
