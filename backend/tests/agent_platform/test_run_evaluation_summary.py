from datetime import datetime, timedelta
from types import SimpleNamespace

from modules.agent_platform.serialization import summarize_run_evaluation


def test_run_evaluation_summary_separates_execution_from_business_quality() -> None:
    started = datetime(2026, 11, 5, 1, 0, 0)
    run = SimpleNamespace(
        id=42,
        status="success",
        started_at=started,
        finished_at=started + timedelta(seconds=12),
        run_context={
            "artifacts": {
                "test_evaluation": {
                    "evaluation": {
                        "metrics": {"f1_score": 0.75, "precision": 0.8},
                    },
                },
            },
        },
    )
    nodes = [
        SimpleNamespace(status="success", attempt=1),
        SimpleNamespace(status="failed", attempt=2),
    ]
    events = [
        SimpleNamespace(sequence=1, event_type="node_started"),
        SimpleNamespace(sequence=2, event_type="node_retry_scheduled"),
        SimpleNamespace(sequence=3, event_type="run_completed"),
    ]
    approvals = [SimpleNamespace(status="approved")]

    result = summarize_run_evaluation(
        run,
        node_runs=nodes,
        events=events,
        approvals=approvals,
    )

    assert result["execution_success"] is True
    assert result["execution_success_rate"] == 1.0
    assert result["success_rate_basis"] == "run_status_only"
    assert result["execution"]["retry_count"] == 1
    assert result["execution"]["duration_seconds"] == 12
    assert result["trajectory"]["event_type_counts"]["node_retry_scheduled"] == 1
    assert result["human_intervention"]["status"] == "resolved"
    assert result["business_evaluation"]["status"] == "recorded"
    assert result["business_evaluation"]["artifact_metrics"]["test_evaluation"]["f1_score"] == 0.75


def test_run_evaluation_summary_marks_missing_business_evaluation_unverified() -> None:
    result = summarize_run_evaluation(
        SimpleNamespace(
            status="success",
            started_at=None,
            finished_at=None,
            run_context={"artifacts": {}},
        )
    )

    assert result["execution_success"] is True
    assert result["business_evaluation"] == {
        "status": "unverified",
        "artifact_keys": [],
        "metrics": {},
        "artifact_metrics": {},
        "metric_scales": {},
    }
