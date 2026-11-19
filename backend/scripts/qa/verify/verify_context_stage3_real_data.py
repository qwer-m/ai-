"""只读真实 v3 检查点，核对短期状态按需取数和已确认项目反馈的实际可用性。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select

from core.db.database import SessionLocal
from core.db.model_defs import AgentContextRecord, AgentNodeRun, AgentRun, Project
from modules.agent_platform.checkpoint_storage import compact_checkpoint, hydrate_checkpoint_state
from modules.agent_platform.context_agent_tools import retrieve_context
from modules.agent_platform.registry import ToolExecutionContext
from modules.knowledge_base_components.context.context_memory_repository import ContextMemoryRepository
from modules.knowledge_base_components.context.context_provider import (
    OnDemandContextProvider, ShortTermStateContextSourcePort,
)
from modules.knowledge_base_components.ports.context_memory_port import ContextBudget, ContextQuery


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _at_pointer(value, pointer):
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def verify(*, run_id: int, query_text: str, max_chars: int = 8000) -> dict:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            raise LookupError("指定的真实运行不存在")
        ids = db.scalars(select(AgentNodeRun.id).where(AgentNodeRun.run_id == run_id)
                         .order_by(AgentNodeRun.id.desc())).all()
        node = next((item for node_id in ids if (item := db.get(AgentNodeRun, node_id)) is not None
                     and (item.sdk_state or {}).get("action_checkpoint")), None)
        if node is None:
            raise LookupError("真实运行没有可读取的持久检查点")
        before_count = db.query(AgentContextRecord).count()
        before_hash = _hash([run.input_payload, run.run_context, run.status, node.sdk_state, node.status])
        checkpoint = compact_checkpoint(node)
        state = hydrate_checkpoint_state(db, node, checkpoint=checkpoint)
        raw_state_chars = len(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        source = ShortTermStateContextSourcePort(lambda: state, run_id=run.id, node_key=node.node_key,
                                                user_id=run.user_id, project_id=run.project_id)
        query = ContextQuery(text=query_text, user_id=run.user_id, project_id=run.project_id,
                             source_kinds=("short_term_state",), max_candidates=5)
        budget = ContextBudget(max_candidates=5, max_chars=max_chars)
        provider = OnDemandContextProvider({source.source_kind: source})
        bundle = provider.retrieve(query=query, budget=budget)
        safe_state, _ = source._safe_state(state)
        checks = {
            "v3_reference_checkpoint": checkpoint["version"] == 3 and "state" not in checkpoint,
            "real_state_identity": all(item.evidence["state_hash"] == _hash(state)
                                       for item in bundle.manifest.selected),
            "rendered_context_within_budget": len(bundle.rendered_text) <= max_chars,
            "selected_real_state": bool(bundle.manifest.selected),
        }
        other_project_id = db.scalar(select(Project.id).where(Project.id != run.project_id)
                                     .order_by(Project.id).limit(1))
        if other_project_id is not None:
            foreign = source.retrieve(ContextQuery(text=query_text, user_id=run.user_id,
                                                  project_id=other_project_id,
                                                  source_kinds=("short_term_state",)), budget=budget)
            checks["other_real_project_cannot_relabel_state"] = all(
                not item.text and item.status == "unavailable"
                and item.evidence["reason"] == "context_scope_mismatch" for item in foreign)
        for item in bundle.manifest.selected:
            body = json.loads(item.text)
            kind = item.evidence["fragment_kind"]
            if kind == "complete_state":
                assert body == safe_state
            elif kind == "complete_structured_unit":
                actual = _at_pointer(safe_state, item.evidence["state_path"])
                assert body["value"] == actual
            else:
                assert kind == "structure_index" and body["state_sha256"] == _hash(state)
            assert item.source.content_hash == hashlib.sha256(item.text.encode()).hexdigest()
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                       run_id=run.id, node_key=node.node_key,
                                       run_input=deepcopy(run.input_payload), action_state=state,
                                       context_provider=provider)
        result = retrieve_context(context, {"query": query_text, "source_kinds": ["short_term_state"],
                                            "max_candidates": 5, "max_chars": max_chars})
        checks["tool_and_provider_agree"] = result["context_text"] == bundle.rendered_text
        checks["manifest_saved_in_current_context"] = len(context.artifacts["context_retrievals"]) == 1
        checks["manifest_saved_in_durable_state"] = context.action_state["context_retrievals"] == context.artifacts["context_retrievals"]
        restored = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                        run_id=run.id, node_key=node.node_key,
                                        run_input=deepcopy(run.input_payload), action_state=deepcopy(context.action_state))
        retrieve_context(restored, {"query": query_text, "source_kinds": ["short_term_state"],
                                   "max_candidates": 5, "max_chars": max_chars})
        checks["restored_context_retains_prior_manifests"] = (
            len(restored.action_state["context_retrievals"]) == 2
            and restored.action_state["context_retrievals"][0] == context.action_state["context_retrievals"][0]
            and restored.artifacts["context_retrievals"] == restored.action_state["context_retrievals"]
        )
        feedback = ContextMemoryRepository(db).retrieve(ContextQuery(
            text=query_text, user_id=run.user_id, project_id=run.project_id,
            source_kinds=("validated_feedback",),
        ), budget=budget)
        checks["no_database_mutations"] = not db.new and not db.dirty and not db.deleted
        checks["real_run_and_checkpoint_unchanged"] = before_hash == _hash(
            [run.input_payload, run.run_context, run.status, node.sdk_state, node.status])
        checks["memory_row_count_unchanged"] = before_count == db.query(AgentContextRecord).count()
        assert all(checks.values()), checks
        report = {
            "status": "passed", "verification": "real_v3_checkpoint_read_only", "run_id": run.id,
            "node_run_id": node.id, "project_id": run.project_id, "checkpoint_version": checkpoint["version"],
            "actual_run_status": run.status, "raw_state_chars": raw_state_chars,
            "context_chars": len(bundle.rendered_text), "max_chars": max_chars,
            "selected": [{"source": item.source.to_dict(), "chars": len(item.text),
                          "evidence": dict(item.evidence)} for item in bundle.manifest.selected],
            "feedback_statuses": [item.status for item in feedback], "persisted_memory_rows": before_count,
            "business_feedback_acceptance": "unverified_requires_real_evaluation_and_human_confirmation",
            "model_requests_sent": 0, "database_writes": 0, "checks": checks,
        }
        db.rollback()
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-chars", type=int, default=8000)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    report = verify(run_id=options.run_id, query_text=options.query, max_chars=options.max_chars)
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(options.report.resolve()), "checks": report["checks"],
                      "context_chars": report["context_chars"], "raw_state_chars": report["raw_state_chars"]},
                     ensure_ascii=False))
