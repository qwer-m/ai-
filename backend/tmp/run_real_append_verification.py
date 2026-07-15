from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except Exception:
    pass

from core.db.database import SessionLocal
from core.db.models import LogEntry, TestGeneration
from modules.test_generation_components.legacy_generation_impl import TestGenerationModule


OUT_DIR = BACKEND_DIR / "tmp" / "real_append_verification"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_PATH = OUT_DIR / "progress.json"
STREAM_LOG_PATH = OUT_DIR / "stream.log"
SUMMARY_PATH = OUT_DIR / "summary.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_cases(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        parsed = json.loads(raw)
    except Exception:
        return -1
    if isinstance(parsed, list):
        return len(parsed)
    if isinstance(parsed, dict):
        for key in ("cases", "generated_result", "final_cases"):
            value = parsed.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def _latest_generation_id(db: Any) -> int:
    row = db.query(TestGeneration).order_by(TestGeneration.id.desc()).first()
    return int(getattr(row, "id", 0) or 0)


def _generation_snapshot(db: Any, generation_id: int) -> dict[str, Any]:
    row = db.query(TestGeneration).filter(TestGeneration.id == int(generation_id)).first()
    if not row:
        return {"generation_id": int(generation_id), "found": False}
    return {
        "generation_id": int(row.id),
        "found": True,
        "project_id": int(row.project_id or 0),
        "user_id": int(row.user_id or 0) if row.user_id is not None else None,
        "created_at": str(row.created_at),
        "case_count": _count_cases(row.generated_result),
        "requirement_len": len(row.requirement_text or ""),
    }


def _latest_new_generation(db: Any, *, after_id: int, project_id: int, user_id: int | None) -> int | None:
    query = db.query(TestGeneration).filter(
        TestGeneration.id > int(after_id),
        TestGeneration.project_id == int(project_id),
    )
    if user_id is not None:
        query = query.filter(TestGeneration.user_id == int(user_id))
    row = query.order_by(TestGeneration.id.desc()).first()
    return int(row.id) if row else None


def _latest_diag(db: Any, *, project_id: int, generation_id: int) -> dict[str, Any]:
    rows = (
        db.query(LogEntry)
        .filter(LogEntry.project_id == int(project_id))
        .order_by(LogEntry.id.desc())
        .limit(300)
        .all()
    )
    result: dict[str, Any] = {}
    for row in rows:
        message = str(getattr(row, "message", "") or "")
        if "GEN_DIAG:" not in message:
            continue
        raw = message.split("GEN_DIAG:", 1)[1].strip()
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if int(payload.get("generation_id") or 0) not in {0, int(generation_id)}:
            continue
        kind = str(payload.get("kind") or "")
        if kind and kind not in result:
            result[kind] = payload
    return result


