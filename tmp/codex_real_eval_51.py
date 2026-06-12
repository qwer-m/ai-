import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
os.chdir(ROOT)
os.environ["PYTHONPATH"] = str(BACKEND)
sys.path.insert(0, str(BACKEND))
os.environ["EVAL_LLM_COMPARE_CHUNK_RETRIES"] = "0"
os.environ["EVAL_LLM_COMPARE_SUB_CHUNK_RETRIES"] = "0"

load_dotenv(BACKEND / ".env")
load_dotenv(ROOT / ".env")

from core.db.database import SessionLocal  # noqa: E402
from core.db.models import TestGenerationComparison  # noqa: E402
from modules.testing.evaluation import EvaluationModule  # noqa: E402


LOG = ROOT / "tmp" / "codex_real_eval_51.progress.jsonl"


def log_event(payload):
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def main():
    started = time.time()
    LOG.write_text("", encoding="utf-8")
    db = SessionLocal()
    try:
        row = db.query(TestGenerationComparison).filter(TestGenerationComparison.id == 51).first()
        if row is None:
            raise RuntimeError("comparison_id=51 not found")

        def progress_callback(payload):
            progress = payload.get("progress") or {}
            log_event(
                {
                    "event": "progress",
                    "analysis_status": payload.get("analysis_status"),
                    "phase": progress.get("phase"),
                    "completed_chunks": progress.get("completed_chunks"),
                    "total_chunks": progress.get("total_chunks"),
                    "failed_chunks": progress.get("failed_chunks"),
                    "retrying_chunks": progress.get("retrying_chunks"),
                    "partial_count": len(payload.get("partial_chunk_results") or []),
                    "last_error": progress.get("last_error"),
                    "elapsed_seconds": round(time.time() - started, 2),
                }
            )

        result = EvaluationModule().compare_test_cases(
            row.generated_test_case or "",
            row.modified_test_case or "",
            db=db,
            project_id=row.project_id,
            user_id=row.user_id,
            requirement_text="",
            persist_result=False,
            progress_callback=progress_callback,
            comparison_id=row.id,
        )
        payload = json.loads(result)
        final = {
            "event": "final",
            "analysis_status": payload.get("analysis_status"),
            "analysis_mode": payload.get("analysis_mode"),
            "is_final_evaluation": payload.get("is_final_evaluation"),
            "metrics": payload.get("metrics"),
            "chunk_summary": payload.get("chunk_summary"),
            "progress": payload.get("progress"),
            "partial_count": len(payload.get("partial_chunk_results") or []),
            "elapsed_seconds": round(time.time() - started, 2),
        }
        if payload.get("analysis_status") == "completed":
            row.comparison_result = result
            db.commit()
            final["persisted_to_comparison_51"] = True
        else:
            final["persisted_to_comparison_51"] = False
        log_event(final)
        print(json.dumps(final, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        db.rollback()
        log_event({"event": "exception", "error": str(exc), "elapsed_seconds": round(time.time() - started, 2)})
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
