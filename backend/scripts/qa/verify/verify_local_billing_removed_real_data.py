"""只读核验本地计费移除，使用真实配置与运行记录；禁止发送供应商请求。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

external_attempts = []


def prohibit_external_connections(event, args):
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "localhost", "::1"}:
            external_attempts.append(str(address))
            raise RuntimeError("本验证禁止供应商及其他外部连接")


sys.addaudithook(prohibit_external_connections)

from sqlalchemy import event, inspect, text
from core.ai.ai_client import get_client_for_user
from core.ai.usage import normalized_usage
from core.db.database import engine, SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.lifecycle import RunLeaseLost, require_model_request_claim
from routers.system.config import router


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("../output/qa/2026-11-11-local-billing-removed-real-data.json"))
    args = parser.parse_args()
    paths = {route.path for route in router.routes}
    assert not any("/spend" in path for path in paths)
    assert all(any(path.endswith(suffix) for path in paths) for suffix in ("/validate", "/save", "/quota"))
    with SessionLocal() as db:
        before = [dict(row) for row in db.execute(text("SELECT id,status,current_node_key FROM agent_runs ORDER BY id")).mappings()]
        run = db.get(AgentRun, 47)
        client = get_client_for_user(run.user_id, db)
        provider = client.provider
        # 按真实配置构造实际协议，仅验证请求准备不依赖本地价格；不调用 generate。
        request_path, payload, _timeout = provider._prepare_request(
            [{"role": "user", "content": json.dumps(run.input_payload, ensure_ascii=False)}], client.model,
            max_tokens=64, response_mode="text")
        assert payload["model"] == client.model
        identity = SimpleNamespace(run_id=run.id, user_id=run.user_id, project_id=run.project_id,
                                   claim_token=run.claim_token)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        try:
            require_model_request_claim(identity)
        except RunLeaseLost:
            inactive_guard = True
        else:
            raise AssertionError("真实已失败运行仍获准发起模型请求")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any("agent_runs.run_context" in query or "agent_artifact_blobs" in query for query in statements)
    snapshot = json.loads(Path("../output/qa/2026-11-11-crm-run47-source-snapshot.json").read_text(encoding="utf-8"))
    usage_rows = [(node["id"], node["sdk_state"]["usage"]) for node in snapshot["nodes"]
                  if (node.get("sdk_state") or {}).get("usage")]
    for _node_id, raw in usage_rows:
        usage = normalized_usage(raw)
        assert all(usage[key] == raw[key] for key in ("input_tokens", "output_tokens", "total_tokens"))
        assert usage["cached_input_tokens"] is None and usage["uncached_input_tokens"] is None
    with SessionLocal() as db:
        after = [dict(row) for row in db.execute(text("SELECT id,status,current_node_key FROM agent_runs ORDER BY id")).mappings()]
        assert before == after
        remaining_tables = {name: inspect(db.bind).has_table(name) for name in
                            ("model_spend_accounts", "model_price_versions", "model_request_records")}
        assert not any(remaining_tables.values())
    assert usage_rows and inactive_guard and not external_attempts
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "passed": True,
        "local_spend_routes": 0, "local_spend_tables_remaining": remaining_tables,
        "configuration_validation_save_and_request_quota_routes_preserved": True,
        "real_config_request_preparation": {"model": client.model, "path": request_path, "http_sent": False},
        "real_usage_nodes_verified": [identity for identity, _ in usage_rows], "unknown_cache_usage_preserved": True,
        "inactive_run_request_guard_preserved": inactive_guard, "guard_reads_control_columns_only": True,
        "runs_before": before, "runs_after": after, "database_writes": 0, "model_requests_sent": 0,
        "external_connection_attempts": len(external_attempts),
        "limit": "请求协议准备、真实用量和保护边界核验；未发送付费连接验证或重新生成用例。"}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
