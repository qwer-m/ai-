"""真实PDF、AP和当前用户消息的隔离CPU组装，不创建线上Run或审批、不调用模型。"""

from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun, AgentNodeRun, AgentApproval, AgentArtifactBlob, KnowledgeDocument
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.generation_evidence import prepare_generation_evidence
from modules.agent_platform.generation_review_contract import required_review_source_ids
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sources import SourceSnapshot


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run77_task_requirement_source_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    assert fixture["provenance"]["source_document_id"] == 287
    assert fixture["provenance"]["model_responses_fabricated"] is False
    data = fixture["data"]
    assert action_state_hash(data) == fixture["provenance"]["data_sha256"]
    assert "29361 token" in data["user_requirement"] and "30万token验收" in data["user_requirement"]
    yield data
    assert path.read_bytes() == original


def row_values(model, value):
    result = deepcopy(value)
    for column in model.__table__.columns:
        if column.type.python_type is datetime and isinstance(result.get(column.name), str):
            result[column.name] = datetime.fromisoformat(result[column.name])
    return result


@pytest.fixture
def context(real):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    # 仅当前隔离引擎把MySQL的长文本容器编译成SQLite TEXT，真实正文及生产模型均不改。
    engine.dialect.type_compiler_instance.visit_LONGTEXT = lambda _value, **_kwargs: "TEXT"
    models = (AgentRun, AgentNodeRun, AgentApproval, AgentArtifactBlob, KnowledgeDocument)
    for model in models:
        model.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        for model, key in ((AgentRun, "runs"), (AgentNodeRun, "nodes"),
                           (AgentApproval, "approvals"), (AgentArtifactBlob, "blobs")):
            for values in real[key]:
                db.add(model(**row_values(model, values)))
        db.add(KnowledgeDocument(**real["document"]))
        db.commit()
        current = db.get(AgentRun, real["current_run_id"])
        args = deepcopy(current.input_payload)
        # 此输入是本轮真实用户消息的CPU投影，绝不声称原Run77曾在线收到此补充。
        args["requirement"] = real["user_requirement"]
        result = ToolExecutionContext(db=db, user_id=current.user_id, project_id=current.project_id,
            run_id=current.id, node_key="agent_network", run_input=deepcopy(args), action_input=deepcopy(args),
            artifacts={"requirement_source": deepcopy(current.run_context["artifacts"]["requirement_source"])})
        yield result, args
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def test_document_and_real_task_input_have_separate_immutable_sources(real, context):
    ctx, args = context
    document_before = deepcopy(ctx.db.get(KnowledgeDocument, 287).content)
    original = prepare_generation_evidence(ctx, {**args, "requirement": ""})
    supplemented = prepare_generation_evidence(ctx, args)
    assert supplemented["source_ref"] == original["source_ref"]
    assert supplemented["requirement"] == original["requirement"] == document_before
    assert ctx.db.get(KnowledgeDocument, 287).content == document_before
    assert supplemented["approved_decisions"] == original["approved_decisions"]
    assert len(supplemented["approved_decisions"]) == 5
    assert [row for row in supplemented["evidence_catalog"]["items"] if row["kind"] != "task_requirement"] == original["evidence_catalog"]["items"]
    task = [row for row in supplemented["evidence_catalog"]["items"] if row["kind"] == "task_requirement"]
    assert len(task) == 1
    task = task[0]
    assert task["evidence_id"] == "TR-0001" and task["origin"] == "run_input.requirement"
    assert task["text"] == real["user_requirement"]
    assert task["content_hash"] == hashlib.sha256(task["text"].encode()).hexdigest()
    assert task["document_id"] is task["page_number"] is None
    assert (task["user_id"], task["project_id"], task["run_id"]) == (ctx.user_id, ctx.project_id, ctx.run_id)
    assert (task["source_offset_start"], task["source_offset_end"]) == (0, len(task["text"]))
    assert ctx.db.query(AgentApproval).count() == 4


def test_current_input_is_fully_bound_and_included_in_independent_review(real, context):
    ctx, args = context
    state = generation.initialize_generation_state(ctx, args)
    assert generation._source_refs(state, ["TR-0001"]) == [{
        "evidence_id": "TR-0001", "quote": real["user_requirement"]}]
    material = generation.generation_model_material(ctx)
    assert "【TR-0001｜task_requirement｜第None页】\n" + real["user_requirement"] in material["source_text"]
    assert "不能据此编造产品功能" in material["source_role"]
    assert "本轮指定对象和范围" in material["source_role"]
    payload = generation._review_input(state)
    assert "TR-0001" in required_review_source_ids(payload)
    assert len(required_review_source_ids(payload)) == 20
    item = next(row for row in payload["evidence_catalog"]["items"] if row["evidence_id"] == "TR-0001")
    assert item["kind"] == "task_requirement" and item["text"] == real["user_requirement"]
    assert not state["open_issues"] and not state["completed"]
    assert ctx.db.query(AgentApproval).count() == 4


