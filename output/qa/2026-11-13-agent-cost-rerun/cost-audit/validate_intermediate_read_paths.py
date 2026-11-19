"""对 Run59 真实行动前快照比较只读视图，保留相同完整验收。"""

from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter

from prepare_read_path_draft import (
    ROOT, generation_draft, source_draft, module_from_text, readonly,
)
from prepare_artifact_decode_draft import draft_source
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform import test_generation_agent as original_generation


def main():
    source_path, _, source_text = source_draft()
    module_from_text("modules.agent_platform._qa_readpath_source", source_path, source_text)
    generation_path, _, generation_text = generation_draft()
    generation = module_from_text("modules.agent_platform._qa_readpath_generation", generation_path,
        generation_text.replace("from .test_generation_source_agent import", "from ._qa_readpath_source import"))
    artifact_path, _, artifact_text = draft_source()
    storage = module_from_text("modules.agent_platform._qa_artifact_decode", artifact_path, artifact_text)
    original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11"
    snapshot = json.loads((original / "final-snapshot.json").read_text(encoding="utf-8"))
    run = snapshot["run"]
    initial_artifacts = json.loads((original / "created-run.json").read_text(encoding="utf-8"))["run"]["run_context"]["artifacts"]
    checkpoint = snapshot["expanded_checkpoints"]["404"]
    receipts = {value["started_sequence"]: value for value in checkpoint["receipts"].values()}
    reports = []
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        store = storage.ArtifactStore(db, user_id=1, project_id=8)
        for index in (1, 5, 15, 17):
            receipt = receipts[index]
            ref = store._ref(receipt["pre_state_hash"])
            state = store.get(ref)
            context = ToolExecutionContext(db=db, user_id=run["user_id"], project_id=run["project_id"],
                run_id=run["id"], node_key="agent_network", run_input=run["input_payload"],
                action_input=run["input_payload"], action_state=state,
                artifacts=initial_artifacts, confirmed_conflicts=state["conflict_confirmations"])
            before = action_state_hash([context.action_state, context.action_input, context.artifacts])
            started = perf_counter()
            expected = original_generation.observe_generation_state(context)
            old_seconds = perf_counter() - started
            started = perf_counter()
            actual = generation.observe_generation_state(context)
            new_seconds = perf_counter() - started
            assert actual == expected
            assert before == action_state_hash([context.action_state, context.action_input, context.artifacts])
            frozen = ToolExecutionContext(db=db, user_id=run["user_id"], project_id=run["project_id"],
                run_id=run["id"], node_key="agent_network", run_input=readonly(run["input_payload"]),
                action_input=readonly(run["input_payload"]), action_state=readonly(state),
                artifacts=readonly(initial_artifacts),
                confirmed_conflicts=readonly(state["conflict_confirmations"]))
            assert generation.observe_generation_state(frozen) == expected
            reports.append({"action_sequence": index, "before_tool": receipt["tool_key"],
                "state_sha256": ref["sha256"], "result_sha256": action_state_hash(actual),
                "previous_seconds": old_seconds, "draft_seconds": new_seconds,
                "result_exact": True, "source_unchanged": True, "nested_write_guard_passed": True})
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    report = {"run_id": 59, "snapshots": reports, "production_code_modified": False,
        "model_requests_sent": 0, "business_database_writes": 0,
        "scope": "真实来源待治理、支持复核待执行、支持修订后、布局接受前四个中间快照；未代替完整回归。"}
    Path(__file__).with_name("read-path-intermediate-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
