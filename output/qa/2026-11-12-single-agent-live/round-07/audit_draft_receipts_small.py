"""仅抓取指定动作的小回执及专业结果，不展开最新领域大状态。"""
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
        row = db.execute(select(AgentNodeRun.id, AgentNodeRun.run_id, AgentNodeRun.input_payload,
            func.json_extract(AgentNodeRun.sdk_state, "$.action_checkpoint").label("checkpoint"),
        ).where(AgentNodeRun.run_id == 55).order_by(AgentNodeRun.id.desc()).limit(1)).one()
        node = SimpleNamespace(id=row.id, run_id=row.run_id, input_payload=row.input_payload,
                               sdk_state={"action_checkpoint": decode(row.checkpoint)})
        checkpoint = storage.compact_checkpoint(node)
        store = storage.store_for_node(db, node)
        receipts = storage.receipt_records(db, node, checkpoint)
        calls = storage.call_records(db, node, checkpoint)
        selected = [value for value in sorted(receipts.values(), key=lambda value: value["started_sequence"])
                    if value["started_sequence"] in {3, 7, 8, 9, 10, 11, 12, 14, 15, 17, 18}
                    or (value["tool_key"] == "review_planning_route_gaps" and value["started_sequence"] <= 16)]
        record = []
        for receipt in selected:
            full = storage.unpack_record(store, receipt, ("arguments", "result", "error"))
            professional = []
            for call in calls.values():
                if call["parent_tool_call_id"] != receipt["tool_call_id"]:
                    continue
                normalized = store.get(call["output_ref"])
                has_gaps = any(value.get("support_gaps") for value in normalized.get("decisions", []))
                professional.append({**{key: value for key, value in call.items() if key not in {"output_ref", "raw_output_ref"}},
                    "has_gaps": has_gaps, "output": normalized,
                    "raw_output": store.get(call["raw_output_ref"]) if call.get("raw_output_ref") else None})
            record.append({"receipt": full, "professionals": professional})
        status = db.execute(select(AgentRun.status).where(AgentRun.id == 55)).scalar_one()
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    result = {"run_id": 55, "node_id": node.id, "observed_at": datetime.now(timezone.utc).isoformat(), "status": status,
        "projection": checkpoint["projection"], "scope": "只读小回执/结果引用；未hydrate最新整state", "actions": record}
    target = Path(__file__).with_name("draft-revision-small-receipts.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(target), "node": node.id, "actions": [{"sequence": item["receipt"]["started_sequence"],
        "tool_key": item["receipt"]["tool_key"], "status": item["receipt"]["status"], "arguments": item["receipt"].get("arguments"),
        "professionals": len(item["professionals"])} for item in record]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
