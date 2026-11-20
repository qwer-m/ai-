"""只读检查真实待确认表单和拒绝边界；没有用户答案时不伪造有效提交。"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db import db_utc_now
from core.db.database import SessionLocal
from core.db.model_defs import AgentApproval, AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.conflict_confirmation import apply_conflict_decision


def verify(run_id, output):
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, run_id)
        assert run and run.status == "waiting_approval", "需要真实的待确认运行"
        approval = (
            db.query(AgentApproval)
            .filter_by(run_id=run_id, status="pending")
            .one()
        )
        assert approval.request_payload["kind"] == "source_conflicts"
        node = db.get(AgentNodeRun, approval.node_run_id)
        before = action_state_hash([node.sdk_state, run.run_context])
        checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
        assert checkpoint["state_hash"] == approval.request_payload["state_hash"]
        items = approval.request_payload["items"]
        assert items and all(all(fact["assertion"] for fact in item["facts"]) for item in items)
        current_storage = checkpoint.get("version") == 3 and checkpoint.get("storage") == "artifact-journal-v1"
        now = db_utc_now()
        rejection_messages = []
        for invalid in ({}, {item["id"]: " " for item in items}):
            try:
                apply_conflict_decision(approval, node, run, invalid, now=now)
            except ValueError as error:
                rejection_messages.append(str(error))
                assert before == action_state_hash([node.sdk_state, run.run_context])
                assert ("全部待明确事项" in str(error)) if current_storage else ("显式迁移" in str(error))
            else:
                raise AssertionError("缺填或旧协议不能被接受")
        # 问题文本不是用户答案；没有真实用户口径时不伪造有效提交或恢复成功。
        historical_confirmations = []
        if current_storage:
            from modules.agent_platform.checkpoint_storage import hydrate_checkpoint_state
            state = hydrate_checkpoint_state(db, node)
            historical_confirmations = list((state.get("conflict_confirmations") or {}).keys())
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        db.refresh(node)
        db.refresh(run)
        assert before == action_state_hash([node.sdk_state, run.run_context])
        report = {"run_id": run_id, "approval_id": approval.id, "conflict_count": len(items),
                  "checkpoint_version": checkpoint.get("version"),
                  "checkpoint_protocol": "artifact-journal-v1" if current_storage else "legacy-inline-archive",
                  "missing_answer_rejection_verified": current_storage,
                  "legacy_checkpoint_rejection_verified": not current_storage,
                  "rejection_messages": rejection_messages,
                  "historical_confirmation_ids_read": historical_confirmations,
                  "valid_new_user_answer_transaction_verified": False,
                  "unverified_reason": "没有真实用户答案；不把问题原因或历史问题文本当作用户业务决定",
                  "source_rows_unchanged": True, "model_requests": 0, "database_writes": 0,
                  "database_rolled_back": True}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.run_id, args.output)
