"""创建 Agent 投递表或幂等提升调度时间精度；默认只预览，不发起任务投递。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.schema import CreateIndex, CreateTable

from core.db.database import SessionLocal
from core.db.model_defs import AgentRunDispatch


def dispatch_datetime_upgrade_sql(columns, *, table_name: str, dialect) -> str | None:
    """只升级现有调度日期列的精度，结构差异仍要求人工审查。"""
    if dialect.name != "mysql":
        return None
    actual = {column["name"]: column for column in columns}
    changes = []
    preparer = dialect.identifier_preparer
    for expected in AgentRunDispatch.__table__.columns:
        if expected.name not in {
            "next_attempt_at", "publish_lease_expires_at", "published_at", "created_at", "updated_at",
        }:
            continue
        column = actual[expected.name]
        if (not isinstance(column["type"], DATETIME)
                or column["nullable"] != expected.nullable or column.get("default") is not None):
            raise ValueError(f"调度时间列定义与当前模型不同，须先审查：{expected.name}")
        precision = column["type"].fsp or 0
        if precision == 6:
            continue
        if precision != 0:
            raise ValueError(f"调度时间列精度不在本次迁移范围：{expected.name}/{precision}")
        nullability = "NULL" if expected.nullable else "NOT NULL"
        changes.append(f"MODIFY COLUMN {preparer.quote(expected.name)} DATETIME(6) {nullability}")
    if not changes:
        return None
    return f"ALTER TABLE {preparer.quote(table_name)} " + ", ".join(changes)


def migrate(*, apply: bool = False) -> bool:
    with SessionLocal() as db:
        inspector = inspect(db.bind)
        if inspector.has_table(AgentRunDispatch.__tablename__):
            columns = inspector.get_columns(AgentRunDispatch.__tablename__)
            actual = {column["name"] for column in columns}
            expected = {column.name for column in AgentRunDispatch.__table__.columns}
            if actual != expected:
                raise ValueError("投递表结构与当前模型不同，须先审查迁移差异")
            actual_unique = {tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints(AgentRunDispatch.__tablename__)}
            if not {("run_id", "generation"), ("task_id",)}.issubset(actual_unique):
                raise ValueError("投递表缺少代际或任务身份唯一约束")
            upgrade = dispatch_datetime_upgrade_sql(
                columns, table_name=AgentRunDispatch.__tablename__, dialect=db.bind.dialect,
            )
            if upgrade is not None:
                if apply:
                    # MySQL DDL 会隐式提交；只在显式 --apply 入口执行，不混入业务事务。
                    db.execute(text(upgrade))
                    db.commit()
                    refreshed = inspect(db.bind).get_columns(AgentRunDispatch.__tablename__)
                    if dispatch_datetime_upgrade_sql(refreshed, table_name=AgentRunDispatch.__tablename__,
                                                    dialect=db.bind.dialect) is not None:
                        raise RuntimeError("Agent 投递时间精度升级后回读不一致")
                    print("已将 Agent 投递日期列升级为 DATETIME(6)；未写入运行或投递任务。")
                else:
                    print("只读预览：将提升 Agent 投递日期精度；未执行 DDL 或任务投递。")
                    print(upgrade)
                db.rollback()
                return True
            print("Agent 投递表及日期精度已符合当前模型，未写入运行或投递任务。")
            db.rollback()
            return False
        if apply:
            AgentRunDispatch.__table__.create(bind=db.bind, checkfirst=False)
            print("已创建 agent_run_dispatches；未写入运行或投递任务。")
        else:
            print("只读预览：将创建 agent_run_dispatches；未执行 DDL 或任务投递。")
            print(CreateTable(AgentRunDispatch.__table__).compile(dialect=db.bind.dialect))
            for index in sorted(AgentRunDispatch.__table__.indexes, key=lambda value: value.name):
                print(CreateIndex(index).compile(dialect=db.bind.dialect))
        db.rollback()
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="创建表或提升投递日期精度，不创建或重投任何 Run")
    migrate(apply=parser.parse_args().apply)
