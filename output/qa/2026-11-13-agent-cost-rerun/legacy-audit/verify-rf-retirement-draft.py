"""最终生产代码对真实 Run59 的已验收 ownership 索引投影；不迁移旧状态。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform import test_generation_planning as current
from modules.agent_platform import test_generation_schemas as current_schemas
from modules.agent_platform.test_generation_planning_agent import _planning_semantics


source = ROOT / "backend/tests/agent_platform/fixtures/run59_artifact_read_cost_real.json.gz"
real = json.loads(gzip.decompress(source.read_bytes()))
assert real["provenance"]["run_id"] == 59
expected_hash = real.pop("data_sha256")
assert action_state_hash(real) == expected_hash
state = real["state"]["planning"]
before = action_state_hash(state)
semantics = _planning_semantics(state)
prepared = current.prepare_planning_scope_routes(SimpleNamespace(artifacts={}), {
    "plan": deepcopy(state["plan"]), "planning_scopes": deepcopy(semantics["planning_scopes"]),
})
routes = {route["scope_id"]: deepcopy(route)
          for record in state["route_records"] for route in record["output"]["routes"]}
records = [{"item_index": index, "output": {"routes": [routes[scope["scope_id"]] for scope in item["scopes"]]}}
           for index, item in enumerate(prepared["batch_items"])]
normalized = [current.postprocess_planning_scope_routing_item(SimpleNamespace(), {
    "item_input": item, "item_output": record["output"],
}) for item, record in zip(prepared["batch_items"], records, strict=True)]
assert [record["output"] for record in records] == normalized
old_projection = current._collect_planning_scope_route_state(plan=state["plan"],
    prepared_items=state["route_inputs"], route_records=state["route_records"])
new_projection = current._collect_planning_scope_route_state(plan=state["plan"],
    prepared_items=prepared["batch_items"], route_records=records)
for key in ("draft_plan", "modules", "fact_scope_by_id", "evidence_by_module", "facts_by_module", "fact_design_routes_by_module"):
    assert old_projection[key] == new_projection[key]
for scope_id, facts in new_projection["facts_by_scope"].items():
    previous = {fact["fact_id"]: fact for fact in old_projection["facts_by_scope"][scope_id]}
    for fact in facts:
        original = previous[fact["fact_id"]]
        assert set(original) - set(fact) <= {"fact_ref"}
        assert all(original[key] == value for key, value in fact.items())
assert action_state_hash(state) == before

rejections = []


def refuses(name, operation):
    try:
        operation()
    except ValueError as error:
        rejections.append({"case": name, "error_type": type(error).__name__, "error": str(error)})
    else:
        raise AssertionError(f"旧协议未被拒绝: {name}")


batch = prepared["batch_items"][0]
output = records[0]["output"]
old_fact_ref = state["route_inputs"][0]["scopes"][0]["facts"][0]["fact_ref"]

for label, include_full_id in (("rf_only_assignment", False), ("rf_plus_full_id_assignment", True)):
    changed = deepcopy(output)
    assignment = changed["routes"][0]["assignments"][0]
    assignment["fact_ref"] = old_fact_ref
    if not include_full_id:
        assignment.pop("fact_id")
    refuses(label, lambda value=changed: current.postprocess_planning_scope_routing_item(SimpleNamespace(), {
        "item_input": batch, "item_output": value,
    }))

old_scope = {**deepcopy(batch["scopes"][0]), "business_modules": deepcopy(batch["business_modules"])}
refuses("single_scope_postprocessor", lambda: current.postprocess_planning_scope_routing_item(SimpleNamespace(), {
    "item_input": old_scope, "item_output": output["routes"][0],
}))
refuses("single_scope_merge_records", lambda: current._expand_planning_scope_route_batches(
    prepared_items=[old_scope], route_records=[{"item_index": 0, "output": output["routes"][0]}],
))
for label, index in (("missing_item_index", None), ("string_item_index", "0"), ("boolean_item_index", False)):
    record = deepcopy(records[0])
    if index is None:
        record.pop("item_index")
    else:
        record["item_index"] = index
    refuses(label, lambda value=record: current._expand_planning_scope_route_batches(
        prepared_items=[batch], route_records=[value],
    ))
old_prepared = deepcopy(batch)
old_prepared["scopes"][0]["facts"][0]["fact_ref"] = old_fact_ref
metadata_input_before = deepcopy(old_prepared)
assert current.postprocess_planning_scope_routing_item(SimpleNamespace(), {
    "item_input": old_prepared, "item_output": output,
}) == output
assert old_prepared == metadata_input_before

from modules.agent_platform.registry import tool_registry
assert not hasattr(current_schemas, "PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA")
assert current_schemas.PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA["properties"]["decisions"]["items"]["properties"]["fact_refs"]["items"]["type"] == "integer"
assert "testing.postprocess_planning_scope_routing_item" not in tool_registry.keys()
assert "testing.postprocess_planning_ownership_item" in tool_registry.keys()
assert "testing.postprocess_planning_route_repair_item" in tool_registry.keys()
report = {"run_id": 59, "model_requests": 0, "database_writes": 0, "production_mutations": 0,
          "source_fixture": str(source.relative_to(ROOT)), "source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
          "source_state_unchanged": True, "scope_count": prepared["scope_count"],
          "historical_projection_batches": len(state["route_inputs"]), "new_projection_batches": len(prepared["batch_items"]),
          "fact_count": len(new_projection["fact_scope_by_id"]),
          "current_accepted_ownership_business_projection_unchanged": True,
          "support_model_uses_current_integer_refs": True, "redundant_input_metadata_not_decoded_or_mutated": True,
          "retired_registry_bindings": ["testing.postprocess_planning_scope_routing_item"],
          "current_ownership_and_support_bindings_retained": True,
          "legacy_rejections": rejections,
          "boundary": "已验收完整fact_id归属结果按当前scope机械重组并严格校验；没有生成或批准新的模型结果。当前工厂不生成RF，既有事实冗余metadata原样保留且不参与身份解析；业务索引保持相同。"}
(BASE / "rf-routing-retirement-real-run59-proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in report.items() if key != "legacy_rejections"}, ensure_ascii=False, indent=2))
print("rejected_legacy_protocols", len(rejections))
