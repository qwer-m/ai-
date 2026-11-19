"""历史真实不批准结果的当前合同CPU投影，不代表模型完成新审查或业务覆盖。"""

from copy import deepcopy
import json

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.generation_review_contract import required_review_source_ids


def cpu_scope_reason(content):
    """只投影实际用例材料，不补造模型已经完成条件与例外分析的结论。"""
    return "CPU协议投影（未证明模型已审查适用条件与例外）：" + json.dumps({
        "前提": content["preconditions"], "输入": content["test_input"], "步骤": content["steps"],
    }, ensure_ascii=False, separators=(",", ":"))


def cpu_case_scope_ids(state):
    """按历史实际引用关系投影范围；补齐编号不等于已证明用例业务正确。"""
    coverage_refs = {goal["coverage_id"]: {ref["evidence_id"] for ref in goal["source_refs"]}
                     for goal in state["coverage_plan"]["items"]}
    return {identity: sorted({ref["evidence_id"] for ref in record["content"]["source_refs"]}
                            | {ref for key in record["content"]["coverage_ids"] for ref in coverage_refs[key]})
            for identity, record in state["cases"]["items"].items()}


def review_cpu_candidate(state, historical):
    old = deepcopy(historical)
    if old["verdict"] == "approved":
        raise ValueError("旧批准不得补造新审查回执")
    checked = old.get("checked_case_ids", [row["case_id"] for row in old.get("case_checks", [])])
    payload = generation._review_input(state)
    evidence = {item["evidence_id"]: item for item in payload["evidence_catalog"]["items"]}
    case_sources = {key: [ref["evidence_id"] for ref in item["content"]["source_refs"]]
                    for key, item in state["cases"]["items"].items()}
    scope_sources = cpu_case_scope_ids(state)
    rows = deepcopy(old.get("source_checks", []))
    # 此处只机械保留真实来源和已存在引用关系；故意不把引用关系当语义证明。
    for identity in ([] if rows else required_review_source_ids(payload)):
        witnesses = [key for key, refs in case_sources.items() if identity in refs]
        item = evidence[identity]
        raw = item.get("text", item.get("resolution", ""))
        rows.append({"evidence_id": identity, "branches": [{
            "rule": "CPU合同投影（未声称已分析条件分支）：" + (raw or identity),
            "case_ids": witnesses,
            "disposition": "covered" if witnesses and item["kind"] != "image" else "reference", "related_evidence_ids": [],
        }]})
    for row in rows:
        for branch in row["branches"]:
            branch.pop("conflicting_case_ids", None)
    findings = old.get("issues", [])
    invalid = {key: issue["reason"] for issue in findings if issue["kind"] == "case_error"
               for key in issue["target_case_ids"]}
    for decision in old["issue_resolutions"]:
        if decision["resolution"] != "resolved":
            issue = state["open_issues"][decision["issue_id"]]
            if issue["kind"] == "case_error":
                invalid.update({key: issue["reason"] for key in issue["target_case_ids"]})
    # 真实历史缺口投影为分支判断，由当前代码编译问题；不伪造新模型已审查的事实。
    for issue in findings:
        if issue["kind"] != "coverage_gap":
            continue
        refs = [ref if isinstance(ref, str) else ref["evidence_id"] for ref in issue["source_refs"]]
        if refs:
            row = next(item for item in rows if item["evidence_id"] == refs[0])
            row["branches"].append({"rule": issue["reason"], "case_ids": list(issue["target_case_ids"]),
                "disposition": "gap", "related_evidence_ids": refs[1:]})
    original_checks = {row["case_id"]: row for row in old.get("case_checks", [])}
    cases = []
    for key in checked:
        row = original_checks.get(key, {})
        cases.append({"case_id": key, "additional_evidence_ids": sorted(
            set(row.get("scope_evidence_ids", [])) - set(scope_sources[key])),
            "scope_reason": invalid.get(key, row.get("scope_reason") or
                cpu_scope_reason(state["cases"]["items"][key]["content"])),
            "valid": key not in invalid and row.get("valid", True)})
    def wire_finding(issue):
        return {**deepcopy(issue), "source_refs": [ref if isinstance(ref, str) else ref["evidence_id"]
                                                   for ref in issue["source_refs"]]}
    return {"source_checks": rows, "case_checks": cases,
        "business_questions": [{key: value for key, value in wire_finding(issue).items() if key != "coverage_ids"}
                               for issue in findings
                               if issue["kind"] in {"source_conflict", "missing_rule"}],
        "issue_resolutions": [wire_finding(decision) for decision in old["issue_resolutions"]]}
