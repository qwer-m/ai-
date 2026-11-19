"""定向读取两次归属行动的真实输入，不展开整棵 state，不调用模型。"""

from copy import deepcopy
import json
from pathlib import Path
import runpy


BASE = Path(__file__).resolve().parent
helpers = runpy.run_path(str(BASE / "capture_cost_progress.py"))
SessionLocal = helpers["SessionLocal"]
TargetedReader = helpers["TargetedReader"]
receipt_record = helpers["receipt_record"]
text = helpers["text"]
action_state_hash = helpers["action_state_hash"]

from modules.agent_platform.planning_dependencies import ownership_batches


def fields_changed(left, right):
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))


calls = ("call_40ec590315fa4788b4d3a3750445d53c", "call_d0796b3c1f624bb3862bf07e29b30183")
with SessionLocal(autoflush=False) as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    reader = TargetedReader(db)
    captures = []
    for call_id in calls:
        record = receipt_record(db, call_id)
        if record is None:
            raise LookupError(f"真实归属行动不存在: {call_id}")
        leaf = reader.subtree(record["pre_state_hash"], ["planning", "ownership_inputs"])
        captures.append({"tool_call_id": call_id, "started_sequence": record["started_sequence"],
            "pre_state_hash": record["pre_state_hash"], "ownership_inputs": leaf,
            "batches": ownership_batches(leaf)})
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()

before, after = captures
by_scope = [{item["scope"]["scope_id"]: item for item in capture["ownership_inputs"]} for capture in captures]
rows = []
for scope_id in sorted(set(by_scope[0]) | set(by_scope[1])):
    left, right = (mapping.get(scope_id) for mapping in by_scope)
    if left is None or right is None:
        rows.append({"scope_id": scope_id, "added_or_removed": "added" if left is None else "removed"})
        continue
    stripped = [{key: value for key, value in item.items() if key != "user_decision_context"} for item in (left, right)]
    before_facts = {item["fact_id"]: item for item in left["scope"]["facts"]}
    after_facts = {item["fact_id"]: item for item in right["scope"]["facts"]}
    fact_changes = [{"fact_id": identity,
        "changed_fields": fields_changed(before_facts.get(identity, {}), after_facts.get(identity, {})),
        "before": before_facts.get(identity), "after": after_facts.get(identity)}
        for identity in sorted(set(before_facts) | set(after_facts))
        if before_facts.get(identity) != after_facts.get(identity)]
    rows.append({"scope_id": scope_id, "full_input_changed": left != right,
        "changed_fields": fields_changed(left, right),
        "input_changed_without_user_decision_context": stripped[0] != stripped[1],
        "fields_changed_without_user_decision_context": fields_changed(*stripped),
        "scope_fields_changed": fields_changed(left["scope"], right["scope"]),
        "changed_facts": fact_changes,
        "before_sha256_without_policy": action_state_hash(stripped[0]),
        "after_sha256_without_policy": action_state_hash(stripped[1])})

batches = []
right_batches = {tuple(scope["scope_id"] for scope in item["scopes"]): item for item in after["batches"]}
for index, item in enumerate(before["batches"]):
    scope_ids = tuple(scope["scope_id"] for scope in item["scopes"])
    right = right_batches.get(scope_ids)
    stripped = {key: value for key, value in item.items() if key != "user_decision_context"}
    right_stripped = {key: value for key, value in (right or {}).items() if key != "user_decision_context"}
    batches.append({"before_batch_index": index, "scope_ids": scope_ids,
                    "same_scope_group_after": right is not None,
                    "full_input_changed": item != right,
                    "input_changed_without_user_decision_context": stripped != right_stripped,
                    "changed_fields_without_policy": fields_changed(stripped, right_stripped)})

report = {"run_id": 61, "model_requests_sent": 0, "business_database_writes": 0,
    "captures": [{key: value for key, value in item.items() if key not in {"ownership_inputs", "batches"}}
                 | {"scope_count": len(item["ownership_inputs"]), "batch_count": len(item["batches"])} for item in captures],
    "scopes": rows, "batches": batches,
    "summary": {"paired_scopes": sum("full_input_changed" in item for item in rows),
        "scopes_full_changed": sum(item.get("full_input_changed", False) for item in rows),
        "scopes_changed_without_policy": sum(item.get("input_changed_without_user_decision_context", False) for item in rows),
        "before_batch_count": len(before["batches"]), "after_batch_count": len(after["batches"]),
        "paired_batch_scope_groups": sum(item["same_scope_group_after"] for item in batches),
        "batches_changed_without_policy": sum(item["input_changed_without_user_decision_context"] for item in batches)},
    "boundary": "真实行动pre-state leaf的CPU对比和当前ownership_batches机械重组，不提交新模型请求或将机械重组当成历史SDK原始请求凭证"}
(BASE / "ownership-policy-diff.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
