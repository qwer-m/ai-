"""真实Run62四次尝试的轻量日志回放；只在隔离SQLite检查恢复进展。"""

from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from core.db.model_defs import (AgentActionReceiptRecord, AgentJournalBranch,
    AgentNodeRun, AgentRun, AgentValidatedCallRecord)
from modules.agent_platform import checkpoint_storage as storage
from modules.agent_platform.action_journal import ActionStateConflict, action_state_hash
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _accepted_retry_progress


MODELS = (AgentRun, AgentNodeRun, AgentJournalBranch, AgentValidatedCallRecord, AgentActionReceiptRecord)


def model(kind, saved):
    values = {column.key: deepcopy(saved[column.key]) for column in kind.__table__.columns if column.key in saved}
    for column in kind.__table__.columns:
        if column.type.python_type is datetime and isinstance(values.get(column.key), str):
            values[column.key] = datetime.fromisoformat(values[column.key])
    return kind(**values)


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "run62_transient_progress_real.json.gz"
    original = path.read_bytes()
    saved = json.loads(gzip.decompress(original))
    assert action_state_hash({key: value for key, value in saved.items() if key != "data_sha256"}) == saved["data_sha256"]
    assert saved["run_row"]["id"] == 62
    assert [row["id"] for row in saved["node_rows"]] == [414, 415, 416, 417]
    yield saved
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()


@pytest.fixture(scope="module")
def engine(real):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    # 故意不创建ArtifactBlob表：轻量进展读取若误展开正文，测试会直接失败。
    for kind in MODELS:
        kind.__table__.create(engine)
    with Session(engine) as db:
        db.add(model(AgentRun, real["run_row"]))
        db.add_all(model(AgentNodeRun, row) for row in real["node_rows"])
        db.add_all(model(AgentJournalBranch, row) for row in real["branch_rows"])
        db.add_all(model(AgentValidatedCallRecord, row) for row in real["call_rows"])
        db.add_all(model(AgentActionReceiptRecord, row) for row in real["receipt_rows"])
        db.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    with Session(engine, autoflush=False) as db:
        yield db
        db.rollback()


def metadata_hash(db):
    saved = []
    for kind in MODELS:
        rows = db.scalars(select(kind).order_by(kind.__table__.primary_key.columns.values()[0])).all()
        saved.append([{column.key: getattr(row, column.key) for column in kind.__table__.columns} for row in rows])
    return action_state_hash(json.loads(json.dumps(saved, ensure_ascii=False, default=str)))


def progress(db, identity):
    before = metadata_hash(db)
    statements = []
    bind = db.get_bind()
    def observe(_conn, _cursor, sql, _params, _execution, _many):
        statements.append(sql)
    event.listen(bind, "before_cursor_execute", observe)
    try:
        return _accepted_retry_progress(AgentRunRepository(db), db.get(AgentNodeRun, identity))
    finally:
        event.remove(bind, "before_cursor_execute", observe)
        assert all(sql.lstrip().split(None, 1)[0].upper() == "SELECT" for sql in statements)
        assert not any("agent_artifact_blobs" in sql.lower() for sql in statements)
        assert not db.new and not db.dirty and not db.deleted
        assert metadata_hash(db) == before


def test_actual_24_43_66_66_frontiers_are_read_across_real_branches_without_hydration(db, real):
    counts, tokens = [], []
    for identity in (414, 415, 416, 417):
        node = db.get(AgentNodeRun, identity)
        checkpoint = storage.compact_checkpoint(node)
        counts.append(len(storage.call_records(db, node, checkpoint)))
        tokens.append(progress(db, identity))
    assert counts == [24, 43, 66, 66]
    assert tokens == [frontier["progress_token"] for frontier in real["frontiers"]]
    assert len(set(tokens[:3])) == 3 and tokens[-2] == tokens[-1]
    assert len(real["call_rows"]) == 66


