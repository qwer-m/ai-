"""只读捕获 Run61→53 真实来源、审批与专业请求的父状态，不生成业务结果。"""

from datetime import datetime
import gzip
import json
from pathlib import Path
import runpy

BASE = Path(__file__).resolve().parent
h = runpy.run_path(str(BASE.parent / "capture_cost_progress.py"))
ROOT = h["ROOT"]
from core.db.model_defs import AgentApproval, AgentJournalBranch, AgentNodeRun, AgentRun
from modules.agent_platform import checkpoint_storage as storage


def row(value, *, sdk_checkpoint_only=False):
    data = {column.key: getattr(value, column.key) for column in value.__table__.columns}
    if sdk_checkpoint_only:
        data["sdk_state"] = {"action_checkpoint": value.sdk_state["action_checkpoint"]}
    return data


data = {"provenance": {"run_id": 61, "node_id": 413, "real_parent_run_id": 53,
    "approval_ids": [13, 15], "business_database_writes": 0, "model_requests_sent": 0,
    "description": "终态真实61来源链，治理raw和父行动状态为实际持久记录；source53原始完整fixture保持独立"}}
with h["SessionLocal"](autoflush=False) as db:
    db.execute(h["text"]("SET TRANSACTION READ ONLY"))
    origin, node = db.get(AgentRun, 61), db.get(AgentNodeRun, 413)
    cp = storage.compact_checkpoint(node)
    state = storage.hydrate_checkpoint_state(db, node, checkpoint=cp)
    data.update(source_run=row(origin), source_node=row(node, sdk_checkpoint_only=True),
        source_branch=row(db.get(AgentJournalBranch, cp["journal_ref"]["branch_id"])),
        source_state=state, approval=row(db.get(AgentApproval, 15)))
    data["source_calls"], data["source_receipts"], data["request_states"] = {}, {}, {}
    for node_id in (396, 413):
        actual_node = db.get(AgentNodeRun, node_id)
        checkpoint = storage.compact_checkpoint(actual_node)
        store = storage.store_for_node(db, actual_node)
        receipts = storage.receipt_records(db, actual_node, checkpoint)
        calls = storage.call_records(db, actual_node, checkpoint)
        for key, packed in calls.items():
            agent_key = packed["identity"]["agent_key"]
            if agent_key not in {"test_source_text_semantics_analyst", "test_source_semantics_analyst",
                                 "test_authority_reconciliation_reviewer"}:
                continue
            receipt = receipts[packed["parent_tool_call_id"]]
            state_hash = receipt["pre_state_hash"]
            if state_hash not in data["request_states"]:
                data["request_states"][state_hash] = store.get(store._ref(state_hash))
            if node_id == 413:
                data["source_calls"][key] = storage.unpack_record(store, packed, ("raw_output", "output"))
                data["source_receipts"][receipt["tool_call_id"]] = storage.unpack_record(
                    store, receipt, ("arguments", "result", "error"))
    original53 = json.loads(gzip.decompress((ROOT / "backend/tests/agent_platform/fixtures/source_context_run53_real.json.gz").read_bytes()))
    expected = state["source"]["analysis_reuse"]["source_state_hash"]
    assert h["action_state_hash"](original53["source_state"]) == expected
    data["provenance"]["parent_source_state_sha256"] = expected
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()
target = ROOT / "backend/tests/agent_platform/fixtures/source_context_lineage_run61_real.json.gz"
raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)).encode()
# datetime已按真实值序列化后再计算规范数据摘要。
data = json.loads(raw)
data["data_sha256"] = h["action_state_hash"](data)
raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
target.write_bytes(gzip.compress(raw, mtime=0))
print(json.dumps({"bytes": target.stat().st_size, "request_states": len(data["request_states"]),
    "governance_raw_calls": len(data["source_calls"]), "data_sha256": data["data_sha256"]}, indent=2))
