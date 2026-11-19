"""只读采集真实生成 Run；轻量监控不展开领域正文，完整终态按节点 ID 单独读取。"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

_PRIVATE_KEYS = {
    "api_key", "apikey", "authorization", "access_token", "refresh_token", "password",
    "secret", "client_secret", "database_url", "connection_string", "base_url", "api_base",
    "endpoint", "proxy_url", "claim_token", "model_endpoint", "model_api_url",
}


def redact(value, paths, path=""):
    """认证和连接配置不进入采集文件；业务正文及用量保留原值。"""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if str(key).lower().replace("-", "_") in _PRIVATE_KEYS and item:
                result[key] = "[已隐去]"
                paths.append(current)
            else:
                result[key] = redact(item, paths, current)
        return result
    if isinstance(value, list):
        return [redact(item, paths, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, str):
        # 只隐藏数据库连接及带认证的 URL，保留需求正文中的普通业务链接。
        return re.sub(r"(?i)\b(?:mysql(?:\+[\w]+)?://[^\s\"'<>]+|https?://[^/\s\"'<>]+@[^\s\"'<>]+)",
                      "[地址已隐去]", value)
    return value


def _dump_row(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def capture(run_id, *, summary):
    # 数据库模块的连接提示不写入标准输出，连接异常也由 main 统一脱敏。
    with redirect_stdout(io.StringIO()):
        from core.db.database import SessionLocal
        from core.db.model_defs import AgentApproval, AgentNodeRun, AgentRun, AgentRunEvent
        from modules.agent_platform.checkpoint_storage import hydrate_action_checkpoint
    from sqlalchemy import func, select, text

    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.execute(select(
            AgentRun.id, AgentRun.project_id, AgentRun.workflow_definition_id, AgentRun.status,
            AgentRun.current_node_key, AgentRun.error_message, AgentRun.created_at, AgentRun.started_at,
            AgentRun.finished_at, AgentRun.updated_at, AgentRun.heartbeat_at,
            AgentRun.run_context["usage"].label("usage"),
        ).where(AgentRun.id == run_id)).mappings().one_or_none()
        if run is None:
            raise ValueError("运行不存在")
        if not summary and run["status"] not in {"success", "failed", "cancelled"}:
            raise ValueError("完整快照仅采集终态运行；进行中的运行请使用 --summary")
        node_ids = list(db.scalars(select(AgentNodeRun.id).where(AgentNodeRun.run_id == run_id)
                                  .order_by(AgentNodeRun.id)))
        nodes, checkpoints = [], {}
        for node_id in node_ids:
            if summary:
                # 仅对单个主键读取薄投影，禁止让 MySQL 对大 JSON 行排序。
                fields = [AgentNodeRun.id, AgentNodeRun.node_key, AgentNodeRun.status, AgentNodeRun.attempt,
                          AgentNodeRun.error_message, AgentNodeRun.started_at, AgentNodeRun.finished_at]
                for label, path in {
                    "checkpoint_projection": "$.action_checkpoint.projection",
                    "state_hash": "$.action_checkpoint.state_hash",
                    "reported_usage": "$.action_checkpoint.budget.reported_usage",
                    "charged": "$.action_checkpoint.budget.charged",
                    "limits": "$.action_checkpoint.budget.limits",
                    "pending_requests": "$.action_checkpoint.budget.pending_requests",
                    "latest_model_request": "$.model_requests[last]",
                    "exception_chain": "$.error_diagnostic.exception_chain",
                    "diagnostic_details": "$.error_diagnostic.details",
                    "latest_failed_model_request": "$.error_diagnostic.failed_model_requests[last]",
                }.items():
                    fields.append(func.json_extract(AgentNodeRun.sdk_state, path).label(label))
                row = dict(db.execute(select(*fields).where(AgentNodeRun.id == node_id)).mappings().one())
                for field in ("checkpoint_projection", "state_hash", "reported_usage", "charged", "limits",
                              "pending_requests", "latest_model_request", "exception_chain",
                              "diagnostic_details", "latest_failed_model_request"):
                    row[field] = _json_value(row[field])
                nodes.append(row)
            else:
                node = db.get(AgentNodeRun, node_id)
                nodes.append(_dump_row(node))
                checkpoints[str(node_id)] = hydrate_action_checkpoint(db, node)
        # 先对轻 ID 排序，再逐主键读取 JSON；不对完整事件或审批正文执行排序。
        event_ids_query = select(AgentRunEvent.id).where(AgentRunEvent.run_id == run_id)
        event_ids_query = event_ids_query.order_by(AgentRunEvent.sequence.desc()).limit(12) if summary else (
            event_ids_query.order_by(AgentRunEvent.sequence))
        event_ids = list(db.scalars(event_ids_query))
        events = []
        for event_id in event_ids:
            if summary:
                row = dict(db.execute(select(
                    AgentRunEvent.id, AgentRunEvent.sequence, AgentRunEvent.event_type, AgentRunEvent.created_at,
                    *[AgentRunEvent.payload[key].label(key) for key in
                      ("message", "error_type", "failure_kind", "tool_key", "status", "tool_call_id")],
                ).where(AgentRunEvent.id == event_id)).mappings().one())
                events.append(row)
            else:
                events.append(_dump_row(db.get(AgentRunEvent, event_id)))
        approval_ids = list(db.scalars(select(AgentApproval.id).where(AgentApproval.run_id == run_id)
                                      .order_by(AgentApproval.id)))
        approvals = []
        for approval_id in approval_ids:
            if summary:
                approvals.append(dict(db.execute(select(
                    AgentApproval.id, AgentApproval.node_run_id, AgentApproval.status,
                    AgentApproval.requested_at, AgentApproval.decided_at,
                ).where(AgentApproval.id == approval_id)).mappings().one()))
            else:
                approvals.append(_dump_row(db.get(AgentApproval, approval_id)))
        result = {
            "capture": {"run_id": run_id, "mode": "summary" if summary else "terminal_snapshot",
                        "captured_at": datetime.now(timezone.utc).isoformat(), "model_requests_sent": 0,
                        "database_writes": False, "transaction_read_only": True},
            "run": dict(run) if summary else _dump_row(db.get(AgentRun, run_id)),
            "nodes": nodes, "approvals": approvals, "events": events,
        }
        if not summary:
            result["expanded_checkpoints"] = checkpoints
        db.rollback()
    redactions = []
    result = redact(result, redactions)
    result["capture"]["redacted_fields"] = redactions
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", action="store_true", help="只读薄投影和最近诊断，适合进行中监控")
    args = parser.parse_args()
    if args.run_id <= 0:
        parser.error("--run-id 必须为正整数")
    try:
        result = capture(args.run_id, summary=args.summary)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        name = "summary.json" if args.summary else "terminal-snapshot.json"
        target = args.output_dir / name
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(target)
        print(json.dumps({"artifact_path": str(target.resolve()), "run_id": args.run_id,
                          "status": result["run"]["status"], "mode": result["capture"]["mode"],
                          "node_count": len(result["nodes"]), "database_writes": False,
                          "model_requests_sent": 0}, ensure_ascii=False))
    except Exception as error:
        # 连接异常可能内含认证 URL，禁止把 str(error) 或 traceback 直接输出。
        allowed = {"运行不存在", "完整快照仅采集终态运行；进行中的运行请使用 --summary"}
        message = str(error) if type(error) is ValueError and str(error) in allowed else "只读采集失败，连接与认证信息已隐藏"
        print(json.dumps({"run_id": args.run_id, "status": "capture_failed",
                          "error_type": type(error).__name__, "message": message}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
