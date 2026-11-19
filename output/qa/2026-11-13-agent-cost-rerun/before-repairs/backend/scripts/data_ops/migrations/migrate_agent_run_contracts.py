"""只读校验 AgentRun 是否满足当前运行契约。

历史数据迁移已经完成。本脚本不再回填、重建或解释旧运行；任何缺少当前
任务身份、输入指纹、检查点版本或执行契约的运行都会被报告为不可用，
需要重新创建任务。
"""

from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import core.db.model_defs  # noqa: F401
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.run_contract import (
    CHECKPOINT_SCHEMA_VERSION,
    CONTRACT_SCHEMA_VERSION,
)


def validate() -> tuple[int, list[str]]:
    """返回已检查数量和不满足当前协议的运行说明。"""
    db = SessionLocal()
    invalid: list[str] = []
    try:
        rows = db.query(AgentRun).order_by(AgentRun.id.asc()).all()
        for run in rows:
            context = dict(run.run_context or {})
            contract = context.get("execution_contract")
            problems: list[str] = []
            if not str(context.get("logical_task_id") or "").strip():
                problems.append("logical_task_id")
            if not str(context.get("attempt_id") or "").strip():
                problems.append("attempt_id")
            if not str(context.get("input_hash") or "").strip():
                problems.append("input_hash")
            if context.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION:
                problems.append("checkpoint_schema_version")
            if not isinstance(contract, dict):
                problems.append("execution_contract")
            elif int(contract.get("schema_version") or 0) != CONTRACT_SCHEMA_VERSION:
                problems.append("execution_contract.schema_version")
            elif not str(contract.get("hash") or "").strip():
                problems.append("execution_contract.hash")
            if problems:
                invalid.append(f"Run {run.id}: {', '.join(problems)}")
        return len(rows), invalid
    finally:
        db.close()


def main() -> int:
    checked, invalid = validate()
    print(f"已检查 {checked} 条 AgentRun；当前协议无效 {len(invalid)} 条。")
    for item in invalid:
        print(item)
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
