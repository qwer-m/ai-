"""真实 Run59 SDK 诊断与 Approval15 题目的异源控制测试，不重建 Run61 缺失事件。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentNodeRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.conflict_confirmation import ConflictConfirmationRequired
from modules.agent_platform.retry_policy import restorable_sdk_state
from modules.agent_platform.runtime import _append_execution_observations, _set_paused_execution_observations
from modules.agent_platform.sdk_adapter import _attach_execution_observations


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "pause_observability_cross_source_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    expected = value.pop("data_sha256")
    assert action_state_hash(value) == expected
    return value


def pause_with_metadata(real, metadata=None):
    """审批只提供真实控制题目，不把它解释成诊断来源 Run59 的业务审批。"""
    pause = ConflictConfirmationRequired(deepcopy(real["approval"]["request_payload"]["items"]))
    values = real["metadata"] if metadata is None else metadata
    _attach_execution_observations(pause, **values)
    return pause


def test_fixture_sources_are_explicit_and_missing_run61_events_are_not_recreated(real):
    source = real["provenance"]
    assert source["sdk_metadata_source"]["run_id"] == 59
    assert source["sdk_metadata_source"]["node_id"] == 404
    assert source["pause_items_source"]["run_id"] == 61
    assert source["pause_items_source"]["approval_id"] == 15
    assert source["does_not_reconstruct_run61_missing_95_requests"] is True
    missing = source["actual_run61_paused_node_observation"]
    assert int(missing["reported_requests"]) == 95
    assert not any(missing[key] for key in ("has_model_requests", "has_tool_calls", "has_tool_observations"))
    assert {request["run_id"] for request in real["metadata"]["model_requests"]} == {59}
    assert len(real["metadata"]["model_requests"]) == 200


def test_base_exception_pause_keeps_exact_sdk_observations_without_error_flags(real):
    pause = pause_with_metadata(real)
    assert isinstance(pause, BaseException) and not isinstance(pause, Exception)
    assert pause.items == real["approval"]["request_payload"]["items"]
    assert pause.diagnostic == {key: real["metadata"][key] for key in ("model_requests", "tool_observations")}
    assert pause.tool_calls == real["metadata"]["tool_calls"]
    assert "exception_chain" not in pause.diagnostic
    assert "budget_usage" not in pause.diagnostic


def test_paused_node_only_adds_observations_and_never_recounts_budget(real):
    node = SimpleNamespace(**deepcopy(real["source_node"]))
    before = deepcopy(node.sdk_state)
    _set_paused_execution_observations(node, pause_with_metadata(real))
    assert {key: value for key, value in node.sdk_state.items() if key not in real["metadata"]} == before
    assert all(node.sdk_state[key] == value for key, value in real["metadata"].items())
    assert node.sdk_state["action_checkpoint"]["budget"] == before["action_checkpoint"]["budget"]
    assert node.sdk_state["action_usage"] == before["action_usage"]
    assert node.sdk_state["usage"] == before["usage"]
    assert "error_diagnostic" not in node.sdk_state
    assert node.status == real["source_node"]["status"]


def test_observation_copies_are_detached_from_sdk_and_pause_buffers(real):
    values = deepcopy(real["metadata"])
    pause = pause_with_metadata(real, values)
    for value in values.values():
        value.clear()
    node = SimpleNamespace(**deepcopy(real["source_node"]))
    _set_paused_execution_observations(node, pause)
    pause.tool_calls.clear()
    for value in pause.diagnostic.values():
        value.clear()
    assert all(node.sdk_state[key] == value for key, value in real["metadata"].items())


def test_actual_observation_fields_survive_isolated_sqlite_node_json_roundtrip(real):
    shadow = create_engine("sqlite+pysqlite:///:memory:")
    AgentNodeRun.__table__.create(shadow)
    try:
        with Session(shadow) as db:
            node = AgentNodeRun(**deepcopy(real["source_node"]))
            db.add(node)
            db.commit()
            _set_paused_execution_observations(node, pause_with_metadata(real))
            db.commit()
            db.expire_all()
            saved = db.get(AgentNodeRun, real["source_node"]["id"])
            assert all(saved.sdk_state[key] == value for key, value in real["metadata"].items())
            assert saved.sdk_state["action_checkpoint"] == real["source_node"]["sdk_state"]["action_checkpoint"]
    finally:
        shadow.dispose()


def test_resumption_omits_old_diagnostics_and_only_attaches_current_control_slice(real):
    original = SimpleNamespace(**deepcopy(real["source_node"]))
    _set_paused_execution_observations(original, pause_with_metadata(real))
    before = deepcopy(original.sdk_state)
    restored = restorable_sdk_state(original)
    assert not set(restored).intersection(real["metadata"])
    # 真实记录切片只检验当前会话边界，不宣称它是 Run61 恢复阶段的新请求。
    current_slice = {key: deepcopy(value[-1:]) for key, value in real["metadata"].items()}
    resumed = SimpleNamespace(sdk_state=restored)
    _set_paused_execution_observations(resumed, pause_with_metadata(real, current_slice))
    assert all(resumed.sdk_state[key] == value for key, value in current_slice.items())
    assert original.sdk_state == before
    assert resumed.sdk_state["action_checkpoint"]["budget"] == before["action_checkpoint"]["budget"]
    assert resumed.sdk_state["action_usage"] == before["action_usage"]


def test_same_node_continuation_appends_disjoint_real_observation_slices(real):
    node = SimpleNamespace(**deepcopy(real["source_node"]))
    prefix = {key: deepcopy(value[:-1]) for key, value in real["metadata"].items()}
    suffix = {key: deepcopy(value[-1:]) for key, value in real["metadata"].items()}
    _set_paused_execution_observations(node, pause_with_metadata(real, prefix))
    paused = deepcopy(node.sdk_state)
    # 同一节点恢复后，success 与再次暂停使用同一追加边界；不把切片说成真实61阶段。
    success_observations = _append_execution_observations(node.sdk_state, suffix)
    assert success_observations == real["metadata"]
    _set_paused_execution_observations(node, pause_with_metadata(real, suffix))
    assert all(node.sdk_state[key] == value for key, value in real["metadata"].items())
    assert all(paused[key] == value for key, value in prefix.items())
    assert node.sdk_state["action_checkpoint"] == real["source_node"]["sdk_state"]["action_checkpoint"]
    assert node.sdk_state["action_usage"] == real["source_node"]["sdk_state"]["action_usage"]


def test_missing_diagnostic_attachment_does_not_invent_empty_or_historical_records(real):
    node = SimpleNamespace(**deepcopy(real["source_node"]))
    before = deepcopy(node.sdk_state)
    pause = ConflictConfirmationRequired(deepcopy(real["approval"]["request_payload"]["items"]))
    _set_paused_execution_observations(node, pause)
    assert node.sdk_state == before
