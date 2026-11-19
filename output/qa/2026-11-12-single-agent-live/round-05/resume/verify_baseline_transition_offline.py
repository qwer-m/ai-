"""真实审批前快照在独立内存库验证规划转换；不读写真实业务表。"""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.action_journal import action_state_hash


def main():
    folder = Path(__file__).resolve().parent
    before = json.loads((folder.parent / "final-snapshot.json").read_text(encoding="utf-8"))
    after = json.loads((folder / "final-snapshot.json").read_text(encoding="utf-8"))
    state = next(iter(before["expanded_checkpoints"].values()))["state"]
    confirmed = next(iter(after["expanded_checkpoints"].values()))["state"]["conflict_confirmations"]
    run_data = before["run"]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    try:
        with Session(engine) as db:
            run = AgentRun(id=run_data["id"], user_id=run_data["user_id"], project_id=run_data["project_id"],
                           workflow_definition_id=run_data["workflow_definition_id"], status=run_data["status"],
                           input_payload=deepcopy(run_data["input_payload"]), run_context=deepcopy(run_data["run_context"]))
            db.add(run)
            db.commit()
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key="agent_network", run_input=deepcopy(run.input_payload), action_input=deepcopy(state["prepared_input"]),
                action_state=deepcopy(state["planning"]), artifacts=deepcopy(state["source"]["artifacts"]),
                confirmed_conflicts=deepcopy(confirmed))
            previous = deepcopy(context.action_state)
            prepared = planning.clear_source_authority(context)
            assert context.action_state["source_authority"] is None
            assert context.action_state["dependency_records"] == previous["dependency_records"]
            assert prepared[0]["authority_baseline"]["result"] == previous["source_authority"]["result"]
            assert semantics.restore_authority_source_review(prepared[0]["source_review"]) == previous["source_authority"]["result"]["authoritative_facts"]
            restored = json.loads(json.dumps(prepared, ensure_ascii=False))
            result = semantics.merge_authority_reconciliation(context, {
                "authoritative_facts": previous["source_semantics"]["authoritative_facts"],
                "prepared_items": restored, "reconciliation_records": [{"item_index": 0, "output": {"decisions": []}}],
            })
            assert result == previous["source_authority"]["result"]
            assert not planning.apply_source_authority(context, result) or context.action_state["source_authority"]["result"] == result
            assert not db.new and not db.dirty and not db.deleted
            report = {"source_run_id": 53, "source_approval_id": 13,
                "database": "isolated_sqlite_memory", "real_database_writes": 0, "model_requests": 0,
                "baseline_after_json_roundtrip_exact": True, "empty_sparse_patch_preserves_baseline": True,
                "preserved_dependency_counts": {key: len(previous["dependency_records"][key]) for key in ("ownership", "support", "support_revisions")},
                "before_source_sha256": action_state_hash(previous["source_semantics"]),
                "baseline_result_sha256": action_state_hash(result)}
            db.rollback()
    finally:
        engine.dispose()
    (folder / "baseline-transition-offline-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
