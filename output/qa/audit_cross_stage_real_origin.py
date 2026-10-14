"""只读提取真实规划缺口与来源冲突，保留跨阶段验证起点。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.run_repository import AgentRunRepository


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mentions(value, needle, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from mentions(item, needle, path + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from mentions(item, needle, f"{path}[{index}]")
    elif isinstance(value, str) and needle in value:
        yield {"path": path, "value": value}


def main():
    output = ROOT / "output/qa/2026-09-21-cross-stage-real-origin-evidence.json"
    assert not output.exists(), "保留既有证据，不覆盖"
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        run = db.get(AgentRun, 31)
        keys = (
            "evidence", "prepare_source_semantics", "source_vision", "source_semantics",
            "plan_batches", "prepare_plan_consolidation", "plan", "prepare_plan_routes",
            "plan_routes", "prepare_plan_route_repairs", "plan_route_repairs", "routed_plan",
            "prepare_authority", "authority", "effective_facts", "prepare_generation",
        )
        nodes = {key: repo.latest_node_run(run_id=31, node_key=key) for key in keys}
        before = {key: digest([repo.read_node_input(node), node.output_payload, node.sdk_state]) for key, node in nodes.items()}
        snapshots = {key: deepcopy(node.output_payload) for key, node in nodes.items()}
        plan = snapshots["plan"]["business_modules"][6]
        routed = snapshots["routed_plan"]["business_modules"][6]
        gap = snapshots["prepare_plan_route_repairs"]["items"][0]
        decision = snapshots["plan_route_repairs"]["items"][0]
        missing = gap["missing_test_design_items"][0]["coverage_intent"]
        fact_ids = {"DOC278-P0007-S447-E1020-F0004", "DOC278-P0007-S447-E1020-F0011", "DOC278-P0003-F0013"}
        facts = [item for item in snapshots["source_semantics"]["authoritative_facts"] if item["fact_id"] in fact_ids]
        scope_ids = {item["scope_id"] for item in facts}
        source_entries = [item for item in snapshots["evidence"]["evidence_catalog"]["items"] if item["evidence_id"] in scope_ids]
        assert decision["output"]["decisions"][0]["disposition"] == "unsupported"
        assert missing not in json.dumps(routed, ensure_ascii=False)
        assert "采集传递" in routed["objective"]
        conflict_ids = {"DOC278-P0006-12", "DOC278-P0006-21"}
        conflict_facts = [item for item in snapshots["effective_facts"]["authoritative_facts"] if item["fact_id"] in conflict_ids]
        conflict_scope_ids = {item["scope_id"] for item in conflict_facts}
        report = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "run_id": 31,
            "run_status": run.status,
            "real_data_only": True,
            "mysql_transaction": "READ ONLY",
            "model_requests": 0,
            "online_database_writes": 0,
            "source": snapshots["evidence"]["source"],
            "node_hashes": {key: {"node_run_id": node.id, "status": node.status, "input_sha256": digest(repo.read_node_input(node)), "output_sha256": digest(node.output_payload), "complete_snapshot_sha256": before[key]} for key, node in nodes.items()},
            "recommended_start": {
                "kind": "natural_unsupported_planning_intent_with_stale_objective",
                "plan_node_run_id": nodes["plan"].id,
                "gap_node_run_id": nodes["prepare_plan_route_repairs"].id,
                "decision_node_run_id": nodes["plan_route_repairs"].id,
                "routed_plan_node_run_id": nodes["routed_plan"].id,
                "module_index": 6,
                "plan_before": plan,
                "prepared_gap_input": gap,
                "actual_repair_record": decision,
                "routed_module_after": routed,
                "coverage_group_source": [item for item in snapshots["prepare_plan_consolidation"]["coverage_group_catalog"] if any(missing == value for value in item["coverage_items"])],
                "upstream_mentions": {key: list(mentions(snapshots[key], "采集")) for key in ("source_semantics", "plan_batches", "prepare_plan_consolidation", "plan", "routed_plan", "prepare_generation")},
                "source_facts": facts,
                "source_evidence": source_entries,
                "inference": "原流程只剪掉无事实支撑的覆盖项，未同步模块与测试点目标。可让协调 Agent 回读真实来源，并重规划受影响模块的目标、测试点及事实路由；不得把审批字段展示推断成未定义的后端传输协议。",
                "verification_boundary": "存在真实计划支撑缺口；不等于原文缺失，也不应人为构造补充事实。",
            },
            "secondary_source_conflict": {
                "kind": "natural_text_image_conflict",
                "authority_node_run_id": nodes["authority"].id,
                "item_index": 5,
                "actual_record": snapshots["authority"]["items"][5],
                "facts": conflict_facts,
                "source_evidence": [item for item in snapshots["evidence"]["evidence_catalog"]["items"] if item["evidence_id"] in conflict_scope_ids],
                "verification_boundary": "事实已被旧权威治理标 uncertain 并移出 effective_facts。可以验证回读文本和图片来源，但无法预先断言图片按钮读取有误；需真实重新看图。",
            },
            "reusable_full_inputs": {
                "plan": snapshots["plan"],
                "planning_scopes": snapshots["source_semantics"]["planning_scopes"],
                "authoritative_facts": snapshots["source_semantics"]["authoritative_facts"],
                "route_prepared": snapshots["prepare_plan_routes"],
                "route_records": snapshots["plan_routes"],
                "case_budget": run.input_payload["case_budget"],
            },
        }
        for key, node in nodes.items():
            db.refresh(node)
            assert digest([repo.read_node_input(node), node.output_payload, node.sdk_state]) == before[key]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "path": str(output), "file_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "primary_node_ids": [864, 867, 868, 869], "actual_candidate_sha256": decision["input_hash"], "source_content_hash": report["source"]["content_hash"], "source_and_records_unchanged": True, "model_requests": 0, "online_database_writes": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
