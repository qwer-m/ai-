"""真实Run63叶子验证草稿；CPU当前协议投影不回写历史状态或模型回执。"""

import gzip
import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.test_generation_escalation import validate_generation_scope_feedback
from modules.agent_platform import test_generation_planning_agent as planning

fixture = BASE / "run63_generation_feedback_lifecycle_real.json.gz"
fixture_bytes = fixture.read_bytes()
raw = gzip.decompress(fixture_bytes)
assert hashlib.sha256(raw).hexdigest() == "ec98e6d79b4a9c4c2cf8b9b95fba5cadb90aae53bd60edb11ebb5a1c85015629"
real = json.loads(raw)
production = ROOT / "backend/modules/agent_platform/test_generation_agent.py"
production_bytes = production.read_bytes()
spec = importlib.util.spec_from_file_location("modules.agent_platform._qa_feedback_lifecycle", BASE / "draft_test_generation_agent.py")
draft = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = draft
spec.loader.exec_module(draft)


def snapshot(name):
    frame = real["snapshots"][name]
    state = {key: deepcopy(real["original"][key]) for key in (
        "accepted_plan", "effective_facts", "conflict_confirmations",
    )}
    state["planning"] = deepcopy(real["shared_planning"])
    for key in ("planning_records", "plan", "plan_input_hash"):
        state["planning"][key] = deepcopy(frame[key])
    for key in ("generation_inputs", "generation_records"):
        state[key] = deepcopy(frame[key])
    if name != "before18":
        state["accepted_plan"] = None
    state.update(problems={}, review=None)
    return state


initial = snapshot("before18")
item = real["original_generation_input"]
normalized = validate_generation_scope_feedback(item, real["original_problem"]["payload"])
assert normalized == real["original_problem"]["payload"]
assert action_state_hash(item) == real["original_problem"]["input_hash"]
draft._record_generation_feedback(initial, item, normalized, real["original_generation_index"])
assert len(initial["problems"]) == 2
recorded = deepcopy(initial["problems"])
for problem in recorded.values():
    assert len(problem["payload"]["planning_feedback"]["issues"]) == 1
    assert set(problem["fact_ids"]) == {identity for group in problem["payload"]["planning_feedback"]["issues"][0]["scenario_groups"] for identity in group["fact_ids"]}

# 用已有正式溯源函数核对四个原始设计项，避免QA另造一套归属判据。
ctx = SimpleNamespace(action_state=initial["planning"], action_input=real["original"]["prepared_input"],
                      artifacts={}, confirmed_conflicts=initial["conflict_confirmations"])
original_ids = sorted({identity for issue in normalized["planning_feedback"]["issues"]
                       for group in issue["scenario_groups"] for identity in group["test_design_item_ids"]})
formal_origins = []
targets = draft._generation_feedback_draft_targets(initial)
for identity in original_ids:
    origin = planning.generation_preflight_draft_origins(
        ctx, initial["planning"], real["original"]["accepted_plan"], identity,
        effective_facts=real["original"]["effective_facts"]["effective_facts"],
    )
    target = targets[origin["coverage_intent"]][0]
    assert target["planning_batch_index"] == origin["draft_origins"][0]["planning_batch_index"]
    assert target["module_candidate_index"] == origin["draft_origins"][0]["module_candidate_index"]
    assert target["coverage_topic_index"] == origin["draft_origins"][0]["coverage_topic_index"]
    formal_origins.append(origin)

counts, active = {}, {}
for name in ("before18", "after18", "after21", "after22"):
    state = snapshot(name)
    state["problems"] = deepcopy(recorded)
    before_sources = draft._generation_feedback_source_hash(state)
    draft._project_current_problems(state)
    counts[name] = len(state["problems"])
    active[name] = [problem["payload"]["planning_feedback"]["issues"][0] for problem in state["problems"].values()]
    assert draft._generation_feedback_source_hash(state) == before_sources
    expected = 2 if name == "before18" else 1
    assert counts[name] == expected, (name, counts[name])
    if name != "before18":
        assert all(group["condition"]["fact_id"] in {"DOC287-P0013-f6", "DOC287-P0013-f8"}
                   for issue in active[name] for group in issue["scenario_groups"])
        assert all(not problem["generation_indexes"] for problem in state["problems"].values())

# 位置调整只重新派生执行索引，完整原子意图与来源身份均不改变。
reordered = snapshot("after22")
reordered["problems"] = deepcopy(recorded)
reordered["planning"]["planning_inputs"].reverse()
reordered["planning"]["planning_records"].reverse()
draft._project_current_problems(reordered)
assert len(reordered["problems"]) == 1
assert next(iter(reordered["problems"].values()))["generation_provenance"] == next(
    problem["generation_provenance"] for problem in recorded.values()
    if "DOC287-P0013-f6" in problem["fact_ids"])

# 只破坏真实身份元数据，不构造业务事实；无法验证的草案必须拒绝。
damaged = snapshot("after22")
damaged["problems"] = deepcopy(recorded)
damaged["planning"]["planning_records"][23].pop("input_hash")
try:
    draft._project_current_problems(damaged)
except (ValueError, KeyError):
    pass
else:
    raise AssertionError("缺少当前草案来源身份却沿用了报告")

assert production.read_bytes() == production_bytes
assert fixture.read_bytes() == fixture_bytes
report = {
    "run_id": 63,
    "evidence_type": "既有真实模型报告与真实历史状态在当前草稿中的CPU投影；不是新付费生成或业务验收",
    "fixture_data_sha256": hashlib.sha256(raw).hexdigest(),
    "original_full_input_hash": action_state_hash(item),
    "original_feedback_normalization_exact": True,
    "issue_counts": counts,
    "active_issues": active,
    "formal_existing_generation_preflight_origins_equal": formal_origins,
    "raw_report_unchanged": real["original_problem"]["payload"] == normalized,
    "atomic_issue_fact_scope_not_whole_generation_batch": True,
    "reordered_planning_positions_do_not_change_binding": True,
    "missing_input_hash_rejected": True,
    "production_sha256_unchanged": hashlib.sha256(production_bytes).hexdigest(),
    "production_writes": 0, "business_database_writes": 0, "model_requests_sent": 0,
    "remaining_before_application": [
        "将真实current_draft_origins纳入现有修订的protected_topics/candidates授权，避免仅问题保留但修改范围仍过宽。",
        "确定同一原子范围新生成报告覆盖旧报告的去重/替代规则，避免重生成后重复的活跃报告。",
        "成功生成后退役分支尚缺同一目标的真实成功样本；当前只实现结构逻辑，不能声称真实验收通过。",
        "严格拒绝缺少provenance的当前replan报告，不能暗留旧问题结构的兼容分支。",
    ],
}
target = BASE / "draft-validation.json"
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"report": str(target), "issue_counts": counts, "formal_origin_count": len(formal_origins),
                  "production_writes": 0, "model_requests_sent": 0}, ensure_ascii=False))
