"""用已保存的真实 504 回执离线验证诊断，不请求模型、不写线上数据库。"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx
from agents import RunContextWrapper
from agents.exceptions import UserError
from agents.items import ItemHelpers
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from openai import InternalServerError

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import (
    ModelRequestDiagnostics, ObservedRunHooks, safe_exception_evidence, unwrap_sdk_execution_error,
)
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_failure_kind, _is_retryable_agent_error, _persistent_error_message
from modules.agent_platform.sdk_adapter import _build_sdk_agent, _runner_input
from modules.agent_platform.test_generation_review import plan_final_review_repairs


async def verify() -> dict:
    workspace = Path(__file__).resolve().parents[4]
    evidence_path = workspace / "output/qa/2026-09-20-review-agent-run28-durable-real-model.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    source = Path(evidence["isolated_action_database"])
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    clients = []
    with TemporaryDirectory(prefix="agent-request-diagnostics-") as directory:
        copied = Path(directory) / "actions.sqlite"
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(copied)) as destination:
                original.backup(destination)
        engine = create_engine("sqlite+pysqlite:///" + copied.as_posix())
        try:
            with Session(engine, expire_on_commit=False) as db, SessionLocal() as online:
                online.execute(text("SET TRANSACTION READ ONLY"))
                run = db.get(AgentRun, evidence["source_run_id"])
                node = db.get(AgentNodeRun, evidence["isolated_node_run_id"])
                run.claim_token = new_claim_token()
                run.lease_expires_at = datetime.utcnow() + timedelta(minutes=5)
                db.commit()
                journal = AgentActionJournal(AgentRunRepository(db), run_id=run.id, node_run_id=node.id,
                                             expected_claim_token=run.claim_token)
                saved = journal.load()
                failed = next(item for item in saved["receipts"].values()
                              if json.loads(item["arguments"]["arguments_json"])["batch_id"] == "R-003")
                assert failed["error"]["error_type"] == "InternalServerError"
                body = failed["error"]["message"].encode("utf-8")
                assert b"504 Gateway Time-out" in body
                raw = node.input_payload
                context = ToolExecutionContext(db=online, user_id=run.user_id, project_id=run.project_id,
                                               run_id=run.id, node_key=node.node_key, run_input=run.input_payload)
                index = next(i for i, item in enumerate(raw["review_inputs"])
                             if item["review_batch"]["batch_id"] == "R-003")
                plan = plan_final_review_repairs(context, {
                    "review_inputs": [raw["review_inputs"][index]],
                    "review_records": [{"item_index": 0, "output": raw["review_records"][index]["output"]}],
                    "generation_inputs": raw["generation_inputs"],
                })
                actual_input = plan["items"][0]
                recorded_call = evidence["tool_calls"][-1]
                assert action_state_hash(actual_input) == recorded_call["input_hash"]
                definition_id = next(item["agent_definition_id"] for item in evidence["specialists"]
                                     if item["agent_key"] == "test_generation_batch_repairer")
                definition = online.get(AgentDefinition, definition_id)
                client = get_client_for_user(run.user_id, online)
                diagnostics = ModelRequestDiagnostics(journal=journal)
                agent, _ = _build_sdk_agent(
                    db=online, client=client, agent_definition=definition, execution_context=context,
                    allow_subagents=False, allocated_clients=clients, request_timeout_seconds=180,
                    disable_server_output_schema=True, request_diagnostics=diagnostics,
                )
                journal.begin(tool_call_id="diagnostic_replay_" + failed["tool_call_id"],
                              tool_key=failed["tool_key"], args=failed["arguments"], prehash=saved["state_hash"])
                budget = AutonomousBudgetHooks(evidence["request_reservation"])
                hooks = ObservedRunHooks(diagnostics, budget)
                wrapper = RunContextWrapper(context=context)
                inputs = ItemHelpers.input_to_new_input_list(_runner_input(
                    execution_context=context, input_payload=actual_input, runtime_config=definition.runtime_config,
                ))
                await hooks.on_llm_start(wrapper, agent, await agent.get_system_prompt(wrapper), inputs)
                before = budget.snapshot()
                # 这里只重建已保存的 HTML 错误，不宣称取得当时未保存的 HTTP 响应头或真实新响应。
                response = httpx.Response(504, content=body, request=httpx.Request("POST", client.provider.base_url))
                transport = InternalServerError(failed["error"]["message"], response=response, body=None)
                wrapped = UserError("专业工具执行失败")
                wrapped.__cause__ = transport
                diagnostics.record_failure(wrapped, execution_context=context)
                record = diagnostics.requests[-1]
                assert record["batch_id"] == "R-003" and record["run_id"] == run.id
                assert record["node_run_id"] == node.id and record["agent_key"] == definition.agent_key
                assert record["measured_context_bytes"] == 22092
                assert record["context_measurement_scope"] == "sdk_context_json_not_http_wire"
                assert record["status"] == "ended_without_model_response"
                assert record["finished_at"] >= record["started_at"] and record["elapsed_seconds"] >= 0
                assert budget.snapshot() == before and before["pending_requests"] == 1
                errors = safe_exception_evidence(wrapped)
                assert [item["error_type"] for item in errors] == ["UserError", "InternalServerError"]
                assert errors[1]["html_title"] == "504 Gateway Time-out"
                assert errors[1]["response_body_sha256"] == hashlib.sha256(body).hexdigest()
                normalized = unwrap_sdk_execution_error(wrapped)
                assert normalized is transport and _is_retryable_agent_error(normalized)
                assert _agent_failure_kind(normalized) == "upstream_server"
                assert "<html>" not in _persistent_error_message(normalized)
                safe_text = json.dumps(record, ensure_ascii=False)
                assert "new-api.leleketang.com" not in safe_text and "<html>" not in safe_text
                diagnostics.record_failure(wrapped)
                assert len(diagnostics.failure_evidence()) == 1
                online.rollback()
                result = {"status": "passed", "model_requests_sent": 0,
                          "verification_scope": "persisted_504_receipt_offline_reconstruction",
                          "original_headers_available": False, "source_report": str(evidence_path),
                          "request_diagnostic": record, "budget_unchanged_after_failure": True,
                          "sdk_wrapper_restored_transport_type": type(normalized).__name__,
                          "runtime_failure_kind": _agent_failure_kind(normalized)}
        finally:
            await asyncio.gather(*(client.close() for client in clients))
            engine.dispose()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
    result["source_evidence_unchanged"] = True
    return result


if __name__ == "__main__":
    result = asyncio.run(verify())
    destination = Path(__file__).resolve().parents[3] / "artifacts/verification/request_diagnostics_run28.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(destination), "model_requests_sent": 0}, ensure_ascii=False))