def test_actual_empty_fork_and_new_failure_attempt_do_not_create_progress(db):
    previous, forked = (db.get(AgentNodeRun, identity) for identity in (416, 417))
    a, b = (storage.compact_checkpoint(node) for node in (previous, forked))
    assert a["state_hash"] == b["state_hash"]
    assert a["journal_ref"]["branch_id"] != b["journal_ref"]["branch_id"]
    branch = db.get(AgentJournalBranch, b["journal_ref"]["branch_id"])
    assert branch.parent_id == a["journal_ref"]["branch_id"] and b["journal_ref"]["call_cursor"] == 0
    assert previous.attempt == 3 and forked.attempt == 4
    assert progress(db, previous.id) == progress(db, forked.id)


def test_only_actual_budget_and_projection_control_changes_do_not_create_progress(db):
    previous, failed = (db.get(AgentNodeRun, identity) for identity in (416, 417))
    before = progress(db, previous.id)
    assert previous.sdk_state["action_checkpoint"]["budget"] != failed.sdk_state["action_checkpoint"]["budget"]
    sdk = deepcopy(previous.sdk_state)
    sdk["action_checkpoint"]["budget"] = deepcopy(failed.sdk_state["action_checkpoint"]["budget"])
    sdk["action_checkpoint"]["projection"] = deepcopy(failed.sdk_state["action_checkpoint"]["projection"])
    previous.sdk_state = sdk
    db.flush()
    assert progress(db, previous.id) == before


def test_only_real_failed_receipt_visibility_does_not_create_progress(db, real):
    saved = next(row for row in reversed(real["receipt_rows"]) if row["record"]["status"] == "failed")
    branch = db.get(AgentJournalBranch, saved["branch_id"])
    node = db.get(AgentNodeRun, branch.owner_node_id)
    sdk = deepcopy(node.sdk_state)
    sdk["action_checkpoint"]["journal_ref"]["receipt_cursor"] = saved["id"] - 1
    node.sdk_state = sdk
    db.flush()
    before = progress(db, node.id)
    invisible = storage.receipt_records(db, node, storage.compact_checkpoint(node)).get(saved["tool_call_id"])
    sdk = deepcopy(node.sdk_state)
    sdk["action_checkpoint"]["journal_ref"]["receipt_cursor"] = saved["id"]
    node.sdk_state = sdk
    db.flush()
    visible = storage.receipt_records(db, node, storage.compact_checkpoint(node))[saved["tool_call_id"]]
    assert visible["status"] == "failed" and invisible != visible
    assert progress(db, node.id) == before


@pytest.mark.parametrize("damage", ["input_identity", "execution_identity", "missing_contract", "unvalidated_status"])
def test_corrupted_accepted_identity_or_status_is_rejected(db, real, damage):
    saved = real["call_rows"][0]
    row = db.get(AgentValidatedCallRecord, saved["id"])
    value = deepcopy(row.record)
    if damage == "input_identity":
        value["identity"]["input_hash"] = real["node_rows"][-1]["sdk_state"]["action_checkpoint"]["state_hash"]
    elif damage == "execution_identity":
        value["identity"]["execution_semantic_hash"] = real["node_rows"][-1]["sdk_state"]["action_checkpoint"]["state_hash"]
    elif damage == "missing_contract":
        value["identity"].pop("contract_hash")
    else:
        value["status"] = real["node_rows"][-1]["status"]
    row.record = value
    db.flush()
    with pytest.raises((ValueError, ActionStateConflict)):
        progress(db, 417)


def test_real_parent_scope_corruption_is_rejected_before_reading_accepted_refs(db, real):
    node = db.get(AgentNodeRun, 417)
    branch = db.get(AgentJournalBranch, node.sdk_state["action_checkpoint"]["journal_ref"]["branch_id"])
    parent = db.get(AgentJournalBranch, branch.parent_id)
    parent.source_hash = real["node_rows"][-1]["sdk_state"]["action_checkpoint"]["state_hash"]
    db.flush()
    with pytest.raises(storage.CheckpointStorageError):
        progress(db, node.id)
