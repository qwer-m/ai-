"""真实Run59产物读取回归；验证内容与隔离性，不用不稳定耗时门槛替代正确性。"""

from copy import deepcopy
from dataclasses import replace
import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentArtifactBlob, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.registry import ToolExecutionContext


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "run59_artifact_read_cost_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: item for key, item in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert action_state_hash(value["state"]) == value["provenance"]["source_state_sha256"]
    assert value["provenance"]["run_id"] == 59 and value["provenance"]["status"] == "success"
    return value


@pytest.fixture(scope="module")
def persisted(real):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentArtifactBlob.__table__.create(engine)
    AgentRun.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        # 仅复制真实运行的来源身份和内容到隔离库，不访问或改写业务数据库。
        db.add(AgentRun(**deepcopy(real["run_metadata"]), input_payload=deepcopy(real["input_payload"]),
            run_context=deepcopy(real["initial_run_context"])))
        store = ArtifactStore(db, user_id=1, project_id=8)
        ref = store.put(real["state"])
        assert store.new_rows > 0
        db.commit()
        yield db, ref
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()




def mutable_aliases(value):
    seen, aliases = {}, []
    stack = [((), value)]
    while stack:
        path, item = stack.pop()
        if not isinstance(item, (dict, list)):
            continue
        if id(item) in seen:
            aliases.append((seen[id(item)], path))
        else:
            seen[id(item)] = path
        children = item.items() if isinstance(item, dict) else enumerate(item)
        stack.extend(((*path, key), child) for key, child in children)
    return aliases


def test_decoded_real_large_state_preserves_all_content_and_exact_hash(real, persisted):
    db, ref = persisted
    restored = ArtifactStore(db, user_id=1, project_id=8).get(ref)
    assert restored == real["state"]
    assert action_state_hash(restored) == ref["sha256"] == real["provenance"]["source_state_sha256"]
    assert not db.new and not db.dirty and not db.deleted


def test_cached_identical_chunks_do_not_alias_distinct_occurrences(persisted):
    db, ref = persisted
    restored = ArtifactStore(db, user_id=1, project_id=8).get(ref)
    assert mutable_aliases(restored) == []


def test_separate_reads_remain_independent_and_cannot_change_saved_content(real, persisted):
    db, ref = persisted
    store = ArtifactStore(db, user_id=1, project_id=8)
    first = store.get(ref)
    second = store.get(ref)
    # 只改变本地返回图中的真实字段，验证隔离；不把该损坏副本作为业务输入保存。
    first["planning"]["planning_records"][0]["output"]["batch_summary"] += "（隔离性核验）"
    assert action_state_hash(first) != ref["sha256"]
    assert action_state_hash(second) == ref["sha256"]
    assert store.get(ref) == second == real["state"]
    assert not db.new and not db.dirty and not db.deleted






