"""仅在隔离模块验证分组草稿；不修改生产源码或运行中的任务。"""

from copy import deepcopy
from datetime import datetime, timezone
import difflib
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
import types


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/tests/agent_platform"))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_batching as original
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from real_planning_protocol_projection import current_plan_from_recorded_topics


OUTPUT = Path(__file__).parent
PRODUCTION = ROOT / "backend/modules/agent_platform/test_generation_batching.py"


HELPER = '''def _prepare_default_generation_layout(
    *, module_facts_by_index: list[list[dict[str, Any]]],
    design_items_by_module: list[list[dict[str, Any]]], routes_by_module: list[list[dict[str, Any]]],
    case_budget: int, batch_case_limit: int, model_projection: Any,
) -> tuple[list[list[list[dict[str, Any]]]], list[int], list[list[list[dict[str, Any]]]]]:
    """预算内确定性细化来源分组，完整支持闭包与用例预算在同一阶段验收。"""
    groups_by_module = [_generation_fact_groups(facts, model_projection=model_projection)
                        for facts in module_facts_by_index]
    while True:
        targets = _allocate_module_case_targets(
            module_facts_by_index=module_facts_by_index,
            module_fact_groups_by_index=groups_by_module, case_budget=case_budget,
            batch_case_limit=batch_case_limit, model_projection=model_projection,
        )
        groups_by_module = [_expand_generation_fact_groups(
            groups, target_count=max(len(groups), math.ceil(target / batch_case_limit)),
            model_projection=model_projection,
        ) for groups, target in zip(groups_by_module, targets, strict=True)]
        designs_by_module = []
        for facts, groups, designs, routes in zip(
            module_facts_by_index, groups_by_module, design_items_by_module, routes_by_module, strict=True,
        ):
            try:
                designs_by_module.append(_allocate_test_design_items_to_fact_groups(
                    design_items=designs, fact_groups=groups, fact_design_routes=routes,
                    model_projection=model_projection,
                ))
            except OutputValidationError as error:
                if (error.details.get("reason") != "generation_group_capacity_exhausted"
                        or sum(len(values) for values in groups_by_module) >= case_budget):
                    raise
                support = set(error.details["support_fact_ids"])
                facts_by_id = {fact["fact_id"]: fact for fact in facts}
                candidates = []
                for group_index, group in enumerate(groups):
                    split = _split_generation_fact_group(group, model_projection=model_projection)
                    if split is None:
                        continue
                    # 按本次失败支持闭包衡量细分后的真实容量，不引入业务关键词或固定预留。
                    capacities = [generation_context_capacity(
                        [facts_by_id[identity] for identity in sorted(support | {fact["fact_id"] for fact in part})],
                        model_projection=model_projection,
                    ) for part in split]
                    best = min((
                        len(capacity["violations"]),
                        max(capacity["actual"][key] / limit for key, limit in capacity["limits"].items()),
                        capacity["actual"]["fact_json_chars"],
                    ) for capacity in capacities)
                    candidates.append((best, group_index, split))
                if not candidates:
                    raise
                _, group_index, split = min(candidates, key=lambda value: (value[0], value[1]))
                groups[group_index:group_index + 1] = split
                # 每次严格增加一组且不复制事实；总组数受原用例预算限制，不能无限细化。
                break
        else:
            return groups_by_module, targets, designs_by_module


'''


