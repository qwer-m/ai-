from pathlib import Path
import sys,json,time
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/"backend"))
from sqlalchemy import select,text
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun,AgentNodeRun,AgentRunEvent
OUT=Path(__file__).resolve().parent
last_sequence=0
previous=None
events=[]
for tick in range(45):
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run=db.execute(select(AgentRun.id,AgentRun.status,AgentRun.started_at,AgentRun.finished_at,AgentRun.error_message,AgentRun.run_context["usage"].label("usage"),AgentRun.run_context["confirmation_wait_seconds"].label("wait_seconds")).where(AgentRun.id==65)).mappings().one()
        nodes=db.execute(select(AgentNodeRun.id,AgentNodeRun.status,AgentNodeRun.sdk_state["action_checkpoint"]["projection"].label("projection"),AgentNodeRun.sdk_state["action_checkpoint"]["budget"].label("budget")).where(AgentNodeRun.run_id==65).order_by(AgentNodeRun.id)).mappings().all()
        fresh=db.execute(select(AgentRunEvent.sequence,AgentRunEvent.event_type,AgentRunEvent.payload,AgentRunEvent.created_at).where(AgentRunEvent.run_id==65,AgentRunEvent.sequence>last_sequence).order_by(AgentRunEvent.sequence)).mappings().all()
        events.extend(dict(row) for row in fresh)
        if fresh: last_sequence=fresh[-1]["sequence"]
        observed=datetime.now(timezone.utc)
        data={"observed_at":observed.isoformat(),"run":dict(run),"nodes":[dict(n) for n in nodes],"last_event_sequence":last_sequence}
        key=json.dumps(data["run"],sort_keys=True,default=str)+json.dumps(data["nodes"],sort_keys=True,default=str)
        if key!=previous:
            (OUT/("snapshot-"+observed.strftime("%Y%m%dT%H%M%S")+".json")).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            (OUT/"events.json").write_text(json.dumps(events,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            latest=nodes[-1] if nodes else {}
            print(json.dumps({"time":data["observed_at"],"status":run["status"],"usage":run["usage"],"progress":(latest.get("projection") or {}).get("progress"),"current_action":(latest.get("projection") or {}).get("current_action"),"last_event":last_sequence},ensure_ascii=False),flush=True)
            previous=key
        if run["status"] in {"success","failed","cancelled","waiting_approval"}:
            boundaries=db.execute(select(AgentNodeRun.id,AgentNodeRun.sdk_state["model_requests"].label("model_requests"),AgentNodeRun.sdk_state["tool_observations"].label("tool_observations"),AgentNodeRun.error_message).where(AgentNodeRun.run_id==65)).mappings().all()
            (OUT/"boundary-observations.json").write_text(json.dumps([dict(x) for x in boundaries],ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            (OUT/"terminal-lite.json").write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            db.rollback()
            print("AUDIT_STOP: "+run["status"],flush=True)
            break
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    time.sleep(30)
