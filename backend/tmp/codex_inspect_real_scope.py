from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai.ai_client_impl import get_client_for_user
from core.cache_layer.cache import cache_service
from core.db.database import SessionLocal
from modules.test_generation_components.control import current_requirement_blueprint
from modules.test_generation_components.control import requirement_fact_ledger_compiler
from modules.test_generation_components.control import requirement_graph_partition_compiler
from modules.test_generation_components.control.requirement_graph_partition_contract import (
    RequirementGraphPartitionContractError,
    partition_requirement_graph_facts,
)
from tmp.codex_run_real_generation_file import _parse_file


def main() -> None:
    db = SessionLocal()
    captured: dict[str, object] = {}
    original = current_requirement_blueprint.compile_requirement_graph_stage
    original_fact_normalizer = (
        requirement_fact_ledger_compiler.normalize_requirement_fact_model_response
    )
    original_local_validator = (
        requirement_graph_partition_compiler.validate_requirement_graph_partition_response
    )
    original_relation_validator = (
        requirement_graph_partition_compiler.validate_requirement_graph_relation_response
    )
    original_local_edge_validator = (
        requirement_graph_partition_compiler.validate_requirement_graph_local_edge_response
    )
    original_workflow_validator = (
        requirement_graph_partition_compiler.validate_requirement_graph_workflow_response
    )

    def capture_scope(**kwargs: object) -> SimpleNamespace:
        captured["ledger"] = kwargs["normalized_scope_ledger"]
        return SimpleNamespace(
            success=False,
            status="scope_captured",
            diagnostics={"semantic_compile_status": "scope_captured"},
        )

    def capture_fact_candidate(*args: object, **kwargs: object) -> dict[str, object]:
        normalized = original_fact_normalizer(*args, **kwargs)
        if normalized.get("valid") is not True:
            errors = [
                dict(item)
                for item in (
                    normalized.get("errors")
                    or (normalized.get("diagnostics") or {}).get("errors")
                    or []
                )
                if isinstance(item, dict)
            ]
            raw_candidate = args[0] if args and isinstance(args[0], dict) else {}
            captured.setdefault("fact_candidate_failures", []).append(
                {
                    "errors": errors,
                    "source_evidence_records": [
                        dict(item)
                        for item in (raw_candidate.get("source_evidence_records") or [])
                        if isinstance(item, dict)
                    ],
                    "target_evidence_refs": list(kwargs.get("target_evidence_refs") or []),
                }
            )
        return normalized

    def capture_graph(**kwargs: object) -> object:
        call_kwargs = dict(kwargs)
        original_evaluator = call_kwargs["candidate_evaluator"]

        def capture_candidate(candidate: dict[str, object]) -> dict[str, object]:
            captured["assembled_candidate"] = candidate
            evaluation = original_evaluator(candidate)
            captured["candidate_evaluation"] = evaluation
            return evaluation

        call_kwargs["candidate_evaluator"] = capture_candidate
        result = original(**call_kwargs)
        captured["graph_result"] = result
        return result

    def capture_contract_error(validator: object, phase: str) -> object:
        def wrapped(*args: object, **kwargs: object) -> dict[str, object]:
            try:
                return validator(*args, **kwargs)
            except RequirementGraphPartitionContractError as exc:
                candidate = args[0] if args and isinstance(args[0], dict) else {}
                graph = kwargs.get("graph") if isinstance(kwargs.get("graph"), dict) else {}
                primary_flow = dict(candidate.get("primary_flow") or {})
                selected_node_ids = {
                    str(item) for item in primary_flow.get("node_ids") or []
                }
                selected_edge_ids = {
                    str(item) for item in primary_flow.get("edge_ids") or []
                }
                captured.setdefault("partition_errors", []).append(
                    {
                        "phase": phase,
                        "code": exc.code,
                        "path": exc.path,
                        "details": exc.details,
                        "candidate_nodes": [
                            {
                                "node_id": item.get("node_id"),
                                "kind": item.get("kind"),
                                "fact_ids": item.get("fact_ids") or [],
                                "boundary_status": item.get("boundary_status"),
                            }
                            for item in candidate.get("nodes") or []
                            if isinstance(item, dict)
                        ][:20],
                        "candidate_edges": [
                            dict(item)
                            for item in candidate.get("edges") or []
                            if isinstance(item, dict)
                        ][:3],
                        "candidate_primary_flow": primary_flow,
                        "selected_graph_nodes": [
                            dict(item)
                            for item in graph.get("nodes") or []
                            if isinstance(item, dict)
                            and str(item.get("node_id") or "")
                            in selected_node_ids
                        ],
                        "selected_graph_edges": [
                            dict(item)
                            for item in graph.get("edges") or []
                            if isinstance(item, dict)
                            and (
                                str(item.get("edge_id") or "")
                                in selected_edge_ids
                                or (
                                    str(item.get("source_node_id") or "")
                                    in selected_node_ids
                                    and str(item.get("target_node_id") or "")
                                    in selected_node_ids
                                )
                            )
                        ],
                        "candidate_workflow_count": len(
                            candidate.get("workflow_blueprints") or []
                        ),
                    }
                )
                raise

        return wrapped

    try:
        # 诊断只读取已持久化的真实阶段结果，避免 Redis 瞬时超时重复拖慢读取。
        cache_service.redis_client = None
        requirement_fact_ledger_compiler.normalize_requirement_fact_model_response = (
            capture_fact_candidate
        )
        requirement, _diagnostics = asyncio.run(_parse_file(db))
        inspect_graph = os.getenv("CODEX_INSPECT_GRAPH", "").strip() == "1"
        if inspect_graph:
            requirement_graph_partition_compiler.validate_requirement_graph_partition_response = capture_contract_error(
                original_local_validator,
                "local",
            )
            requirement_graph_partition_compiler.validate_requirement_graph_relation_response = capture_contract_error(
                original_relation_validator,
                "relation",
            )
            requirement_graph_partition_compiler.validate_requirement_graph_local_edge_response = capture_contract_error(
                original_local_edge_validator,
                "local_edge",
            )
            requirement_graph_partition_compiler.validate_requirement_graph_workflow_response = capture_contract_error(
                original_workflow_validator,
                "workflow",
            )
        current_requirement_blueprint.compile_requirement_graph_stage = (
            capture_graph if inspect_graph else capture_scope
        )
        _blueprints, diagnostics = (
            current_requirement_blueprint.extract_current_requirement_blueprints(
            client=get_client_for_user(1, db),
            requirement_text=requirement,
            db=db,
            project_id=2,
            user_id=1,
        )
        )
        if inspect_graph:
            graph_result = captured["graph_result"]
            failed_phase = getattr(
                graph_result,
                "diagnostics",
                {},
            ).get("partition_compile_failed_phase")
            partition_errors = [
                dict(item)
                for item in captured.get("partition_errors", [])
                if isinstance(item, dict)
                and (
                    not failed_phase
                    or str(item.get("phase") or "") == str(failed_phase)
                )
            ]
            evaluation = dict(captured.get("candidate_evaluation") or {})
            normalization = dict(
                evaluation.get("normalization_diagnostics") or {}
            )
            errors = [
                dict(item)
                for item in normalization.get("semantic_graph_rejections") or []
                if isinstance(item, dict)
            ]
            samples: dict[str, list[dict[str, object]]] = defaultdict(list)
            for item in errors:
                code = str(item.get("code") or item.get("reason") or "unknown")
                if len(samples[code]) < 8:
                    samples[code].append(item)
            rejected_node_ids = {
                str(item.get("id") or "")
                for item in errors
                if str(item.get("code") or item.get("reason") or "")
                == "orphan_node"
                and str(item.get("id") or "")
            }
            assembled_candidate = dict(
                captured.get("assembled_candidate") or {}
            )
            semantic_graph = dict(
                assembled_candidate.get("semantic_graph") or {}
            )
            failed_attempt_summaries = []
            for item in getattr(
                graph_result,
                "diagnostics",
                {},
            ).get("semantic_compile_attempts", []):
                if not isinstance(item, dict) or item.get("status") == "validated":
                    continue
                envelope = dict(item.get("model_envelope") or {})
                envelope_attempts = [
                    dict(attempt)
                    for attempt in envelope.get("attempts") or []
                    if isinstance(attempt, dict)
                ]
                failed_attempt_summaries.append(
                    {
                        "phase": item.get("phase"),
                        "shard_id": item.get("shard_id"),
                        "attempt": item.get("attempt"),
                        "status": item.get("status"),
                        "error_code": item.get("error_code"),
                        "error_type": item.get("error_type"),
                        "error_details": item.get("error_details"),
                        "input_chars": item.get("input_chars"),
                        "raw_chars": item.get("raw_chars"),
                        "duration_ms": (
                            envelope_attempts[0].get("duration_ms")
                            if envelope_attempts
                            else None
                        ),
                    }
                )
            print(
                json.dumps(
                    {
                        "status": getattr(graph_result, "status", ""),
                        "diagnostic_status": diagnostics.get(
                            "semantic_compile_status"
                        ),
                        "failed_phase": failed_phase,
                        "failed_shard_id": getattr(
                            graph_result,
                            "diagnostics",
                            {},
                        ).get("partition_compile_failed_shard_id"),
                        "failed_attempts": failed_attempt_summaries[-12:],
                        "partition_errors": [
                            {
                                "phase": item.get("phase"),
                                "code": item.get("code"),
                                "path": item.get("path"),
                                "details": item.get("details"),
                            }
                            for item in partition_errors[-12:]
                        ],
                        "error_count": len(errors),
                        "error_counts": dict(
                            Counter(
                                str(
                                    item.get("code")
                                    or item.get("reason")
                                    or "unknown"
                                )
                                for item in errors
                            )
                        ),
                        "samples": samples,
                        "rejected_graph_nodes": [
                            dict(item)
                            for item in semantic_graph.get("nodes") or []
                            if isinstance(item, dict)
                            and str(item.get("node_id") or "")
                            in rejected_node_ids
                        ],
                        "rejected_incident_edges": [
                            dict(item)
                            for item in semantic_graph.get("edges") or []
                            if isinstance(item, dict)
                            and (
                                str(item.get("source_node_id") or "")
                                in rejected_node_ids
                                or str(item.get("target_node_id") or "")
                                in rejected_node_ids
                                or rejected_node_ids.intersection(
                                    str(node_id)
                                    for node_id in item.get(
                                        "transferred_entity_node_ids"
                                    )
                                    or []
                                )
                            )
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if "ledger" not in captured:
            print(
                json.dumps(
                    {
                        "status": diagnostics.get("semantic_compile_status"),
                        "failed_stage": diagnostics.get(
                            "semantic_pipeline_failed_stage"
                        ),
                        "scope_status": diagnostics.get(
                            "scope_ledger_compile_status"
                        ),
                        "failed_phase": diagnostics.get(
                            "scope_ledger_compile_failed_phase"
                        ),
                        "failed_shard_id": diagnostics.get(
                            "scope_ledger_compile_failed_shard_id"
                        ),
                        "attempts": [
                            item
                            for item in diagnostics.get(
                                "scope_ledger_compile_attempts",
                                [],
                            )
                            if isinstance(item, dict)
                            and item.get("status") != "validated"
                        ][-8:],
                        "fact_candidate_failures": [
                            dict(item)
                            for item in captured.get("fact_candidate_failures", [])
                            if isinstance(item, dict)
                        ][-3:],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        ledger = dict(captured["ledger"])
        inspect_partition_id = os.getenv(
            "CODEX_INSPECT_PARTITION_ID",
            "",
        ).strip()
        if inspect_partition_id:
            partition = next(
                item
                for item in partition_requirement_graph_facts(ledger)
                if item.shard_id == inspect_partition_id
            )
            facts_by_id = {
                str(item.get("fact_id") or ""): item
                for item in ledger.get("evidence_facts") or []
                if isinstance(item, dict)
            }
            bindings_by_id = {
                str(item.get("fact_id") or ""): item
                for item in ledger.get("fact_bindings") or []
                if isinstance(item, dict)
            }
            print(
                json.dumps(
                    {
                        "shard_id": partition.shard_id,
                        "owner_scope_ids": partition.owner_scope_ids,
                        "facts": [
                            {
                                **dict(facts_by_id[fact_id]),
                                "binding": bindings_by_id.get(fact_id),
                            }
                            for fact_id in partition.fact_ids
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        facts = {
            str(item.get("fact_id") or ""): str(item.get("statement") or "")
            for item in ledger.get("evidence_facts") or []
            if isinstance(item, dict)
        }
        inspect_fact_id = os.getenv("CODEX_INSPECT_FACT_ID", "").strip()
        inspect_fact_ids = {
            item.strip()
            for item in os.getenv("CODEX_INSPECT_FACT_IDS", "").split(",")
            if item.strip()
        }
        if inspect_fact_ids:
            print(
                json.dumps(
                    [
                        {
                            "fact": fact,
                            "binding": next(
                                (
                                    item
                                    for item in ledger.get("fact_bindings") or []
                                    if isinstance(item, dict)
                                    and str(item.get("fact_id") or "")
                                    == str(fact.get("fact_id") or "")
                                ),
                                {},
                            ),
                        }
                        for fact in ledger.get("evidence_facts") or []
                        if isinstance(fact, dict)
                        and str(fact.get("fact_id") or "") in inspect_fact_ids
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if inspect_fact_id:
            fact = next(
                (
                    item
                    for item in ledger.get("evidence_facts") or []
                    if isinstance(item, dict)
                    and str(item.get("fact_id") or "") == inspect_fact_id
                ),
                {},
            )
            binding = next(
                (
                    item
                    for item in ledger.get("fact_bindings") or []
                    if isinstance(item, dict)
                    and str(item.get("fact_id") or "") == inspect_fact_id
                ),
                {},
            )
            print(
                json.dumps(
                    {"fact": fact, "binding": binding},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        rows = []
        for boundary in ledger.get("boundaries") or []:
            if not isinstance(boundary, dict):
                continue
            support = [
                {
                    "signal": item.get("signal"),
                    "fact_ids": item.get("fact_ids") or [],
                    "facts": [facts.get(str(fact_id), "") for fact_id in item.get("fact_ids") or []],
                }
                for item in boundary.get("support") or []
                if isinstance(item, dict)
            ]
            rows.append(
                {
                    "boundary_id": boundary.get("boundary_id"),
                    "label": boundary.get("label"),
                    "decision": boundary.get("decision"),
                    "parent_boundary_id": boundary.get("parent_boundary_id"),
                    "membership_relation_ids": boundary.get("membership_relation_ids") or [],
                    "membership_fact_ids": boundary.get("membership_fact_ids") or [],
                    "membership_facts": [
                        facts.get(str(fact_id), "")
                        for fact_id in boundary.get("membership_fact_ids") or []
                    ],
                    "support": support,
                }
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        current_requirement_blueprint.compile_requirement_graph_stage = original
        requirement_fact_ledger_compiler.normalize_requirement_fact_model_response = (
            original_fact_normalizer
        )
        requirement_graph_partition_compiler.validate_requirement_graph_partition_response = original_local_validator
        requirement_graph_partition_compiler.validate_requirement_graph_relation_response = original_relation_validator
        requirement_graph_partition_compiler.validate_requirement_graph_local_edge_response = original_local_edge_validator
        requirement_graph_partition_compiler.validate_requirement_graph_workflow_response = original_workflow_validator
        db.close()


if __name__ == "__main__":
    main()
