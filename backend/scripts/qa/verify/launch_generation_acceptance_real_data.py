"""只创建一次真实生成验收任务；网络异常后必须先核查数据库，不能盲目重发。"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx
from sqlalchemy import text
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, KnowledgeDocument, User
from modules.agent_platform.registry import runtime_registry_signature


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--case-count", type=int, default=70)
    parser.add_argument("--batch-limit", type=int, default=8)
    parser.add_argument("--requirement-file", type=Path, help="读取本轮真实用户补充；与原PDF分别保存为来源")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    requirement = args.requirement_file.read_text(encoding="utf8") if args.requirement_file else ""
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        username = db.query(User.username).filter(User.id == args.user_id).scalar()
        if not username:
            raise ValueError("实际用户不存在")
        active = db.query(AgentRun.id).filter(AgentRun.user_id == args.user_id,
            AgentRun.project_id == args.project_id,
            AgentRun.status.in_(["pending", "running", "waiting_approval", "recovering"])).all()
        if active:
            raise ValueError(f"项目仍有活动任务: {active}")
        document = db.get(KnowledgeDocument, args.document_id)
        if not document or document.user_id != args.user_id or document.project_id != args.project_id:
            raise ValueError("真实文档与当前用户/项目不一致")
        source = {"document_id": document.id, "filename": document.filename,
                  "content_hash": document.content_hash}
        previous = [row.id for row in db.query(AgentRun.id).filter(
            AgentRun.project_id == args.project_id).all()]
        db.rollback()
    token = create_access_token({"sub": username}, expires_delta=timedelta(minutes=40))
    headers = {"Authorization": f"Bearer {token}"}
    request = {"project_id": args.project_id, "workflow_key": "test_generation",
        "input_payload": {"requirement": requirement, "requirement_doc_id": args.document_id,
            "case_budget": args.case_count, "batch_case_limit": args.batch_limit,
            "disable_result_cache": True}, "stop_on_failure": False}
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=60) as client:
        client.get("/api/health").raise_for_status()
        client.get("/api/agents/catalog", params={"project_id": args.project_id}).raise_for_status()
        launch = {"request": request, "source": source, "previous_run_ids": previous,
            "runtime_signature": runtime_registry_signature(),
            "requested_at": datetime.now(timezone.utc).isoformat()}
        # 排他文件在 POST 前建立，保证脚本失败后不会自动重复付费。
        with (args.output_dir / "create-once.json").open("x", encoding="utf8") as handle:
            json.dump(launch, handle, ensure_ascii=False, indent=2)
        response = client.post("/api/agents/runs", json=request)
        try:
            body = response.json()
        except ValueError:
            body = {"status_code": response.status_code, "body": response.text[:1000]}
        (args.output_dir / "creation-response.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf8")
        response.raise_for_status()
        launch["run_id"] = body["run"]["id"]
        (args.output_dir / "launch.json").write_text(
            json.dumps(launch, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"run_id": launch["run_id"], "status": body["run"]["status"],
                          "source": source, "request": request}, ensure_ascii=False))


if __name__ == "__main__":
    main()
