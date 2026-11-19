"""显式移除已废弃的三张本地计费空表；默认只读预览，非空表始终拒绝自动删除。

--apply 会核对外部引用、锁定固定目标表并重新检查行数，三表均为空才执行 DROP。
若发现非空数据，可用 --apply --backup PATH 导出完整结构和数据后停止，留待人工处置。
本迁移不导入 AI 客户端、不调用供应商、不创建运行，也不触碰原有业务表。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import bindparam, text

from core.db.database import engine


# 请求表先列出，确保其对价格版本的内部外键随同一次 DDL 移除。
TARGET_TABLES = ("model_request_records", "model_price_versions", "model_spend_accounts")


def _inventory(connection):
    existing = set(connection.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name IN :names"
    ).bindparams(bindparam("names", expanding=True)), {"names": TARGET_TABLES}).scalars())
    return {name: {"exists": name in existing,
                   "rows": connection.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
                   if name in existing else None}
            for name in TARGET_TABLES}


def _external_references(connection):
    rows = connection.execute(text(
        "SELECT DISTINCT TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME "
        "FROM information_schema.key_column_usage "
        "WHERE REFERENCED_TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IN :names "
        "AND (TABLE_SCHEMA<>DATABASE() OR TABLE_NAME NOT IN :names)"
    ).bindparams(bindparam("names", expanding=True)), {"names": TARGET_TABLES}).mappings()
    return [dict(row) for row in rows]


def _backup_locked_tables(connection, names, destination):
    """锁内导出精确值；文件只允许新建，不在终端展示任何数据正文。"""
    payload = {"format": "removed-local-model-spend-v1",
               "created_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    for name in names:
        ddl = connection.execute(text(f"SHOW CREATE TABLE `{name}`")).one()[1]
        result = connection.execute(text(f"SELECT * FROM `{name}`"))
        columns = list(result.keys())
        rows = [dict(row) for row in result.mappings()]
        payload["tables"][name] = {"create_table_sql": ddl, "columns": columns,
                                  "row_count": len(rows), "rows": rows}
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        # Decimal 与日期以精确字符串保存；列类型由原始建表 DDL 保留。
        json.dump(payload, output, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        output.flush()
        os.fsync(output.fileno())
    return {"path": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "rows": {name: item["row_count"] for name, item in payload["tables"].items()}}


def migrate(*, apply=False, backup=None):
    report = {"observed_at": datetime.now(timezone.utc).isoformat(),
              "mode": "apply" if apply else "preview", "removed_tables": []}
    with engine.connect() as connection:
        report["tables"] = _inventory(connection)
        report["external_references"] = _external_references(connection)
        names = [name for name in TARGET_TABLES if report["tables"][name]["exists"]]
        if not names:
            report["status"] = "already_absent"
            return report
        report["status"] = ("blocked_external_references" if report["external_references"] else
                            "blocked_nonempty" if any(report["tables"][name]["rows"] for name in names) else
                            "ready_empty_tables_only")
        if not apply or report["external_references"]:
            return report
        # 不能只依赖预览行数：WRITE 锁阻止计数与 DROP 之间插入新数据。
        connection.execute(text("SET SESSION lock_wait_timeout=10"))
        connection.execute(text("SET SESSION innodb_lock_wait_timeout=10"))
        connection.execute(text("LOCK TABLES " + ", ".join(f"`{name}` WRITE" for name in names)))
        try:
            counts = {name: connection.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
                      for name in names}
            for name, count in counts.items():
                report["tables"][name]["rows"] = count
            if any(counts.values()):
                report["status"] = "blocked_nonempty"
                report["reason"] = "目标存在数据，禁止自动删除；备份后仍须人工确认处置。"
                if backup is not None:
                    report["backup"] = _backup_locked_tables(connection, names, backup)
                return report
            # MySQL 8 的单次 DROP 不关闭外键检查；意外外部引用将直接阻止 DDL。
            connection.execute(text("DROP TABLE " + ", ".join(f"`{name}`" for name in names)))
            report["removed_tables"] = names
            report["status"] = "removed_empty_tables"
        finally:
            connection.execute(text("UNLOCK TABLES"))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="仅显式删除核实为空的三张目标表")
    parser.add_argument("--backup", type=Path, help="仅 apply 遇到非空表时新建完整备份，备份后仍拒绝删除")
    parser.add_argument("--output", type=Path, help="另存不含数据正文的迁移报告，不覆盖已有报告")
    options = parser.parse_args()
    if options.backup is not None and not options.apply:
        parser.error("--backup 仅用于 --apply 遇到非空表时的锁内备份；默认预览不写数据备份。")
    if options.output is not None and options.output.exists():
        raise FileExistsError(options.output)
    result = migrate(apply=options.apply, backup=options.backup)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if options.output is not None:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        with options.output.open("x", encoding="utf-8") as report_file:
            report_file.write(rendered)
    print(rendered)
    if options.apply and result["status"].startswith("blocked_"):
        raise SystemExit(2)
