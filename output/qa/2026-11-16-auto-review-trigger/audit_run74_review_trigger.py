"""独立重放 Run74 修稿后为何未自动复审；只读历史文件，不调用模型或数据库。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "2026-11-16-review-completeness" / "live-scope"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def refs(value):
    return {item["evidence_id"] for item in value["source_refs"]}


snapshot_path = SOURCE / "generation-repaired-snapshot.json"
raw = snapshot_path.read_bytes()
snapshot = json.loads(raw)
state = snapshot["state"]
before = deepcopy(state)
cases, review = state["cases"]["items"], state["review"]
coverage = {item["coverage_id"]: item for item in state["coverage_plan"]["items"]}
changed = {identity for identity, record in cases.items()
           if record["version"] != review["snapshot"]["case_versions"].get(identity)}
rows = []
for issue in state["open_issues"].values():
    if issue["status"] == "resolved":
        continue
    previous = issue["target_versions"]
    current = {"cases": {key: cases[key]["version"] for key in issue["target_case_ids"]},
               "coverage": {key: digest(coverage[key]) if key in coverage else None
                            for key in issue["coverage_ids"]}}
    # 这是被核查的旧公式，不调用可能正在修改的生产函数，也不把修后结果冒充旧行为。
    old_progress = any(current[kind].get(key) != value
        for kind in ("cases", "coverage") for key, value in previous[kind].items())
    issue_refs = refs(issue)
    coverage_linked = sorted(key for key in changed
        if set(cases[key]["content"]["coverage_ids"]) & set(issue["coverage_ids"]))
    source_linked = sorted(key for key in changed if issue_refs & (
        refs(cases[key]["content"]) | set().union(*(
            refs(coverage[identity]) for identity in cases[key]["content"]["coverage_ids"]))))
    rows.append({"issue_id": issue["issue_id"], "kind": issue["kind"], "reason": issue["reason"],
        "target_case_ids": issue["target_case_ids"], "coverage_ids": issue["coverage_ids"],
        "source_evidence_ids": sorted(issue_refs), "target_versions": previous,
        "old_formula_has_progress": old_progress,
        "changed_cases_linked_by_coverage": coverage_linked,
        "changed_cases_linked_by_source": source_linked})
blocked = [row for row in rows if not row["old_formula_has_progress"]]
assert len(rows) == 15 and len(blocked) == 4
assert sum(not row["target_case_ids"] and not row["coverage_ids"] for row in blocked) == 3
assert all(row["changed_cases_linked_by_source"] for row in blocked)
assert state == before and snapshot_path.read_bytes() == raw
cost = json.loads((SOURCE / "generation-cost-audit.json").read_text(encoding="utf8"))
trigger = next(row for row in cost["categories"] if row["category"] == "显式触发复审")
assert trigger["requests"] == 1 and trigger["total_tokens"] == 29361
result = {"run_id": snapshot["run"]["id"], "node_id": snapshot["node_id"],
    "historical_snapshot_sha256": hashlib.sha256(raw).hexdigest(),
    "snapshot_capture": snapshot["capture"], "database_reads": False,
    "database_writes": False, "model_requests_sent": 0,
    "case_count": len(cases), "changed_cases": sorted(changed),
    "active_issue_count": len(rows), "old_readiness": False, "blocked_issue_count": len(blocked),
    "blocked_issues": blocked, "all_issue_progress": rows, "avoidable_main_model_request": trigger,
    "boundary": "来源或覆盖关系只能证明相关合法修稿发生，不能证明业务缺口已解决；结案仍由实际独审完成。"}
target = HERE / "run74-trigger-root-cause.json"
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({"artifact": str(target), "blocked_issues": len(blocked),
                  "avoidable_tokens": trigger["total_tokens"], "model_requests_sent": 0}, ensure_ascii=False))