def _run_stream(
    *,
    label: str,
    db: Any,
    module: TestGenerationModule,
    requirement: str,
    project_id: int,
    user_id: int | None,
    expected_count: int,
    append: bool,
    previous_generation_id: int | None,
) -> dict[str, Any]:
    before_id = _latest_generation_id(db)
    stream_line_count = 0
    last_status = ""
    last_error = ""
    detected_generation_ids: list[int] = []
    started_at = time.time()
    progress: dict[str, Any] = {
        "phase": label,
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "before_latest_generation_id": before_id,
        "expected_count": int(expected_count),
        "append": bool(append),
        "previous_generation_id": previous_generation_id,
    }
    _write_json(PROGRESS_PATH, progress)

    with STREAM_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n===== {label} started {datetime.now().isoformat(timespec='seconds')} =====\n")
        for chunk in module.generate_test_cases_stream(
            requirement=requirement,
            project_id=project_id,
            db=db,
            doc_type="requirement",
            compress=False,
            expected_count=int(expected_count),
            batch_size=10,
            overwrite=False,
            append=bool(append),
            previous_generation_id=previous_generation_id,
            user_id=user_id,
            multi_pass=True,
            generation_mode="",
            enable_sample_pool_feedback=True,
        ):
            text = str(chunk)
            log.write(text)
            log.flush()
            stream_line_count += text.count("\n") or 1
            if "@@STATUS@@:" in text:
                last_status = text.strip()[-500:]
            if "Error:" in text:
                last_error = text.strip()[-1000:]
            for match in re.finditer(r'"generation_id"\s*:\s*(\d+)', text):
                detected_generation_ids.append(int(match.group(1)))
            if stream_line_count % 20 == 0:
                progress.update(
                    {
                        "stream_line_count": stream_line_count,
                        "last_status": last_status,
                        "last_error": last_error,
                        "elapsed_seconds": round(time.time() - started_at, 1),
                    }
                )
                _write_json(PROGRESS_PATH, progress)

    db.commit()
    persisted_id = None
    for candidate in reversed(detected_generation_ids):
        if candidate > 0:
            persisted_id = candidate
            break
    if not persisted_id:
        persisted_id = _latest_new_generation(
            db,
            after_id=before_id,
            project_id=project_id,
            user_id=user_id,
        )
    snapshot = _generation_snapshot(db, int(persisted_id or 0)) if persisted_id else {"found": False}
    diagnostics = _latest_diag(db, project_id=project_id, generation_id=int(persisted_id or 0)) if persisted_id else {}
    result = {
        "label": label,
        "status": "completed" if snapshot.get("found") else "no_persisted_generation",
        "elapsed_seconds": round(time.time() - started_at, 1),
        "before_latest_generation_id": before_id,
        "stream_line_count": stream_line_count,
        "last_status": last_status,
        "last_error": last_error,
        "detected_generation_ids": detected_generation_ids[-10:],
        "persisted": snapshot,
        "diagnostics": {
            key: {
                "kind": value.get("kind"),
                "final_count": value.get("final_count"),
                "candidate_count_before_review": value.get("candidate_count_before_review"),
                "review_selected_count": value.get("review_selected_count"),
                "append_target_count": value.get("append_target_count"),
                "append_final_cap_count": value.get("append_final_cap_count"),
                "underfilled": value.get("underfilled"),
                "underfill_reason": value.get("underfill_reason"),
                "underfill_root_cause": value.get("underfill_root_cause"),
                "min_acceptable_final": value.get("min_acceptable_final"),
                "case_count": value.get("case_count"),
                "passed": value.get("passed"),
                "blocked": value.get("blocked"),
                "failure_code": value.get("failure_code"),
                "quality_hard_failures": value.get("quality_hard_failures"),
            }
            for key, value in diagnostics.items()
            if isinstance(value, dict)
        },
    }
    _write_json(PROGRESS_PATH, {"phase": label, "status": result["status"], **result})
    return result


def main() -> None:
    db = SessionLocal()
    try:
        source = db.query(TestGeneration).filter(TestGeneration.id == 512).first()
        if not source:
            raise RuntimeError("source generation 512 not found")
        requirement = source.requirement_text or ""
        project_id = int(source.project_id or 0)
        user_id = int(source.user_id) if source.user_id is not None else None
        module = TestGenerationModule()
        summary: dict[str, Any] = {
            "source_generation_id": 512,
            "project_id": project_id,
            "user_id": user_id,
            "source_requirement_len": len(requirement),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_json(SUMMARY_PATH, summary)

        base = _run_stream(
            label="base_generation",
            db=db,
            module=module,
            requirement=requirement,
            project_id=project_id,
            user_id=user_id,
            expected_count=36,
            append=False,
            previous_generation_id=None,
        )
        summary["base"] = base
        _write_json(SUMMARY_PATH, summary)
        base_id = int(base.get("persisted", {}).get("generation_id") or 0)
        base_count = int(base.get("persisted", {}).get("case_count") or 0)
        if not base_id or base_count <= 0:
            raise RuntimeError(f"base generation did not persist usable result: {base}")

        appended = _run_stream(
            label="append_10_generation",
            db=db,
            module=module,
            requirement=requirement,
            project_id=project_id,
            user_id=user_id,
            expected_count=base_count + 10,
            append=True,
            previous_generation_id=base_id,
        )
        summary["append"] = appended
        summary["completed_at"] = datetime.now().isoformat(timespec="seconds")
        summary["status"] = "completed"
        _write_json(SUMMARY_PATH, summary)
        _write_json(PROGRESS_PATH, {"phase": "done", "status": "completed", "summary_path": str(SUMMARY_PATH)})
    except Exception as exc:
        payload = {
            "phase": "error",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
        }
        _write_json(PROGRESS_PATH, payload)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
