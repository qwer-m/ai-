"""将项目级内置 Agent 收敛为全局模板。

脚本只处理 builtin=True 的旧项目副本；项目自定义定义（builtin=False）不会被修改。
执行前应完成数据库备份，脚本在单个事务内迁移定义、节点运行引用和工具绑定。
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import core.db.model_defs  # noqa: F401
from core.db.database import engine
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentToolBinding
from modules.agent_platform.registry import BUILTIN_AGENT_SPECS


def migrate() -> None:
    active_keys = {str(spec["agent_key"]) for spec in BUILTIN_AGENT_SPECS}
    with engine.begin() as connection:
        # 旧表是 NOT NULL；先放开 project_id，NULL 才能表示全局模板。
        connection.execute(
            text("ALTER TABLE agent_definitions MODIFY COLUMN project_id INT NULL")
        )

    with Session(engine) as db:
        # 先把全局模板按代码事实源写入，seed 不会创建任何项目副本。
        from modules.agent_platform.seed import seed_builtin_definitions

        project_ids = [
            int(value)
            for (value,) in db.query(AgentDefinition.project_id)
            .filter(AgentDefinition.project_id.is_not(None))
            .distinct()
            .all()
        ]
        if not project_ids:
            print("迁移已完成：未发现项目级内置副本，无需重复处理。")
            return
        seed_user_id = int(
            db.query(AgentDefinition.user_id)
            .filter(AgentDefinition.user_id.is_not(None))
            .order_by(AgentDefinition.user_id.asc())
            .first()[0]
        )
        seed_builtin_definitions(
            db=db,
            project_id=project_ids[0] if project_ids else 0,
            user_id=seed_user_id,
        )

        global_rows = {
            str(row.agent_key): row
            for row in db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id.is_(None),
                AgentDefinition.builtin.is_(True),
                AgentDefinition.agent_key.in_(active_keys),
                AgentDefinition.enabled.is_(True),
            )
            .all()
        }
        if set(global_rows) != active_keys:
            missing = sorted(active_keys - set(global_rows))
            raise RuntimeError(f"全局模板迁移不完整，缺少: {missing}")

        old_rows = (
            db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id.is_not(None),
                AgentDefinition.builtin.is_(True),
            )
            .all()
        )
        old_ids = [int(row.id) for row in old_rows]
        for row in old_rows:
            target = global_rows.get(str(row.agent_key))
            if target is not None:
                db.query(AgentNodeRun).filter(
                    AgentNodeRun.agent_definition_id == row.id
                ).update(
                    {AgentNodeRun.agent_definition_id: target.id},
                    synchronize_session=False,
                )

        if old_ids:
            db.query(AgentToolBinding).filter(
                AgentToolBinding.agent_definition_id.in_(old_ids)
            ).delete(synchronize_session=False)
            db.query(AgentDefinition).filter(
                AgentDefinition.id.in_(old_ids)
            ).delete(synchronize_session=False)
        db.commit()
        print(
            f"迁移完成：全局模板 {len(global_rows)} 条，删除旧项目内置副本 {len(old_ids)} 条。"
        )


if __name__ == "__main__":
    migrate()
