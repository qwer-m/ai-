from __future__ import annotations

import json
from typing import Any


def parse_gen_diag_payload(raw_message: Any) -> dict[str, Any]:
    text = str(raw_message or "")
    marker = "GEN_DIAG:"
    if marker not in text:
        return {}
    payload_text = text.split(marker, 1)[1].strip()
    try:
        parsed = json.loads(payload_text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def find_generation_quality_ledger(
    *,
    db: Any,
    log_entry_model: Any,
    entry: Any,
) -> dict[str, Any]:
    if getattr(entry, "project_id", None) is None or getattr(entry, "id", None) is None:
        return {}
    try:
        rows = (
            db.query(log_entry_model)
            .filter(log_entry_model.project_id == int(entry.project_id))
            .filter(log_entry_model.message.like("%GEN_DIAG:%generation_quality_ledger%"))
            .order_by(log_entry_model.id.desc())
            .limit(40)
            .all()
        )
        for row in rows:
            payload = parse_gen_diag_payload(getattr(row, "message", ""))
            if int(payload.get("generation_id") or 0) == int(entry.id):
                return payload
    except Exception:
        return {}
    return {}
