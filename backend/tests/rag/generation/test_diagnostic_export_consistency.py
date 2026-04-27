from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from scripts.qa.diagnostics.export_review_decision_table import (  # noqa: E402
    _build_detail_export_meta as build_review_export_meta,
    _write_outputs as write_review_outputs,
)
from scripts.qa.diagnostics.trace_generation_funnel import (  # noqa: E402
    _build_detail_export_meta as build_funnel_export_meta,
)


def _rows(n: int) -> list[dict]:
    return [
        {
            "candidate_index": idx + 1,
            "case_id": f"TC-{idx + 1:03d}",
            "retained_final": bool((idx % 2) == 0),
            "dropped_stage": "review_llm" if (idx % 2) else "retained",
            "dropped_reason": "drop_not_selected_by_review_llm:coverage_redundant" if (idx % 2) else "retained",
        }
        for idx in range(n)
    ]


def test_export_meta_summary_total_overrides_exported_row_count() -> None:
    summary = {"candidate_total": 65}
    detail = {"row_count": 30, "row_count_total": 65, "rows_scope": "sampled_due_to_size"}
    rows = _rows(30)

    meta = build_review_export_meta(summary_payload=summary, detail_payload=detail, exported_rows=rows)
    assert meta["candidate_total"] == 65
    assert meta["exported_row_count"] == 30
    assert meta["is_truncated"] is True
    assert meta["truncation_reason"] == "detail_limit"
    assert meta["source_summary_available"] is True
    assert meta["source_detail_available"] is True


def test_export_meta_falls_back_to_detail_count_when_summary_missing() -> None:
    summary = {}
    detail = {"row_count": 30, "row_count_total": 30}
    rows = _rows(30)

    meta = build_review_export_meta(summary_payload=summary, detail_payload=detail, exported_rows=rows)
    assert meta["candidate_total"] == 30
    assert meta["exported_row_count"] == 30
    assert meta["is_truncated"] is False
    assert meta["truncation_reason"] == ""
    assert meta["source_summary_available"] is False
    assert meta["source_detail_available"] is True


def test_export_meta_message_truncated_does_not_change_candidate_total() -> None:
    summary = {"candidate_total": 65}
    detail = {"row_count": 0, "row_count_total": 65, "rows_scope": "summary_only_due_to_size"}
    rows: list[dict] = []

    meta = build_review_export_meta(summary_payload=summary, detail_payload=detail, exported_rows=rows)
    assert meta["candidate_total"] == 65
    assert meta["exported_row_count"] == 0
    assert meta["is_truncated"] is True
    assert meta["truncation_reason"] == "message_truncated"
    assert meta["source_summary_available"] is True
    assert meta["source_detail_available"] is True


def test_review_summary_output_has_consistent_field_naming(tmp_path: Path) -> None:
    generation_id = 999
    summary_payload = {"candidate_total": 65, "dropped_total": 35}
    detail_payload = {"row_count": 30, "row_count_total": 65, "rows_scope": "sampled_due_to_size"}
    rows = _rows(30)

    summary_path, table_path = write_review_outputs(
        generation_id=generation_id,
        summary_payload=summary_payload,
        table_payload=detail_payload,
        table_rows=rows,
        out_dir=tmp_path,
    )
    summary_obj = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary_obj["generation_id"] == generation_id
    assert summary_obj["candidate_total"] == 65
    assert summary_obj["exported_row_count"] == 30
    assert summary_obj["row_count"] == 30
    assert summary_obj["is_truncated"] is True
    assert summary_obj["truncation_reason"] == "detail_limit"
    assert summary_obj["source_summary_available"] is True
    assert summary_obj["source_detail_available"] is True
    assert table_path.exists()


def test_funnel_and_review_meta_calculation_are_consistent() -> None:
    summary = {"candidate_total": 65}
    detail = {"row_count": 30, "row_count_total": 65, "rows_scope": "sampled_due_to_size"}
    rows = _rows(30)

    review_meta = build_review_export_meta(summary_payload=summary, detail_payload=detail, exported_rows=rows)
    funnel_meta = build_funnel_export_meta(summary_payload=summary, detail_payload=detail, exported_rows=rows)

    keys = (
        "candidate_total",
        "exported_row_count",
        "is_truncated",
        "truncation_reason",
        "source_summary_available",
        "source_detail_available",
    )
    for key in keys:
        assert review_meta.get(key) == funnel_meta.get(key)
