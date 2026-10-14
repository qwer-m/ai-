"""只读提取真实运行，在独立 SQLite 验证终态提交的租约边界。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent, AgentWorkflowDefinition
from modules.agent_platform.lifecycle import RunLeaseLost, new_claim_token
from modules.agent_platform.runtime import _finalize_workflow_success, _handle_workflow_exception
from scripts.qa.verify.verify_lifecycle_sources_real_data import isolated_repository


def _columns(record: Any) -> dict[str, Any]:
    return {column.key: deepcopy(getattr(record, column.key)) for column in record.__table__.columns}


def verify_terminal_claim(
    run: AgentRun, node: AgentNodeRun, workflow: AgentWorkflowDefinition,
    *, terminal: str, claim_state: str,
) -> dict[str, Any]:
    """仅改变真实记录副本的生命周期字段，不替换处理器、模型或业务产物。"""

    with isolated_repository(workflow, run, node) as repo:
        active = repo.get_run(run_id=run.id)
        active_node = repo.db.get(AgentNodeRun, node.id)
        claimed_token = new_claim_token()
        active.status = "running"
        active.finished_at = None
        active.current_node_key = active_node.node_key
        active.claim_token = new_claim_token() if claim_state == "replaced" else claimed_token
        active.lease_expires_at = datetime.utcnow() + timedelta(
            seconds=-1 if claim_state == "expired" else 600,
        )
        if terminal == "failure":
            active_node.status = "running"
            active_node.finished_at = None
        repo.db.commit()
        before = (_columns(active), _columns(active_node))
        operation_error = RunLeaseLost("真实运行副本的终态租约回放")
        caught = None
        result = None
        try:
            if terminal == "success":
                result = _finalize_workflow_success(
                    repo, active, active.id, claimed_token, active_node.node_key,
                    {active_node.node_key: deepcopy(active_node.output_payload)},
                )
            else:
                _handle_workflow_exception(repo, active.id, claimed_token, operation_error)
        except RunLeaseLost as error:
            caught = error
        # 拒绝路径由工作流外层回滚；这里同样关闭读取事务后再验证持久记录。
        repo.db.rollback()
        repo.db.refresh(active)
        repo.db.refresh(active_node)
        events = [event.event_type for event in repo.db.query(AgentRunEvent).all()]
        if claim_state in {"expired", "replaced"}:
            assert (_columns(active), _columns(active_node)) == before
            assert events == []
            if claim_state == "expired" or terminal == "failure":
                assert isinstance(caught, RunLeaseLost)
            else:
                assert result["status"] == "not_claimed"
        elif terminal == "success":
            assert caught is None and result["status"] == "success"
            assert active.status == "success" and events == ["run_completed"]
        else:
            assert caught is operation_error
            assert active.status == "failed" and active_node.status == "failed"
            assert events == ["run_failed"]
        return {
            "terminal_path": terminal, "claim_state": claim_state,
            "run_status": active.status, "event_types": events,
            "verified": True,
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
        node_id = online.query(AgentNodeRun.id).filter(
            AgentNodeRun.run_id == run.id, AgentNodeRun.status == "success",
        ).order_by(AgentNodeRun.id.desc()).first()
        if node_id is None:
            raise ValueError("真实运行缺少成功节点")
        node = online.get(AgentNodeRun, node_id[0])
        workflow = online.get(AgentWorkflowDefinition, run.workflow_definition_id)
        if workflow is None:
            raise ValueError("真实运行缺少工作流定义")
        before = (_columns(run), _columns(node), _columns(workflow))
        checks = [
            verify_terminal_claim(run, node, workflow, terminal=terminal, claim_state=claim_state)
            for terminal in ("success", "failure")
            for claim_state in ("expired", "replaced", "valid")
        ]
        for record in (run, node, workflow):
            online.refresh(record)
        assert (_columns(run), _columns(node), _columns(workflow)) == before
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
        print(json.dumps({
            "run_id": options.run_id, "checks": checks,
            "model_requests_sent": 0, "online_database_writes": 0,
            "validation_scope": "真实 ORM 记录副本与终态处理器；未验证 MySQL 多进程锁并发",
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
