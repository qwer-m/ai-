"""把现有 DAG 定义升级为显式 execution_mode。"""

from __future__ import annotations

import argparse
import os
import sys
from copy import deepcopy

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from core.db.database import SessionLocal
from core.db.model_defs import AgentWorkflowDefinition


def migrate(*, apply: bool) -> tuple[int, int]:
    db = SessionLocal()
    pending = 0
    invalid = 0
    try:
        rows = db.query(AgentWorkflowDefinition).order_by(AgentWorkflowDefinition.id.asc()).all()
        for row in rows:
            definition = deepcopy(dict(row.definition or {}))
            mode = definition.get("execution_mode")
            if mode is None:
                if not isinstance(definition.get("nodes"), list) or not definition.get("output_node_key"):
                    invalid += 1
                    print(f"无法升级工作流 {row.id}：缺少当前 DAG 必需字段")
                    continue
                definition["execution_mode"] = "dag"
                pending += 1
                if apply:
                    row.definition = definition
                    db.add(row)
            elif mode not in {"dag", "agent_network"}:
                invalid += 1
                print(f"工作流 {row.id} 的 execution_mode 无效：{mode}")
        if apply:
            if invalid:
                db.rollback()
                raise RuntimeError(f"存在 {invalid} 条无法升级的工作流定义，事务已回滚")
            db.commit()
        else:
            db.rollback()
        return pending, invalid
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="升级工作流为显式 execution_mode")
    parser.add_argument("--apply", action="store_true", help="写入真实数据库；默认只读预览")
    args = parser.parse_args()
    pending, invalid = migrate(apply=bool(args.apply))
    print(f"{'已写入' if args.apply else '只读预览'}：待升级 {pending} 条，无效 {invalid} 条。")


if __name__ == "__main__":
    main()
