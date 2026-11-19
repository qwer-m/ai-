"""只读真实失败与成功行动，验证恢复信息的身份、消解及 SDK 当前投影。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent
from agents.items import ItemHelpers
from agents.run_config import CallModelData, ModelInputData
from sqlalchemy.orm import Session

from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash, project_action_failures
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _runner_input
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine


def checkpoint(path):
    with sqlite3.connect("file:" + path.resolve().as_posix() + "?mode=ro", uri=True) as connection:
        return json.loads(connection.execute("SELECT sdk_state FROM agent_node_runs ORDER BY id DESC LIMIT 1").fetchone()[0])["action_checkpoint"]


def verify(failed_path: Path, progressed_path: Path):
    paths = [failed_path, progressed_path]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    failed = checkpoint(failed_path)
    visible = project_action_failures(failed, state=failed["state"])
    assert visible and visible[-1]["tool_key"] == "route_planning_scopes"
    assert visible[-1]["arguments"] == {}
    assert visible[-1]["state_matches_failure"]
    assert visible[-1]["validated_child_call_counts"]["test_planning_scope_router"] > 0
    progressed = checkpoint(progressed_path)
    receipts = list(progressed["receipts"].values())
    closed = []
    for index, receipt in enumerate(receipts):
        if receipt["status"] != "failed":
            continue
        successor = next((item for item in receipts[index + 1:] if item["status"] == "completed"
                          and item["tool_key"] == receipt["tool_key"]
                          and item["arguments_hash"] == receipt["arguments_hash"]), None)
        if successor is None:
            continue
        # 按真实历史前缀比较，既不改成功状态，也不制造新回执。
        before = {**progressed, "receipts": {item["tool_call_id"]: item for item in receipts[:index + 1]}}
        end = receipts.index(successor)
        after = {**progressed, "receipts": {item["tool_call_id"]: item for item in receipts[:end + 1]}}
        assert receipt["tool_call_id"] in {item["tool_call_id"] for item in project_action_failures(before, state=progressed["state"])}
        assert receipt["tool_call_id"] not in {item["tool_call_id"] for item in project_action_failures(after, state=progressed["state"])}
        closed.append({"failed_call_id": receipt["tool_call_id"], "completed_call_id": successor["tool_call_id"], "tool_key": receipt["tool_key"]})
    assert closed, "必须有真实失败后同参数行动成功的历史，不以假回执代替"
    engine = sqlite_engine(failed_path)
    try:
        with Session(engine) as db:
            node = db.query(AgentNodeRun).order_by(AgentNodeRun.id.desc()).first()
            run = db.get(AgentRun, node.run_id)
            repo = AgentRunRepository(db)
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload), action_input=deepcopy(node.input_payload),
                action_state=deepcopy(failed["state"]), artifacts=deepcopy(run.run_context["artifacts"]),
                action_journal=AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=run.claim_token))
            config = next(spec["runtime_config"] for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == "test_generation_coordinator")
            state_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
            observation = state_filter.observe()
            initial = _runner_input(execution_context=context, input_payload=observation, runtime_config=config)
            state_filter.bind_initial_input(initial, observation)
            original = ItemHelpers.input_to_new_input_list(initial)
            filtered = state_filter(CallModelData(agent=Agent(name="真实行动投影校验"), context=context,
                model_data=ModelInputData(input=original, instructions="")))
            assert json.loads(filtered.input[0]["content"])["recent_action_errors"] == visible
            assert original == ItemHelpers.input_to_new_input_list(initial)
            assert context.action_state == failed["state"] and not db.new and not db.dirty and not db.deleted
    finally:
        engine.dispose()
    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return {"status": "passed", "model_requests": 0, "online_database_writes": 0,
            "source_sha256": hashes, "current_failed_actions": visible, "actual_failure_success_pairs": closed,
            "sdk_filter_reads_current_journal": True, "original_state_and_evidence_unchanged": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-sqlite", type=Path, required=True)
    parser.add_argument("--progressed-sqlite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("报告已存在，请保留历史证据")
    result = verify(args.failed_sqlite, args.progressed_sqlite)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "closed_real_failures": len(result["actual_failure_success_pairs"])}, ensure_ascii=False))
