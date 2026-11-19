"""仅从已抓取快照核对口径失效与来源表示，不导入数据库或执行模型。"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
FOLDER = Path(__file__).resolve().parent


def pure_functions(relative, names, namespace):
    """只加载仓库中明确列出的纯函数 AST，不执行模块导入副作用。"""
    source = ROOT.joinpath(relative).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, relative, "exec"), namespace)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def sizes(value):
    rendered = encoded(value)
    return {"chars": len(rendered), "utf8_bytes": len(rendered.encode()), "estimated_tokens": namespace["estimate_json_tokens"](value)}


namespace = {"json": json, "math": math, "deepcopy": deepcopy, "Counter": Counter, "Any": Any,
             "_json_value": lambda value: (_ for _ in ()).throw(TypeError(type(value).__name__))}
pure_functions("backend/modules/agent_platform/runtime.py", ["_project_input_value"], namespace)
pure_functions("backend/modules/agent_platform/autonomous_budget.py", ["estimate_json_tokens"], namespace)
pure_functions("backend/modules/agent_platform/test_generation_semantics.py", [
    "_source_record_json", "authority_source_review_view", "restore_authority_source_review"], namespace)


def main():
    snapshot = json.loads((FOLDER / "final-snapshot.json").read_text(encoding="utf-8"))
    run = snapshot["run"]
    checkpoint = next(iter(snapshot["expanded_checkpoints"].values()))
    state = checkpoint["state"]
    planning = state["planning"]
    old_inputs, new_inputs = state["source"]["planning_inputs"], planning["planning_inputs"]
    policy = state["conflict_confirmations"]
    policy_ids = {identity for item in policy.values() for identity in item["fact_ids"]}
    original_authority = state["source"]["source_authority"]["result"]["authoritative_facts"]
    final_authority = planning["source_authority"]["result"]["authoritative_facts"]
    old_facts = {fact["fact_id"]: fact for fact in original_authority}
    new_facts = {fact["fact_id"]: fact for fact in final_authority}
    governance_changes = [{"fact_id": identity, "changed_fields": [field for field in sorted(set(old_facts[identity]) | set(fact))
        if old_facts[identity].get(field) != fact.get(field)], "old_status": old_facts[identity]["status"], "new_status": fact["status"]}
        for identity, fact in new_facts.items() if old_facts[identity] != fact]
    old_full = {digest(item) for item in old_inputs}
    business = lambda item: {key: value for key, value in item.items() if key != "planning_batch"}
    old_business = {digest(business(item)): index for index, item in enumerate(old_inputs)}
    old_content_matches = [{"new_index": index, "old_index": old_business[digest(business(item))],
        "old_batch": old_inputs[old_business[digest(business(item))]]["planning_batch"], "new_batch": item["planning_batch"]}
        for index, item in enumerate(new_inputs) if digest(business(item)) in old_business]
    new_draft_scope = []
    for index, item in enumerate(new_inputs):
        ids = {fact["fact_id"] for scope in item["planning_scopes"] for fact in scope["facts"]}
        closure = set(ids)
        pending = list(ids)
        while pending:
            identity = pending.pop()
            for relation in new_facts[identity].get("governed_by", []):
                directive = relation["directive_fact_id"]
                if directive not in closure:
                    closure.add(directive)
                    pending.append(directive)
        new_draft_scope.append({"item_index": index, "fact_count": len(ids),
            "direct_confirmation_fact_ids": sorted(ids & policy_ids),
            "confirmation_fact_ids_in_source_closure": sorted(closure & policy_ids),
            "changed_governance_fact_ids": sorted(ids & {value["fact_id"] for value in governance_changes})})

    definitions = run["run_context"]["execution_contract"]["agents"]
    specialist = next(item for item in definitions if item["key"] == "test_planning_route_gap_reviewer")
    projection = specialist["runtime_config"]["input_projection"]
    output_limit = specialist["runtime_config"]["max_output_tokens"]
    catalog = state["prepared_input"]["evidence_catalog"]["items"]
    payload_reports, module_reports, repeated_scope_text = [], [], Counter()
    total_before = Counter()
    total_after = Counter()
    overlap_char_total = 0
    for item in planning["gap_inputs"]:
        item = deepcopy(item)
        scopes = {fact["scope_id"] for fact in item["candidate_facts"]}
        item["scope_evidence"] = [{"scope_id": source["evidence_id"], "page_number": source.get("page_number"),
            "kind": "image" if "image_region" in source else "text", "source_dependency_sha256": digest(source),
            **({"text": source["text"]} if 0 < len(source["text"]) <= 4000 else {})}
            for source in catalog if source["evidence_id"] in scopes]
        fact_ids = [fact["fact_id"] for fact in item["candidate_facts"]]
        batches, current, cost = [], [], 0
        for design in item["missing_test_design_items"]:
            decision = {"test_design_item_index": design["test_design_item_index"], "disposition": "unsupported", "fact_ids": fact_ids,
                "reason": design["coverage_intent"], "support_gaps": [{"coverage_fragment": design["coverage_intent"],
                    "kind": "source_conflict", "evidence_fact_ids": fact_ids, "reason": design["coverage_intent"]}]}
            estimate = 2 * namespace["estimate_json_tokens"]({"decision": decision})
            if current and cost + estimate > output_limit:
                batches.append({**item, "missing_test_design_items": current})
                current, cost = [], 0
            current.append(design)
            cost += estimate
        if current:
            batches.append({**item, "missing_test_design_items": current})
        module_reports.append({"module_index": item["module_index"], "module_name": item["module_name"],
            "candidate_fact_count": len(fact_ids), "design_count": len(item["missing_test_design_items"]), "requests": len(batches)})
        for batch_index, raw in enumerate(batches):
            projected = namespace["_project_input_value"]({**raw, "_user_confirmations": policy}, projection)
            source_view = namespace["authority_source_review_view"](projected["candidate_facts"])
            assert namespace["restore_authority_source_review"](source_view) == projected["candidate_facts"]
            compact = {**projected, "candidate_facts": source_view["authoritative_facts"], "source_records": source_view["source_records"]}
            before_size, after_size = sizes(projected), sizes(compact)
            total_before.update(before_size)
            total_after.update(after_size)
            contexts = [fact.get("source_context") for fact in projected["candidate_facts"] if "source_context" in fact]
            context_count = Counter(encoded(value) for value in contexts)
            evidence_by_scope = {value["scope_id"]: value.get("text", "") for value in projected["scope_evidence"]}
            text_overlaps = []
            for fact in projected["candidate_facts"]:
                text = evidence_by_scope[fact["scope_id"]]
                if not text:
                    continue
                leaves = []
                def strings(value):
                    if isinstance(value, str):
                        leaves.append(value)
                    elif isinstance(value, dict):
                        for child in value.values(): strings(child)
                    elif isinstance(value, list):
                        for child in value: strings(child)
                strings(fact.get("source_context"))
                matched = sorted({leaf for leaf in leaves if len(leaf) >= 8 and leaf in text})
                if matched:
                    text_overlaps.append({"fact_id": fact["fact_id"], "matched_leaf_count": len(matched), "matched_leaf_chars": sum(map(len, matched))})
            for evidence in projected["scope_evidence"]:
                if evidence.get("text"):
                    repeated_scope_text[digest({"scope_id": evidence["scope_id"], "text": evidence["text"]})] += 1
            overlap_char_total += sum(row["matched_leaf_chars"] for row in text_overlaps)
            payload_reports.append({"module_index": item["module_index"], "batch_index": batch_index,
                "design_indexes": [design["test_design_item_index"] for design in raw["missing_test_design_items"]],
                "raw_payload_hash_with_confirmations": digest({**raw, "_user_confirmations": policy}),
                "model_payload": before_size, "compact_model_payload": after_size,
                "component_sizes": {key: sizes(value) for key, value in projected.items()},
                "context_count": len(contexts), "unique_complete_context_count": len(context_count),
                "shared_records": len(source_view["source_records"]), "reconstructed_facts_exact": True,
                "scope_text_overlap": text_overlaps})

    receipts = sorted(checkpoint["receipts"].values(), key=lambda value: value["started_sequence"])
    call_counts = defaultdict(Counter)
    for call in checkpoint["validated_agent_calls"].values():
        call_counts[call["parent_tool_call_id"]][call["identity"]["agent_key"]] += 1
    actions = [{"sequence": receipt["started_sequence"], "tool_key": receipt["tool_key"], "status": receipt["status"],
        "specialist_counts": dict(call_counts[receipt["tool_call_id"]]), "error_type": (receipt.get("error") or {}).get("error_type")}
        for receipt in receipts if receipt["started_sequence"] >= 24]
    support_parent = next((receipt["tool_call_id"] for receipt in reversed(receipts) if receipt["tool_key"] == "review_planning_route_gaps"), None)
    actual_support = [call for call in checkpoint["validated_agent_calls"].values()
                      if call["parent_tool_call_id"] == support_parent and call["identity"]["agent_key"] == specialist["key"]]
    reconstructed_hashes = {item["raw_payload_hash_with_confirmations"] for item in payload_reports}
    actual_hashes = {call["identity"]["input_hash"] for call in actual_support}
    assert actual_hashes <= reconstructed_hashes
    remaining_before, remaining_after = Counter(), Counter()
    for item in payload_reports:
        item["already_validated_in_snapshot"] = item["raw_payload_hash_with_confirmations"] in actual_hashes
        if not item["already_validated_in_snapshot"]:
            remaining_before.update(item["model_payload"])
            remaining_after.update(item["compact_model_payload"])
    report = {"run_id": run["id"], "snapshot_observed_at": snapshot["observed_at"], "audited_at": datetime.now(timezone.utc).isoformat(),
        "policy_items": [{"id": identity, "fact_ids": value["fact_ids"], "resolution": value["resolution"]} for identity, value in policy.items()],
        "authority": {"old_status_counts": dict(Counter(fact["status"] for fact in original_authority)),
            "new_status_counts": dict(Counter(fact["status"] for fact in final_authority)), "changes": governance_changes},
        "draft_invalidation": {"old_count": len(old_inputs), "new_count": len(new_inputs),
            "exact_input_matches": sum(digest(item) in old_full for item in new_inputs),
            "business_input_matches_excluding_batch_metadata": old_content_matches,
            "per_batch_policy_scope": new_draft_scope},
        "post_approval_actions": actions,
        "dependency_policy": {kind: {"count": len(planning["dependency_records"][kind]),
            "all_records_contain_global_policy": all(planning["dependency_records"]["values"].get(value["input_refs"].get("user_decision_context")) == policy
                for value in planning["dependency_records"][kind].values())}
            for kind in ("ownership", "support", "support_revisions")},
        "support_projection_keys": list(projection),
        "support_capacity": {"module_count": len(module_reports), "request_count": len(payload_reports),
            "actual_validated_parent_call_count": len(actual_support), "actual_call_input_hashes_match_reconstruction": True,
            "module_requests": module_reports, "model_payload_totals": dict(total_before), "codec_payload_totals": dict(total_after),
            "reduction": {key: round(1-total_after[key]/total_before[key], 6) for key in total_before},
            "remaining_request_count": len(payload_reports) - len(actual_hashes),
            "remaining_model_payload_totals": dict(remaining_before), "remaining_codec_payload_totals": dict(remaining_after),
            "repeated_identical_scope_text_across_requests": {"scope_texts": len(repeated_scope_text), "repeated_deliveries": sum(count-1 for count in repeated_scope_text.values())},
            "context_text_overlapping_scope_text_leaf_chars": overlap_char_total, "requests": payload_reports},
        "scope": "本地快照数据及既有纯函数 AST；无数据库读取、写入、模型请求或源码改动。体积仅为模型业务 payload，非 SDK 完整请求预算。"}
    target = FOLDER / "policy-invalidation-capacity-audit.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "governance_changes": len(governance_changes), "business_input_matches": len(old_content_matches),
        "exact_input_matches": report["draft_invalidation"]["exact_input_matches"], "post_approval_actions": actions,
        "support_model_payload_totals": dict(total_before), "codec_payload_totals": dict(total_after),
        "reduction": report["support_capacity"]["reduction"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
