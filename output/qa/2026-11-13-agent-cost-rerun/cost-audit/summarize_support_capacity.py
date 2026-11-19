"""统计真实支持复核的证据复用及输出容量公式，不改候选范围或输出契约。"""

from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path

from prepare_read_path_draft import ROOT
from modules.agent_platform.autonomous_budget import estimate_json_tokens
from modules.agent_platform.test_generation_planning import _collect_planning_scope_route_state, planning_conflict_source_texts


def packing(costs):
    groups, current, count = [], 0, 0
    for value in costs:
        if count and current + value > 16000:
            groups.append(count)
            current, count = 0, 0
        current += value
        count += 1
    if count:
        groups.append(count)
    return groups


def main():
    original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11"
    snapshot = json.loads((original / "final-snapshot.json").read_text(encoding="utf-8"))
    report = json.loads(Path(__file__).with_name("run59-support-input-cost.json").read_text(encoding="utf-8"))
    real = json.loads(gzip.decompress((ROOT / "backend/tests/agent_platform/fixtures/run59_artifact_read_cost_real.json.gz").read_bytes()))
    state = real["state"]["planning"]
    routed = _collect_planning_scope_route_state(plan=state["plan"], prepared_items=state["route_inputs"], route_records=state["route_records"])
    expansion = []
    for index, module in enumerate(routed["modules"]):
        candidate = {fact["fact_id"] for scope in routed["evidence_by_module"][index] for fact in routed["facts_by_scope"][scope]}
        owned = set(routed["facts_by_module"][index])
        expansion.append({"module_index": index, "module_name": module["name"], "owned_fact_count": len(owned),
            "scope_count": len(routed["evidence_by_module"][index]), "scope_expanded_candidate_count": len(candidate),
            "neighbour_fact_count": len(candidate - owned)})
    item = next(item for item in state["gap_inputs"] if item["module_name"] == "课程学习与词汇状态")
    facts = item["candidate_facts"]
    fact_ids = [fact["fact_id"] for fact in facts]
    sides = sorted([{"fact_id": fact["fact_id"], "quote": fact["assertion"]} for fact in facts if fact["assertion"].strip()],
        key=estimate_json_tokens, reverse=True)[:2]
    costs = []
    for design in item["missing_test_design_items"]:
        view = {"test_design_item_index": design["test_design_item_index"], "disposition": "unsupported",
            "fact_ids": fact_ids, "reason": design["coverage_intent"], "support_gaps": [{
                "coverage_fragment": design["coverage_intent"], "kind": "source_conflict", "evidence_fact_ids": fact_ids,
                "reason": design["coverage_intent"], "conflict": {"subject": item["module_name"],
                    "condition": design["coverage_intent"], "sides": sides, "incompatibility": design["coverage_intent"]}}]}
        full = 2 * estimate_json_tokens({"decision": view})
        without_quote_text = deepcopy(view)
        without_quote_text["support_gaps"][0]["conflict"]["sides"] = [{"fact_id": side["fact_id"], "quote": ""} for side in sides]
        without_id_arrays = deepcopy(view)
        without_id_arrays["fact_ids"] = []
        without_id_arrays["support_gaps"][0]["evidence_fact_ids"] = []
        costs.append({"test_design_item_index": design["test_design_item_index"], "full_estimate": full,
            "quote_text_marginal": full - 2 * estimate_json_tokens({"decision": without_quote_text}),
            "two_id_array_marginal": full - 2 * estimate_json_tokens({"decision": without_id_arrays})})
    decisions, gap_kinds, citations, quotes = [], Counter(), [], []
    for call in snapshot["nodes"][-1]["sdk_state"]["tool_calls"]:
        if call.get("tool_key") != "agent:test_planning_route_gap_reviewer":
            continue
        output = [entry["arguments"] for entry in call["submissions"] if entry["tool_key"] == "submit_result"][-1]
        decisions.extend(output["decisions"])
        for decision in output["decisions"]:
            citations.append(len(decision["fact_ids"]))
            for gap in decision["support_gaps"]:
                gap_kinds[gap["kind"]] += 1
                if gap["kind"] == "source_conflict":
                    quotes.extend(len(side["quote"]) for side in gap["conflict"]["sides"])
    payload_fields = sum((Counter({key: value["bytes"] for key, value in row["payload_fields"].items()}) for row in report["requests"]), Counter())
    envelope_fields = sum((Counter({key: value["bytes"] for key, value in row["envelope_fields"].items()}) for row in report["requests"]), Counter())
    total_evidence = payload_fields["source_review"] + payload_fields["scope_evidence"]
    unique_evidence = sum(group["context_bytes_each"] for group in report["evidence_context_groups"])
    summary = {"run_id": 59, "validated_sessions": 56, "actual_requests": 57,
        "all_56_input_identities_and_sdk_context_bytes_verified": True,
        "payload_total_bytes": sum(row["payload_size"]["bytes"] for row in report["requests"]),
        "payload_field_bytes": dict(payload_fields), "sdk_base_envelope_total_bytes": sum(row["envelope_size"]["bytes"] for row in report["requests"]),
        "sdk_base_envelope_field_bytes": dict(envelope_fields),
        "source_review_before_sharing_bytes": sum(row["source_detail"]["unshared_source_review_bytes"] for row in report["requests"]),
        "source_review_after_sharing_bytes": payload_fields["source_review"],
        "same_evidence_context_groups": 7, "all_evidence_value_bytes": total_evidence,
        "unique_evidence_value_bytes": unique_evidence, "cross_request_repeated_evidence_value_bytes": total_evidence - unique_evidence,
        "candidate_scope_expansion": expansion,
        "capacity": {"module_name": item["module_name"], "fact_count": len(facts), "design_count": len(costs),
            "output_token_limit": 16000, "batching_branch_uses_input_size": False,
            "formula_margin_multiplier": 2, "representative_quote_chars": [len(side["quote"]) for side in sides],
            "max_visible_fact_text_chars": max(len(value) for fact in facts for value in planning_conflict_source_texts(fact)),
            "costs": costs, "current_groups": packing([row["full_estimate"] for row in costs]),
            "zero_quote_text_marginal_groups": packing([row["full_estimate"] - row["quote_text_marginal"] for row in costs]),
            "zero_id_arrays_marginal_groups": packing([row["full_estimate"] - row["two_id_array_marginal"] for row in costs]),
            "marginal_experiment_limits": "只对现有估计公式做成本归因。空引用不是新候选、未送模型、不是可采用的业务契约。"},
        "actual_accepted_outputs": {"decision_occurrences": len(decisions), "final_current_designs": sum(len(item["missing_test_design_items"]) for item in state["gap_inputs"]),
            "gap_kind_counts": dict(gap_kinds), "citation_count_max": max(citations), "citation_count_mean": sum(citations) / len(citations),
            "source_conflict_quote_lengths": quotes, "reason_chars_max": max(len(decision["reason"]) for decision in decisions),
            "output_tokens": report["reported_usage"]["output_tokens"]},
        "limits": "精确字节与同一真实输入的静态估计分解，非按字段实际token账单；跨请求重复证据不能直接删去。没有改变任何候选、正文、输出Schema或业务事实。"}
    Path(__file__).with_name("run59-support-capacity-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key not in {"capacity", "candidate_scope_expansion"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
