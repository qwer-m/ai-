"""回放真实规划与来源输出，验证跨阶段行动边界；不调用模型或写入线上库。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.test_generation_planning import (
    prepare_business_plan_batches, prepare_business_plan_consolidation,
    validate_business_plan_output,
)
from modules.agent_platform.test_generation_semantics import (
    merge_source_semantics, postprocess_source_semantics_item,
)
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, sqlite_longtext as _sqlite_longtext


NODE_KEYS = (
    "evidence", "prepare_source_semantics", "source_text", "source_vision", "source_semantics",
    "prepare_plan_batches", "plan_batches", "prepare_plan_consolidation", "plan",
    "prepare_plan_routes", "plan_routes", "prepare_plan_route_repairs", "plan_route_repairs", "routed_plan",
)


def rejected(operation, error_type=OutputValidationError) -> Exception:
    """只执行真实拒绝分支，不构造模型响应。"""
    try:
        operation()
    except error_type as error:
        return error
    raise AssertionError(f"操作必须被 {error_type.__name__} 拒绝")


def snapshot(node, repo: AgentRunRepository) -> dict[str, Any]:
    return {
        "node_run_id": node.id, "status": node.status,
        "input": deepcopy(repo.read_node_input(node)), "output": deepcopy(node.output_payload),
        "sdk_state": deepcopy(node.sdk_state),
    }


def planning_arguments(nodes: dict[str, dict], case_budget: int) -> dict[str, Any]:
    """从真实节点输入引用还原完整数据，不从历史摘要反推事实。"""
    source = nodes["source_semantics"]["input"]
    planning = nodes["prepare_plan_consolidation"]["input"]
    return {
        "evidence_source": deepcopy(nodes["evidence"]["output"]["source"]),
        "evidence_catalog": deepcopy(nodes["evidence"]["output"]["evidence_catalog"]),
        "source_text_inputs": deepcopy(source["text_inputs"]),
        "source_text_records": deepcopy(source["text_records"]),
        "source_vision_inputs": deepcopy(source["vision_inputs"]),
        "source_vision_records": deepcopy(source["vision_records"]),
        "planning_inputs": deepcopy(planning["prepared_items"]),
        "planning_records": deepcopy(planning["plan_records"]),
        "case_budget": case_budget,
    }


def context_for(db, run, *, artifacts: dict | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id, node_key="plan",
        run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(artifacts if artifacts is not None else (run.run_context or {}).get("artifacts", {})),
    )


def load_planning_fixture(db, run_id: int) -> tuple[Any, dict[str, dict], dict[str, Any]]:
    """加载同一真实运行的完整节点输入与原始输出，供只读回放共用。"""
    repo = AgentRunRepository(db)
    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"真实运行不存在: run_id={run_id}")
    nodes = {}
    for key in NODE_KEYS:
        node = repo.latest_node_run(run_id=run_id, node_key=key)
        if node is None or node.status != "success":
            raise ValueError(f"真实规划阶段没有完整成功记录: run_id={run_id}, node_key={key}")
        nodes[key] = snapshot(node, repo)
    arguments = planning_arguments(nodes, int(run.input_payload["case_budget"]))
    return run, nodes, arguments


def verify_historical_contracts(context, nodes: dict[str, dict]) -> dict[str, Any]:
    """重放来源、草案与成功/被拒规划的原始契约，为新协调状态提供真实基线。"""
    source_input = deepcopy(nodes["source_semantics"]["input"])
    source = merge_source_semantics(context, source_input)
    assert source == nodes["source_semantics"]["output"]
    prepared = prepare_business_plan_batches(context, {
        "planning_scopes": source["planning_scopes"],
        "case_budget": nodes["prepare_plan_batches"]["input"]["case_budget"],
    })
    assert prepared == nodes["prepare_plan_batches"]["output"]
    consolidation = prepare_business_plan_consolidation(context, deepcopy(nodes["prepare_plan_consolidation"]["input"]))
    assert consolidation == nodes["prepare_plan_consolidation"]["output"]
    candidate_results = []
    for call in nodes["plan"]["sdk_state"]["tool_calls"]:
        if call["tool_key"] != "submit_business_plan":
            continue
        candidate = deepcopy(call["arguments"])
        try:
            normalized = validate_business_plan_output(context, {
                "input_payload": deepcopy(nodes["plan"]["input"]), "output": candidate,
            })
        except OutputValidationError as error:
            candidate_results.append({"tool_call_id": call["tool_call_id"], "status": "rejected",
                                      "reason": error.details.get("reason"), "details": deepcopy(error.details)})
        else:
            assert normalized == nodes["plan"]["output"]
            candidate_results.append({"tool_call_id": call["tool_call_id"], "status": "accepted",
                                      "normalized_output_sha256": action_state_hash(normalized)})
        assert candidate == call["arguments"]
    assert candidate_results and candidate_results[-1]["status"] == "accepted"

    source_candidates = []
    for kind in ("text", "vision"):
        node = nodes[f"source_{kind}"]
        inputs = node["input"]["items"]
        outputs = {record["item_index"]: record["output"] for record in node["output"]["items"]}
        for state in node["sdk_state"]["items"]:
            index = state["item_index"]
            calls = [call for call in state["tool_calls"] if call["tool_key"] == "submit_source_semantics"]
            assert calls, (kind, index)
            candidate = deepcopy(calls[-1]["arguments"])
            normalized = postprocess_source_semantics_item(context, {
                "item_input": deepcopy(inputs[index]), "item_output": candidate,
            })
            assert normalized == outputs[index]
            assert candidate == calls[-1]["arguments"]
            source_candidates.append({"source_kind": kind, "item_index": index,
                                      "normalized_output_sha256": action_state_hash(normalized)})
    return {"source_semantics_identical": True, "planning_batches_identical": True,
            "consolidation_identical": True, "source_candidates": source_candidates,
            "historical_plan_candidates": candidate_results}


def outputs_for_action(action: dict, inputs: list[dict], records: list[dict]) -> list[dict]:
    """仅将完整输入完全相同的历史输出交给当前行动，不能按碰巧相同的下标匹配。"""
    records_by_index = {record["item_index"]: record for record in records}
    outputs = []
    for item in action["items"]:
        matches = [index for index, original in enumerate(inputs) if item == original]
        assert len(matches) == 1, "行动输入必须唯一命中真实历史输入"
        outputs.append(deepcopy(records_by_index[matches[0]]["output"]))
    return outputs


def regroup_historical_routes(action: dict, inputs: list[dict], records: list[dict]) -> list[dict]:
    """只重组真实逐 scope 路由；核对全部事实和完整覆盖意图未变，不伪造模型输出。"""
    scoped = {}
    for original, record in zip(inputs, records, strict=True):
        catalog = deepcopy(original["business_modules"])
        for module in catalog:
            for design in module["test_design_items"]:
                design.pop("test_point", None)
                design.pop("technique", None)
        routes = {route["scope_id"]: route for route in record["output"]["routes"]}
        for scope in original["scopes"]:
            assert scope["scope_id"] not in scoped
            scoped[scope["scope_id"]] = (scope, catalog, routes[scope["scope_id"]])
    outputs = []
    for item in action["items"]:
        routes = []
        for scope in item["scopes"]:
            previous_scope, previous_catalog, route = scoped.pop(scope["scope_id"])
            assert previous_scope == scope and previous_catalog == item["business_modules"]
            routes.append(deepcopy(route))
        outputs.append({"routes": routes})
    assert not scoped
    return outputs


def verify_planning_state(db, run, nodes: dict[str, dict], arguments: dict, fixtures: dict) -> dict:
    from modules.agent_platform import test_generation_planning_agent as planning

    context = context_for(db, run)
    initial = deepcopy(planning.initialize_planning_state(context, deepcopy(arguments)))
    artifacts_before = action_state_hash(context.artifacts)
    assert initial["source_semantics"] == nodes["source_semantics"]["output"]
    assert initial["planning_inputs"] == arguments["planning_inputs"]
    assert [record["output"] for record in initial["planning_records"]] == [
        record["output"] for record in arguments["planning_records"]
    ]
    assert initial["source_version"] == initial["draft_version"] == initial["plan_version"] == 0
    assert context.invoke_agent is None
    assert planning.initialize_planning_state(context, deepcopy(arguments)) == initial
    rejected(lambda: planning.finish_planning(context, {}))
    assert context.action_state == initial
    rejected(lambda: planning.validate_planning_resolution(context, {
        "input_payload": deepcopy(arguments), "output": deepcopy(nodes["routed_plan"]["output"]),
    }))

    reads = []
    evidence_items = arguments["evidence_catalog"]["items"]
    # 目录及全部真实证据都通过分页接口还原，包含原文和真实图片块引用。
    for evidence_id in [None, *[item["evidence_id"] for item in evidence_items]]:
        offset, parts, page_count = 0, [], 0
        while True:
            result = planning.read_planning_evidence(context, {
                "evidence_id": evidence_id, "offset": offset, "max_chars": 777,
            })
            assert result["offset"] == offset and 0 < len(result["content"]) <= 777
            assert result["source_ref"]["content_hash"] == arguments["evidence_source"]["content_hash"]
            parts.append(result["content"])
            page_count += 1
            if result["next_offset"] is None:
                break
            assert result["next_offset"] > offset
            offset = result["next_offset"]
        text_value = "".join(parts)
        assert result["resource_fully_read"] is True and len(text_value) == result["total_chars"]
        assert hashlib.sha256(text_value.encode("utf-8")).hexdigest() == result["content_sha256"]
        value = json.loads(text_value)
        if evidence_id is None:
            assert [item["evidence_id"] for item in value] == [item["evidence_id"] for item in evidence_items]
        else:
            assert value == next(item for item in evidence_items if item["evidence_id"] == evidence_id)
        version_before = (context.action_state["source_version"], context.action_state["draft_version"], context.action_state["plan_version"])
        repeated = planning.read_planning_evidence(context, {"evidence_id": evidence_id, "offset": 0, "max_chars": 777})
        assert repeated["new_evidence_read"] is False
        assert version_before == (context.action_state["source_version"], context.action_state["draft_version"], context.action_state["plan_version"])
        reads.append({"evidence_id": evidence_id, "page_count": page_count,
                      "total_chars": result["total_chars"], "sha256": result["content_sha256"]})

    read_state = deepcopy(context.action_state)
    for bad_args in (
        {"evidence_id": arguments["evidence_source"]["content_hash"], "offset": 0, "max_chars": 100},
        {"evidence_id": None, "offset": -1, "max_chars": 100},
        {"evidence_id": None, "offset": 0, "max_chars": 4001},
    ):
        rejected(lambda bad_args=bad_args: planning.read_planning_evidence(context, bad_args))
        assert context.action_state == read_state

    corrupted_inputs = {}
    changed_source = deepcopy(arguments)
    changed_source["evidence_source"]["content_hash"] = "0" * 64
    corrupted_inputs["run_source_hash_changed"] = changed_source
    changed_catalog = deepcopy(arguments)
    changed_catalog["evidence_catalog"]["items"][0]["asset_source_sha256"] = "0" * 64
    corrupted_inputs["catalog_source_hash_changed"] = changed_catalog
    changed_prepared = deepcopy(arguments)
    item = changed_prepared["source_text_inputs"][0]
    page = item["pages"][0] if item["source_kind"] == "document_batch" else item
    page["asset_source_sha256"] = "0" * 64
    corrupted_inputs["prepared_source_hash_changed"] = changed_prepared
    changed_record = deepcopy(arguments)
    changed_record["source_text_records"][0]["input_hash"] = "0" * 64
    corrupted_inputs["source_record_input_hash_changed"] = changed_record
    source_errors = []
    for name, damaged in corrupted_inputs.items():
        error = rejected(lambda damaged=damaged: planning.initialize_planning_state(context_for(db, run), damaged), ValueError)
        assert not isinstance(error, OutputValidationError)
        source_errors.append({"case": name, "error_type": type(error).__name__, "message": str(error)})
    # 仅损坏真实资产副本中的身份或坐标，验证首次初始化也不能承接伪造的上下文资产。
    for artifact_key in ("source_semantics_source_pages", "source_semantics_coordinate_maps"):
        damaged_context = context_for(db, run)
        stored = damaged_context.artifacts[artifact_key]
        assert stored, "本项边界需要真实保存的压缩原页及坐标映射"
        first = stored[next(iter(stored))]
        if artifact_key == "source_semantics_source_pages":
            assert isinstance(first["region"]["x"], (int, float))
            first["region"]["x"] += 0.01
        else:
            assert first and isinstance(first[0]["local_span"]["start"], int)
            first[0]["local_span"]["start"] += 1
        error = rejected(lambda damaged_context=damaged_context: planning.initialize_planning_state(
            damaged_context, deepcopy(arguments)), ValueError)
        assert not isinstance(error, OutputValidationError)
        assert "与运行持久来源不一致" in str(error)
        source_errors.append({"case": f"initial_{artifact_key}_changed", "error_type": type(error).__name__,
                              "message": str(error)})

    # 自然历史候选已通过原严格规划校验，直接应用其规范输出，不编写替代模型。
    plan_action = planning.prepare_business_plan_rebuild(context, {})
    assert plan_action["items"] == [nodes["plan"]["input"]]
    plan_result = planning.apply_business_plan_rebuild(context, plan_action, [deepcopy(nodes["plan"]["output"])])
    assert plan_result["changed"] is True and context.action_state["plan"] == nodes["plan"]["output"]
    assert context.action_state["plan_version"] == 1
    route_action = planning.prepare_scope_routing(context, {})
    route_outputs = regroup_historical_routes(route_action, nodes["plan_routes"]["input"]["items"],
                                       nodes["plan_routes"]["output"]["items"])
    planning.apply_scope_routing(context, route_action, route_outputs)
    gap_action = planning.prepare_route_gap_review(context, {})
    gap_outputs = outputs_for_action(gap_action, nodes["plan_route_repairs"]["input"]["items"],
                                     nodes["plan_route_repairs"]["output"]["items"])
    gap_result = planning.apply_route_gap_review(context, gap_action, gap_outputs)
    unsupported = [decision for value in gap_outputs for decision in value["decisions"]
                   if decision["disposition"] == "unsupported"]
    assert unsupported and gap_result["unsupported_designs"] and gap_result["can_finish"] is False
    unsupported_state = deepcopy(context.action_state)
    error = rejected(lambda: planning.finish_planning(context, {}))
    assert error.details["unsupported_designs"] and context.action_state == unsupported_state
    rejected(lambda: planning.prepare_route_gap_review(context, {}))
    rejected(lambda: planning.validate_planning_resolution(context, {
        "input_payload": deepcopy(arguments), "output": {"completed": True},
    }))
    # 原来已经剪掉无依据项的 routed_plan 不能被新计划验收边界接纳。
    pruned_action = planning.prepare_business_plan_rebuild(context, {})
    rejected(lambda: planning.apply_business_plan_rebuild(context, pruned_action,
                                                         [deepcopy(nodes["routed_plan"]["output"])]))
    assert context.action_state == unsupported_state

    draft_action = planning.prepare_draft_revision(context, {
        "item_index": 0, "question": "依据已保存的真实来源核对当前草案的事实承接范围",
    })
    draft_result = planning.apply_draft_revision(context, draft_action,
                                                 [deepcopy(arguments["planning_records"][0]["output"])])
    assert draft_result["changed"] is False and context.action_state == unsupported_state

    # 两个真实运行使用相同来源分析输入；交叉回放真实不同输出以验证失效传播。
    alternatives = []
    for other_id, (_other_run, _other_nodes, other) in fixtures.items():
        if other_id == run.id or other["evidence_source"] != arguments["evidence_source"]:
            continue
        for kind in ("text", "vision"):
            for index, original in enumerate(arguments[f"source_{kind}_inputs"]):
                for other_index, other_input in enumerate(other[f"source_{kind}_inputs"]):
                    candidate = other[f"source_{kind}_records"][other_index]["output"]
                    if original == other_input and candidate != arguments[f"source_{kind}_records"][index]["output"]:
                        alternatives.append((other_id, kind, index, other_index, candidate))
    assert alternatives, "需要同一真实来源输入的另一份实际模型输出"
    other_id, kind, index, other_index, candidate = alternatives[-1]
    source_action = planning.prepare_source_reinspection(context, {
        "source_kind": kind, "item_index": index, "question": "核对这项真实来源的事实解释与遗漏，保留原始来源约束",
    })
    same_result = planning.apply_source_reinspection(context, source_action,
                                                      [deepcopy(arguments[f"source_{kind}_records"][index]["output"])])
    assert same_result["changed"] is False and context.action_state == unsupported_state
    late_plan_action = planning.prepare_business_plan_rebuild(context, {})
    source_action = planning.prepare_source_reinspection(context, {
        "source_kind": kind, "item_index": index, "question": "核对这项真实来源的事实解释与遗漏，保留原始来源约束",
    })
    changed = planning.apply_source_reinspection(context, source_action, [deepcopy(candidate)])
    assert changed["changed"] is True and context.action_state["source_version"] == 1
    assert context.action_state["source_semantics"] != initial["source_semantics"]
    assert context.action_state["planning_inputs"] != initial["planning_inputs"]
    assert context.action_state["plan"] is None
    assert context.action_state["route_inputs"] == context.action_state["route_records"] == []
    assert context.action_state["gap_inputs"] == context.action_state["gap_records"] == []
    previous_by_hash = {record["input_hash"]: record for record in initial["planning_records"]}
    reused = 0
    for current, record in zip(context.action_state["planning_inputs"], context.action_state["planning_records"], strict=True):
        previous = previous_by_hash.get(action_state_hash(current))
        assert (record is None) == (previous is None)
        if record is not None:
            reused += 1
            assert record["output"] == previous["output"]
    changed_state = deepcopy(context.action_state)
    rejected(lambda: planning.apply_business_plan_rebuild(context, late_plan_action,
                                                         [deepcopy(nodes["plan"]["output"])]), ValueError)
    rejected(lambda: planning.finish_planning(context, {}))
    assert context.action_state == changed_state
    assert action_state_hash(context.artifacts) == artifacts_before

    restored = context_for(db, run)
    restored.action_state = json.loads(json.dumps(changed_state, ensure_ascii=False))
    assert planning.initialize_planning_state(restored, deepcopy(arguments)) == changed_state
    assert planning.observe_planning_state(restored, {}) == planning.observe_planning_state(context, {})
    damaged_state = deepcopy(changed_state)
    damaged_state["domain_sha256"] = "0" * 64
    corrupted = context_for(db, run)
    corrupted.action_state = damaged_state
    rejected(lambda: planning.initialize_planning_state(corrupted, deepcopy(arguments)), ValueError)
    return {
        "initial_state_from_real_sources": True, "initial_planning_batches": len(initial["planning_inputs"]),
        "evidence_pagination": reads, "repeated_read_does_not_advance_versions": True,
        "source_identity_hard_rejections": source_errors, "historical_plan_accepted": True,
        "historical_route_count": len(route_outputs), "historical_gap_count": len(gap_outputs),
        "historical_unsupported_decisions": unsupported, "unsupported_finish_rejected": True,
        "old_pruned_plan_rejected": True, "same_draft_and_source_no_false_change": True,
        "actual_changed_source_replay": {"other_run_id": other_id, "source_kind": kind,
            "item_index": index, "other_item_index": other_index,
            "prepared_input_sha256": action_state_hash(arguments[f"source_{kind}_inputs"][index]),
            "candidate_sha256": action_state_hash(candidate), "source_version": 1,
            "changed_state_sha256": action_state_hash(changed_state), "reused_draft_count": reused,
            "pending_draft_count": sum(record is None for record in changed_state["planning_records"]),
            "plan_and_routes_invalidated": True, "late_plan_rejected": True},
        "domain_state_json_round_trip": True, "corrupt_domain_hash_rejected": True,
        "source_artifacts_unchanged": True,
        "limits": ["无现成同输入真实草案变化样本，因此未宣称草案重写后自然收敛通过",
                   "两轮均存在自然 unsupported，严格完成拒绝是预期结果；未伪造完成分支"],
    }


def verify_checkpoint_round_trip(online, run, nodes: dict[str, dict], arguments: dict, path: Path) -> dict:
    """在隔离文件库提交真实取证行动，关闭会话和引擎后恢复同一领域状态。"""
    from modules.agent_platform.test_generation_planning_agent import (
        initialize_planning_state, observe_planning_state, read_planning_evidence,
    )

    if path.exists():
        raise FileExistsError("隔离规划检查点已存在，禁止覆盖")
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    token, call_id = uuid4().hex, uuid4().hex
    node_id = nodes["plan"]["node_run_id"]
    evidence_args = {"evidence_id": arguments["evidence_catalog"]["items"][0]["evidence_id"],
                     "offset": 0, "max_chars": 400}
    try:
        for model in (AgentRun, AgentNodeRun, AgentRunEvent):
            model.__table__.create(engine)
        with Session(engine, expire_on_commit=False) as isolated:
            copied_run = copy_record(run)
            copied_run.status, copied_run.finished_at = "running", None
            copied_run.claim_token = token
            copied_run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
            copied_node = copy_record(online.get(AgentNodeRun, node_id))
            copied_node.status, copied_node.node_type = "running", "agent_network"
            copied_node.input_payload = deepcopy(arguments)
            copied_node.output_payload, copied_node.sdk_state = {}, {}
            isolated.add_all([copied_run, copied_node])
            isolated.commit()
            repo = AgentRunRepository(isolated)
            context = context_for(isolated, copied_run)
            initial = initialize_planning_state(context, deepcopy(arguments))
            journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node_id, expected_claim_token=token)
            saved = journal.init(state=initial, input_hash=action_state_hash(arguments))
            assert saved["state"] == initial
            journal.begin(tool_call_id=call_id, tool_key="read_planning_evidence", args=evidence_args,
                          prehash=action_state_hash(initial))
            result = read_planning_evidence(context, deepcopy(evidence_args))
            receipt = journal.complete(tool_call_id=call_id, nextstate=deepcopy(context.action_state), result=result)
            assert receipt.status == "completed"
            expected_state = deepcopy(context.action_state)
            expected_observation = observe_planning_state(context, {})
            events_before = isolated.query(AgentRunEvent).filter(AgentRunEvent.run_id == run.id).count()
    finally:
        engine.dispose()

    reopened = create_engine(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    try:
        with Session(reopened, expire_on_commit=False) as isolated:
            repo = AgentRunRepository(isolated)
            copied_run = isolated.get(AgentRun, run.id)
            journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node_id, expected_claim_token=token)
            restored = journal.load()
            assert restored["state"] == expected_state
            context = context_for(isolated, copied_run)
            context.action_state = deepcopy(restored["state"])
            initialize_planning_state(context, repo.read_node_input(isolated.get(AgentNodeRun, node_id)))
            assert observe_planning_state(context, {}) == expected_observation
            replay = journal.begin(tool_call_id=call_id, tool_key="read_planning_evidence", args=evidence_args,
                                   prehash=action_state_hash(expected_state))
            assert replay.replayed and replay.status == "completed" and replay.result == result
            assert journal.load()["state"] == expected_state
            assert isolated.query(AgentRunEvent).filter(AgentRunEvent.run_id == run.id).count() == events_before
    finally:
        reopened.dispose()
    return {"sqlite_path": str(path), "run_id": run.id, "source_node_run_id": node_id,
            "state_sha256": action_state_hash(expected_state), "source_sha256": arguments["evidence_source"]["content_hash"],
            "closed_and_reopened_engine": True, "completed_evidence_receipt_replayed": True,
            "replay_added_events": 0, "model_requests": 0,
            "boundary": "隔离库仅改变执行元数据和节点输入组合；未执行模型、在线强杀或SDK会话恢复。"}


def verify_failed_diagnostic_observation(db, fixtures: dict, checkpoint_path: Path) -> dict[str, Any]:
    """只读重放自然失败的真实协调状态，验证缺口定位和上下文投影。"""
    from modules.agent_platform import test_generation_planning_agent as planning

    with sqlite3.connect(checkpoint_path.resolve().as_uri() + "?mode=ro", uri=True) as isolated:
        run_id, payload = isolated.execute("SELECT run_id, sdk_state FROM agent_node_runs").fetchone()
    checkpoint = json.loads(payload)["action_checkpoint"]
    run, _nodes, arguments = fixtures[run_id]
    context = context_for(db, run)
    context.action_input = deepcopy(arguments)
    context.action_state = deepcopy(checkpoint["state"])
    before = action_state_hash(context.action_state)
    observation = planning.observe_planning_state(context)
    assert observation["unrouted_modules"]
    for module in observation["unrouted_modules"]:
        assert module["test_design_items"] and module["planning_batch_indexes"] and module["scope_ids"]
        assert all(design["planning_batch_indexes"] for design in module["test_design_items"])
    assert all("candidate_facts" not in item for item in observation["route_gaps"])
    for original, compact in zip(context.action_state["gap_inputs"], observation["route_gaps"], strict=True):
        facts = {fact["fact_id"]: fact for fact in original["candidate_facts"]}
        for fact in compact["supporting_facts"]:
            assert fact["assertion"] == facts[fact["fact_id"]]["assertion"]
    assert action_state_hash(context.action_state) == before
    rejected(lambda: planning.finish_planning(context, {}))
    rebuild = planning.prepare_business_plan_rebuild(context, {})
    assert rebuild["items"][0]["_planning_feedback"]["unrouted_modules"] == observation["unrouted_modules"]
    old_feedback = [item for item in checkpoint["receipts"].values()
                    if item["tool_key"] == "review_planning_route_gaps" and item["status"] == "completed"][-1]
    previous = json.loads(old_feedback["result"]["output"])
    encoded_bytes = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert encoded_bytes(observation["route_gaps"]) < encoded_bytes(previous["route_gaps"])
    return {"checkpoint_path": str(checkpoint_path), "run_id": run_id, "state_unchanged": True,
            "unrouted_modules": observation["unrouted_modules"],
            "previous_feedback_bytes": encoded_bytes(previous["route_gaps"]),
            "current_feedback_bytes": encoded_bytes(observation["route_gaps"]),
            "full_specialist_inputs_unchanged": True, "premature_finish_rejected": True,
            "rebuild_received_actionable_modules": True, "model_requests": 0}


def verify(run_ids: list[int], output_path: Path, diagnostic_checkpoint: Path | None = None) -> dict[str, Any]:
    if len(run_ids) < 2 or len(set(run_ids)) != len(run_ids):
        raise ValueError("验证至少需要两个不同的真实运行，供同输入历史输出交叉回放")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        fixtures = {run_id: load_planning_fixture(db, run_id) for run_id in run_ids}
        before = {
            run_id: {
                "run": action_state_hash([run.input_payload, run.run_context, run.output_payload]),
                "nodes": {key: action_state_hash(node) for key, node in nodes.items()},
            }
            for run_id, (run, nodes, _arguments) in fixtures.items()
        }
        results = []
        for run_id, (run, nodes, arguments) in fixtures.items():
            contracts = verify_historical_contracts(context_for(db, run), nodes)
            state_checks = verify_planning_state(db, run, nodes, arguments, fixtures)
            results.append({
                "run_id": run_id, "historical_run_status": run.status,
                "source_sha256": arguments["evidence_source"]["content_hash"],
                "input_sha256": action_state_hash(arguments),
                "node_snapshots": {key: {"node_run_id": value["node_run_id"],
                                          "snapshot_sha256": before[run_id]["nodes"][key]}
                                   for key, value in nodes.items()},
                "historical_contracts": contracts, "planning_state": state_checks,
            })
        first_run, first_nodes, first_arguments = fixtures[run_ids[0]]
        checkpoint = verify_checkpoint_round_trip(db, first_run, first_nodes, first_arguments,
                                                  output_path.with_suffix(".sqlite"))
        diagnostic_observation = (verify_failed_diagnostic_observation(db, fixtures, diagnostic_checkpoint)
                                  if diagnostic_checkpoint else None)
        for run_id, (run, nodes, _arguments) in fixtures.items():
            db.refresh(run)
            assert action_state_hash([run.input_payload, run.run_context, run.output_payload]) == before[run_id]["run"]
            for key in nodes:
                node = repo.latest_node_run(run_id=run_id, node_key=key)
                db.refresh(node)
                assert action_state_hash(snapshot(node, repo)) == before[run_id]["nodes"][key]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    return {
        "status": "passed", "observed_at": datetime.now(timezone.utc).isoformat(),
        "real_data_only": True, "mysql_transaction": "READ ONLY", "runs": results,
        "checkpoint": checkpoint,
        "failed_diagnostic_observation": diagnostic_observation,
        "source_and_historical_nodes_unchanged": True, "model_requests": 0,
        "online_database_writes": 0,
        "verification_boundary": "真实历史输出的纯领域回放；不代表模型自主选择、在线工作流或断电恢复通过。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-ids", type=int, nargs="+", default=[31, 30])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-checkpoint", type=Path)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError("验证报告已存在，禁止覆盖历史证据")
    report = verify(options.run_ids, options.output, options.diagnostic_checkpoint)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "run_ids": options.run_ids,
                      "report": str(options.output), "model_requests": 0,
                      "online_database_writes": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
