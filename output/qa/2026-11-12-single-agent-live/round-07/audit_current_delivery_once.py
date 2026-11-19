"""只读验收当前单主 Agent 成功交付，重建两次结果，不派发模型或任务。"""

from copy import deepcopy
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select, text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import hydrate_action_checkpoint
from modules.agent_platform.registry import ToolExecutionContext, execution_source_signature, runtime_registry_signature
from modules.agent_platform.test_generation_agent import validate_generation_resolution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-case-count", type=int)
    args = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, args.run_id)
        assert run is not None and run.status == "success", "当前真实 Run 尚未成功，不能验收交付"
        frozen = run.run_context["execution_contract"]
        assert frozen["hash"] == action_state_hash({key: value for key, value in frozen.items() if key != "hash"})
        current_signature = runtime_registry_signature()
        assert current_signature == run.run_context["runtime_registry_signature"] == frozen["runtime_registry_signature"], "当前代码与真实成功运行的冻结签名不同，不能混作当前验收"
        node_id = db.scalar(select(func.max(AgentNodeRun.id)).where(
            AgentNodeRun.run_id == run.id, AgentNodeRun.status == "success",
            AgentNodeRun.node_key == "agent_network"))
        node = db.get(AgentNodeRun, node_id) if node_id is not None else None
        assert node is not None
        checkpoint = hydrate_action_checkpoint(db, node)
        state = checkpoint["state"]
        original = action_state_hash([run.input_payload, run.run_context, run.output_payload, node.sdk_state])
        assert state["completed"] and state["delivery"] is not None
        finish = [r for r in checkpoint["receipts"].values() if r["tool_key"] == "finish_generation"]
        completed_finish = [receipt for receipt in finish if receipt["status"] == "completed"]
        assert len(completed_finish) == 1
        artifact = run.run_context["artifacts"]["test_generation"]
        cases = artifact["test_cases"]
        assert len(cases) == run.input_payload["case_budget"]
        if args.expected_case_count is not None:
            assert len(cases) == args.expected_case_count
        assert len({case["case_id"] for case in cases}) == len(cases)
        outputs, artifacts, state_hashes = [], [], []
        for _ in range(2):
            # 两次从序列化结构恢复，确保不借用上次的内存产物。
            restored = json.loads(json.dumps(state, ensure_ascii=False))
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload),
                action_input=deepcopy(run.input_payload), action_state=restored,
                confirmed_conflicts=deepcopy(restored["conflict_confirmations"]))
            before = action_state_hash(restored)
            result = validate_generation_resolution(context, {
                "output": {"completed": True}, "input_payload": deepcopy(run.input_payload)})
            assert result == run.output_payload["result"] == node.output_payload
            assert context.artifacts["test_generation"] == artifact
            assert action_state_hash(context.action_state) == before
            assert context.invoke_agent is None and context.action_journal is None
            outputs.append(action_state_hash(result))
            artifacts.append(action_state_hash(context.artifacts["test_generation"]))
            state_hashes.append(before)
        assert len(set(outputs)) == len(set(artifacts)) == len(set(state_hashes)) == 1
        assert not db.new and not db.dirty and not db.deleted
        assert original == action_state_hash([run.input_payload, run.run_context, run.output_payload, node.sdk_state])
        report = {
            "run_id": run.id, "node_id": node.id, "status": run.status,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(cases), "unique_case_ids": len({c["case_id"] for c in cases}),
            "finish_receipt_count": len(completed_finish), "failed_finish_receipt_count": sum(receipt["status"] == "failed" for receipt in finish), "serialized_readback_passes": 2,
            "result_sha256": outputs[0], "artifact_sha256": artifacts[0],
            "current_source_plan_generation_review_revalidated": True,
            "saved_result_and_artifact_exact": True, "domain_state_unchanged": True,
            "database_writes": 0, "model_requests_sent": 0,
            "frozen_contract_sha256": frozen["hash"], "runtime_registry_signature": current_signature,
            "execution_source_signature": execution_source_signature(), "actual_frozen_signature_matches_current_code": True,
            "scope": "真实成功交付的确定性重建和读回；未注入终止窗口崩溃，不代表人工业务验收"}
        db.rollback()
    path = args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
