"""真实AP15原问答的当前协议CPU映射；隔离SQLite验恢复，不冒充新线上批准。"""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from core.db.database import engine as source_engine
from core.db.model_defs import (
    AgentActionReceiptRecord, AgentApproval, AgentArtifactBlob, AgentJournalBranch,
    AgentNodeRun, AgentRun, AgentRunEvent, AgentValidatedCallRecord,
)
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.checkpoint_storage import hydrate_checkpoint_state
from modules.agent_platform.conflict_confirmation import apply_conflict_decision, pending_conflicts
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform import test_generation_agent as generation
from test_single_agent_direct_protocol_real_data import append_real_candidates, model_input, real


@pytest.fixture(scope="module")
def actual_pending_source(real):
    """Run61首次执行时AP15尚未决定，按UTC冻结来源而非从Run63删批准。"""
    archived = Path(__file__).with_name("fixtures") / "run61_before_approval_real.json"
    pause = json.loads(archived.read_text(encoding="utf-8-sig"))
    commands = []

    def only_select(connection, cursor, statement, parameters, context, executemany):
        command = statement.lstrip().split(None, 1)[0].upper()
        assert command == "SELECT", f"人工确认CPU验证禁止线上数据库写入：{command}"
        commands.append(command)

    protect_run = text("SELECT SHA2(CAST(input_payload AS CHAR),256), SHA2(CAST(run_context AS CHAR),256),"
                       " SHA2(CAST(output_payload AS CHAR),256),status FROM agent_runs WHERE id=61")
    protect_approval = text("SELECT SHA2(CAST(request_payload AS CHAR),256), SHA2(CAST(decision_payload AS CHAR),256),"
                            " status,decided_at,decided_by_user_id FROM agent_approvals WHERE id=15")
    with source_engine.connect() as connection:
        connection.exec_driver_sql("START TRANSACTION READ ONLY")
        event.listen(connection, "before_cursor_execute", only_select)
        try:
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
                protected = [tuple(db.execute(query).one()) for query in (protect_run, protect_approval)]
                run, original = db.get(AgentRun, 61), db.get(AgentApproval, 15)
                assert (original.run_id, original.node_run_id, original.status) == (61, 413, "approved")
                assert original.decided_by_user_id == run.user_id
                assert run.started_at is not None and original.decided_at > run.started_at
                assert pause["run_id"] == run.id and pause["node_id"] == original.node_run_id
                assert pause["approval_request"] == original.request_payload
                arguments = {key: deepcopy(run.input_payload[key])
                             for key in ("requirement", "requirement_doc_id", "case_budget", "batch_case_limit")}
                context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                    run_id=run.id, node_key="agent_network", run_input=arguments)
                generation.initialize_generation_state(context, arguments)
                assert {item["approval_id"] for item in context.action_state["approved_decisions"]} == {13}
                for item in context.action_state["evidence_catalog"]["items"]:
                    if item["kind"] != "image":
                        assert context.action_state["evidence_reads"][item["evidence_id"]] == {
                            "content_hash": generation.hashlib.sha256(item["text"].encode("utf-8")).hexdigest(),
                            "total_chars": len(item["text"]), "ranges": [[0, len(item["text"])]],
                            "provided_by": "initial_model_material"}
                assert "read_source" not in generation.observe_generation_state(context)["available_actions"]
                images = {ref["evidence_id"] for item in real["current_cases_before"]
                          for ref in item["content"]["source_refs"] if not ref["quote"]}
                pages = {item["page_number"] for item in context.action_state["evidence_catalog"]["items"]
                         if item["evidence_id"] in images}
                for page in sorted(pages):
                    generation.view_source_image(context, {"page_number": page})
                append_real_candidates(context, real)
                question = deepcopy(original.request_payload["items"][0])
                assert len(original.request_payload["items"]) == 1
                answer = original.decision_payload["conflict_answers"][question["id"]]
                assert answer == next(item["resolution"] for item in real["current_initial_state"]["approved_decisions"]
                                      if item["approval_id"] == 15)
                refs = []
                for side in question["conflict"]["sides"]:
                    page = int(re.search(r"-P([0-9]+)-", side["fact_id"]).group(1))
                    # 只容纳PDF换行空白，命中后保留真实原文子串，不改写原问答。
                    pattern = re.compile(r"\s*".join(re.escape(character) for character in side["quote"] if not character.isspace()))
                    matches = [(item, pattern.search(item["text"])) for item in context.action_state["evidence_catalog"]["items"]
                               if item["kind"] == "text" and item.get("page_number") == page]
                    item, match = next((item, match) for item, match in matches if match is not None)
                    refs.append({"evidence_id": item["evidence_id"], "quote": match.group(0)})
                result = generation.report_issue(context, {"kind": question["kind"], "target_case_ids": [],
                    "coverage_ids": [], "reason": question["reason"], "source_refs": model_input({"source_refs": refs})["source_refs"]})
                issue_id = result["issue_id"]
                generation.request_confirmation(context, {"issues": []})
                items = pending_conflicts(context.action_state)
                assert items[0]["reason"] == question["reason"]
                metadata = {key: deepcopy(getattr(run, key)) for key in (
                    "id", "user_id", "project_id", "workflow_definition_id", "created_at", "started_at")}
                result = {"metadata": metadata, "input": arguments, "state": deepcopy(context.action_state),
                    "original_question": question, "original_answer": answer, "issue_id": issue_id,
                    "original_request": deepcopy(original.request_payload), "now": original.decided_at,
                    "approval_id": original.id, "node_id": original.node_run_id,
                    "budget": deepcopy(pause["checkpoint"]["budget"]),
                    "frozen_context": {key: deepcopy(pause["run_context"][key]) for key in (
                        "logical_task_id", "deadline_at", "stage_deadline_at") if key in pause["run_context"]},
                    "source": deepcopy(context.action_state["source_ref"])}
                assert [tuple(db.execute(query).one()) for query in (protect_run, protect_approval)] == protected
                assert not db.new and not db.dirty and not db.deleted
        finally:
            connection.rollback()
            event.remove(connection, "before_cursor_execute", only_select)
    assert commands and set(commands) == {"SELECT"}
    return result


