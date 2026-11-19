"""以Run65冻结时间和定义核对会话/HTTP期限，不发模型请求或伪造业务结果。"""

from datetime import datetime
import json
from pathlib import Path

import pytest

from modules.agent_platform.sdk_adapter import _execution_session_timeout


@pytest.fixture(scope="module")
def actual_run():
    path = Path(__file__).with_name("fixtures") / "run65_session_deadlines_real.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["run"]["id"] == 65 and value["run"]["status"] == "failed"
    return value


def test_persistent_session_uses_actual_stage_deadline_not_single_http_timeout(actual_run):
    run = actual_run["run"]
    context = run["run_context"]
    main = next(agent for agent in context["execution_contract"]["agents"]
                if agent["key"] == "test_generation_coordinator")
    request_timeout = main["runtime_config"]["request_timeout_seconds"]
    first_request = actual_run["nodes"][0]["sdk_state"]["model_requests"][0]
    started = datetime.fromisoformat(first_request["started_at"]).replace(tzinfo=None)
    deadline = min(datetime.fromisoformat(context[key]) for key in ("deadline_at", "stage_deadline_at"))
    remaining = (deadline - started).total_seconds()
    assert request_timeout == 180 and remaining > request_timeout
    selected = _execution_session_timeout(request_timeout_seconds=request_timeout,
        session_timeout_seconds=remaining, managed_max_turns=None)
    assert selected == remaining
    # 本次实测为120秒上游504，不能把新增的会话期限修复当成本轮504的原因。
    failed = next(item for node in actual_run["nodes"] for item in node["sdk_state"]["model_requests"]
                  if item["status"] != "response_received")
    assert failed["elapsed_seconds"] < request_timeout


def test_managed_review_keeps_frozen_per_request_and_local_turn_bounds(actual_run):
    child = next(agent for agent in actual_run["run"]["run_context"]["execution_contract"]["agents"]
                 if agent["key"] == "test_generation_reviewer")
    cfg = child["runtime_config"]
    assert _execution_session_timeout(request_timeout_seconds=cfg["request_timeout_seconds"],
        session_timeout_seconds=None, managed_max_turns=cfg["max_turns"]) == (
            cfg["request_timeout_seconds"] * cfg["max_turns"])


def test_explicit_nonpositive_session_deadline_is_rejected():
    # 仅测试控制参数的拒绝边界，不构造需求或模型回执。
    for invalid in (0, -1):
        with pytest.raises(ValueError, match="期限必须大于0"):
            _execution_session_timeout(request_timeout_seconds=180,
                session_timeout_seconds=invalid, managed_max_turns=None)
