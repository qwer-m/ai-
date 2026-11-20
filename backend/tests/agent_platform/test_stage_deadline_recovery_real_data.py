"""Run75 真实恢复时间的 CPU 重放：截止时间不能续杯，不调用模型或改线上历史。"""

from copy import deepcopy
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform import runtime
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import WorkflowNode
from modules.agent_platform.run_repository import AgentRunRepository


def parsed_row(model, raw):
    values = deepcopy(raw)
    for column in model.__table__.columns:
        if column.type.python_type is datetime and isinstance(values.get(column.name), str):
            values[column.name] = datetime.fromisoformat(values[column.name])
    return model(**values)


@pytest.fixture(scope="module")
def saved():
    path = Path(__file__).parent / "fixtures/run75_stage_deadline_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    assert fixture["provenance"]["source_run_id"] == 75
    assert fixture["provenance"]["source_status"] == "cancelled"
    assert action_state_hash(fixture["data"]) == fixture["provenance"]["data_sha256"]
    yield fixture["data"]
    assert path.read_bytes() == original


@pytest.fixture
def records(saved):
    run = parsed_row(AgentRun, saved["run"])
    nodes = [parsed_row(AgentNodeRun, item) for item in saved["nodes"]]
    stage = WorkflowNode(node_key=nodes[0].node_key, node_type="agent_network",
        reference_key=saved["workflow"]["entry_agent_key"],
        time_budget_seconds=saved["workflow"]["time_budget_seconds"])
    return run, nodes, stage


def test_every_actual_restart_and_504_retry_keeps_original_stage_end(records):
    run, nodes, stage = records
    before = deepcopy(run.run_context)
    first = min(node.started_at for node in nodes)
    expected = first + timedelta(seconds=stage.time_budget_seconds)
    assert len(nodes) == 4 and expected < datetime.fromisoformat(run.run_context["stage_deadline_at"])
    for previous in nodes[1:]:
        actual = runtime._node_stage_deadline(run, stage, previous,
            first_started_at=first, now=previous.started_at)
        assert actual == expected
        assert (actual - previous.started_at).total_seconds() < stage.time_budget_seconds
    assert run.run_context == before


def test_an_existing_earlier_stage_end_is_never_extended(records):
    run, nodes, stage = records
    original = nodes[0].started_at + timedelta(seconds=stage.time_budget_seconds)
    run.run_context = {**run.run_context, "stage_deadline_at": original.isoformat()}
    assert runtime._node_stage_deadline(run, stage, nodes[-1],
        first_started_at=nodes[0].started_at, now=nodes[-1].started_at) == original


def test_new_stage_has_its_own_budget_bounded_by_global_deadline(records):
    run, nodes, stage = records
    next_stage = stage.model_copy(update={"node_key": stage.node_key + "_next"})
    at = nodes[-1].started_at
    expected = min(datetime.fromisoformat(run.run_context["deadline_at"]),
                   at + timedelta(seconds=stage.time_budget_seconds))
    assert runtime._node_stage_deadline(run, next_stage, None, first_started_at=None, now=at) == expected
    # 仅收窄控制预算，验证不同阶段也不能突破已有运行总期限。
    run.run_context = {**run.run_context, "deadline_at": nodes[-1].started_at.isoformat()}
    assert runtime._node_stage_deadline(run, next_stage, None, first_started_at=None, now=at) == at


def test_real_registered_wait_keeps_existing_exclusion_policy(records, saved):
    run, nodes, stage = records
    # 只回放 Run69 实际登记的人工等待控制参数，不把 Run75 断线时间冒充人工等待。
    assert saved["registered_wait_example"]["run_id"] == 69
    wait = saved["registered_wait_example"]["confirmation_wait_seconds"]
    previous = SimpleNamespace(sdk_state={"confirmation_wait_seconds": wait})
    original = nodes[0].started_at + timedelta(seconds=stage.time_budget_seconds + wait)
    run.run_context = {**run.run_context, "stage_deadline_at": original.isoformat(),
        "deadline_at": (datetime.fromisoformat(run.run_context["deadline_at"]) + timedelta(seconds=wait)).isoformat()}
    assert runtime._node_stage_deadline(run, stage, previous,
        first_started_at=nodes[0].started_at, now=nodes[-1].started_at) == original


def test_expired_original_stage_has_no_new_model_session(records, monkeypatch):
    run, nodes, stage = records
    after = nodes[0].started_at + timedelta(seconds=stage.time_budget_seconds + 1)
    deadline = runtime._node_stage_deadline(run, stage, nodes[-1], first_started_at=nodes[0].started_at, now=after)
    run.run_context = {**run.run_context, "stage_deadline_at": deadline.isoformat()}
    monkeypatch.setattr(runtime, "db_utc_now", lambda: after)
    with pytest.raises(runtime._RunDeadlineExceeded, match="阶段预算"):
        runtime._ensure_run_deadline(run, node_key=stage.node_key)


def test_real_repository_first_start_and_claim_do_not_reset_deadline_or_usage(records, monkeypatch):
    run, nodes, stage = records
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (AgentRun, AgentNodeRun, AgentRunEvent):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        # 隔离接管控制流程；业务输入、节点时间、用量均直接取自原记录。
        run.status, run.task_id, run.lease_expires_at = "pending", None, None
        db.add(run)
        db.add_all(nodes)
        db.commit()
        repo = AgentRunRepository(db)
        assert repo.first_node_started_at(run_id=run.id, node_key=stage.node_key) == nodes[0].started_at
        before = deepcopy(run.run_context)
        monkeypatch.setattr(runtime, "db_utc_now", lambda: nodes[1].started_at)
        claimed = runtime._claim_run(repo, run_id=run.id, task_id=None)
        assert claimed.run_context["stage_deadline_at"] == before["stage_deadline_at"]
        assert claimed.run_context["usage"] == before["usage"]
        assert claimed.started_at == nodes[0].started_at
    engine.dispose()
