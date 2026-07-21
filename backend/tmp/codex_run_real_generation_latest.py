from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai.ai_client_impl import get_client_for_user
from core.db.database import SessionLocal
from core.db.models import SystemConfig, TestGeneration
from modules.testing.test_generation import test_generator


PROJECT_ID = 2
USER_ID = 1
EXPECTED_COUNT = int(os.getenv("CODEX_REAL_EXPECTED_COUNT", "80"))


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _print(kind: str, payload: object) -> None:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    print(f"{_now()} {kind} {text}", flush=True)


def _latest_requirement(db):
    row = (
        db.query(TestGeneration)
        .filter(
            TestGeneration.project_id == PROJECT_ID,
            TestGeneration.user_id == USER_ID,
            TestGeneration.requirement_text.isnot(None),
            TestGeneration.requirement_text != "",
        )
        .order_by(TestGeneration.id.desc())
        .first()
    )
    if not row:
        raise RuntimeError("No persisted requirement_text found for project/user")
    return row.id, row.requirement_text


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


def main() -> None:
    db = SessionLocal()
    request_id = ""
    before_max_id = None
    try:
        source_generation_id, requirement = _latest_requirement(db)
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
                "source_generation_id": source_generation_id,
                "before_max_generation_id": before_max_id,
                "requirement_len": len(requirement or ""),
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
                    "generation_model": client.select_model(requirement, "generation"),
                    "compression_model": client.select_model(requirement, "compression"),
                    "ocr_model": client.select_model(requirement, "ocr"),
                },
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
                        kind = str(payload.get("kind") or "")
                        compact = {
                            key: payload.get(key)
                            for key in (
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
                            if key in payload
                        }
                        _print("GEN_DIAG", compact or {"kind": kind, "request_id": request_id})
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
