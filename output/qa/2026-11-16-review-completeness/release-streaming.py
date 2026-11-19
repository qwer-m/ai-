"""冻结本轮定义并核验真实前置状态，不发送模型请求。"""

import hashlib
import json
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentApproval, Project
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.lifecycle import ACTIVE_RUN_STATUSES
from modules.agent_platform.registry import runtime_registry_signature
from modules.agent_platform.seed import seed_builtin_definitions


def identity(row):
    payload = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def preserved(db, extra_run_ids, extra_approval_ids):
    return {name: {str(key): identity(db.get(model, key)) for key in keys}
            for name, model, keys in [
                ("runs", AgentRun, sorted({69, 70, 71, 72, 73, *extra_run_ids})),
                ("approvals", AgentApproval, sorted({13, 15, 16, 17, *extra_approval_ids})),
            ]}


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-file", type=Path, required=True)
parser.add_argument("--preserve-run-id", type=int, action="append", default=[])
parser.add_argument("--preserve-approval-id", type=int, action="append", default=[])
parser.add_argument("--require-main-stream", action="store_true")
args = parser.parse_args()

with SessionLocal() as db:
    active = db.query(AgentRun.id, AgentRun.project_id, AgentRun.status).filter(
        AgentRun.status.in_(ACTIVE_RUN_STATUSES)).all()
    assert not active, active
    before = preserved(db, args.preserve_run_id, args.preserve_approval_id)
    projects = db.query(Project.id, Project.user_id).all()
    for project_id, user_id in projects:
        seed_builtin_definitions(db=db, project_id=project_id, user_id=user_id)
    db.commit()
    assert preserved(db, args.preserve_run_id, args.preserve_approval_id) == before
    reviewer = AgentDefinitionRepository(db).get_agent(project_id=8, agent_key="test_generation_reviewer")
    main = AgentDefinitionRepository(db).get_agent(project_id=8, agent_key="test_generation_coordinator")
    if args.require_main_stream:
        assert main.runtime_config["stream_response"] is True
    assert reviewer.runtime_config["stream_response"] is True
    assert set(reviewer.output_schema["required"]) == {
        "source_checks", "case_checks", "business_questions", "issue_resolutions"}
    assert "scope_reason" in reviewer.output_schema["properties"]["case_checks"]["items"]["required"]
    assert "additional_evidence_ids" in reviewer.output_schema["properties"]["case_checks"]["items"]["required"]
    result = {"runtime_signature": runtime_registry_signature(), "preserved": before,
              "main": {"id": main.id, "version": main.version, "runtime_config": main.runtime_config},
              "projects_synced": len(projects), "reviewer": {
                  "id": reviewer.id, "version": reviewer.version,
                  "instructions_sha256": hashlib.sha256(reviewer.instructions.encode()).hexdigest(),
                  "runtime_config": reviewer.runtime_config,
                  "schema_required": reviewer.output_schema["required"]},
              "model_requests_sent": 0}
    target = args.output_file
    assert not target.exists(), "发布证据不能覆盖"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({k: v for k, v in result.items() if k != "preserved"}, ensure_ascii=False))
