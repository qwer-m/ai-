"""真实历史 v2 与 Run59 v3 的协议边界；不迁移、不启动业务或模型。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import CheckpointStorageError, compact_checkpoint


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "checkpoint_protocol_real.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    expected = value.pop("data_sha256")
    assert action_state_hash(value) == expected
    assert value["legacy_node"]["id"] == value["provenance"]["legacy_node_id"]
    assert value["current_node"]["id"] == value["provenance"]["current_node_id"]
    return value


def test_current_real_v3_checkpoint_is_read_without_rewriting_evidence(real):
    record = deepcopy(real["current_node"])
    before = deepcopy(record)
    checkpoint = compact_checkpoint(SimpleNamespace(**record))
    assert checkpoint == record["sdk_state"]["action_checkpoint"]
    assert checkpoint["version"] == 3
    assert checkpoint["storage"] == "artifact-journal-v1"
    assert record == before
    assert checkpoint is not record["sdk_state"]["action_checkpoint"]


def test_actual_archived_v2_checkpoint_is_refused_without_migration(real):
    record = deepcopy(real["legacy_node"])
    assert record["sdk_state"]["action_checkpoint"]["version"] == 2
    before = deepcopy(record)
    with pytest.raises(CheckpointStorageError):
        compact_checkpoint(SimpleNamespace(**record))
    assert record == before


@pytest.mark.parametrize("version", [None, 1, 2, 4])
def test_corrupted_real_v3_protocol_version_is_refused(real, version):
    record = deepcopy(real["current_node"])
    # 只破坏真实协议版本，不编造业务事实、模型结果或回执。
    record["sdk_state"]["action_checkpoint"]["version"] = version
    before = deepcopy(record)
    with pytest.raises(CheckpointStorageError):
        compact_checkpoint(SimpleNamespace(**record))
    assert record == before


@pytest.mark.parametrize("embedded_field", ["state", "receipts", "validated_agent_calls"])
def test_current_checkpoint_cannot_mix_archived_inline_bodies(real, embedded_field):
    record = deepcopy(real["current_node"])
    legacy = real["legacy_node"]["sdk_state"]["action_checkpoint"]
    record["sdk_state"]["action_checkpoint"][embedded_field] = deepcopy(legacy[embedded_field])
    before = deepcopy(record)
    with pytest.raises(CheckpointStorageError):
        compact_checkpoint(SimpleNamespace(**record))
    assert record == before


def test_current_real_checkpoint_requires_explicit_storage_protocol(real):
    record = deepcopy(real["current_node"])
    del record["sdk_state"]["action_checkpoint"]["storage"]
    with pytest.raises(CheckpointStorageError):
        compact_checkpoint(SimpleNamespace(**record))
