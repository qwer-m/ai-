"""为全局和项目 Agent 定义建立统一的数据库唯一约束。

默认只读检查并展示 SQL；传入 --apply 才执行 DDL。发现重复定义时停止，
不修改业务数据。应先执行此迁移，再启动使用 project_scope_id 的新版服务。
MySQL DDL 会隐式提交，脚本使用独立连接，不能依赖事务回滚撤销表结构变更。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import Integer, inspect, text
from sqlalchemy.engine import Connection


BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TABLE_NAME = "agent_definitions"
SCOPE_COLUMN = "project_scope_id"
OLD_INDEX = "uq_agent_definition_project_key_version"
NEW_INDEX = "uq_agent_definition_scope_key_version"


def _require_unique_index(index: dict[str, Any], columns: list[str]) -> None:
    if (
        not index.get("unique")
        or index.get("column_names") != columns
        or index.get("dialect_options", {}).get("mysql_length")
    ):
        raise RuntimeError(f"索引 {index['name']} 的实际定义与预期不同，请先人工核查")


def plan_migration(connection: Connection) -> list[str]:
    """根据真实表结构和数据生成一条 ALTER 的操作列表，不执行写入。"""
    if connection.dialect.name != "mysql":
        raise RuntimeError("此迁移仅支持项目当前使用的 MySQL")
    inspector = inspect(connection)
    if not inspector.has_table(TABLE_NAME):
        raise RuntimeError("agent_definitions 表不存在，请先初始化数据库")
    columns = {item["name"]: item for item in inspector.get_columns(TABLE_NAME)}
    for name in ("id", "project_id", "agent_key", "version"):
        if name not in columns:
            raise RuntimeError(f"agent_definitions 缺少预期字段 {name}")
    project_column = columns["project_id"]
    if not project_column["nullable"] or not isinstance(project_column["type"], Integer):
        raise RuntimeError("project_id 必须已迁移为可空整数，全局模板使用 NULL")
    if getattr(project_column["type"], "unsigned", False):
        raise RuntimeError("project_id 是无符号整数，与当前模型不一致，请先核查取值范围")
    if columns["agent_key"]["nullable"] or columns["version"]["nullable"]:
        raise RuntimeError("agent_key 和 version 必须非空，才能保证唯一约束完整生效")

    invalid_projects = connection.execute(text(
        "SELECT id FROM projects WHERE id <= 0 ORDER BY id LIMIT 20"
    )).scalars().all()
    invalid_scopes = connection.execute(text(
        "SELECT id, project_id FROM agent_definitions "
        "WHERE project_id <= 0 ORDER BY id LIMIT 20"
    )).all()
    if invalid_projects or invalid_scopes:
        raise RuntimeError(
            "0 只能用于生成列的全局作用域，现有项目编号必须为正整数；"
            f"异常项目={invalid_projects}，异常定义={invalid_scopes}"
        )
    duplicates = connection.execute(text(
        "SELECT COALESCE(project_id, 0) AS scope_id, agent_key, version, COUNT(*) AS total "
        "FROM agent_definitions GROUP BY COALESCE(project_id, 0), agent_key, version "
        "HAVING COUNT(*) > 1 ORDER BY scope_id, agent_key, version"
    )).mappings().all()
    if duplicates:
        raise RuntimeError(
            "发现重复定义，迁移已停止；请先核查引用关系并明确数据处理方案："
            f"{[dict(row) for row in duplicates]}"
        )

    indexes = {item["name"]: item for item in inspector.get_indexes(TABLE_NAME)}
    if OLD_INDEX not in indexes and NEW_INDEX not in indexes:
        raise RuntimeError("未找到预期的旧或新唯一索引，请先核查现有表结构")
    if OLD_INDEX in indexes:
        _require_unique_index(indexes[OLD_INDEX], ["project_id", "agent_key", "version"])
    if NEW_INDEX in indexes:
        _require_unique_index(indexes[NEW_INDEX], [SCOPE_COLUMN, "agent_key", "version"])
    if not any(
        item["name"] != OLD_INDEX and item["column_names"][:1] == ["project_id"]
        for item in indexes.values()
    ):
        raise RuntimeError("project_id 缺少独立索引，不能移除可能被外键使用的旧唯一索引")

    # 存储生成列的基础字段不能带级联外键动作；项目模型目前使用 RESTRICT。
    for foreign_key in inspector.get_foreign_keys(TABLE_NAME):
        if "project_id" not in foreign_key["constrained_columns"]:
            continue
        options = foreign_key.get("options", {})
        if any(
            str(options.get(action) or "").upper() not in {"", "RESTRICT", "NO ACTION"}
            for action in ("onupdate", "ondelete")
        ):
            raise RuntimeError("project_id 存在级联外键动作，不能直接添加存储生成列")

    operations: list[str] = []
    if SCOPE_COLUMN in columns:
        scope = columns[SCOPE_COLUMN]
        computed = scope.get("computed") or {}
        expression = "".join(str(computed.get("sqltext") or "").lower().split()).replace("`", "")
        if (
            not isinstance(scope["type"], Integer)
            or getattr(scope["type"], "unsigned", False)
            or scope["nullable"]
            or computed.get("persisted") is not True
            or expression not in {"coalesce(project_id,0)", "(coalesce(project_id,0))"}
        ):
            raise RuntimeError("已存在的 project_scope_id 与目标生成列不一致，请先人工核查")
    else:
        operations.append(
            "ADD COLUMN project_scope_id INT GENERATED ALWAYS AS "
            "(coalesce(project_id, 0)) STORED NOT NULL"
        )
    if NEW_INDEX not in indexes:
        operations.append(
            f"ADD UNIQUE INDEX {NEW_INDEX} (project_scope_id, agent_key, version)"
        )
    if OLD_INDEX in indexes:
        operations.append(f"DROP INDEX {OLD_INDEX}")
    return operations


def migrate(*, apply: bool = False) -> None:
    from core.db.database import engine

    with engine.connect() as connection:
        operations = plan_migration(connection)
        if not operations:
            print("检查通过：作用域生成列和唯一约束已存在，无需重复迁移。")
            return
        statement = f"ALTER TABLE {TABLE_NAME}\n  " + ",\n  ".join(operations)
        print(statement + ";")
        if not apply:
            print("只读检查通过，尚未执行 DDL；使用 --apply 应用上述迁移。")
            return
        # 同一条 ALTER 完成新增约束和移除旧索引，避免中间状态失去唯一保护。
        connection.commit()
        connection.execute(text(statement))
        connection.commit()
        if plan_migration(connection):
            raise RuntimeError("DDL 已执行，但最终结构验证未通过，请检查数据库状态")
        print("迁移完成：全局模板和项目覆盖均由作用域、Agent 标识、版本联合唯一约束保护。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="通过只读检查后实际执行 DDL")
    arguments = parser.parse_args()
    try:
        migrate(apply=arguments.apply)
    except RuntimeError as exc:
        print(f"迁移停止：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
