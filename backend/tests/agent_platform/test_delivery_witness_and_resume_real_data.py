"""回放 Run89 的原始终止回执和交付状态，在隔离库实际恢复后续节点。"""

from copy import deepcopy
from datetime import timedelta
import asyncio
import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db import db_utc_now
from core.db.model_defs import AgentApproval, AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform import checkpoint_storage as storage, test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _revalidate_program_checkpoint
from modules.agent_platform.service import _restore_checkpoint_node
from modules.agent_platform.sources import SourceSnapshot
from modules.agent_platform.test_generation_schemas import REVIEW_SCHEMA
from modules.agent_platform.generation_review_witnesses import witness_batches, witness_gaps, witness_review_input
from jsonschema import validate


@pytest.fixture(scope="module")
def recorded():
    data = load_recording("run89_terminal_real.json.gz")
    assert data["state"]["delivery"]["run_id"] == data["run"]["id"] == 89
    return data


def load_recording(filename):
    path = Path(__file__).with_name("fixtures") / filename
    data = json.loads(gzip.decompress(path.read_bytes()))
    checksum = data.pop("sha256")
    assert action_state_hash(data) == checksum
    return data


@pytest.fixture
def repo(recorded):
    engine = create_engine("sqlite://")
    for model in (AgentRun, AgentNodeRun, AgentRunEvent, storage.AgentArtifactBlob,
                  storage.AgentJournalBranch, storage.AgentActionReceiptRecord):
        model.__table__.create(engine)
    with Session(engine) as db:
        run = AgentRun(**deepcopy(recorded["run"]))
        node = AgentNodeRun(**deepcopy(recorded["node"]))
        db.add_all([run, node, storage.AgentJournalBranch(**recorded["branch"]),
                    storage.AgentActionReceiptRecord(**recorded["terminal"])])
        db.flush()
        store = storage.store_for_node(db, node)
        checkpoint = storage.compact_checkpoint(node)
        assert store.put(recorded["state"]) == checkpoint["state_ref"]
        assert store.put(run.input_payload) == checkpoint["input_ref"]
        assert store.put(recorded["terminal_result"]) == recorded["terminal"]["record"]["result_ref"]
        db.commit()
        yield AgentRunRepository(db)
    engine.dispose()


def next_attempt(repo, parent):
    run = AgentRun(user_id=parent.user_id, project_id=parent.project_id, parent_run_id=parent.id,
                   workflow_definition_id=parent.workflow_definition_id, input_payload=deepcopy(parent.input_payload),
                   run_context=deepcopy(parent.run_context), status="running", claim_token=new_claim_token(),
                   lease_expires_at=db_utc_now() + timedelta(minutes=5))
    repo.db.add(run)
    repo.db.flush()
    return run


def test_terminal_revalidation_publishes_for_each_descendant_without_changing_evidence(recorded, repo):
    parent = repo.db.get(AgentRun, recorded["run"]["id"])
    node = repo.db.get(AgentNodeRun, recorded["node"]["id"])
    for _ in range(2):
        child = next_attempt(repo, parent)
        node = _restore_checkpoint_node(repo, checkpoint=node, run_id=child.id)
        assert _revalidate_program_checkpoint(repo, child, node, "test_generation_coordinator") == {"completed": True}
        delivered = child.run_context["artifacts"]["test_generation"]
        assert delivered == {**recorded["state"]["delivery"], "run_id": child.id}
        assert storage.hydrate_checkpoint_state(repo.db, node)["delivery"] == recorded["state"]["delivery"]
        assert repo.list_events(run_id=child.id, limit=10)[-1].payload["model_requests_sent"] == 0
        parent = child


def test_unrelated_run_and_changed_content_cannot_reuse_accepted_delivery(recorded, repo):
    parent = repo.db.get(AgentRun, recorded["run"]["id"])
    child = next_attempt(repo, parent)
    state = deepcopy(recorded["state"])
    ctx = ToolExecutionContext(db=repo.db, user_id=child.user_id, project_id=child.project_id,
        run_id=child.id, node_key="agent_network", run_input=child.input_payload,
        action_state=state, action_input=child.input_payload)
    args = {"input_payload": child.input_payload, "output": {"completed": True}}
    child.parent_run_id = None
    with pytest.raises(ValueError, match="交付内容"):
        generation.validate_generation_resolution(ctx, args)
    child.parent_run_id = parent.id
    state["delivery"]["test_cases"].pop()
    generation.refresh_generation_state(state)
    with pytest.raises(ValueError, match="交付内容"):
        generation.validate_generation_resolution(ctx, args)


def test_original_approval_alone_cannot_publish_a_new_delivery(recorded, repo):
    run = repo.db.get(AgentRun, recorded["run"]["id"])
    state = deepcopy(recorded["state"])
    state.update(completed=False, delivery=None, requires_witness_review=True)
    generation.refresh_generation_state(state)
    ctx = ToolExecutionContext(db=repo.db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key="agent_network", run_input=run.input_payload,
        action_state=state, action_input=run.input_payload)
    assert not generation._can_finish(state)
    with pytest.raises(OutputValidationError, match="合法目标"):
        generation.finish_generation(ctx, {})
    assert not ctx.artifacts and not ctx.action_state["completed"]