@pytest.fixture
def pending_approval(actual_pending_source):
    source = actual_pending_source
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (AgentRun, AgentNodeRun, AgentApproval, AgentRunEvent, AgentArtifactBlob,
                  AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord):
        model.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        assert db.get_bind().dialect.name == "sqlite"
        claim = new_claim_token()
        context = {**deepcopy(source["frozen_context"]), "artifacts": {"requirement_source": source["source"]}}
        run = AgentRun(**source["metadata"], status="running", claim_token=claim,
            lease_expires_at=datetime.utcnow() + timedelta(hours=1),
            input_payload=deepcopy(source["input"]), run_context=context, output_payload={})
        node = AgentNodeRun(id=source["node_id"], run_id=run.id, node_key="agent_network", node_type="agent_network",
            status="running", attempt=1, input_payload=deepcopy(source["input"]), output_payload={}, sdk_state={})
        db.add_all([run, node])
        db.commit()
        journal = AgentActionJournal(AgentRunRepository(db), run_id=run.id, node_run_id=node.id, expected_claim_token=claim)
        header = journal.init(state=source["state"], input_hash=action_state_hash(source["input"]), budget=source["budget"])
        approval = AgentApproval(id=source["approval_id"], run_id=run.id, node_run_id=node.id, status="pending",
            request_payload={"kind": "source_conflicts", "items": pending_conflicts(source["state"]),
                "paused_at": source["original_request"]["paused_at"], "state_hash": header["state_hash"]}, decision_payload={})
        run.status = node.status = "waiting_approval"
        db.add_all([run, node, approval])
        db.commit()
        yield SimpleNamespace(db=db, run=run, node=node, approval=approval, source=source,
                              answers={source["issue_id"]: source["original_answer"]})
        db.rollback()
    engine.dispose()


def _state(resource):
    return hydrate_checkpoint_state(resource.db, resource.node)


def _protected(resource):
    return {"state": action_state_hash(_state(resource)), "checkpoint": deepcopy(resource.node.sdk_state),
            "run_context": deepcopy(resource.run.run_context),
            "artifact_count": resource.db.scalar(select(func.count()).select_from(AgentArtifactBlob)),
            "receipt_count": resource.db.scalar(select(func.count()).select_from(AgentActionReceiptRecord))}