def draft_source(source):
    marker = "def prepare_test_case_batches("
    assert source.count(marker) == 1
    changed = source.replace(marker, HELPER + marker)
    old_groups = '''    module_fact_groups_by_index = [
        _generation_fact_groups(module_facts, model_projection=model_projection)
        for module_facts in module_facts_by_index
    ]
'''
    assert changed.count(old_groups) == 1
    changed = changed.replace(old_groups, "")
    old_default = '''        module_design_batches = [_allocate_test_design_items_to_fact_groups(
            design_items=designs, fact_groups=groups, fact_design_routes=routes,
            model_projection=model_projection,
        ) for designs, groups, routes in zip(active_design_items_by_module, module_fact_groups_by_index,
                                             active_fact_design_routes_by_module, strict=True)]
        module_targets = _allocate_module_case_targets(
            module_facts_by_index=module_facts_by_index,
            module_fact_groups_by_index=module_fact_groups_by_index,
            case_budget=case_budget,
            batch_case_limit=batch_case_limit,
            model_projection=model_projection,
        )
'''
    new_default = '''        module_fact_groups_by_index, module_targets, module_design_batches = _prepare_default_generation_layout(
            module_facts_by_index=module_facts_by_index, design_items_by_module=active_design_items_by_module,
            routes_by_module=active_fact_design_routes_by_module, case_budget=case_budget,
            batch_case_limit=batch_case_limit, model_projection=model_projection,
        )
'''
    assert changed.count(old_default) == 1
    changed = changed.replace(old_default, new_default)
    old_expansion = '''            minimum_batch_count = math.ceil(module_target / batch_case_limit)
            if len(fact_groups) < minimum_batch_count:
                fact_groups = _expand_generation_fact_groups([group for group in fact_groups if group], target_count=minimum_batch_count, model_projection=model_projection)
                design_items_by_batch = _allocate_test_design_items_to_fact_groups(
                    design_items=active_design_items_by_module[module_index], fact_groups=fact_groups,
                    fact_design_routes=active_fact_design_routes_by_module[module_index],
                    model_projection=model_projection,
                )
'''
    assert changed.count(old_expansion) == 1
    # 预算扩组已进入同一准备阶段，删去此处不再可达的第二份扩组/设计分配路径。
    changed = changed.replace(old_expansion, "")
    return changed


def load_run55():
    fixtures = ROOT / "backend/tests/agent_platform/fixtures"
    with gzip.open(fixtures / "run55_layout_preflight_real.json.gz", "rt", encoding="utf-8") as stream:
        fixture = json.load(stream)
    expected = fixture.pop("data_sha256")
    assert action_state_hash(fixture) == expected
    candidate = json.loads((fixtures / "run55_layout_candidate_real.json").read_text(encoding="utf-8"))
    expected = candidate.pop("data_sha256")
    assert action_state_hash(candidate) == expected
    plan = current_plan_from_recorded_topics(candidate["resolved_plan"]["plan"], fixture["state"]["planning"]["planning_records"])
    return {"run_id": 55, "run_metadata": fixture["run_metadata"], "input_payload": fixture["input_payload"],
            "run_context": fixture["run_context"], "plan": plan,
            "effective_facts": fixture["state"]["effective_facts"]["effective_facts"],
            "source_note": "真实历史Run55字段；仅沿已有当前协议CPU编译完整名称与目标，不伪改历史模型回执"}


def load_run59():
    fixture = json.loads((OUTPUT / "run59-accepted-generation-inputs-real.json").read_text(encoding="utf-8"))
    expected = fixture.pop("data_sha256")
    assert action_state_hash(fixture) == expected
    return {"run_id": 59, "run_metadata": {**fixture["run_metadata"], "user_id": fixture["user_id"]},
            "input_payload": fixture["input_payload"], "run_context": fixture["run_context"],
            "plan": fixture["accepted_plan"]["plan"], "effective_facts": fixture["effective_facts"],
            "source_note": "真实accept后两个目标子树，未展开完整state"}


