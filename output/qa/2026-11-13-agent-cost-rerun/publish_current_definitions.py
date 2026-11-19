"""沿现有服务同步真实项目目录，保留冻结历史，记录本轮源码身份。"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from core.db.database import SessionLocal
from core.db.model_defs import Project
from modules.agent_platform.registry import runtime_registry_signature
from modules.agent_platform.service import AgentPlatformService

with SessionLocal() as db:
    projects = db.query(Project.id, Project.user_id).order_by(Project.id).all()
    assert all(user_id is not None for _, user_id in projects)
    service = AgentPlatformService(db)
    for project_id, user_id in projects:
        assert service.ensure_builtins(project_id=project_id, user_id=user_id)
    db.commit()
    report = {"projects": len(projects), "runtime_registry_signature": runtime_registry_signature(),
              "operation": "existing_ensure_builtins", "historical_run_mutations": 0}
Path(__file__).with_name("current-final-definitions-published.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
