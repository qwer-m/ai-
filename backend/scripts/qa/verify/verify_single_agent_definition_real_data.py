"""在真实项目的回滚事务中发布并冻结单主定义，不创建任务或调用模型。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.database import engine, SessionLocal
from core.db.model_defs import AgentDefinition, AgentToolBinding, AgentToolDefinition, AgentWorkflowDefinition, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentProgramDefinition, parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import tool_registry, runtime_registry_signature
from modules.agent_platform.run_contract import build_execution_contract
from modules.agent_platform.seed import seed_builtin_definitions


def fingerprint(db):
    """前后核对真实定义及绑定内容，而不是只比较行数。"""
    records = {model.__tablename__: [dict(row) for row in db.execute(
        select(model.__table__).order_by(model.id)).mappings()]
        for model in (AgentDefinition, AgentToolDefinition, AgentToolBinding, AgentWorkflowDefinition)}
    return action_state_hash(json.loads(json.dumps(records, default=str)))


def verify(run_id):
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            raise ValueError("需要实际运行定位真实项目和用户")
        project_id, user_id = run.project_id, run.user_id
        before = fingerprint(db)
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as db:
                seed_builtin_definitions(db=db, project_id=project_id, user_id=user_id)
                repo = AgentDefinitionRepository(db)
                workflow = repo.get_workflow(project_id=project_id, workflow_key="test_generation")
                execution = parse_execution_definition(workflow.definition)
                assert isinstance(execution, AgentProgramDefinition)
                assert execution.required_artifact_key == "test_generation"
                contract = build_execution_contract(
                    workflow=workflow, execution=execution, definitions=repo,
                    runtime_registry_signature=runtime_registry_signature(),
                    model_client=get_client_for_user(user_id, db),
                )
                main = repo.get_agent(project_id=project_id, agent_key=execution.entry_agent_key)
                tools = repo.list_agent_tools(main.id, project_id=project_id)
                bound = {tool.tool_key for tool in tools}
                assert bound == set(main.runtime_config["tool_keys"])
                assert all(tool_registry.is_state_only(tool.handler_key) for tool in tools)
                assert {"analyze_source", "govern_source", "draft_source_plan", "retrieve_context", "finish_generation"} <= bound
                retired = {"resolve_requirement_evidence", "prepare_source_semantics", "merge_source_semantics",
                           "prepare_source_authority", "merge_source_authority", "prepare_business_plan_batches",
                           "approve_synthesized_test_cases", "validate_test_cases", "prepare_execution_chain",
                           "select_execution_chain", "validate_execution_chain", "persist_test_cases"}
                active = {tool.tool_key for tool in repo.list_tools(project_id=project_id)}
                assert not active & retired
                frozen = next(item for item in contract["agents"] if item["key"] == execution.entry_agent_key)
                assert set(frozen["tool_ids"]) == {tool.id for tool in tools}
                report = {"source_run_id": run_id, "project_id": project_id,
                          "execution_mode": execution.execution_mode, "entry_agent_key": execution.entry_agent_key,
                          "bound_state_only_tool_count": len(tools), "frozen_agent_count": len(contract["agents"]),
                          "frozen_tool_count": len(contract["tools"]), "retired_public_tool_count": len(retired),
                          "model_requests_sent": 0, "new_runs": 0}
                assert outer.is_active
        finally:
            outer.rollback()
    with SessionLocal() as db:
        assert fingerprint(db) == before, "回滚后真实定义或绑定内容被改变"
    return {**report, "definition_and_binding_fingerprints_unchanged": True, "persisted_changes": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
