from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from starlette.datastructures import Headers

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai.ai_client_impl import get_client_for_user
from core.db.database import SessionLocal
from core.db.models import SystemConfig, TestGeneration
from core.settings.config import settings
from modules.testing.test_generation import test_generator
from routers.test_generation_routes.support import parse_requirement_for_generation


PROJECT_ID = int(os.getenv("CODEX_REAL_PROJECT_ID", "2"))
USER_ID = int(os.getenv("CODEX_REAL_USER_ID", "1"))
EXPECTED_COUNT = int(os.getenv("CODEX_REAL_EXPECTED_COUNT", "80"))
BATCH_SIZE = max(
    1,
    int(
        os.getenv(
            "CODEX_REAL_BATCH_SIZE",
            str(settings.TEST_GENERATION_BATCH_SIZE),
        )
    ),
)
OVERWRITE = os.getenv("CODEX_REAL_OVERWRITE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_SOURCE_FILE_VALUE = os.getenv("CODEX_REAL_SOURCE_FILE", "").strip()
SOURCE_FILE = Path(_SOURCE_FILE_VALUE).expanduser() if _SOURCE_FILE_VALUE else None


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _print(kind: str, payload: object) -> None:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    print(f"{_now()} {kind} {text}", flush=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_version() -> str:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT.parent,
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
        )
        return f"{revision}+dirty" if dirty else revision
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _case_count(raw: str) -> int:
    try:
        payload = json.loads(raw or "[]")
    except Exception:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("cases", "test_cases", "generated_result"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def _compact_diag(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "")
    common_keys = (
        "kind",
        "batch_index",
        "total_batches",
        "batch_target_count",
        "subshard_target_counts",
        "accepted_case_count",
        "shortfall_count",
        "repair_shard_count",
        "repair_attempt_count",
        "new_valid_cases_count",
        "duration_ms",
        "response_chars",
        "final_count",
        "candidate_total",
        "retained_total",
        "pass_count",
        "reject_count",
        "passed",
        "failure_code",
        "failure_reasons",
        "execution_reasons",
        "quality_score",
        "quality_score_grade",
        "request_id",
        "current_requirement_blueprint_error",
        "payload_omitted_due_to_size",
        "original_payload_size_bytes",
        "fallback_summary_only",
        "omitted_keys",
    )
    compact = {key: payload.get(key) for key in common_keys if key in payload}
    if kind == "prompt_context_intake":
        compact["requirement_understanding"] = payload.get("requirement_understanding")
        compact["source_lanes"] = payload.get("source_lanes")
        compact["risk_flags"] = payload.get("risk_flags")
        control = payload.get("control") if isinstance(payload.get("control"), dict) else {}
        compact["control"] = {
            key: control.get(key)
            for key in (
                "workflow_blueprint_count",
                "generation_execution_plan_step_count",
                "fact_profile_confirmed_count",
                "hard_flow_constraints_count",
            )
        }
    elif kind == "generation_quality_ledger":
        compact["quality_score_deduction_keys"] = [
            str(item.get("key") or "")
            for item in (payload.get("quality_score_deductions") or [])
            if isinstance(item, dict)
        ]
        manual = payload.get("manual_delivery") if isinstance(payload.get("manual_delivery"), dict) else {}
        compact["manual_delivery"] = {
            key: manual.get(key)
            for key in (
                "applied",
                "scoring_mode",
                "high_priority_ratio_shortfall",
                "display_ratio_excess",
            )
            if key in manual
        }
    elif kind == "feedback_control_state":
        source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
        compact["source_meta"] = {
            key: source_meta.get(key)
            for key in (
                "current_requirement_blueprint_status",
                "current_requirement_blueprint_count",
                "current_requirement_blueprint_step_count",
                "requirement_understanding_used",
                "requirement_understanding_visual_fact_count",
                "requirement_understanding_invalid_visual_block_count",
                "semantic_compile_status",
                "semantic_compile_mode",
                "semantic_compile_success",
                "semantic_compile_attempt_count",
                "semantic_compile_physical_call_count",
                "semantic_compile_provider_call_count",
                "semantic_compile_cache_hit_count",
                "semantic_compile_cache_miss_count",
                "semantic_compile_cache_bypass_count",
                "semantic_compile_candidate_attempt_count",
                "semantic_compile_candidate_attempt_limit",
                "semantic_compile_independent_recompile_limit",
                "semantic_compile_independent_recompile_used",
                "semantic_compile_independent_recompile_attempt",
                "semantic_compile_independent_recompile_trigger_codes",
                "semantic_compile_independent_recompile_outcome",
                "semantic_compile_transport_retry_count",
                "semantic_compile_transport_failure_count",
                "semantic_compile_retry_used",
                "semantic_compile_attempts",
                "partition_compile_status",
                "partition_compile_success",
                "partition_compile_failed_phase",
                "partition_compile_failed_shard_id",
                "partition_compile_fact_shard_count",
                "partition_compile_completed_fact_shard_count",
                "partition_compile_relation_fact_count",
                "partition_compile_relation_shard_count",
                "partition_compile_completed_relation_shard_count",
                "partition_compile_workflow_called",
                "partition_compile_node_count",
                "partition_compile_edge_count",
                "partition_compile_control_edge_count",
                "partition_compile_provider_call_count",
                "partition_compile_cache_hit_count",
                "partition_compile_cache_miss_count",
                "semantic_pipeline_failed_stage",
                "fact_ledger_compile_status",
                "fact_ledger_compile_success",
                "fact_ledger_compile_candidate_attempt_count",
                "fact_ledger_compile_physical_call_count",
                "fact_ledger_compile_provider_call_count",
                "fact_ledger_compile_cache_hit_count",
                "fact_ledger_compile_cache_miss_count",
                "fact_ledger_compile_cache_bypass_count",
                "fact_ledger_compile_chunk_count",
                "fact_ledger_compile_partition_group_count",
                "fact_ledger_compile_oversized_partition_group_count",
                "fact_ledger_compile_completed_chunk_count",
                "fact_ledger_compile_failed_chunk_index",
                "fact_ledger_compile_global_status",
                "fact_ledger_compile_global_error_codes",
                "fact_ledger_fact_count",
                "scope_ledger_compile_status",
                "scope_ledger_compile_success",
                "scope_ledger_compile_candidate_attempt_count",
                "scope_ledger_compile_physical_call_count",
                "scope_ledger_compile_provider_call_count",
                "scope_ledger_compile_cache_hit_count",
                "scope_ledger_compile_cache_miss_count",
                "scope_ledger_compile_cache_bypass_count",
                "scope_ledger_binding_shard_count",
                "scope_ledger_binding_oversized_fact_count",
                "scope_ledger_binding_completed_shard_count",
                "scope_ledger_binding_failed_shard_index",
                "workflow_declaration_status",
                "workflow_absence_declared",
                "raw_workflow_candidate_count",
                "normalized_workflow_count",
                "rejected_workflow_count",
                "verified_functional_module_count",
                "requirement_semantic_graph_fact_count",
                "requirement_semantic_graph_node_count",
                "requirement_semantic_graph_edge_count",
                "semantic_graph_rejections",
                "semantic_graph_diagnostics",
                "source_evidence_catalog",
                "source_evidence_catalog_coverage",
            )
            if key in source_meta
        }
    elif kind == "semantic_compilation_abort":
        compact.update(
            {
                key: payload.get(key)
                for key in (
                    "abort_code",
                    "message",
                    "semantic_compile_status",
                    "semantic_compile_mode",
                    "semantic_compile_attempt_count",
                    "semantic_compile_candidate_attempt_count",
                    "semantic_compile_candidate_attempt_limit",
                    "semantic_compile_independent_recompile_limit",
                    "semantic_compile_independent_recompile_used",
                    "semantic_compile_independent_recompile_attempt",
                    "semantic_compile_independent_recompile_trigger_codes",
                    "semantic_compile_independent_recompile_outcome",
                    "semantic_compile_transport_retry_count",
                    "semantic_compile_transport_failure_count",
                    "semantic_compile_retry_used",
                    "semantic_compile_attempts",
                    "partition_compile_status",
                    "partition_compile_success",
                    "partition_compile_failed_phase",
                    "partition_compile_failed_shard_id",
                    "partition_compile_fact_shard_count",
                    "partition_compile_completed_fact_shard_count",
                    "partition_compile_relation_fact_count",
                    "partition_compile_relation_shard_count",
                    "partition_compile_completed_relation_shard_count",
                    "partition_compile_workflow_called",
                    "partition_compile_node_count",
                    "partition_compile_edge_count",
                    "partition_compile_control_edge_count",
                    "semantic_compile_request_timeout_seconds",
                    "semantic_compile_timeout_count",
                    "semantic_compile_stop_reason",
                    "semantic_compile_final_gate_error_code",
                    "semantic_compile_final_gate_error_type",
                    "semantic_compile_final_gate_error_message",
                    "workflow_declaration_status",
                    "workflow_absence_declared",
                    "raw_workflow_candidate_count",
                    "normalized_workflow_count",
                    "rejected_workflow_count",
                    "workflow_rejection_reasons",
                    "typed_state_rejections",
                    "workflow_consistency_rejections",
                    "verified_functional_module_count",
                    "current_requirement_blueprint_error",
                    "semantic_pipeline_failed_stage",
                    "fact_ledger_compile_status",
                    "fact_ledger_compile_success",
                    "fact_ledger_compile_candidate_attempt_count",
                    "fact_ledger_compile_candidate_attempt_limit",
                    "fact_ledger_compile_physical_call_count",
                    "fact_ledger_compile_provider_call_count",
                    "fact_ledger_compile_cache_hit_count",
                    "fact_ledger_compile_cache_miss_count",
                    "fact_ledger_compile_cache_bypass_count",
                    "fact_ledger_compile_transport_retry_count",
                    "fact_ledger_compile_transport_failure_count",
                    "fact_ledger_compile_fresh_candidate_used",
                    "fact_ledger_compile_fresh_candidate_trigger_codes",
                    "fact_ledger_compile_last_parseable_candidate_attempt",
                    "fact_ledger_compile_last_parseable_candidate_status",
                    "fact_ledger_compile_last_parseable_candidate_error_codes",
                    "fact_ledger_compile_stop_reason",
                    "fact_ledger_compile_chunked",
                    "fact_ledger_compile_chunk_count",
                    "fact_ledger_compile_chunk_limit",
                    "fact_ledger_compile_chunk_budget_units",
                    "fact_ledger_compile_catalog_budget_units",
                    "fact_ledger_compile_partition_group_count",
                    "fact_ledger_compile_oversized_partition_group_count",
                    "fact_ledger_compile_completed_chunk_count",
                    "fact_ledger_compile_failed_chunk_index",
                    "fact_ledger_compile_chunk_summaries",
                    "fact_ledger_compile_global_status",
                    "fact_ledger_compile_global_error_codes",
                    "scope_ledger_compile_status",
                    "scope_ledger_compile_success",
                    "scope_ledger_compile_candidate_attempt_count",
                    "scope_ledger_compile_candidate_attempt_limit",
                    "scope_ledger_compile_physical_call_count",
                    "scope_ledger_compile_provider_call_count",
                    "scope_ledger_compile_cache_hit_count",
                    "scope_ledger_compile_cache_miss_count",
                    "scope_ledger_compile_cache_bypass_count",
                    "scope_ledger_compile_transport_retry_count",
                    "scope_ledger_compile_transport_failure_count",
                    "scope_ledger_compile_stop_reason",
                    "scope_ledger_compile_global_status",
                    "scope_ledger_compile_global_error_codes",
                    "scope_ledger_binding_shard_count",
                    "scope_ledger_binding_oversized_fact_count",
                    "scope_ledger_binding_completed_shard_count",
                    "scope_ledger_binding_failed_shard_index",
                    "requirement_semantic_graph_fact_count",
                    "requirement_semantic_graph_node_count",
                    "requirement_semantic_graph_edge_count",
                    "semantic_graph_rejections",
                    "semantic_graph_diagnostics",
                    "source_evidence_catalog",
                    "source_evidence_catalog_coverage",
                )
                if key in payload
            }
        )
    elif kind == "persistence_gate":
        execution_validation = (
            payload.get("execution_plan_validation")
            if isinstance(payload.get("execution_plan_validation"), dict)
            else {}
        )
        execution_metrics = (
            execution_validation.get("metrics")
            if isinstance(execution_validation.get("metrics"), dict)
            else {}
        )
        compact["details"] = {
            "execution_failure_reasons": execution_validation.get("failure_reasons") or [],
            "semantic_conflict_count": execution_metrics.get("semantic_conflict_count"),
            "state_conflict_count": execution_metrics.get("state_conflict_count"),
            "main_chain_stage_kinds": execution_metrics.get("main_chain_stage_kinds") or [],
            "semantic_conflicts": (execution_validation.get("semantic_conflicts") or [])[:3],
        }
    elif kind == "case_quality_gate":
        compact["metrics"] = payload.get("metrics")
    return compact or {"kind": kind, "request_id": payload.get("request_id")}


async def _parse_file(db) -> tuple[str, dict[str, Any]]:
    if SOURCE_FILE is None:
        raise RuntimeError("必须设置 CODEX_REAL_SOURCE_FILE 指向真实需求文件")
    content_type = "application/pdf" if SOURCE_FILE.suffix.lower() == ".pdf" else "application/octet-stream"
    with SOURCE_FILE.open("rb") as fh:
        upload = UploadFile(
            filename=SOURCE_FILE.name,
            file=fh,
            headers=Headers({"content-type": content_type}),
        )
        return await parse_requirement_for_generation(
            upload,
            "requirement",
            db=db,
            user_id=USER_ID,
            project_id=PROJECT_ID,
            source="codex_real_generation_file",
        )


def main() -> None:
    if SOURCE_FILE is None:
        raise RuntimeError("必须设置 CODEX_REAL_SOURCE_FILE 指向真实需求文件")
    if not SOURCE_FILE.is_file():
        raise FileNotFoundError(str(SOURCE_FILE))

    db = SessionLocal()
    request_id = ""
    before_max_id = None
    try:
        before_max_id = db.query(TestGeneration.id).order_by(TestGeneration.id.desc()).first()
        before_max_id = int(before_max_id[0]) if before_max_id else 0
        active_config = (
            db.query(SystemConfig)
            .filter(SystemConfig.user_id == USER_ID, SystemConfig.is_active == 1)
            .order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc())
            .first()
        )
        client = get_client_for_user(USER_ID, db)
        _print(
            "RUN_START",
            {
                "project_id": PROJECT_ID,
                "user_id": USER_ID,
                "source_file": str(SOURCE_FILE),
                "source_file_sha256": _file_sha256(SOURCE_FILE),
                "code_version": _code_version(),
                "before_max_generation_id": before_max_id,
                "expected_count": EXPECTED_COUNT,
                "batch_size": BATCH_SIZE,
                "overwrite": OVERWRITE,
                "active_config": {
                    "id": getattr(active_config, "id", None),
                    "provider": getattr(active_config, "provider", None),
                    "model": getattr(active_config, "model_name", None),
                    "turbo": getattr(active_config, "turbo_model_name", None),
                    "vision": getattr(active_config, "vl_model_name", None),
                    "base_url": getattr(active_config, "base_url", None),
                },
                "client": {
                    "provider_type": type(client.provider).__name__ if client.provider else None,
                    "generation_model": client.select_model("", "generation"),
                    "compression_model": client.select_model("", "compression"),
                    "ocr_model": client.select_model("", "ocr"),
                },
            },
        )

        parse_started = time.perf_counter()
        requirement, parse_diag = asyncio.run(_parse_file(db))
        understanding = parse_diag.get("requirement_understanding") if isinstance(parse_diag, dict) else {}
        blocks = parse_diag.get("blocks") if isinstance(parse_diag, dict) else []
        _print(
            "PARSE_DONE",
            {
                "duration_sec": round(time.perf_counter() - parse_started, 2),
                "requirement_len": len(requirement or ""),
                "block_count": len(blocks or []),
                "alignment_count": parse_diag.get("alignment_count"),
                "requirement_understanding": {
                    "visual_fact_count": (understanding or {}).get("visual_fact_count"),
                    "invalid_visual_block_count": (understanding or {}).get("invalid_visual_block_count"),
                    "visual_sources": [
                        item.get("source")
                        for item in ((understanding or {}).get("visual_facts") or [])[:6]
                        if isinstance(item, dict)
                    ],
                },
                "block_summaries": [
                    {
                        "role": item.get("role"),
                        "filename": item.get("filename"),
                        "strategy": item.get("parse_strategy"),
                        "chars": item.get("text_length"),
                        "ocr_status": item.get("ocr_status"),
                    }
                    for item in (blocks or [])[:8]
                    if isinstance(item, dict)
                ],
                "has_requirement_understanding_section": "[Requirement Understanding]" in requirement,
                "has_pdf_visual_attachment_section": "[Attachment:" in requirement and "pdf_visual" in requirement,
            },
        )

        started = time.perf_counter()
        stream_chars = 0
        last_status = ""
        generation_id_from_stream = None
        for chunk in test_generator.generate_test_cases_stream(
            requirement=requirement,
            project_id=PROJECT_ID,
            db=db,
            doc_type="requirement",
            compress=True,
            expected_count=EXPECTED_COUNT,
            batch_size=BATCH_SIZE,
            overwrite=OVERWRITE,
            append=False,
            user_id=USER_ID,
            current_biz_key="",
            only_current_biz=False,
            multi_pass=True,
            generation_mode="",
            enable_sample_pool_feedback=True,
        ):
            text = str(chunk or "")
            stream_chars += len(text)
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("@@STATUS@@:"):
                    status = line[len("@@STATUS@@:") :]
                    if status != last_status:
                        last_status = status
                        _print("STATUS", status)
                    match = re.search(r"generation_id=(\d+)", status)
                    if match:
                        generation_id_from_stream = int(match.group(1))
                elif line.startswith("GEN_DIAG:"):
                    try:
                        payload = json.loads(line[len("GEN_DIAG:") :])
                    except Exception:
                        payload = {"raw": line[:1000]}
                    if isinstance(payload, dict):
                        request_id = str(payload.get("request_id") or request_id or "")
                        if payload.get("kind") == "generation_persisted":
                            persisted_stream_id = payload.get("generation_id")
                            if isinstance(persisted_stream_id, int):
                                generation_id_from_stream = persisted_stream_id
                            elif str(persisted_stream_id or "").isdigit():
                                generation_id_from_stream = int(persisted_stream_id)
                        _print("GEN_DIAG", _compact_diag(payload))
                elif "Generation failed" in line or line.startswith("Error:"):
                    _print("STREAM_ERROR", line[:1200])

        elapsed = round(time.perf_counter() - started, 2)
        db.expire_all()
        latest = (
            db.query(TestGeneration)
            .filter(TestGeneration.project_id == PROJECT_ID, TestGeneration.user_id == USER_ID)
            .order_by(TestGeneration.id.desc())
            .first()
        )
        persisted_id = int(latest.id) if latest and int(latest.id) > int(before_max_id or 0) else None
        _print(
            "RUN_END",
            {
                "elapsed_sec": elapsed,
                "stream_chars": stream_chars,
                "request_id": request_id,
                "generation_id_from_stream": generation_id_from_stream,
                "persisted_generation_id": persisted_id,
                "latest_generation_id": getattr(latest, "id", None),
                "latest_case_count": _case_count(getattr(latest, "generated_result", "") or "") if latest else 0,
            },
        )
    except Exception as exc:
        _print("RUN_EXCEPTION", {"type": type(exc).__name__, "message": str(exc)})
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
