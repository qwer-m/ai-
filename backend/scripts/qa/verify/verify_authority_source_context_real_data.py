"""用真实路由和最新单元格事实验证来源权威审核触发，不调用模型或保存运行状态。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import validate
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_planning import _collect_planning_scope_route_state
from modules.agent_platform.test_generation_schemas import AUTHORITATIVE_FACT_SCHEMA
from modules.agent_platform.test_generation_semantics import (
    _AUTHORITY_REVIEW_SIGNALS, _requires_authority_review, prepare_authority_reconciliation,
)


def existing_signals(fact):
    return {
        "image": "image_region" in fact["source_anchor"],
        "inactive_status": fact["status"] != "effective",
        "governed_by": bool(fact["governed_by"]),
        "governance_words": any(signal in fact["assertion"] for signal in _AUTHORITY_REVIEW_SIGNALS),
    }


def previous_positions(facts):
    """仅复算旧门槛，保留其把同页全部正文压成一个位置的行为作为对照。"""
    return sorted({(fact["source_anchor"]["source_kind"], fact["source_anchor"].get("page_number", 0),
                   str(fact["source_anchor"].get("block_id", ""))
                   if "image_region" in fact["source_anchor"] else "") for fact in facts})


def verify(route_path: Path, source_path: Path):
    original_files = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (route_path, source_path)}
    route_report = json.loads(route_path.read_text("utf-8"))
    source_report = json.loads(source_path.read_text("utf-8"))
    state = route_report["planning_state"]
    routes = _collect_planning_scope_route_state(plan=state["plan"],
        prepared_items=state["route_inputs"], route_records=state["route_records"])
    old_facts = deepcopy(state["source_semantics"]["authoritative_facts"])
    old_by_id = {fact["fact_id"]: fact for fact in old_facts}
    # fact_ids 直接来自生产路由归并器，不从模型断言或模块名称推测归属。
    route_plan = {**deepcopy(state["plan"]), "business_modules": [
        {**deepcopy(module), "fact_ids": list(routes["facts_by_module"][index])}
        for index, module in enumerate(routes["modules"])
    ]}
    new_facts = [deepcopy(fact) for fragment in source_report["fragments"]
                 for fact in fragment["normalized_output"]["authoritative_facts"]]
    for fact in new_facts:
        validate(fact, AUTHORITATIVE_FACT_SCHEMA)
    table_facts = [fact for fact in new_facts if fact.get("source_context", {}).get("table_contexts")]
    assert table_facts
    baseline_checks = []
    for fact in [*old_facts, *new_facts]:
        if fact.get("source_context", {}).get("table_contexts"):
            continue
        signals = existing_signals(fact)
        assert _requires_authority_review([fact]) == any(signals.values())
        baseline_checks.append({"fact_id": fact["fact_id"], "signals": signals})
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, route_report["source_run_id"])
            before = action_state_hash([run.status, run.run_context, run.output_payload])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="resolve_generation", run_input=deepcopy(run.input_payload), artifacts={})
            prepared = prepare_authority_reconciliation(context, {"plan": route_plan, "authoritative_facts": old_facts})
            active = {item["module_index"] for item in prepared["items"]}
            modules = []
            for index, module in enumerate(route_plan["business_modules"]):
                facts = [old_by_id[fact_id] for fact_id in module["fact_ids"]]
                positions = previous_positions(facts)
                existing = any(any(existing_signals(fact).values()) for fact in facts)
                assert (index in active) == _requires_authority_review(facts)
                modules.append({"module_index": index, "module_name": module["name"],
                    "fact_ids": module["fact_ids"], "previous_source_positions": positions,
                    "existing_signal": existing, "previous_eligible": len(positions) >= 2 and existing,
                    "current_eligible": index in active})
            assert any(not item["previous_eligible"] and item["current_eligible"]
                       and len(item["previous_source_positions"]) == 1 for item in modules)
            assert any(not item["current_eligible"] for item in modules), "不能把全部模块改成固定审核"
            initial_artifact = deepcopy(context.artifacts["authority_reconciliation_prepare"])
            single_checks = []
            # 用真实模块说明及真实模型事实的单项子集检验入口；不声称这是已提交的新事实路由。
            source_scopes = {fact["scope_id"] for fact in table_facts}
            module_index = next(index for index, ids in enumerate(routes["facts_by_module"])
                                if any(old_by_id[fact_id]["scope_id"] in source_scopes for fact_id in ids))
            real_module = routes["modules"][module_index]
            for fact in table_facts:
                isolated_module = {**deepcopy(real_module), "fact_ids": [fact["fact_id"]]}
                item = prepare_authority_reconciliation(context, {
                    "plan": {"business_modules": [isolated_module]}, "authoritative_facts": [fact]})
                assert item["review_module_count"] == 1
                assert item["items"][0]["authoritative_facts"] == [fact]
                assert len(previous_positions([fact])) == 1
                assert not any(existing_signals(fact).values())
                single_checks.append({"fact_id": fact["fact_id"], "table_cell": fact["source_anchor"]["table_cell"],
                    "source_context": fact["source_context"], "previous_eligible": False, "current_eligible": True,
                    "source_context_is_only_review_signal": True})
            assert before == action_state_hash([run.status, run.run_context, run.output_payload])
            assert not db.new and not db.dirty and not db.deleted
        finally:
            db.rollback()
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value for path, value in original_files.items())
    return {"status": "passed", "model_requests_sent": 0, "database_writes": 0,
        "source_files": original_files, "source_run_id": route_report["source_run_id"],
        "historical_route_module_checks": modules, "authority_prepare_artifact": initial_artifact,
        "historical_review_module_count": len(active), "historical_skipped_module_count": len(modules) - len(active),
        "new_single_fact_context_checks": single_checks,
        "non_context_fact_baseline_checks": baseline_checks,
        "verification_limits": [
            "历史模块 fact_ids 由真实生产路由归并器派生；未通过/绕过支持审查，也未提交新业务规划。",
            "单事实检查使用真实模块说明及新模型事实子集，只验证既有authority入口，不伪造正式归属。",
            "这里只证明现有专家可被调用；没有调用模型裁决表头与正文冲突，也未修改事实状态。",
            "原有accept_generation_plan仍要求规划支持审查通过；本次没有增加新阶段或绕过该门槛。",
        ]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-report", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("拒绝覆盖真实权威触发验证报告")
    result = verify(args.route_report, args.source_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in
        ("status", "historical_review_module_count", "historical_skipped_module_count", "model_requests_sent", "database_writes")}, ensure_ascii=False))
