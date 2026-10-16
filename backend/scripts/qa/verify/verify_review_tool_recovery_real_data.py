"""用真实终审工具与 Run 28 状态验证拒绝反馈、跨恢复工具额度；不调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from functools import partial
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.tool_context import ToolContext
from agents.exceptions import ModelBehaviorError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentApproval, AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent, AgentToolDefinition, AgentWorkflowDefinition
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLimitError, AutonomousToolLoop
from modules.agent_platform.lifecycle import RunLeaseLost, new_claim_token
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, OutputPostprocessingError, StructuredOutputJSONError,
    StructuredOutputValidationError, ToolArgumentsJSONError, ToolArgumentsValidationError,
    _function_tool,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_review_agent import initialize_review_state
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, sqlite_longtext as _sqlite_longtext
from scripts.qa.verify.verify_review_agent_real_data import load_nodes, rejected, review_arguments
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


async def _invoke(tool, context):
    """每次都是新工具调用，使用 SDK 的真实 ToolContext 与 FunctionTool 入口。"""
    call_id = uuid4().hex
    result = await tool.on_invoke_tool(ToolContext(
        context=context, tool_name=tool.name, tool_call_id=call_id, tool_arguments="{}",
    ), "{}")
    return call_id, result


def verify_tool_recovery(run, workflow, source_node, arguments, state) -> dict:
    agent_spec = next(item for item in BUILTIN_AGENT_SPECS if item["runtime_config"].get("state_initializer"))
    definition = builtin_definition(AgentDefinition, agent_spec, project_id=run.project_id, user_id=run.user_id)
    tool_spec = next(item for item in BUILTIN_TOOL_SPECS if item["tool_key"] == "finish_review")
    tool_definition = builtin_definition(AgentToolDefinition, tool_spec, project_id=run.project_id, user_id=run.user_id)
    token = new_claim_token()
    call_ids = []
    with TemporaryDirectory(prefix="qoder-review-tool-recovery-") as directory:
        uri = "sqlite+pysqlite:///" + (Path(directory) / "review.sqlite").as_posix()
        # 每轮关闭全部连接和 Engine，下一轮只从文件恢复检查点与回执。
        for phase in range(3):
            engine = create_engine(uri)
            try:
                if phase == 0:
                    for model in (AgentWorkflowDefinition, AgentRun, AgentNodeRun, AgentRunEvent, AgentApproval):
                        model.__table__.create(engine)
                with Session(engine, expire_on_commit=False) as db:
                    if phase == 0:
                        local_run, local_node = copy_record(run), copy_record(source_node)
                        local_run.status, local_run.claim_token = "running", token
                        local_run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
                        local_node.status, local_node.sdk_state = "running", {}
                        local_node.input_payload = deepcopy(arguments)
                        db.add_all([copy_record(workflow), local_run, local_node])
                        db.commit()
                    repo = AgentRunRepository(db)
                    journal = AgentActionJournal(repo, run_id=run.id, node_run_id=source_node.id, expected_claim_token=token)
                    if phase == 0:
                        journal.init(state=state, input_hash=action_state_hash(arguments))
                    checkpoint = journal.load()
                    assert checkpoint["state"] == state and len(checkpoint["receipts"]) == phase
                    context = ToolExecutionContext(
                        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                        node_key=source_node.node_key, run_input=deepcopy(run.input_payload),
                        action_input=deepcopy(arguments), action_state=deepcopy(checkpoint["state"]),
                        action_journal=journal, claim_token=token,
                    )
                    contract = AgentOutputContract(deepcopy(arguments))
                    loop = AutonomousToolLoop(
                        terminal_tool_names={tool_definition.tool_key},
                        validate_submission=partial(contract.validate, definition, context),
                        recoverable_errors=(ToolArgumentsJSONError, ToolArgumentsValidationError,
                                            StructuredOutputJSONError, StructuredOutputValidationError,
                                            OutputValidationError),
                        max_tool_calls=2, action_context=context,
                    )
                    actual_tool = _function_tool(tool_definition, context)
                    tool = loop.wrap_tool(actual_tool, allow_feedback=True)
                    assert loop.events == []
                    if phase == 0:
                        # 真实领域拒绝，以及真实后处理器产生的 SDK 包装异常，均可返回业务反馈。
                        direct_error = rejected(lambda: asyncio.run(_invoke(actual_tool, context)), OutputValidationError)
                        assert loop.feedback_for_error(direct_error)["error_type"] == "OutputValidationError"
                        wrapped_error = rejected(
                            lambda: contract.validate(definition, context, {"completed": True}),
                            OutputPostprocessingError,
                        )
                        assert loop.feedback_for_error(wrapped_error)["error_type"] == "OutputValidationError"
                    if phase < 2:
                        call_id, output = asyncio.run(_invoke(tool, context))
                        call_ids.append(call_id)
                        feedback = json.loads(output)
                        assert feedback["status"] == "rejected"
                        assert feedback["feedback"]["error_type"] == "OutputValidationError"
                        saved = journal.load()
                        receipt = saved["receipts"][call_id]
                        assert receipt["status"] == "failed" and receipt["error"]["recoverable"] is True
                        assert receipt["error"]["feedback"] == feedback["feedback"]
                        assert len(saved["receipts"]) == phase + 1
                        assert saved["state"] == context.action_state == state
                        assert len(loop.events) == 1 and loop.events[0]["status"] == "rejected"
                        rejected(loop.require_submission, ModelBehaviorError)
                    else:
                        before = journal.load()
                        events_before = db.query(AgentRunEvent).count()
                        limit_error = rejected(lambda: asyncio.run(_invoke(tool, context)), AutonomousToolLimitError)
                        assert "累计工具调用预算" in str(limit_error)
                        assert loop.feedback_for_error(limit_error) is None
                        assert journal.load() == before and context.action_state == state
                        assert loop.events == [] and db.query(AgentRunEvent).count() == events_before == 4
                        rejected(loop.require_submission, ModelBehaviorError)
                        # 同一真实入口再验证租约过期属于平台异常，不会变成可继续修补的反馈。
                        active = repo.get_run_for_update(run_id=run.id)
                        active.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
                        db.commit()
                        lease_error = rejected(lambda: asyncio.run(_invoke(tool, context)), RunLeaseLost)
                        assert loop.feedback_for_error(lease_error) is None
                        stored = db.get(AgentNodeRun, source_node.id).sdk_state["action_checkpoint"]
                        assert stored == before and context.action_state == state
                        assert db.query(AgentRunEvent).count() == events_before
            finally:
                engine.dispose()
    assert len(set(call_ids)) == 2
    return {
        "sqlite_reopen_count": 2, "real_function_tool": "finish_review", "tool_limit": 2,
        "rejected_actions_persisted": 2, "new_call_ids_across_recovery": True,
        "cumulative_limit_blocked_before_third_action": True, "domain_state_unchanged": True,
        "direct_and_sdk_wrapped_business_errors_have_feedback": True,
        "tool_limit_and_lease_errors_are_not_feedback": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    options = parser.parse_args()
    with SessionLocal() as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        run = online.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("必须提供真实成功运行")
        nodes = load_nodes(online, run.id)
        arguments = review_arguments(run, nodes)
        context = ToolExecutionContext(db=online, user_id=run.user_id, project_id=run.project_id,
                                       run_id=run.id, node_key=nodes["prepare_final_review"].node_key,
                                       run_input=deepcopy(run.input_payload))
        state = initialize_review_state(context, arguments)
        report = verify_tool_recovery(run, online.get(AgentWorkflowDefinition, run.workflow_definition_id),
                                      nodes["prepare_final_review"], arguments, state)
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
        print(json.dumps({"run_id": options.run_id, **report, "model_requests_sent": 0,
                          "online_database_writes": 0, "mysql_concurrency_verified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