def test_real_ap15_answer_applies_atomically_without_approving_cases_or_resetting_budget(pending_approval, record_property):
    r = pending_approval
    before = _state(r)
    header = deepcopy(r.node.sdk_state["action_checkpoint"])
    apply_conflict_decision(r.approval, r.node, r.run, r.answers, now=r.source["now"])
    r.db.commit()
    r.db.expire_all()
    after = _state(r)
    assert after["cases"] == before["cases"] and after["coverage_plan"] == before["coverage_plan"]
    assert after["projection"]["versions"] == before["projection"]["versions"]
    assert r.node.sdk_state["action_checkpoint"]["budget"] == header["budget"]
    assert r.node.sdk_state["action_checkpoint"]["journal_ref"] == header["journal_ref"]
    assert after["open_issues"][r.source["issue_id"]]["status"] == "pending_review"
    assert after["open_issues"][r.source["issue_id"]]["assessment_status"] == "pending_recheck"
    assert after["confirmation_requested"] == [] and after["review"] is None
    assert after["completed"] is False and after["delivery"] is None and not generation._can_finish(after)
    assert r.approval.status == "pending"  # 表单状态由调用服务处理；函数不自动批准用例。
    assert len(after["approved_decisions"]) == len(before["approved_decisions"]) + 1
    approved = after["approved_decisions"][-1]
    assert approved["evidence_id"] == f"AP-{r.approval.id}-1"
    assert approved["resolution"] == r.source["original_answer"]
    assert approved["question"]["reason"] == r.source["original_question"]["reason"]
    assert (approved["approval_id"], approved["origin_run_id"], approved["origin_node_id"], approved["decided_by_user_id"]) == (
        15, r.run.id, r.node.id, r.run.user_id)
    assert approved["source_ref"] == before["source_ref"] == r.source["source"]
    assert approved["approval_state_hash"] == header["state_hash"]
    assert approved["decided_at"] == r.source["now"].isoformat()
    assert after["evidence_reads"][approved["evidence_id"]]["provided_by"] == "initial_model_material"
    assert after["evidence_reads"][approved["evidence_id"]]["ranges"] == [[0, len(approved["text"])]]
    assert generation._source_refs(after, [approved["evidence_id"]]) == [
        {"evidence_id": approved["evidence_id"], "quote": approved["resolution"]}]
    assert after["conflict_confirmations"][r.source["issue_id"]]["resolution"] == r.source["original_answer"]
    # 使用真实审批落库时刻检验当前函数的顺延算术；不冒称重演旧服务的实际人工等待扣除。
    elapsed = (r.source["now"] - datetime.fromisoformat(r.source["original_request"]["paused_at"])).total_seconds()
    assert r.node.sdk_state["confirmation_wait_seconds"] == elapsed
    assert r.run.run_context["confirmation_wait_seconds"] == elapsed
    for key in ("deadline_at", "stage_deadline_at"):
        assert datetime.fromisoformat(r.run.run_context[key]) == datetime.fromisoformat(r.source["frozen_context"][key]) + timedelta(seconds=elapsed)
    record_property("provenance", "真实Run61/AP15原问答机械映射；隔离当前schema4 Journal，不是新线上批准")
    record_property("actual_answer", r.source["original_answer"])
    record_property("historical_approval_decided_at_used_as_cpu_input", approved["decided_at"])
    record_property("supplied_decision_interval_seconds", elapsed)
    record_property("unchanged_case_count", len(after["cases"]["items"]))
    record_property("unchanged_budget_attempts", header["budget"]["charged"]["attempted_requests"])


@pytest.mark.parametrize("corruption", ["stale_hash", "tampered_item", "missing_answer", "extra_answer", "approval_node"])
def test_invalid_confirmation_rejected_without_state_or_artifact_changes(pending_approval, corruption):
    r = pending_approval
    answers = deepcopy(r.answers)
    if corruption == "stale_hash":
        r.approval.request_payload = {**r.approval.request_payload, "state_hash": r.source["original_request"]["state_hash"]}
    elif corruption == "tampered_item":
        request = deepcopy(r.approval.request_payload)
        request["items"][0]["reason"] += " "
        r.approval.request_payload = request
    elif corruption == "missing_answer":
        answers = {}
    elif corruption == "extra_answer":
        answers[r.source["original_question"]["id"]] = r.source["original_answer"]
    else:
        r.approval.node_run_id = r.node.id + 1
    r.db.flush()
    before = _protected(r)
    with pytest.raises(ValueError, match="状态已变化|表单内容|逐项填写|待处理批准"):
        apply_conflict_decision(r.approval, r.node, r.run, answers, now=r.source["now"])
    assert _protected(r) == before


def test_same_pending_form_cannot_apply_twice_after_current_state_cas(pending_approval):
    r = pending_approval
    apply_conflict_decision(r.approval, r.node, r.run, r.answers, now=r.source["now"])
    r.db.commit()
    before = _protected(r)
    with pytest.raises(ValueError, match="状态已变化"):
        apply_conflict_decision(r.approval, r.node, r.run, r.answers, now=r.source["now"])
    assert _protected(r) == before
