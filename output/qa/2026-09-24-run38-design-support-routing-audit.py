"""只读重放 Run38 的真实分组与设计派生契约，不调用模型或数据库。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from modules.agent_platform.test_generation_batching import (  # noqa: E402
    GENERATION_MAX_REQUIRED_FACTS_PER_BATCH,
    _validate_generation_layout,
)
from modules.agent_platform.test_generation_facts import derive_test_design_item_ids  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


sources = [
    ROOT / "output/qa/2026-09-23-run38-local-failure-real-model.checkpoint.json",
    ROOT / "output/qa/2026-09-23-run38-local-failure-item8-semantic-audit.json",
    ROOT / "output/qa/2026-09-23-run38-local-failure-item8-review-comparison.json",
]
code_paths = [ROOT / "backend/modules/agent_platform" / name for name in (
    "test_generation_batching.py", "test_generation_facts.py", "test_generation_agent.py",
    "test_generation_planning.py", "test_generation_planning_agent.py",
    "test_generation_review.py", "test_generation_escalation.py", "test_generation_builtin_specs.py",
)]
before = {str(p.relative_to(ROOT)): sha(p) for p in [*sources, *code_paths]}
state = json.loads(sources[0].read_text(encoding="utf-8"))["checkpoint"]["state"]
audit = json.loads(sources[1].read_text(encoding="utf-8"))
comparison = json.loads(sources[2].read_text(encoding="utf-8"))
item = state["generation_inputs"][8]
assert item == audit["source_evidence"]["input"]
modules = state["accepted_plan"]["plan"]["business_modules"]
facts_by_id = {f["fact_id"]: f for f in state["effective_facts"]["effective_facts"]}
module_facts = [[facts_by_id[f] for f in m["fact_ids"] if f in facts_by_id] for m in modules]
groups, budgets = _validate_generation_layout(
    state["generation_layout"], module_facts_by_index=module_facts,
    case_budget=50, batch_case_limit=5,
)
record = state["generation_records"][8]["output"]
derived = derive_test_design_item_ids(
    case_fact_bindings=record["case_fact_bindings"],
    fact_design_item_ids=item["case_fact_contract"]["fact_design_item_ids"],
    required_fact_ids=item["case_fact_contract"]["required_fact_ids"],
    required_design_item_ids=item["case_fact_contract"]["required_test_design_item_ids"],
)
assert all(derived[c["case_id"]] == c["test_design_item_ids"] for c in record["test_cases"])

designs = {d["test_design_item_id"]: d for d in item["plan"]["test_design_items"]}
observations = []
for design_id in audit["source_evidence"]["relevant_designs"]:
    support = []
    for index, batch in enumerate(state["generation_inputs"]):
        if batch["batch"]["module_index"] != 2:
            continue
        local = {f["fact_id"]: f for f in batch["authoritative_facts"]}
        routes = batch["case_fact_contract"]["fact_design_item_ids"]
        routed = [{"fact_id": f, "assertion": local[f]["assertion"]}
                  for f, ids in routes.items() if design_id in ids]
        if routed:
            support.append({"item_index": index, "batch_id": batch["batch"]["batch_id"],
                            "case_budget": batch["case_budget"], "routed_facts": routed})
    observations.append({"design": designs[design_id], "current_routed_facts_by_batch": support})

module = modules[2]
edges = {fact_id: set() for fact_id in module["fact_ids"]}
design_facts = {}
for route in module["fact_design_routes"]:
    for design_index in route["test_design_item_indexes"]:
        design_facts.setdefault(design_index, set()).add(route["fact_id"])
for related in design_facts.values():
    for fact_id in related:
        edges[fact_id].update(related - {fact_id})
visited, components = set(), []
for fact_id in edges:
    if fact_id in visited:
        continue
    pending, component = [fact_id], set()
    while pending:
        current = pending.pop()
        if current in component:
            continue
        component.add(current)
        pending.extend(edges[current] - component)
    visited.update(component)
    components.append(sorted(component))
module_batches = [b for b in state["generation_inputs"] if b["batch"]["module_index"] == 2]
all_design_ids = {d for b in module_batches for d in b["case_fact_contract"]["required_test_design_item_ids"]}
cross_batch_ids = sorted(d for d in all_design_ids if sum(
    d in b["case_fact_contract"]["required_test_design_item_ids"] for b in module_batches) > 1)
after = {str(p.relative_to(ROOT)): sha(p) for p in [*sources, *code_paths]}
assert before == after
report = {
    "scope": "Run38 历史隔离诊断的 item8 与当前源码只读审计；不是新模型验证，也不恢复正式 Run38。",
    "boundaries": {"model_calls": 0, "database_reads": 0, "database_writes": 0,
                   "production_edits": 0, "business_fact_mutations": 0,
                   "historical_sources_and_code_unchanged": before == after},
    "sources_sha256": before,
    "real_replays": {
        "original_layout_accepted": True,
        "case_budget": sum(sum(x) for x in budgets), "batch_count": sum(map(len, groups)),
        "item8_fact_count": len(item["authoritative_facts"]), "item8_case_budget": item["case_budget"],
        "item8_required_design_count": len(designs),
        "derived_design_ids_match_real_candidate": True, "derived_design_ids": derived,
        "module3_fact_count": len(module["fact_ids"]), "module3_design_count": len(all_design_ids),
        "module3_cross_batch_design_count": len(cross_batch_ids),
        "module3_cross_batch_design_ids": cross_batch_ids,
        "all_routes_as_mandatory_support_component_sizes": sorted(map(len, components), reverse=True),
        "all_routes_as_mandatory_support_components": sorted(components, key=len, reverse=True),
        "fact_count_limit": GENERATION_MAX_REQUIRED_FACTS_PER_BATCH,
        "graph_limit_interpretation": "该图只证明不能把全部关联边直接当共同必要支持集；不证明存在唯一正确业务拆分。",
        "revision_history": state["revision_history"],
    },
    "observed_designs": observations,
    "findings": [
        {"id": "F1", "kind": "错误路由", "detail": "TD-003-002-003 要求到期续约，item8 的 P3-14/15 实为提前续约；完整到期依据 P2-18 已在 item7。"},
        {"id": "F2", "kind": "批次缺信息、系统并不缺来源", "detail": "TD-003-002-004、TD-003-003-008、TD-003-004-001 的完整支持事实已在同模块 item6/7/9，item8 获得完整 TD 文本但只获得局部事实。"},
        {"id": "F3", "kind": "缺契约", "detail": "草案主题保留自然语言复合目标；整合目录删除候选 fact_ids；路由只表达相关性；分批和用例设计编号派生都用 any 关联作为完整覆盖。"},
        {"id": "F4", "kind": "缺行动入口", "detail": "范围报告只接纳两组以上互斥/生命周期/容量问题；单项 TD 支持不足无准确类型。已完成 scope 路由不能带问题定向重审，gap reviewer 仅处理零路由，且合并只能增加边。"},
        {"id": "F5", "kind": "已有跨阶段行动", "detail": "修订草案→重建规划→新输入路由→接受→权威协调→重新分批已存在；布局可在保持事实唯一分区、总50及容量上限下调整，并按完整输入复用缓存；这不是完全固定 DAG。"},
        {"id": "F6", "kind": "审查范围差异", "detail": "R004 合并 M003 的4生成包、10用例、50事实、32TD；更大事实池能发现部分局部用例问题，但合并输入没有逐生成包完整支持契约，初审未指出此分批缺口。"},
    ],
    "code_anchors": {
        "draft_catalog_drops_fact_ids": "backend/modules/agent_platform/test_generation_planning.py:668",
        "missing_routes_only": "backend/modules/agent_platform/test_generation_planning.py:1243",
        "existing_gap_reviewer_input": "backend/modules/agent_platform/test_generation_planning.py:1264",
        "load_based_fact_groups": "backend/modules/agent_platform/test_generation_batching.py:827",
        "layout_invariants": "backend/modules/agent_platform/test_generation_batching.py:1057",
        "full_design_copied_by_any_edge": "backend/modules/agent_platform/test_generation_batching.py:1313",
        "existential_support_contract": "backend/modules/agent_platform/test_generation_batching.py:1354",
        "design_credit_from_any_bound_fact": "backend/modules/agent_platform/test_generation_facts.py:185",
        "scope_feedback_limited_kinds": "backend/modules/agent_platform/test_generation_escalation.py:26",
        "completed_route_no_reassessment": "backend/modules/agent_platform/test_generation_planning_agent.py:604",
        "version_invalidation": "backend/modules/agent_platform/test_generation_agent.py:261",
        "existing_layout_action": "backend/modules/agent_platform/test_generation_agent.py:402",
        "review_merges_generation_packages": "backend/modules/agent_platform/test_generation_review.py:429",
        "review_repair_fact_scope": "backend/modules/agent_platform/test_generation_review.py:934",
    },
    "recommended_next_slice": {
        "scope": "复用现有规划路由缺口复核组件，让已路由意图可基于当前真实问题被定向重审；先产出完整意图支持集合及当前版本身份，不在本切片宣称批次覆盖已修复。",
        "input": "由 problem_ids 与 design 索引选择真实当前意图；复用 prepare_planning_route_repairs 的模块事实和现有路由，补上原草案来源索引；完整意图、既有事实和问题均来自持久状态。",
        "output": "沿用每项 decision 的 fact_ids/reason，明确其表示共同充分支持完整意图的一组原事实；保留源/规划/路由输入哈希和问题来源；引用存在只属结构验收，完整语义仍需独立专家判断。",
        "action": "扩展现有 review_planning_route_gaps 以支持已路由选择；替换被复核意图的支持结论，不再只给缺失项追加边；无完整依据交给已有草案修订，来源冲突保持未解决。",
        "invalidation": "规划外层当前只观察 source/draft/plan 三版本；路由或支持结论单独变化也必须改变有效规划身份并使 accepted_plan、authority、generation、review 失效，缓存只复用完整输入一致记录。",
        "next_contract_migration": "之后让分批分配和设计覆盖派生消费同一支持集合：完整支持集未进入当前执行范围不得记完整TD覆盖；共享上下文与必须覆盖事实的归属需区分，不能将全部相关边当共同支持或复制事实覆盖义务。",
        "limits": "没有原文优先级证据时不能把图文冲突判为图错。此次只读报告不提出替换 F11 的业务事实，也未通过模型证明推荐方案有效。",
    },
}
out = Path(__file__).with_suffix(".json")
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"output": str(out), "real_replays": {k:v for k,v in report["real_replays"].items()
      if k not in {"all_routes_as_mandatory_support_components", "module3_cross_batch_design_ids", "derived_design_ids"}},
      "boundaries": report["boundaries"]}, ensure_ascii=False, indent=2))