def verify(data, draft):
    engine = create_engine("sqlite://")
    AgentRun.__table__.create(engine)
    started = time.perf_counter()
    with Session(engine, autoflush=False) as db:
        # 隔离库只复制该真实Run的容量元数据，不生成虚构用户或来源事实。
        db.add(AgentRun(**data["run_metadata"], input_payload=deepcopy(data["input_payload"]),
                        run_context=deepcopy(data["run_context"])))
        db.commit()
        def context():
            return ToolExecutionContext(db=db, user_id=1, project_id=8, run_id=data["run_id"],
                                        node_key="qa_cpu_packing", run_input=deepcopy(data["input_payload"]))
        arguments = {"plan": deepcopy(data["plan"]), "effective_facts": deepcopy(data["effective_facts"]),
                     "case_budget": 70, "batch_case_limit": 5}
        before = action_state_hash(arguments)
        old_failure = None
        try:
            original.prepare_test_case_batches(context(), deepcopy(arguments))
        except Exception as error:
            old_failure = {"type": type(error).__name__, "message": str(error), "details": getattr(error, "details", {})}
        allocation = draft._allocate_test_design_items_to_fact_groups
        calls, refinements = [], []
        def observed_allocation(**values):
            calls.append({"groups": len(values["fact_groups"]), "designs": len(values["design_items"])})
            try:
                return allocation(**values)
            except Exception as error:
                if getattr(error, "details", {}).get("reason") == "generation_group_capacity_exhausted":
                    refinements.append({"test_design_item_id": error.details["test_design_item_id"],
                                        "support_fact_ids": error.details["support_fact_ids"],
                                        "module_group_count_before_split": len(values["fact_groups"])})
                raise
        # 此包装只记录隔离CPU草稿的分配尝试，不增加生产接口或修改生产模块。
        draft._allocate_test_design_items_to_fact_groups = observed_allocation
        try:
            compiled = draft.prepare_test_case_batches(context(), deepcopy(arguments))
        finally:
            draft._allocate_test_design_items_to_fact_groups = allocation
        items = compiled["items"]
        proposal = original.generation_layout_from_inputs(items)
        # 候选来自确定性CPU草稿，正式原生产函数的显式布局分支独立再验，不冒称模型回执。
        rechecked = original.prepare_test_case_batches(context(), {**deepcopy(arguments), "generation_layout": proposal})
        assert rechecked["items"] == items
        assert sum(item["case_budget"] for item in items) == 70
        assert max(item["case_budget"] for item in items) <= 5
        requirements = original.generation_layout_requirements(arguments["plan"], arguments["effective_facts"])
        for requirement, module in zip(requirements, proposal, strict=True):
            assert requirement["module_index"] == module["module_index"]
            fact_ids = [identity for group in module["groups"] for identity in group["fact_ids"]]
            designs = [identity for group in module["groups"] for identity in group["test_design_item_ids"]]
            assert len(fact_ids) == len(set(fact_ids)) and set(fact_ids) == set(requirement["required_fact_ids"])
            assert len(designs) == len(set(designs)) and set(designs) == {item["test_design_item_id"] for item in requirement["test_design_items"]}
        projection = original.generation_model_projection(context())
        capacities = [original.generation_context_capacity(item["authoritative_facts"], model_projection=projection) for item in items]
        assert all(capacity["fits"] for capacity in capacities)
        assert action_state_hash(arguments) == before and not db.new and not db.dirty and not db.deleted
        result = {"run_id": data["run_id"], "source_note": data["source_note"], "old_default_failure": old_failure,
                  "draft_default_success": True, "formal_original_explicit_validation": True,
                  "batch_count": len(items), "case_budget": 70, "batch_case_budgets": [item["case_budget"] for item in items],
                  "facts_and_TDs_exactly_once_per_module": True, "capacity_maxima": {
                      key: max(capacity["actual"][key] for capacity in capacities) for key in capacities[0]["actual"]},
                  "frozen_projection_sha256": action_state_hash(projection), "cpu_seconds": round(time.perf_counter() - started, 3),
                  "deterministic_refinements": refinements, "cpu_allocation_calls": len(calls),
                  "proposal_kind": "CPU候选，不是模型回执，也未提交到真实Worker", "proposal": proposal}
        db.rollback()
    engine.dispose()
    return result


def main():
    source_sha256 = hashlib.sha256(PRODUCTION.read_bytes()).hexdigest()
    source = PRODUCTION.read_text(encoding="utf-8")
    changed = draft_source(source)
    diff = "".join(difflib.unified_diff(source.splitlines(True), changed.splitlines(True),
                                     fromfile="a/backend/modules/agent_platform/test_generation_batching.py",
                                     tofile="b/backend/modules/agent_platform/test_generation_batching.py"))
    (OUTPUT / "default-packing-source-fix-draft.patch").write_text(diff, encoding="utf-8", newline="\n")
    (OUTPUT / "test_generation_batching_draft.py").write_text(changed, encoding="utf-8")
    draft = types.ModuleType("modules.agent_platform.qa_default_packing_draft")
    draft.__package__ = "modules.agent_platform"
    exec(compile(changed, str(OUTPUT / "test_generation_batching_draft.py"), "exec"), draft.__dict__)
    results = []
    for data in (load_run55(), load_run59()):
        print(json.dumps({"starting_cpu_run": data["run_id"]}), flush=True)
        result = verify(data, draft)
        results.append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "proposal"}, ensure_ascii=False), flush=True)
    assert PRODUCTION.read_text(encoding="utf-8") == source
    assert hashlib.sha256(PRODUCTION.read_bytes()).hexdigest() == source_sha256
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "policy": "只有QA草稿与独立CPU候选；生产文件、真实Run、旧回执与预算不变",
              "production_sha256_before_and_after": source_sha256, "production_unchanged": True,
              "model_calls": 0, "business_database_writes": 0, "worker_layout_submissions": 0,
              "results": results}
    (OUTPUT / "default-packing-source-fix-draft-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