def test_approval_references_use_real_item_numbers_and_preserve_full_identity(context):
    """真实AP13有两条同答复，短引用仍分别绑定原问题，不截断或猜测摘要。"""
    ctx, args = context
    state = generation.initialize_generation_state(ctx, args)
    rows = state["approved_decisions"]
    assert len(rows) == len({row["evidence_id"] for row in rows}) == 5
    for row in rows:
        approval = ctx.db.get(AgentApproval, row["approval_id"])
        items = approval.request_payload["items"]
        position, original = next((index, item) for index, item in enumerate(items, 1)
                                  if item["id"] == row["original_issue_id"])
        assert row["evidence_id"] == f"AP-{approval.id}-{position}"
        assert row["question"] == original
        assert row["resolution"] == approval.decision_payload["conflict_answers"][original["id"]]
        assert row["approval_state_hash"] == approval.request_payload["state_hash"]
        assert row["origin_node_id"] == approval.node_run_id
        assert generation._source_refs(state, [row["evidence_id"]]) == [{
            "evidence_id": row["evidence_id"], "quote": row["resolution"]}]
        with pytest.raises(OutputValidationError):
            generation._source_refs(state, [f"AP-{approval.id}-{original['id']}"])
    first = [row for row in rows if row["approval_id"] == rows[0]["approval_id"]]
    assert len(first) == 2 and first[0]["resolution"] == first[1]["resolution"]
    assert first[0]["original_issue_id"] != first[1]["original_issue_id"]
    assert first[0]["evidence_id"] != first[1]["evidence_id"]


@pytest.mark.parametrize("requirement", ["", " ", "\n\t"])
def test_empty_document_supplement_does_not_create_a_source(requirement, context):
    ctx, args = context
    source = prepare_generation_evidence(ctx, {**args, "requirement": requirement})
    assert not any(item["kind"] == "task_requirement" for item in source["evidence_catalog"]["items"])
    assert source["requirement"] == ctx.db.get(KnowledgeDocument, 287).content


def test_supplement_preserves_literal_whitespace_and_full_hash(real, context):
    ctx, args = context
    literal = "\n" + real["user_requirement"] + " \n"
    source = prepare_generation_evidence(ctx, {**args, "requirement": literal})
    task = next(item for item in source["evidence_catalog"]["items"] if item["kind"] == "task_requirement")
    assert task["text"] == literal
    assert task["content_hash"] == hashlib.sha256(literal.encode()).hexdigest()
    assert task["content_hash"] != hashlib.sha256(real["user_requirement"].encode()).hexdigest()


def test_inline_input_is_not_duplicated_as_task_requirement(real, context):
    ctx, args = context
    inline = SourceSnapshot.from_text(real["user_requirement"]).to_dict()
    current = ctx.db.get(AgentRun, ctx.run_id)
    # 仅调整隔离库的冻结技术输入，业务文字始终取真正的当前用户消息。
    current.run_context = {"artifacts": {"requirement_source": inline}}
    ctx.db.commit()
    ctx.artifacts = {"requirement_source": inline}
    source = prepare_generation_evidence(ctx, {**args, "requirement_doc_id": None})
    assert source["source_ref"] == inline
    assert len(source["evidence_catalog"]["items"]) == 1
    assert source["evidence_catalog"]["items"][0]["text"] == real["user_requirement"]
    assert not source["approved_decisions"]


def test_prior_run_input_is_not_automatically_loaded_as_current_supplement(real, context):
    ctx, args = context
    previous = ctx.db.get(AgentRun, real["approvals"][0]["run_id"])
    # 同一真实消息仅放入隔离历史输入作范围控制，不冒充该历史Run在线收到过它。
    previous.input_payload = {**previous.input_payload, "requirement": real["user_requirement"]}
    ctx.db.commit()
    source = prepare_generation_evidence(ctx, {**args, "requirement": ""})
    assert len(source["approved_decisions"]) == 5
    assert not any(row["kind"] == "task_requirement" for row in source["evidence_catalog"]["items"])


def test_nonstring_document_supplement_is_rejected(context):
    ctx, args = context
    with pytest.raises(ValueError, match="本轮用户补充必须是字符串"):
        prepare_generation_evidence(ctx, {**args, "requirement": None})
