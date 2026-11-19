"""Run79 自动审查失败前沿的 CPU 回放；不调用模型、不改历史或伪造审查成功。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from agents.tool import FunctionTool
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import (AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob,
    AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord)
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AgentSubtreeRequestBudgetExceeded
from modules.agent_platform.autonomous_tools import AutonomousToolLoop, AgentNoProgressError
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _function_tool, _feedback_tool_names
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from modules.agent_platform import test_generation_agent as generation


@pytest.fixture(scope="module")
def actual():
    path = Path(__file__).parent / "fixtures/run79_automatic_failure_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    assert fixture["provenance"]["source_run_id"] == 79
    assert fixture["provenance"]["new_model_responses"] is False
    assert action_state_hash(data) == fixture["provenance"]["data_sha256"]
    assert action_state_hash(data["state"]) == data["automatic_failure"]["pre_state_hash"]
    assert data["budget_at_failure"]["charged"]["attempted_requests"] == 8
    assert sum(row["usage"]["total_tokens"] for row in data["later_main_requests"]) == 41492
    yield data
    assert path.read_bytes() == original


@pytest.fixture
def resource(actual, request):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob,
                  AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord):
        model.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        claim = new_claim_token()
        finish = bool(getattr(request, "param", False))
        run = AgentRun(**deepcopy(actual["real_finish_run"] if finish else actual["run"]), status="running", claim_token=claim,
            lease_expires_at=datetime.utcnow() + timedelta(hours=1))
        node = AgentNodeRun(id=actual["real_finish_node_id"] if finish else actual["node_id"], run_id=run.id, node_key="agent_network",
            node_type="agent_network", status="running", attempt=1,
            input_payload=deepcopy(run.input_payload), output_payload={}, sdk_state={})
        db.add_all([run, node])
        db.commit()
        journal = AgentActionJournal(AgentRunRepository(db), run_id=run.id,
            node_run_id=node.id, expected_claim_token=claim)
        state = deepcopy(actual["real_finish_state"] if finish else actual["state"])
        journal.init(state=state, input_hash=action_state_hash(run.input_payload),
                     budget=deepcopy(actual["real_finish_budget"] if finish else actual["budget_at_failure"]))
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(run.input_payload), action_state=state, action_journal=journal,
            claim_token=claim, artifacts=deepcopy(run.run_context["artifacts"]))
        yield SimpleNamespace(context=context, journal=journal)
        db.rollback()
    engine.dispose()


def tools_at_boundary(resource, actual, *, observation=None, finish_error=None):
    context, journal = resource.context, resource.journal
    config = deepcopy(actual["main_runtime_config"])
    state_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
    loop = AutonomousToolLoop(terminal_tool_names={"finish_generation"},
        validate_submission=lambda output: generation.validate_generation_resolution(context,
            {"input_payload": deepcopy(context.action_input), "output": output}),
        recoverable_errors=(OutputValidationError, AgentSubtreeRequestBudgetExceeded),
        max_tool_calls=32, action_context=context, budget_snapshot=lambda: journal.metadata()["budget"])
    raw_tools = [_function_tool(SimpleNamespace(**item), context, state_input_filter=state_filter)
                 for item in BUILTIN_TOOL_SPECS]
    if observation is not None:
        async def recorded_observation(_context, _arguments):
            # 仅回放旧审查的非终止观察，未将该结果应用于本Run79用例或制造新批准。
            return json.dumps(observation, ensure_ascii=False)
        raw_tools = [FunctionTool(name=tool.name, description=tool.description,
            params_json_schema=deepcopy(tool.params_json_schema), on_invoke_tool=recorded_observation,
            strict_json_schema=False) if tool.name == "request_review" else tool for tool in raw_tools]
    if finish_error is not None:
        async def recorded_failure(_context, _arguments):
            # 注入已记录同类异常只检查控制链，不声称旧Run78发生过finish失败。
            raise finish_error
        raw_tools = [FunctionTool(name=tool.name, description=tool.description,
            params_json_schema=deepcopy(tool.params_json_schema), on_invoke_tool=recorded_failure,
            strict_json_schema=False) if tool.name == "finish_generation" else tool for tool in raw_tools]
    allowed = _feedback_tool_names(config, raw_tools, [], persistent=True)
    tools = [loop.wrap_tool(tool, allow_feedback=tool.name in allowed) for tool in raw_tools]
    state_filter.validate_platform_actions(tools)
    return state_filter, loop, tools


def test_real_subtree_exhaustion_is_recorded_and_raises_before_main_nine_and_ten(resource, actual):
    context, journal = resource.context, resource.journal
    original_budget = deepcopy(journal.metadata()["budget"])
    original_state = action_state_hash(context.action_state)
    invoked, main_admission = [], []
    failure = AgentSubtreeRequestBudgetExceeded(actual["automatic_failure"]["error"]["message"])
    failure.diagnostic = deepcopy(actual["automatic_failure"]["error"]["feedback"]["diagnostic"])

    async def replay_actual_failure(**kwargs):
        invoked.append(kwargs["agent_key"])
        raise failure

    context.invoke_agent = replay_actual_failure
    state_filter, loop, tools = tools_at_boundary(resource, actual)

    async def runner_boundary():
        await state_filter.complete_if_ready(tools, loop)
        main_admission.extend([9, 10])

    with pytest.raises(AgentSubtreeRequestBudgetExceeded) as stopped:
        asyncio.run(runner_boundary())
    assert stopped.value is failure
    assert invoked == ["test_generation_reviewer"] and not main_admission
    record = journal.receipts(include_payloads=True)[-1]
    assert record["status"] == "failed" and record["error"]["error_type"] == "AgentSubtreeRequestBudgetExceeded"
    assert record["error"]["recoverable"] is False
    assert loop.events[-1]["status"] == "error"
    assert journal.metadata()["budget"] == original_budget
    assert action_state_hash(context.action_state) == original_state


def test_same_failed_automatic_state_cannot_fall_through_to_main_or_repeat_tool(resource, actual):
    context, journal = resource.context, resource.journal
    old = actual["automatic_failure"]
    call_id = "platform_action_" + action_state_hash([old["tool_key"], context.action_state])
    assert call_id == old["tool_call_id"]
    journal.begin(tool_call_id=call_id, tool_key=old["tool_key"], args=deepcopy(old["arguments"]),
                  prehash=action_state_hash(context.action_state))
    journal.fail(tool_call_id=call_id, error=deepcopy(old["error"]), budget=deepcopy(actual["budget_at_failure"]))
    state_filter, loop, tools = tools_at_boundary(resource, actual)
    before = deepcopy(journal.metadata()["budget"])
    with pytest.raises(AgentNoProgressError, match="必需自动行动已有失败回执"):
        asyncio.run(state_filter.complete_if_ready(tools, loop))
    assert not loop.events and journal.receipt_count() == 1
    assert journal.metadata()["budget"] == before


def test_normal_real_needs_revision_observation_still_returns_control_to_main(resource, actual):
    observation = actual["real_needs_revision_observation"]
    assert observation["verdict"] == "changes_requested"
    state_filter, loop, tools = tools_at_boundary(resource, actual, observation=observation)
    assert asyncio.run(state_filter.complete_if_ready(tools, loop)) is False
    assert loop.events[-1]["status"] == "observation"
    assert resource.journal.receipts(include_payloads=True)[-1]["status"] == "completed"
    assert not loop.has_submission and "test_generation" not in resource.context.artifacts


def test_real_http_failure_keeps_original_transport_recovery_classification(resource, actual):
    status = next(row["http_status"] for row in actual["real_transport_failure"]["exception_chain"] if "http_status" in row)

    class RecordedTransportFailure(Exception):
        # 回放真实故障状态供原传输分类器判断，不发送请求或虚构模型响应。
        status_code = status

    failure = RecordedTransportFailure(actual["real_transport_failure"]["message"])

    async def replay_transport(**_kwargs):
        raise failure

    resource.context.invoke_agent = replay_transport
    state_filter, _loop, tools = tools_at_boundary(resource, actual)
    with pytest.raises(RecordedTransportFailure):
        asyncio.run(state_filter.complete_if_ready(tools, _loop))
    record = resource.journal.receipts(include_payloads=True)[-1]
    assert record["error"]["transport_failure_kind"] == "upstream_server"
    assert record["pre_state_hash"] == action_state_hash(resource.context.action_state)
    assert resource.journal.metadata()["budget"] == actual["budget_at_failure"]


@pytest.mark.parametrize("resource", [True], indirect=True)
def test_automatic_finish_rejection_keeps_original_receipt_but_does_not_resume_main(resource, actual):
    state = resource.context.action_state
    assert action_state_hash(state) == actual["real_finish_receipt"]["pre_state_hash"]
    assert generation._can_finish(state) and not state["completed"]
    recorded = actual["automatic_failure"]["error"]["feedback"]["diagnostic"]["tool_observations"][0]["feedback"]
    error = OutputValidationError(recorded["message"], details=deepcopy(recorded["details"]))
    state_filter, loop, tools = tools_at_boundary(resource, actual, finish_error=error)
    before = deepcopy(resource.journal.metadata()["budget"])
    with pytest.raises(AgentNoProgressError, match="自动完成未通过验收"):
        asyncio.run(state_filter.complete_if_ready(tools, loop))
    receipt = resource.journal.receipts(include_payloads=True)[-1]
    assert receipt["tool_key"] == "finish_generation" and receipt["status"] == "failed"
    assert receipt["error"]["error_type"] == "OutputValidationError"
    assert loop.events[-1]["status"] == "rejected"
    assert resource.journal.metadata()["budget"] == before
    assert not state["completed"] and "test_generation" not in resource.context.artifacts


@pytest.mark.parametrize("resource", [True], indirect=True)
def test_real_approved_finish_still_completes_without_a_model_round(resource, actual):
    state_filter, loop, tools = tools_at_boundary(resource, actual)
    assert "finish_generation" in _feedback_tool_names(actual["main_runtime_config"], tools, [], persistent=True)
    before = deepcopy(resource.journal.metadata()["budget"])
    assert asyncio.run(state_filter.complete_if_ready(tools, loop)) is True
    assert resource.context.action_state["completed"] and loop.has_submission
    assert len(resource.context.artifacts["test_generation"]["test_cases"]) == 70
    assert resource.journal.metadata()["budget"] == before