def test_actual_steps_replay_blocks_false_approval_and_keeps_supported_witnesses(recorded, repo):
    state = deepcopy(recorded["state"])
    raw = state["review"]["raw_result"]
    state.update(completed=False, review=None, delivery=None)
    generation.refresh_generation_state(state)
    assert action_state_hash(state) == recorded["prior_review_state_hash"]
    run = repo.db.get(AgentRun, recorded["run"]["id"])
    ctx = ToolExecutionContext(db=repo.db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key="agent_network", run_input=run.input_payload,
        action_state=state, action_input=run.input_payload)
    payload = generation._review_input(state)
    assert generation._validate_review(state, payload, raw)["verdict"] == "approved"
    with pytest.raises(OutputValidationError, match="witness_checks"):
        generation._apply_review(ctx, payload, raw)
    replay = recorded["witness_replay"]
    request = witness_review_input(payload, raw)
    batch = next(batch for batch in witness_batches(request) if action_state_hash(batch) == replay["request_sha256"])
    receipt = replay["response"]
    gaps = witness_gaps(batch, receipt)
    assert any("课文重读" in reason for reason in gaps["EV-0012/11"])
    assert "EV-0012/1" not in gaps
    decision = generation._apply_review(ctx, payload, raw, witness_receipt=receipt)
    assert decision["verdict"] == "changes_requested"
    assert any(issue["kind"] == "coverage_gap" and "课文重读" in issue["reason"]
               for issue in ctx.action_state["open_issues"].values())
    assert not generation._can_finish(ctx.action_state)

    positive_rows = [row for row in receipt["witness_checks"] if not row["gaps"]]
    partial = {"witness_checks": positive_rows}
    with pytest.raises(OutputValidationError, match="遗漏"):
        witness_gaps(batch, partial, stop_on_gap=True)
    positives = {row["claim_id"]: batch["claims"][row["claim_id"]] for row in positive_rows}
    assert not witness_gaps({**batch, "claims": positives}, partial)
    damaged = deepcopy(receipt)
    anchor = next(row["verified"][0] for row in damaged["witness_checks"] if row["verified"])
    anchor["action_step"] = len(batch["cases"][anchor["case_id"]]["steps"]) + 1
    with pytest.raises(OutputValidationError, match="真实步骤"):
        witness_gaps(batch, damaged)


def test_other_document_actual_review_passes_through_append_review_finish_and_revalidation():
    """Run78 的家长扫码条款与原用例局部回放；两次模型原始答复均按真实请求指纹读取。"""
    data = load_recording("run78_witness_replay_real.json.gz")
    assert (data["origin_run"], data["origin_case"]) == (78, "TC-0060")
    source = "\n".join(data["source_lines"])
    args = {"requirement": source, "case_budget": 1, "requirement_doc_id": None, "batch_case_limit": 1}
    engine = create_engine("sqlite://")
    for model in (AgentRun, AgentApproval):
        model.__table__.create(engine)
    with Session(engine) as db:
        run = AgentRun(user_id=data["user_id"], project_id=data["project_id"],
            workflow_definition_id=data["workflow_definition_id"], status="running", started_at=db_utc_now(),
            input_payload=args, run_context={"artifacts": {"requirement_source": SourceSnapshot.from_text(source).to_dict()}})
        db.add(run)
        db.flush()
        receipts = list(data["receipts"])

        async def read_recorded_review(*, agent_key, input_payload, validate_candidate):
            assert agent_key == "test_generation_reviewer"
            receipt = receipts.pop(0)
            assert receipt["input_hash"] == action_state_hash(input_payload)
            validate(receipt["response"], REVIEW_SCHEMA)
            validate_candidate(receipt["response"])
            return deepcopy(receipt["response"])

        ctx = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key="agent_network", run_input=args, invoke_agent=read_recorded_review)
        state = generation.initialize_generation_state(ctx, args)
        actual_source = state["evidence_catalog"]["items"][0]["evidence_id"]
        generation.append_cases(ctx, {"cases": [{**data["case"], "coverage_ids": [], "source_refs": [actual_source]}]})
        assert asyncio.run(generation.request_review(ctx, {}))["verdict"] == "approved"
        assert receipts == []
        output = generation.finish_generation(ctx, {})
        assert generation.validate_generation_resolution(ctx, {"input_payload": args, "output": output}) == output
        delivered = ctx.artifacts["test_generation"]
        assert delivered["test_cases"][0]["steps"] == data["case"]["steps"]
        assert delivered["final_review"]["witness_checks_sha256"] == action_state_hash(data["receipts"][1]["response"])
    engine.dispose()
