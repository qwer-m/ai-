"""以真实事件关联历史动作回执，离线验证显式顺序；不迁移检查点或写数据库。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.agent_platform.action_journal import (
    ActionStateConflict, action_state_hash, ordered_action_receipts, project_action_failures,
)


def bind_real_start_events(checkpoint, audit, *, run_id, node_id):
    """仅为离线验证关联原有事件；不作为生产恢复或旧版本兼容入口。"""
    run = next(item for item in audit["runs"] if item["run_id"] == run_id)
    node = next(item for item in run["nodes"] if item["node_id"] == node_id)
    assert action_state_hash(checkpoint) == node["checkpoint_sha256"]
    derived = deepcopy(checkpoint)
    derived["version"] = 2
    bindings = []
    for call_id, receipt in derived["receipts"].items():
        starts = [event for event in run["events"]
                  if event["tool_call_id"] == call_id and event["event_type"] == "agent_action_started"]
        assert len(starts) == 1
        event = starts[0]
        assert event["node_run_id"] == node_id
        assert all(receipt[key] == event[key] for key in ("tool_key", "arguments_hash", "pre_state_hash"))
        receipt["started_sequence"] = event["sequence"]
        bindings.append({"tool_call_id": call_id, "started_event_id": event["id"],
                         "started_sequence": event["sequence"]})
    return derived, bindings, node


def verify(options):
    paths = [options.checkpoint, options.event_audit]
    originals = {str(path.resolve()): path.read_bytes() for path in paths}
    checkpoint, audit = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    derived, bindings, node = bind_real_start_events(
        checkpoint, audit, run_id=options.run_id, node_id=options.node_id)
    visible = project_action_failures(derived, state=derived["state"])
    assert visible == node["event_order_failure_projection"]
    assert visible != node["current_failure_projection"]
    for items in (reversed(list(derived["receipts"].items())), sorted(derived["receipts"].items())):
        reordered = {**derived, "receipts": dict(items)}
        assert project_action_failures(reordered, state=derived["state"]) == visible
    sequences = [item["started_sequence"] for item in ordered_action_receipts(derived)]
    assert sequences == sorted(item["started_sequence"] for item in bindings)
    invalid = [("original_version_1", checkpoint)]
    call_ids = list(derived["receipts"])
    missing = deepcopy(derived)
    missing["receipts"][call_ids[0]].pop("started_sequence")
    invalid.append(("missing_started_sequence", missing))
    boolean = deepcopy(derived)
    boolean["receipts"][call_ids[0]]["started_sequence"] = True
    invalid.append(("boolean_started_sequence", boolean))
    duplicate = deepcopy(derived)
    duplicate["receipts"][call_ids[1]]["started_sequence"] = duplicate["receipts"][call_ids[0]]["started_sequence"]
    invalid.append(("duplicate_started_sequence", duplicate))
    mismatched = deepcopy(derived)
    mismatched["receipts"][call_ids[0]]["tool_call_id"] = call_ids[1]
    invalid.append(("receipt_key_identity_mismatch", mismatched))
    rejected = []
    for label, value in invalid:
        try:
            project_action_failures(value, state=value["state"])
        except ActionStateConflict as error:
            rejected.append({"case": label, "error": str(error)})
        else:
            raise AssertionError(label)
    assert all(Path(path).read_bytes() == raw for path, raw in originals.items())
    module = Path(__file__).resolve().parents[3] / "modules/agent_platform"
    hashes = {}
    for name in ("action_journal.py", "sdk_adapter.py"):
        raw = (module / name).read_bytes()
        compile(raw, str(module / name), "exec")
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return {"status": "passed", "verified_at": datetime.now(timezone.utc).isoformat(),
        "production_applied": True, "source_run_id": options.run_id, "source_node_id": options.node_id,
        "real_event_bindings": bindings, "old_projection_from_read_only_audit": node["current_failure_projection"],
        "current_production_projection": visible, "json_member_order_invariance": True,
        "strict_rejections": rejected, "source_files_unchanged": True,
        "source_sha256": {path: hashlib.sha256(raw).hexdigest() for path, raw in originals.items()},
        "production_sha256": hashes, "model_requests_sent": 0, "business_database_queries": 0,
        "database_writes": 0, "historical_checkpoint_migrated": False,
        "historical_usage": "离线将真实回执与原有启动事件关联，仅供纯函数回放；未恢复旧规划、改变源数据或补写线上序号",
        "transaction_write_path_executed": False,
        "write_path_limit": "本次只读代码复核同一事务内事件分配、回执保存和用量入账；没有用mock或数据库写入替代新动作验证",
        "database_connection_note": "无业务Session或查询；应用导入会初始化数据库连接并执行连接探测"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--event-audit", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError("保留既有证据，请使用新输出路径")
    report = verify(options)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "real_receipts": len(report["real_event_bindings"]),
                      "visible_failures": len(report["current_production_projection"]),
                      "source_files_unchanged": report["source_files_unchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
