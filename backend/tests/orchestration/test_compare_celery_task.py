from __future__ import annotations

from modules.orchestration import tasks


def test_compare_test_cases_task_delegates_to_background_service(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_update_state(**kwargs):
        calls.append({"update_state": kwargs})

    def fake_run_compare_background_job(**kwargs):
        calls.append({"job": kwargs})

    monkeypatch.setattr(tasks.compare_test_cases_task, "update_state", fake_update_state)
    monkeypatch.setattr(tasks, "run_compare_background_job", fake_run_compare_background_job)

    result = tasks.compare_test_cases_task.run(
        comparison_id=9,
        generated_test_case="generated",
        modified_test_case="modified",
        project_id=3,
        user_id=4,
        generation_id=5,
        requirement_text="requirement",
        upload_persist={"filename": "case.xlsx"},
    )

    assert calls[0] == {
        "update_state": {
            "state": "STARTED",
            "meta": {"status": "test_case_compare_running", "comparison_id": 9},
        }
    }
    assert calls[1] == {
        "job": {
            "comparison_id": 9,
            "generated_test_case": "generated",
            "modified_test_case": "modified",
            "project_id": 3,
            "user_id": 4,
            "generation_id": 5,
            "requirement_text": "requirement",
            "upload_persist": {"filename": "case.xlsx"},
        }
    }
    assert result == {"comparison_id": 9, "status": "completed"}


def test_run_rag_eval_task_delegates_to_eval_service(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_update_state(**kwargs):
        calls.append({"update_state": kwargs})

    def fake_execute_eval_run(**kwargs):
        calls.append({"job": kwargs})

    monkeypatch.setattr(tasks.run_rag_eval_task, "update_state", fake_update_state)
    monkeypatch.setattr(tasks, "execute_eval_run", fake_execute_eval_run)

    result = tasks.run_rag_eval_task.run(run_id=17, user_id=3)

    assert calls == [
        {
            "update_state": {
                "state": "STARTED",
                "meta": {"status": "rag_eval_running", "run_id": 17},
            }
        },
        {"job": {"run_id": 17, "user_id": 3}},
    ]
    assert result == {"run_id": 17, "status": "completed"}


def test_run_pipeline_task_delegates_to_pipeline_worker(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_update_state(**kwargs):
        calls.append({"update_state": kwargs})

    def fake_run_pipeline_worker(**kwargs):
        calls.append({"job": kwargs})

    monkeypatch.setattr(tasks.run_pipeline_task, "update_state", fake_update_state)
    monkeypatch.setattr(tasks, "run_pipeline_worker", fake_run_pipeline_worker)

    result = tasks.run_pipeline_task.run(run_id=23, start_stage="evaluation")

    assert calls == [
        {
            "update_state": {
                "state": "STARTED",
                "meta": {
                    "status": "pipeline_running",
                    "run_id": 23,
                    "start_stage": "evaluation",
                    "task_id": None,
                },
            }
        },
        {"job": {"run_id": 23, "start_stage": "evaluation", "task_id": None}},
    ]
    assert result == {"run_id": 23, "status": "completed"}


def test_recover_expired_pipeline_runs_task_delegates_to_recovery_service(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_update_state(**kwargs):
        calls.append({"update_state": kwargs})

    def fake_recover_expired_pipeline_runs(**kwargs):
        calls.append({"job": kwargs})
        return {"checked": 2, "requeued": 1}

    monkeypatch.setattr(tasks.recover_expired_pipeline_runs_task, "update_state", fake_update_state)
    monkeypatch.setattr(tasks, "recover_expired_pipeline_runs", fake_recover_expired_pipeline_runs)

    result = tasks.recover_expired_pipeline_runs_task.run(limit=7)

    assert calls == [
        {
            "update_state": {
                "state": "STARTED",
                "meta": {"status": "pipeline_recovery_running", "limit": 7},
            }
        },
        {"job": {"limit": 7}},
    ]
    assert result == {"checked": 2, "requeued": 1}
