"""创建第三阶段长期上下文权威记录表。默认只读检查。"""

from __future__ import annotations

import argparse
import os
import sys
from sqlalchemy import inspect

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import core.db.model_defs  # noqa: F401
from core.db.database import SessionLocal
from core.db.model_defs import AgentContextRecord


def migrate(*, apply: bool) -> bool:
    db = SessionLocal()
    try:
        exists = inspect(db.bind).has_table(AgentContextRecord.__tablename__)
        if exists:
            print(f"表 {AgentContextRecord.__tablename__} 已存在。")
            return False
        if apply:
            AgentContextRecord.__table__.create(bind=db.bind, checkfirst=False)
            db.commit()
            print(f"已创建表 {AgentContextRecord.__tablename__}。")
        else:
            db.rollback()
            print(f"只读预览：将创建表 {AgentContextRecord.__tablename__}。")
        return True
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="创建 Agent 长期上下文记录表")
    parser.add_argument("--apply", action="store_true", help="写入真实数据库；默认只读预览")
    args = parser.parse_args()
    migrate(apply=bool(args.apply))


if __name__ == "__main__":
    main()
