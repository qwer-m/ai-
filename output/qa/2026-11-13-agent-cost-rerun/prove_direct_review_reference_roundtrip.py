"""真实70条候选的引用共享可逆证明；没有模型调用或业务数据写入。"""

from copy import deepcopy
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
namespace = runpy.run_path(str(ROOT / "backend/tests/agent_platform/test_single_agent_direct_protocol_real_data.py"))
real = namespace["real"].__wrapped__()
resource = namespace["persisted"].__wrapped__(real)
db, state = next(resource)
context = namespace["context_from_real"](real, db, state=state)
from modules.agent_platform import test_generation_agent as core
from modules.agent_platform.generation_evidence import source_image_descriptor
from modules.agent_platform.action_journal import action_state_hash


def size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


before = action_state_hash(context.action_state)
payload = core._review_input(context.action_state)
restored = deepcopy(payload)
catalog = restored.pop("source_references")
owners = [*restored["cases"], *restored["coverage_plan"]["items"], *restored["open_issues"]]
for item in owners:
    item["source_refs"] = [deepcopy(catalog[identity]) for identity in item["source_refs"]]
assert restored["cases"] == core._review_case_view(context.action_state)
assert restored["coverage_plan"] == context.action_state["coverage_plan"]
assert restored["open_issues"] == core._review_issue_view(context.action_state)
assert all(action_state_hash({key: case[key] for key in core._CASE_FIELDS}) == case["content_hash"]
           for case in restored["cases"])
observation = core.observe_generation_state(context)
observed_items = [{"coverage_id": item["coverage_id"], "objective": item["objective"],
                  "source_refs": [deepcopy(observation["source_references"][reference]) for reference in item["source_refs"]]}
                 for item in observation["coverage_plan"]["items"]]
assert observed_items == context.action_state["coverage_plan"]["items"]
by_id = {item["evidence_id"]: item for item in context.action_state["evidence_catalog"]["items"]}
image_refs = {ref["evidence_id"] for item in owners for ref in item["source_refs"] if by_id[ref["evidence_id"]]["kind"] == "image"}
selected = {identity for evidence_id in image_refs for identity in by_id[evidence_id]["block_ids"]}
actual = {block["block_id"] for page in payload["image_pages"] for block in page["blocks"]}
assert actual == selected
assert all(set(block) == {"block_id", "type", "bbox"} for page in payload["image_pages"] for block in page["blocks"])
fresh = source_image_descriptor(context.action_state["source_ref"], payload["image_pages"][0]["page_number"])
assert all(set(block) == {"block_id", "type", "bbox"} and block["type"] == "image" for block in fresh["blocks"])
assert action_state_hash(context.action_state) == before
findings = namespace["actual_global_review_candidate"](real, context.action_state)["issues"]
assert findings
for finding in findings:
    core.report_issue(context, finding)
issue_state_hash = action_state_hash(context.action_state)
with_issues = core._review_input(context.action_state)
restored_issues = deepcopy(with_issues["open_issues"])
for item in restored_issues:
    item["source_refs"] = [deepcopy(with_issues["source_references"][identity]) for identity in item["source_refs"]]
assert restored_issues == core._review_issue_view(context.action_state)
assert action_state_hash(context.action_state) == issue_state_hash
report = {
    "fixture": "run63_direct_protocol_cpu_real.json.gz",
    "scope": "真实旧候选的当前协议CPU投影；不是新模型生成或独立业务审查，不构成正常新任务的容量上界。",
    "cases": len(payload["cases"]), "unique_source_references": len(catalog),
    "shared_review_bytes": size(payload), "expanded_same_review_bytes": size(restored),
    "reference_bytes_saved": size(restored) - size(payload),
    "coverage_state_roundtrip_exact": True, "cases_state_roundtrip_exact": True,
    "case_content_hashes_exact_after_restoration": True, "issue_roundtrip_exact": True,
    "nonempty_real_issue_candidates_proved": len(restored_issues),
    "coverage_observation_roundtrip_exact": True, "source_state_hash_unchanged": True,
    "image_page_count": len(payload["image_pages"]), "quoted_image_evidence_count": len(image_refs),
    "rendered_crop_count": len(actual), "rendered_image_count": len(payload["image_pages"]) + len(actual),
    "only_referenced_crops": True, "page_descriptors_do_not_duplicate_text_blocks": True,
    "model_requests": 0, "business_database_writes": 0,
}
try:
    next(resource)
except StopIteration:
    pass
path = Path(__file__).with_name("direct-review-reference-roundtrip.json")
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
