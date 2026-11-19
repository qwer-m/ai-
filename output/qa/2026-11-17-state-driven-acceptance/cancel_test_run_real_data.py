"""结束本轮已创建的真实验证运行，保留预算和轨迹，不代答人工审批。"""

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/scripts/qa/verify"))
import httpx
from sqlalchemy import text
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, User
from capture_generation_run_real_data import redact

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, required=True)
args = parser.parse_args()
launch = json.loads((args.output_dir / "launch.json").read_text(encoding="utf8"))
with SessionLocal() as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    run = db.get(AgentRun, launch["run_id"])
    assert run and run.project_id == launch["request"]["project_id"]
    assert run.input_payload == launch["request"]["input_payload"]
    assert run.status in {"pending", "running", "waiting_approval", "recovering"}, run.status
    username = db.query(User.username).filter(User.id == run.user_id).scalar()
    run_id, prior_status = run.id, run.status
    db.rollback()
token = create_access_token({"sub": username}, expires_delta=timedelta(minutes=5))
with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30,
                  headers={"Authorization": f"Bearer {token}"}) as client:
    response = client.post(f"/api/agents/runs/{run_id}/cancel")
    response.raise_for_status()
    result = redact(response.json(), [])
(args.output_dir / "cancel-response.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({"run_id": run_id, "prior_status": prior_status,
                  "status": result["run"]["status"], "http_status": response.status_code}, ensure_ascii=False))
