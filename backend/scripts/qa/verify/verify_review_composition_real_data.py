"""只读回放真实终审记录，验收父协调器组合接口和完整输入身份。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.test_generation_review_agent import (
    _new_review_candidates,
    apply_global_review,
    compose_review_context,
    finish_review,
    observe_review_state,
    prepare_global_review,
    resolve_review_output,
    review_input_identity,
    validate_review_resolution,
)


def rejected(operation, error_type):
    try:
        operation()
    except error_type as error:
        return str(error)
    raise AssertionError(f"真实对象副本的负向契约应被 {error_type.__name__} 拒绝")


def verify(db, run_id):
    run = db.get(AgentRun, run_id)
    assert run is not None and run.status == "success"
    repo = AgentRunRepository(db)
    node = repo.latest_node_run(run_id=run_id, node_key="resolve_final_review")
    assert node is not None and node.status == "success"
    raw = deepcopy(repo.read_node_input(node))
    saved = node.sdk_state["action_checkpoint"]["state"]
    before = action_state_hash([run.run_context, run.output_payload, node.sdk_state, node.output_payload])
    parent = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key="review_composition_readonly", run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context["artifacts"]),
    )
    parent_before = action_state_hash([parent.action_input, parent.action_state, parent.artifacts])
    fresh = compose_review_context(parent, input_payload=raw)
    restored = compose_review_context(parent, input_payload=raw, state=saved)
    assert resolve_review_output(restored) == node.output_payload
    assert validate_review_resolution(restored, {
        "input_payload": raw, "output": {"completed": True},
    }) == node.output_payload
    observation = observe_review_state(fresh)
    # 本回放仅接受真实记录本身没有修复的运行，不能伪造修复候选补足分支覆盖。
    assert observation["can_review_all_cases"] and saved["generation_version"] == 0
    action = prepare_global_review(fresh, {})
    assert action["input_sha256"] == action_state_hash(action["items"])
    assert action_state_hash(action["items"][0]) == saved["global_review"]["input_sha256"]
    apply_global_review(fresh, action, [deepcopy(saved["global_review"]["output"])])
    assert resolve_review_output(fresh) == node.output_payload
    repeated_global = rejected(lambda: prepare_global_review(fresh, {}), OutputValidationError)
    finish_review(fresh, {})
    assert validate_review_resolution(fresh, {
        "input_payload": raw, "output": {"completed": True},
    }) == node.output_payload

    identity_kwargs = {key: raw[key] for key in ("generation_inputs", "authoritative_facts")}
    item = raw["review_inputs"][0]
    identity = review_input_identity(item, **identity_kwargs)
    dimensions = []
    for field in ("review_facts", "test_design_items", "test_cases", "case_fact_bindings"):
        damaged = deepcopy(item)
        assert damaged[field]
        damaged[field].pop()
        assert review_input_identity(damaged, **identity_kwargs) != identity
        dimensions.append(field)
    damaged = deepcopy(item)
    damaged["review_batch"]["case_ids"].pop()
    assert review_input_identity(damaged, **identity_kwargs) != identity
    dimensions.append("review_batch.case_ids")
    batch_id = item["review_batch"]["generation_batch_ids"][0]
    damaged_kwargs = deepcopy(identity_kwargs)
    source = next(value for value in damaged_kwargs["generation_inputs"] if value["batch"]["batch_id"] == batch_id)
    source.pop("requirement")
    assert review_input_identity(item, **damaged_kwargs) != identity
    dimensions.append("generation_inputs.requirement")

    mismatch_errors = {}
    for field in ("review_facts", "test_design_items"):
        damaged = deepcopy(raw)
        damaged["review_inputs"][0][field].pop()
        mismatch_errors[field] = rejected(
            lambda: compose_review_context(parent, input_payload=damaged), ValueError,
        )
    damaged = deepcopy(raw)
    damaged["generation_inputs"][0]["authoritative_facts"][0]["source_anchor"].pop("quote")
    mismatch_errors["authoritative_source_version"] = rejected(
        lambda: compose_review_context(parent, input_payload=damaged), ValueError,
    )
    current = compose_review_context(parent, input_payload=raw)
    unchanged_candidate = rejected(lambda: _new_review_candidates(
        current, deepcopy(current.action_state), item["review_batch"]["batch_id"],
        raw["generation"], [item],
    ), OutputValidationError)
    assert parent_before == action_state_hash([parent.action_input, parent.action_state, parent.artifacts])
    assert before == action_state_hash([run.run_context, run.output_payload, node.sdk_state, node.output_payload])
    return {
        "status": "passed", "observed_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run.id, "node_run_id": node.id, "batch_count": len(raw["review_inputs"]),
        "case_count": len(raw["generation"]["test_cases"]),
        "persisted_domain_output_revalidated": True, "real_global_action_replayed": True,
        "standalone_completion_revalidated": True, "parent_state_isolated": True,
        "full_review_identity_sensitive_dimensions": dimensions,
        "stale_input_rejections": mismatch_errors,
        "unchanged_candidate_rejection": unchanged_candidate,
        "repeated_global_review_rejection": repeated_global,
        "database_writes": 0, "model_requests_sent": 0, "persisted_records_unchanged": True,
        "boundary": "真实持久产物和全局动作只读回放；负向契约只删改真实对象副本。该运行没有自然修复/复审，不宣称其动作覆盖。",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = verify(db, args.run_id)
        db.rollback()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
