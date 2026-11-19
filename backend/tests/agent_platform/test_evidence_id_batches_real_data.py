"""真实Run67三次失败批次的当前ID合同回放；不调用模型，不冒充线上交付。"""

from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_agent as generation
from test_single_agent_direct_protocol_real_data import model_input, scope_state_from_real


@pytest.fixture(scope="module")
def actual_rejected_batches():
    path = Path(__file__).with_name("fixtures") / "run67_evidence_id_batches_real.json.gz"
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: value for key, value in data.items() if key != "data_sha256"}) == data["data_sha256"]
    assert data["provenance"]["run_status"] == "failed"
    assert data["provenance"]["historical_candidate_cases"] == 64
    assert data["provenance"]["historical_delivered_cases"] == 0
    assert [record["sequence"] for record in data["records"]] == [1, 7, 11]
    for record in data["records"]:
        assert action_state_hash(record["state"]) == record["state_hash"]
        assert record["original_error"]["message"] == "引用原话不在真实来源中"
    return data


@pytest.fixture(params=range(3), ids=["Run67-seq1", "Run67-seq7", "Run67-seq11"])
def rejected_batch(request, actual_rejected_batches):
    data = actual_rejected_batches
    record = data["records"][request.param]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    metadata = deepcopy(data["run_metadata"])
    for column in AgentRun.__table__.columns:
        if isinstance(column.type, DateTime) and metadata.get(column.name) is not None:
            metadata[column.name] = datetime.fromisoformat(metadata[column.name])
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        # 仅将真实归属和不可变前状态复制到隔离库；绝不恢复或修改真实Run67。
        db.add(AgentRun(**metadata, input_payload=deepcopy(data["run_input"]),
            run_context=deepcopy(data["frozen_run_context"]), output_payload={}))
        db.commit()
        context = ToolExecutionContext(db=db, user_id=metadata["user_id"], project_id=metadata["project_id"],
            run_id=metadata["id"], node_key="agent_network", run_input=deepcopy(data["run_input"]),
            action_input=deepcopy(data["run_input"]), action_state=scope_state_from_real(record["state"]),
            artifacts={"requirement_source": deepcopy(record["state"]["source_ref"])})
        yield context, record
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def test_actual_failed_append_batches_save_after_id_only_projection(rejected_batch, record_property):
    context, record = rejected_batch
    before = deepcopy(context.action_state)
    args = model_input(record["arguments"])
    result = generation.append_cases(context, args)
    assert result["case_count"] == len(before["cases"]["items"]) + len(args["cases"])
    assert len(result["created_case_ids"]) == len(args["cases"])
    for identity, existing in before["cases"]["items"].items():
        assert context.action_state["cases"]["items"][identity] == existing
    evidence = {item["evidence_id"]: item for item in context.action_state["evidence_catalog"]["items"]}
    for identity, submitted in zip(result["created_case_ids"], args["cases"]):
        saved = context.action_state["cases"]["items"][identity]["content"]
        assert saved == generation._case_content(context.action_state, submitted)
        for ref in saved["source_refs"]:
            original = evidence[ref["evidence_id"]]
            assert ref["quote"] == (original["resolution"] if original["kind"] == "approved_decision" else original["text"])
    assert context.action_state["completed"] is False
    assert context.action_state["review"] is None and "test_generation" not in context.artifacts
    if record["sequence"] == 11:
        assert result["case_count"] == 70
        assert generation.observe_generation_state(context)["automatic_action"] == "request_review"
    record_property("historical_failed_run_id", 67)
    record_property("historical_failed_sequence", record["sequence"])
    record_property("cpu_replayed_case_count", result["case_count"])
    record_property("new_model_requests_sent", 0)
    record_property("new_online_delivery", False)


def test_original_retyped_quotes_are_not_accepted_as_a_compatibility_contract(rejected_batch):
    context, record = rejected_batch
    before = action_state_hash(context.action_state)
    arguments = deepcopy(record["arguments"])
    scoped = model_input(arguments)
    for original, projected in zip(arguments["coverage_updates"], scoped["coverage_updates"]):
        original["conditions"], original["exceptions"] = projected["conditions"], projected["exceptions"]
    with pytest.raises(OutputValidationError, match="不接受对象或quote"):
        generation.append_cases(context, arguments)
    assert action_state_hash(context.action_state) == before


def test_platform_resolved_sources_preserve_pdf_glyphs_and_interleaved_mark(actual_rejected_batches):
    state = actual_rejected_batches["records"][0]["state"]
    for identity, expected in (("EV-0001", "首⻚"), ("EV-0004", "用戶"), ("EV-0007", "上⻆"), ("EV-0024", "✕")):
        refs = generation._source_refs(state, [identity])
        assert expected in refs[0]["quote"]
        original = next(item for item in state["evidence_catalog"]["items"] if item["evidence_id"] == identity)
        assert refs == [{"evidence_id": identity, "quote": original["text"]}]
