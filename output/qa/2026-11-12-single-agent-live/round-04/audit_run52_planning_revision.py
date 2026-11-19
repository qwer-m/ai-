"""只读核对真实修订前后状态与完整请求身份；禁止执行专业模型。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentApproval, AgentNodeRun, AgentRun
from modules.agent_platform import checkpoint_storage as storage
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _project_agent_map_input


def changed_paths(before, after, path=""):
    if type(before) is not type(after):
        return [path]
    if isinstance(before, dict):
        return [item for key in sorted(set(before) | set(after))
                for item in (changed_paths(before[key], after[key], path + "." + key)
                             if key in before and key in after else [path + "." + key])]
    if isinstance(before, list):
        if len(before) != len(after):
            return [path + ".length"]
        return [item for index, (old, new) in enumerate(zip(before, after, strict=True))
                for item in changed_paths(old, new, path + f"[{index}]")]
    return [] if before == after else [path]


def main():
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, 52)
        node = db.query(AgentNodeRun).filter_by(run_id=52).order_by(AgentNodeRun.id.desc()).first()
        compact = storage.compact_checkpoint(node)
        store = storage.store_for_node(db, node)
        receipts = storage.receipt_records(db, node, compact)
        calls = storage.call_records(db, node, compact)
        ordered = sorted(receipts.values(), key=lambda value: value["started_sequence"])
        by_sequence = {value["started_sequence"]: value for value in ordered}

        def state_at(receipt, phase):
            return store.get(store._ref(receipt[phase + "_state_hash"]))

        before = state_at(by_sequence[8], "pre")
        revised = state_at(by_sequence[8], "post")
        rebuilt = state_at(by_sequence[9], "post")
        approved = db.query(AgentApproval.id).filter_by(run_id=run.id, status="approved").all()
        assert not approved, "出现真实人工裁决后需从正式审批读取上下文，不能猜答案"
        source_input = AgentRunRepository(db).read_node_input(node)

        def context(state):
            return ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key=node.node_key,
                run_input=deepcopy(run.input_payload), artifacts=deepcopy(run.run_context.get("artifacts") or {}),
                action_input=deepcopy(source_input), action_state=deepcopy(state),
            )

        args = json.loads(store.get(by_sequence[9]["arguments_ref"])["arguments_json"])
        request = planning.prepare_business_plan_rebuild(generation._planning_context(context(revised), revised), args)["items"][0]
        initial_state = state_at(by_sequence[5], "pre")
        initial_args = json.loads(store.get(by_sequence[5]["arguments_ref"])["arguments_json"])
        initial_request = planning.prepare_business_plan_rebuild(
            generation._planning_context(context(initial_state), initial_state), initial_args)["items"][0]
        definition = next(item for item in run.run_context["execution_contract"]["agents"] if item["key"] == "test_business_planner")
        model, projection = _project_agent_map_input(definition=SimpleNamespace(runtime_config=definition["runtime_config"]), raw_item=request)
        initial_model, _ = _project_agent_map_input(definition=SimpleNamespace(runtime_config=definition["runtime_config"]), raw_item=initial_request)
        planner_calls = [value for value in calls.values() if value["identity"]["agent_key"] == "test_business_planner"]
        revision_calls = [value for value in planner_calls if value["parent_tool_call_id"] == by_sequence[9]["tool_call_id"]]
        assert len(revision_calls) == 1
        assert revision_calls[0]["identity"]["input_hash"] == action_state_hash(request)

        old_planning, revised_planning, new_planning = before["planning"], revised["planning"], rebuilt["planning"]
        draft_changes = []
        for index, (old, new) in enumerate(zip(old_planning["planning_records"], revised_planning["planning_records"], strict=True)):
            if old != new:
                draft_changes.append({"item_index": index, "input_hash_unchanged": old["input_hash"] == new["input_hash"],
                    "old_output_hash": action_state_hash(old["output"]), "new_output_hash": action_state_hash(new["output"]),
                    "changed_output_paths": changed_paths(old["output"], new["output"]),
                    "old_output": old["output"], "new_output": new["output"]})

        dependencies = {}
        for kind in ("ownership", "support", "support_revisions"):
            old_values = old_planning["dependency_records"][kind]
            new_values = new_planning["dependency_records"][kind]
            retained = set(old_values) & set(new_values)
            dependencies[kind] = {"before": len(old_values), "after": len(new_values),
                "retained": len(retained), "retained_exact": all(old_values[key] == new_values[key] for key in retained),
                "removed": len(set(old_values) - set(new_values)), "new": len(set(new_values) - set(old_values))}

        latest = storage.hydrate_checkpoint_state(db, node)
        final_planning = latest["planning"]
        report = {
            "run_id": run.id, "node_id": node.id, "run_status": run.status,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "真实回执与不可变内容引用的只读重建，不调用模型、不写数据库",
            "source_ref": latest["source"]["source_ref"],
            "draft_revision": {"count": len(draft_changes), "unchanged_count": len(old_planning["planning_records"]) - len(draft_changes),
                "changes": draft_changes, "draft_version": [old_planning["draft_version"], revised_planning["draft_version"]]},
            "planner_request": {"initial_hash": action_state_hash(initial_request), "revised_hash": action_state_hash(request),
                "matching_real_validated_call_hash": revision_calls[0]["identity"]["input_hash"],
                "initial_model_hash": action_state_hash(initial_model), "revised_model_hash": action_state_hash(model),
                "changed_raw_paths": changed_paths(initial_request, request), "changed_model_paths": changed_paths(initial_model, model),
                "projection": projection, "validated_planner_calls": len(planner_calls),
                "old_plan_input_hash": old_planning["plan_input_hash"], "new_plan_input_hash": new_planning["plan_input_hash"]},
            "plan": {"same": old_planning["plan"] == new_planning["plan"], "changed_paths": changed_paths(old_planning["plan"], new_planning["plan"]),
                "plan_versions": [old_planning["plan_version"], new_planning["plan_version"]],
                "module_counts": [len(old_planning["plan"]["business_modules"]), len(new_planning["plan"]["business_modules"])]},
            "dependencies": dependencies,
            "current": {"route_completed": sum(value is not None for value in final_planning["route_records"]), "route_total": len(final_planning["route_records"]),
                "support_completed": sum(value is not None for value in final_planning["gap_records"]), "support_total": len(final_planning["gap_records"]),
                "dependency_counts": {kind: len(final_planning["dependency_records"][kind]) for kind in ("ownership", "support", "support_revisions")},
                "approval_statuses": dict(Counter(status for status, in db.query(AgentApproval.status).filter_by(run_id=run.id).all())),
                "tool_actions": [{"sequence": value["started_sequence"], "tool_key": value["tool_key"], "status": value["status"]} for value in ordered]},
        }
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    target = Path(__file__).with_name("planning-revision-audit.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "draft_changes": len(draft_changes), "model_input_changed": initial_model != model,
                      "planner_calls": len(planner_calls), "plan_changed": report["plan"]["same"] is False,
                      "dependencies": dependencies, "current": report["current"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
