from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
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
from modules.testing.test_generation import test_generator
from routers.test_generation_routes.support import parse_requirement_for_generation


PROJECT_ID = int(os.getenv("CODEX_REAL_PROJECT_ID", "2"))
USER_ID = int(os.getenv("CODEX_REAL_USER_ID", "1"))
EXPECTED_COUNT = int(os.getenv("CODEX_REAL_EXPECTED_COUNT", "80"))
SOURCE_FILE = Path(
    os.getenv(
        "CODEX_REAL_SOURCE_FILE",
        r"C:\Users\Administrator\Downloads\【天天练-功能】论坛优化.pdf",
    )
)


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _print(kind: str, payload: object) -> None:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    print(f"{_now()} {kind} {text}", flush=True)


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
            )
            if key in source_meta
        }
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
    if not SOURCE_FILE.exists():
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
                "before_max_generation_id": before_max_id,
                "expected_count": EXPECTED_COUNT,
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
            batch_size=10,
            overwrite=True,
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
