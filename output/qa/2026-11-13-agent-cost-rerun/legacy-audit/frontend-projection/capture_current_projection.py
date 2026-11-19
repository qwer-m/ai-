"""用真实63来源只读执行当前初始化器，保存尚未生成的CPU投影。"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import event, text
from sqlalchemy.orm import Session

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from core.db.database import engine
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_agent import initialize_generation_state, observe_generation_state


statements = []


def read_only(connection, cursor, statement, parameters, context, executemany):
    command = statement.lstrip().split(None, 1)[0].upper()
    if command != "SELECT":
        raise AssertionError(f"CPU验证禁止数据库变更：{command}")
    statements.append(command)


protected_sql = text("""SELECT id, status,
 SHA2(CAST(input_payload AS CHAR),256) AS input_hash,
 SHA2(CAST(run_context AS CHAR),256) AS context_hash,
 SHA2(CAST(output_payload AS CHAR),256) AS output_hash
 FROM agent_runs WHERE id=63""")

with engine.connect() as connection:
    connection.exec_driver_sql("START TRANSACTION READ ONLY")
    event.listen(connection, "before_cursor_execute", read_only)
    try:
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
            before = dict(db.execute(protected_sql).mappings().one())
            run = db.get(AgentRun, 63)
            assert run.status == "failed"
            original_input = deepcopy(run.input_payload)
            # 输入只取实际需求与数量；20是当前正式输入上限，不迁移旧专家状态。
            arguments = {key: deepcopy(original_input[key]) for key in ("requirement", "requirement_doc_id", "case_budget")}
            arguments["batch_case_limit"] = 20
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                           run_id=run.id, node_key="agent_network", run_input=deepcopy(arguments))
            state = initialize_generation_state(context, arguments)
            observation = observe_generation_state(context)
            assert not state["completed"] and not state["cases"]["items"] and state["review"] is None
            assert run.input_payload == original_input and not db.new and not db.dirty and not db.deleted
            after = dict(db.execute(protected_sql).mappings().one())
            assert before == after
            module = ROOT / "backend/modules/agent_platform/test_generation_agent.py"
            result = {
                "provenance": {
                    "kind": "current_domain_initializer_cpu_only_on_actual_run63_source",
                    "source_run_id": 63, "source_run_status": run.status,
                    "source_document_id": state["source_ref"]["document_id"],
                    "source_content_hash": state["source_ref"]["content_hash"],
                    "source_input_sha256": action_state_hash(original_input),
                    "current_input_sha256": action_state_hash(arguments),
                    "current_module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
                    "execution": "当前initialize_generation_state与observe_generation_state；只读事务，结束后回滚",
                    "database_writes": 0, "model_requests": 0,
                    "old_domain_state_migrated": False, "online_generation_accepted": False,
                    "new_business_or_model_results": False,
                    "approved_decisions_count": len(state["approved_decisions"]),
                    "evidence_catalog_items": len(state["evidence_catalog"]["items"]),
                    "state_domain_sha256": state["domain_sha256"],
                },
                "input": arguments,
                "projection": deepcopy(state["projection"]),
                "observation": {key: deepcopy(observation[key]) for key in (
                    "progress", "versions", "completed", "can_finish", "available_actions")},
                "protected_run_before": before,
                "protected_run_after": after,
            }
    finally:
        connection.rollback()
        event.remove(connection, "before_cursor_execute", read_only)

result["provenance"]["sql_commands"] = dict(Counter(statements))
result["data_sha256"] = action_state_hash(result)
target = ROOT / "frontend/src/components/agent-platform/fixtures/run63.current-domain-initialization.json"
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"fixture": str(target.relative_to(ROOT)), "bytes": target.stat().st_size,
                  "data_sha256": result["data_sha256"], "provenance": result["provenance"],
                  "projection": result["projection"]}, ensure_ascii=False))
