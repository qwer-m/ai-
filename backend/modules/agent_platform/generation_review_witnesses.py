"""把拟批准的分支还原到真实来源和步骤，核对结果沿已有 coverage_gap 链路处理。"""

from copy import deepcopy

from .generation_evidence import quote_presentation
from .output_repair import OutputValidationError
from .test_generation_schemas import MAX_WITNESS_CLAIMS


def witness_review_input(payload, candidate):
    cases = {case["case_id"]: case for case in payload["cases"]}
    claims, cited_cases = {}, set()
    for row in candidate["source_checks"]:
        for index, branch in enumerate(row["branches"], 1):
            if branch["disposition"] not in {"covered", "transition_covered", "effect_covered"}:
                continue
            anchors = branch.get("verification_steps", branch.get("transition_steps", []))
            claims[f"{row['evidence_id']}/{index}"] = {
                "source_ids": [row["evidence_id"], *branch["related_evidence_ids"]],
                "rule": branch["rule"], "case_ids": branch["case_ids"], "cited_steps": anchors,
            }
            cited_cases.update(branch["case_ids"])
    if not claims:
        raise OutputValidationError("拟批准的审查没有任何可核对的正文覆盖见证")
    return {"review_mode": "verify_witnesses", "claims": claims, "image_pages": [],
            "sources": [item for item in payload["evidence_catalog"]["items"] if item["kind"] != "image"],
            "cases": {key: {field: cases[key][field] for field in ("preconditions", "test_input", "steps")}
                      for key in sorted(cited_cases)}}


def witness_batches(request):
    """相邻短来源共用一次核对；长来源分段，限制单次逐项回执的大小。"""
    by_source = {}
    for key, claim in request["claims"].items():
        by_source.setdefault(claim["source_ids"][0], []).append(key)
    batches, pending = [], []
    for keys in by_source.values():
        if pending and len(pending) + len(keys) > MAX_WITNESS_CLAIMS:
            batches.append(pending)
            pending = []
        pending.extend(keys)
        while len(pending) >= MAX_WITNESS_CLAIMS:
            batches.append(pending[:MAX_WITNESS_CLAIMS])
            pending = pending[MAX_WITNESS_CLAIMS:]
    if pending:
        batches.append(pending)
    for keys in batches:
        claims = {key: request["claims"][key] for key in keys}
        cases = {case for claim in claims.values() for case in claim["case_ids"]}
        yield {**request, "claims": claims, "cases": {key: request["cases"][key] for key in sorted(cases)}}


def witness_gaps(request, response, *, stop_on_gap=False):
    """核对原文出处、操作到断言的先后关系及结果步骤归属；同义表达由独审判断。"""
    if not isinstance(response, dict) or set(response) != {"witness_checks"}:
        raise OutputValidationError("步骤核对只能提交 witness_checks")
    sources = {item["evidence_id"]: quote_presentation(item.get("resolution") or item.get("text", ""))
               for item in request["sources"]}
    claims = request["claims"]
    gaps, seen = {}, set()
    for row in response["witness_checks"]:
        key = row["claim_id"]
        if key not in claims or key in seen:
            raise OutputValidationError("步骤核对引用未知或重复的分支", details={"claim_id": key})
        seen.add(key)
        claim = claims[key]
        if not row["verified"] and not row["gaps"]:
            raise OutputValidationError("每个分支须给出逐项见证或明确缺口", details={"claim_id": key})
        for item in [*row["verified"], *row["gaps"]]:
            quote = quote_presentation(item["source_quote"])
            if not quote or not any(quote in sources.get(identity, "") for identity in claim["source_ids"]):
                raise OutputValidationError("动作或缺口须绑定该分支所据的原文短句", details={"claim_id": key})
        for item in row["verified"]:
            case = item["case_id"]
            action, expected = item["action_step"], item["expected_step"]
            if (case not in claim["case_ids"] or type(action) is not int or type(expected) is not int
                    or not 1 <= action <= expected <= len(request["cases"][case]["steps"])):
                raise OutputValidationError("操作与随后断言须来自同一见证用例的真实步骤", details={"claim_id": key})
            if claim["cited_steps"] and {"case_id": case, "step_number": expected} not in claim["cited_steps"]:
                raise OutputValidationError("结果步骤须属于原审引用的位置", details={"claim_id": key})
        if row["gaps"]:
            gaps[key] = [f"「{gap['source_quote']}」{gap['missing']}" for gap in row["gaps"]]
    if seen != set(claims) and not (stop_on_gap and gaps):
        raise OutputValidationError("步骤核对遗漏拟批准分支", details={"missing_claim_ids": sorted(set(claims) - seen)})
    return gaps


def apply_witness_gaps(candidate, gaps):
    corrected = deepcopy(candidate)
    for row in corrected["source_checks"]:
        branches = []
        for index, branch in enumerate(row["branches"], 1):
            reasons = gaps.get(f"{row['evidence_id']}/{index}")
            if not reasons:
                branches.append(branch)
                continue
            for reason in dict.fromkeys(reasons):
                branches.append({"disposition": "gap", "rule": f"{branch['rule']}。步骤核对发现缺口：{reason}",
                                 "case_ids": branch["case_ids"], "related_evidence_ids": branch["related_evidence_ids"]})
        row["branches"] = branches
    return corrected
