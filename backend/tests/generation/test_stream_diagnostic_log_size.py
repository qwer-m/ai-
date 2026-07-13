from __future__ import annotations

import json

from modules.test_generation_components.legacy.stream.persistence_post_persist_diagnostics import (
    _MAX_GEN_DIAG_MESSAGE_BYTES,
    add_diagnostic_log,
)


def test_add_diagnostic_log_compacts_oversized_payload_without_breaking_json() -> None:
    large_suite = {
        "kind": "execution_suite",
        "case_count": 120,
        "execution_readiness": "partial",
        "suites": [
            {
                "suite_id": f"suite-{index}",
                "cases": [
                    {
                        "case_id": f"TC-{index:03d}-{case_index:03d}",
                        "description": "x" * 800,
                        "steps": ["step " + ("y" * 200)] * 5,
                    }
                    for case_index in range(8)
                ],
            }
            for index in range(30)
        ],
    }
    payload = {
        "kind": "generation_execution_suite",
        "generation_id": 506,
        "project_id": 2,
        "request_id": "req-large",
        "source": "persistence_gate_pre_projection",
        "case_count": 120,
        "execution_readiness": "partial",
        "execution_suite": large_suite,
    }

    line = add_diagnostic_log(
        db=None,
        log_entry_type=object,
        project_id=2,
        user_id=1,
        payload=payload,
    )

    assert len(line.encode("utf-8")) <= _MAX_GEN_DIAG_MESSAGE_BYTES
    assert line.startswith("GEN_DIAG:")
    fitted = json.loads(line.split(":", 1)[1])
    assert fitted["kind"] == "generation_execution_suite"
    assert fitted["generation_id"] == 506
    assert fitted["payload_omitted_due_to_size"] is True
    assert fitted["execution_suite_omitted_due_to_size"] is True
    assert "execution_suite" not in fitted
    assert fitted["execution_suite_summary"]["case_count"] == 120
    assert fitted["execution_suite_compact"]["case_count"] == 120
    assert fitted["execution_suite_compact"]["suites"][0]["case_count"] == 8
    compact_case = fitted["execution_suite_compact"]["suites"][0]["cases"][0]
    assert compact_case["case_id"] == "TC-000-000"
    assert "description" not in compact_case
    assert "steps" not in compact_case
