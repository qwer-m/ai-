"""以 Run 39 原始回执重建历史支持否决，检查重建边界；不迁移或批准旧审查输出。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform import test_generation_planning as support
from modules.agent_platform import test_generation_planning_agent as planning


def verify(checkpoint_path: Path, audit_path: Path) -> dict:
    original = checkpoint_path.read_bytes()
    audit_bytes = audit_path.read_bytes()
    checkpoint = json.loads(original)
    audit = json.loads(audit_bytes)
    calls = list(checkpoint["validated_agent_calls"].values())
    historical_planning = checkpoint["state"]["planning"]
    scopes = historical_planning["source_semantics"]["planning_scopes"]
    cases = []
    legacy_rejected = 0
    checked_support_inputs = set()

    def actual_call(agent_key: str, input_hash: str):
        matches = [call for call in calls if call["identity"]["agent_key"] == agent_key
                   and call["identity"]["input_hash"] == input_hash]
        assert len(matches) == 1, (agent_key, input_hash)
        call = matches[0]
        assert call["status"] == "validated"
        assert action_state_hash(call["output"]) == call["output_hash"]
        assert action_state_hash(call["raw_output"]) == call["raw_output_hash"]
        return call

    for plan_meta in audit["plans"]:
        plan_call = checkpoint["validated_agent_calls"][plan_meta["child_receipt_id"]]
        assert action_state_hash(plan_call["output"]) == plan_call["output_hash"]
        plan = deepcopy(plan_call["output"])
        route_inputs = support.prepare_planning_scope_routes(None, {
            "plan": plan, "planning_scopes": scopes,
        })["batch_items"]
        route_records = []
        for index, item in enumerate(route_inputs):
            matches = [call for call in calls if call["identity"]["agent_key"] == "test_planning_scope_router"
                       and call["identity"]["input_hash"] == action_state_hash(item)]
            if not matches:
                break
            call = actual_call("test_planning_scope_router", action_state_hash(item))
            route_records.append({"item_index": index, "input_hash": call["identity"]["input_hash"],
                                  "output": deepcopy(call["output"])})
        if len(route_records) != len(route_inputs):
            assert plan_meta["version"] == 4
            continue

        base = {"plan": plan, "prepared_items": route_inputs, "route_records": route_records}
        current_items = support.prepare_planning_route_repairs(None, base)["items"]
        historical_inputs, records = [], []
        for index, item in enumerate(current_items):
            # 本次仅重建旧请求用于匹配原始回执；绝不将旧输出补成新版 support_gaps 结论。
            historical_input = deepcopy(item)
            historical_input.pop("additional_scope_ids")
            call = actual_call("test_planning_route_gap_reviewer", action_state_hash(historical_input))
            checked_support_inputs.add(call["identity"]["input_hash"])
            historical_inputs.append(historical_input)
            records.append({"item_index": index, "input_hash": call["identity"]["input_hash"],
                            "output": deepcopy(call["output"])})
            try:
                support.postprocess_planning_route_repair_item(None, {
                    "item_input": item, "item_output": deepcopy(call["raw_output"]),
                })
            except OutputValidationError:
                legacy_rejected += 1
            else:
                raise AssertionError("旧审查输出缺少结构化缺口却被新版支持契约批准")

        state = deepcopy(historical_planning)
        state.update(plan=plan, route_inputs=route_inputs, route_records=route_records,
                     gap_inputs=historical_inputs, gap_records=records, route_review_revisions=[])
        blocker = planning._business_plan_rebuild_blocker(state)
        assert blocker is not None, plan_meta["version"]
        actual_rejections = [{"module_index": record["output"]["module_index"],
                              "test_design_item_indexes": [decision["test_design_item_index"]
                                  for decision in record["output"]["decisions"]
                                  if decision["disposition"] == "unsupported"]}
                             for record in records]
        actual_rejections = [item for item in actual_rejections if item["test_design_item_indexes"]]
        assert blocker["design_selections"] == actual_rejections
        assert set(blocker["available_repairs"]) == {
            "read_planning_evidence", "reinspect_planning_source", "revise_planning_draft", "review_planning_route_gaps",
        }

        original_item = current_items[0]
        outside = sorted({scope["scope_id"] for scope in scopes}
                         - {fact["scope_id"] for fact in original_item["candidate_facts"]})
        additional = [outside[-1], outside[0]]
        selection = [{"module_index": original_item["module_index"],
                      "test_design_item_indexes": [original_item["missing_test_design_items"][0]["test_design_item_index"]]}]
        first = support.prepare_planning_route_repairs(None, {
            **base, "design_selections": selection, "additional_scope_ids": additional,
        })["items"][0]
        reconstructed = support.prepare_planning_route_repairs(None, {
            **base, "design_selections": selection, "additional_scope_ids": first["additional_scope_ids"],
        })["items"][0]
        assert first == reconstructed
        assert len({fact["fact_id"] for fact in first["candidate_facts"]}) == len(first["candidate_facts"])
        cases.append({"plan_version": plan_meta["version"], "plan_receipt_id": plan_meta["child_receipt_id"],
                      "route_record_count": len(route_records), "support_record_count": len(records),
                      "blocked_rebuild": blocker, "additional_scope_ids_tested": additional,
                      "sorted_scope_input_reconstruction_equal": True})

    assert len(cases) == 3
    assert checkpoint_path.read_bytes() == original and audit_path.read_bytes() == audit_bytes
    return {"status": "passed", "model_requests_sent": 0, "database_writes": 0,
            "checkpoint_path": str(checkpoint_path.resolve()),
            "checkpoint_sha256": hashlib.sha256(original).hexdigest(),
            "audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
            "validation_scope": "历史真实状态的纯领域重建边界及新增scope身份验证；未将旧契约回执视为新版通过，未运行协调Agent或模型。",
            "distinct_historical_support_receipts": len(checked_support_inputs),
            "legacy_outputs_rejected_by_current_contract": legacy_rejected, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = verify(args.checkpoint, args.audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "blocked_versions": [case["plan_version"] for case in result["cases"]],
                      "legacy_outputs_rejected": result["legacy_outputs_rejected_by_current_contract"]}, ensure_ascii=False))
