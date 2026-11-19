"""只读抓取草案修订小回执；不读取完整领域状态，不调用模型。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select, text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import checkpoint_storage as storage


def decode(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def main():
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        row = db.execute(select(
            AgentNodeRun.id, AgentNodeRun.run_id, AgentNodeRun.input_payload,
            func.json_extract(AgentNodeRun.sdk_state, "$.action_checkpoint").label("checkpoint"),
        ).where(AgentNodeRun.run_id == 53).order_by(AgentNodeRun.id.desc()).limit(1)).one()
        node = SimpleNamespace(id=row.id, run_id=row.run_id, input_payload=row.input_payload,
                               sdk_state={"action_checkpoint": decode(row.checkpoint)})
        checkpoint = storage.compact_checkpoint(node)
        store = storage.store_for_node(db, node)
        receipts = storage.receipt_records(db, node, checkpoint)
        calls = storage.call_records(db, node, checkpoint)
        revisions = []
        for receipt in sorted(receipts.values(), key=lambda item: item["started_sequence"]):
            if receipt["tool_key"] != "revise_planning_draft":
                continue
            arguments = store.get(receipt["arguments_ref"])
            specialists = [call for call in calls.values()
                           if call["parent_tool_call_id"] == receipt["tool_call_id"]
                           and call["identity"]["agent_key"] == "test_business_plan_batcher"]
            revisions.append({"sequence": receipt["started_sequence"], "status": receipt["status"],
                "tool_call_id": receipt["tool_call_id"], "arguments": arguments,
                "pre_state_hash": receipt["pre_state_hash"], "post_state_hash": receipt.get("post_state_hash"),
                "validated_outputs": [{"identity": call["identity"], "output_ref": call["output_ref"],
                    "output": store.get(call["output_ref"])} for call in specialists]})
        status = db.execute(select(AgentRun.status).where(AgentRun.id == 53)).scalar_one()
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    report = {"run_id": 53, "node_id": node.id, "observed_at": datetime.now(timezone.utc).isoformat(),
              "status": status, "scope": "只读动作参数与分批草案小输出；未 hydrate 大状态", "revisions": revisions}
    target = Path(__file__).with_name("draft-revisions-real-receipts.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(target), "revision_count": len(revisions),
        "revisions": [{"sequence": item["sequence"], "status": item["status"],
                       "arguments": item["arguments"], "outputs": len(item["validated_outputs"])} for item in revisions]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
