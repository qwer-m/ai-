"""只读对比63两轮真实生成输入及冻结模型视图，不放宽生产缓存身份。"""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import runpy

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
h = runpy.run_path(str(ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-04/capture_cost_progress.py"))
from core.db.model_defs import AgentRun
from modules.agent_platform.model_input_projection import project_input_value


def without_summary(item):
    value = deepcopy(item)
    value["plan"].pop("requirement_summary", None)
    return value


def without_summary_and_rationale(item):
    value = without_summary(item)
    for design in value["plan"]["test_design_items"]:
        design.pop("rationale", None)
    return value


with h["SessionLocal"](autoflush=False) as db:
    db.execute(h["text"]("SET TRANSACTION READ ONLY"))
    run = db.get(AgentRun, 63)
    definition = next(item for item in run.run_context["execution_contract"]["agents"] if item["key"] == "test_case_generator")
    projection = definition["runtime_config"]["input_projection"]
    assert "requirement_summary" not in projection["plan"] and "requirement" not in projection
    before = h["receipt_record"](db, "call_a21737a010fd4cf1a25d13051cbf58e8")
    after = h["receipt_record"](db, "call_e4fbc4406fc34ba58ff2a6983c13ea28")
    reader = h["TargetedReader"](db)
    old = reader.subtree(before["pre_state_hash"], ["generation_inputs"])
    new = reader.subtree(after["pre_state_hash"], ["generation_inputs"])
    protected = h["action_state_hash"]([old, new])
    next_by_batch = {item["batch"]["batch_id"]: item for item in new}
    rows, changed_design_fields = [], Counter()
    for left in old:
        right = next_by_batch[left["batch"]["batch_id"]]
        views = [project_input_value(item, projection) for item in (left, right)]
        projected_without_rationale = [without_summary_and_rationale(view) for view in views]
        left_designs = {item["test_design_item_id"]: item for item in left["plan"]["test_design_items"]}
        right_designs = {item["test_design_item_id"]: item for item in right["plan"]["test_design_items"]}
        fields = set()
        for identity in set(left_designs) & set(right_designs):
            a, b = left_designs[identity], right_designs[identity]
            fields.update(key for key in set(a) | set(b) if a.get(key) != b.get(key))
        changed_design_fields.update(fields)
        rows.append({"batch_id": left["batch"]["batch_id"], "full_raw_equal": left == right,
            "facts_equal": left["authoritative_facts"] == right["authoritative_facts"],
            "requirement_equal": left["requirement"] == right["requirement"],
            "case_contract_equal": left["case_fact_contract"] == right["case_fact_contract"],
            "raw_without_global_summary_equal": without_summary(left) == without_summary(right),
            "raw_without_summary_and_rationale_equal": without_summary_and_rationale(left) == without_summary_and_rationale(right),
            "frozen_model_view_equal": views[0] == views[1],
            "model_view_without_rationale_equal": projected_without_rationale[0] == projected_without_rationale[1],
            "changed_design_fields": sorted(fields),
            "old_raw_sha256": h["action_state_hash"](left), "new_raw_sha256": h["action_state_hash"](right),
            "old_model_view_sha256": h["action_state_hash"](views[0]), "new_model_view_sha256": h["action_state_hash"](views[1])})
    assert h["action_state_hash"]([old, new]) == protected
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()
report = {"run_id": 63, "business_database_writes": 0, "model_requests_sent": 0,
    "old_pre_state_hash": before["pre_state_hash"], "new_pre_state_hash": after["pre_state_hash"],
    "frozen_projection_version": definition["runtime_config"]["input_projection_version"],
    "projection_already_excludes_global_summary_and_requirement": True,
    "batch_count": len(rows), "same_counts": {key: sum(row[key] for row in rows) for key in rows[0] if key.endswith("_equal")},
    "changed_design_field_batch_counts": dict(changed_design_fields), "batches": rows,
    "source_level_recommendation": "完整全局plan摘要继续用于原计划展示和执行链审计；生成batch契约不应复制它。若删除需同步BATCH_PLAN_SCHEMA与builder，保持原始plan/事实/完整提交验收，不在哈希函数临时忽略字段。rationale若只作解释，应从生成契约及模型视图移出或由真实稳定来源保持原值；真实意图、对象、前置、支持事实、数量及ID变化必须继续失效。",
    "candidate_projection_not_applied": True,
    "scope": "真实旧输入的CPU字段差分；去字段仅度量候选契约，不自动批准复用，不声称删除rationale天然安全，不修改63或新模型返回"}
(BASE / "generation-projection-readonly-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in report.items() if key != "batches"}, ensure_ascii=False, indent=2))
