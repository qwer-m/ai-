"""只读采集架构审查所需的真实表结构与聚合，不输出连接凭据或业务正文。"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from core.settings.config import settings


def main() -> None:
    report: dict = {"observed_at": datetime.now(timezone.utc).isoformat(), "mode": "read_only"}
    engine = create_engine(settings.DATABASE_URL, connect_args={"connect_timeout": 3, "read_timeout": 30})
    try:
        with engine.connect() as connection:
            # 会话内禁止写事务；以下只执行元数据和聚合 SELECT。
            connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
            report["server_version"] = connection.execute(text("SELECT VERSION()")).scalar_one()
            report["database_clock"] = dict(connection.execute(text(
                "SELECT @@session.time_zone AS session_zone, @@system_time_zone AS system_zone, "
                "NOW() AS database_now, UTC_TIMESTAMP() AS utc_now"
            )).mappings().one())
            names = connection.execute(text(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
            )).scalars().all()
            selected = [name for name in names if name.startswith(("agent_", "rag_")) or name in {
                "knowledge_documents", "users", "projects", "system_configs"
            }]
            report["selected_tables"] = selected
            report["memory_or_task_tables"] = [name for name in names if any(
                token in name.lower() for token in ("memory", "feedback", "preference", "agent_task")
            )]
            report["tables"] = {}
            for name in selected:
                parameters = {"table_name": name}
                columns = connection.execute(text(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = :table_name ORDER BY ORDINAL_POSITION"
                ), parameters).mappings().all()
                indexes = connection.execute(text(
                    "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME "
                    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = :table_name ORDER BY INDEX_NAME, SEQ_IN_INDEX"
                ), parameters).mappings().all()
                foreign_keys = connection.execute(text(
                    "SELECT k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
                    "r.UPDATE_RULE, r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
                    "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON "
                    "k.CONSTRAINT_SCHEMA = r.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = r.CONSTRAINT_NAME "
                    "AND k.TABLE_NAME = r.TABLE_NAME WHERE k.TABLE_SCHEMA = DATABASE() "
                    "AND k.TABLE_NAME = :table_name AND k.REFERENCED_TABLE_NAME IS NOT NULL"
                ), parameters).mappings().all()
                # 表名来自实际元数据集合；此处不接受用户或模型参数。
                count = connection.exec_driver_sql(f"SELECT COUNT(*) FROM `{name}`").scalar_one()
                report["tables"][name] = {
                    "columns": [dict(row) for row in columns],
                    "indexes": [dict(row) for row in indexes],
                    "foreign_keys": [dict(row) for row in foreign_keys], "row_count": count,
                }
            if "agent_runs" in selected:
                report["run_status_counts"] = [dict(row) for row in connection.execute(text(
                    "SELECT status, COUNT(*) AS count FROM agent_runs GROUP BY status ORDER BY status"
                )).mappings()]
                report["recent_runs"] = [dict(row) for row in connection.execute(text(
                    "SELECT id, project_id, workflow_definition_id, status, current_node_key, parent_run_id, "
                    "created_at, updated_at, finished_at FROM agent_runs ORDER BY id DESC LIMIT 8"
                )).mappings()]
                report["run_sizes"] = dict(connection.execute(text(
                    "SELECT MAX(OCTET_LENGTH(run_context)) AS max_context_bytes, "
                    "MAX(OCTET_LENGTH(output_payload)) AS max_output_bytes FROM agent_runs"
                )).mappings().one())
            if "agent_node_runs" in selected:
                report["checkpoint_sizes"] = dict(connection.execute(text(
                    "SELECT MAX(OCTET_LENGTH(sdk_state)) AS max_sdk_state_bytes, "
                    "SUM(JSON_CONTAINS_PATH(sdk_state, 'one', '$.action_checkpoint')) AS checkpoint_nodes "
                    "FROM agent_node_runs"
                )).mappings().one())
            if "agent_workflow_definitions" in selected:
                report["workflow_modes"] = [dict(row) for row in connection.execute(text(
                    "SELECT workflow_key, version, enabled, JSON_UNQUOTE(JSON_EXTRACT(definition, "
                    "'$.execution_mode')) AS execution_mode, COUNT(*) AS count "
                    "FROM agent_workflow_definitions GROUP BY workflow_key, version, enabled, execution_mode"
                )).mappings()]
            if "knowledge_documents" in selected:
                report["document_status_counts"] = [dict(row) for row in connection.execute(text(
                    "SELECT doc_type, parse_status, COUNT(*) AS count FROM knowledge_documents "
                    "GROUP BY doc_type, parse_status ORDER BY doc_type, parse_status"
                )).mappings()]
                report["duplicate_document_sources"] = dict(connection.execute(text(
                    "SELECT COUNT(*) AS same_project_hash_groups, COALESCE(SUM(n), 0) AS documents "
                    "FROM (SELECT project_id, content_hash, COUNT(*) n FROM knowledge_documents "
                    "WHERE content_hash IS NOT NULL AND content_hash <> '' "
                    "GROUP BY project_id, content_hash HAVING COUNT(*) > 1) grouped_sources"
                )).mappings().one())
                report["duplicate_requirement_sources"] = dict(connection.execute(text(
                    "SELECT COUNT(*) AS same_project_hash_groups, COALESCE(SUM(n), 0) AS documents "
                    "FROM (SELECT project_id, content_hash, COUNT(*) n FROM knowledge_documents "
                    "WHERE content_hash IS NOT NULL AND content_hash <> '' "
                    "AND doc_type IN ('requirement', 'product_requirement', 'incomplete') "
                    "GROUP BY project_id, content_hash HAVING COUNT(*) > 1) grouped_sources"
                )).mappings().one())
            if all(name in selected for name in ("agent_runs", "agent_node_runs", "agent_approvals")):
                from modules.agent_platform.serialization import serialize_run

                def actual_record(row):
                    values = dict(row)
                    for key in ("input_payload", "run_context", "output_payload", "sdk_state", "request_payload", "decision_payload"):
                        if key in values and isinstance(values[key], str):
                            values[key] = json.loads(values[key])
                    return SimpleNamespace(**values)

                # 使用真实行经过当前序列化器计算大小，只保存大小，不保存业务正文。
                report["serialized_run_sizes"] = []
                recent_ids = connection.execute(text("SELECT id FROM agent_runs ORDER BY id DESC LIMIT 8")).scalars().all()
                for run_id in recent_ids:
                    row = connection.execute(text("SELECT * FROM agent_runs WHERE id = :run_id"), {"run_id": run_id}).mappings().one()
                    run = actual_record(row)
                    nodes = [actual_record(value) for value in connection.execute(text(
                        "SELECT * FROM agent_node_runs WHERE run_id = :run_id"
                    ), {"run_id": run.id}).mappings().all()]
                    nodes.sort(key=lambda value: value.id)
                    approvals = [actual_record(value) for value in connection.execute(text(
                        "SELECT * FROM agent_approvals WHERE run_id = :run_id"
                    ), {"run_id": run.id}).mappings().all()]
                    approvals.sort(key=lambda value: value.id)
                    value = serialize_run(run, node_runs=nodes, approvals=approvals)
                    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=lambda value: value.isoformat()).encode("utf-8")
                    report["serialized_run_sizes"].append({"run_id": run.id, "node_count": len(nodes), "json_bytes": len(encoded)})
            connection.rollback()
    except Exception as error:
        # 保留失败类型，不将可能携带主机、账号等信息的异常原文写入报告。
        report["inspection_error_type"] = type(error).__name__
        original_args = getattr(getattr(error, "orig", None), "args", ())
        report["inspection_error_code"] = original_args[0] if original_args and isinstance(original_args[0], int) else None
    finally:
        engine.dispose()
    target = Path(__file__).with_name("database-readonly.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "report": str(target), "selected_table_count": len(report.get("selected_tables", [])),
        "inspection_error_type": report.get("inspection_error_type"),
        "run_status_counts": report.get("run_status_counts"), "checkpoint_sizes": report.get("checkpoint_sizes"),
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
