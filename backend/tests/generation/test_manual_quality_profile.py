from __future__ import annotations

from modules.testing.manual_quality_profile import build_manual_quality_profile


def _sample(
    *,
    case_id: str,
    module: str,
    priority: str,
    confirmed: bool = True,
    candidate: bool = False,
    signal_type: str = "positive",
) -> dict[str, object]:
    sample = {
        "case_id": case_id,
        "source_case_module": module,
        "source_case_title": f"{module} case",
        "source_case_expected_result": f"{module} assertion",
        "expected_priority": priority,
        "signal_type": signal_type,
        "pattern_usage": "prefer" if signal_type == "positive" else "avoid",
        "pattern_summary": f"{module} {priority} reusable pattern",
        "source_type": "quality_evaluation_defect" if candidate else "manual_pool_input",
        "learning_status": "system_candidate" if candidate else "user_confirmed",
        "ST": "PASS",
        "release": "PASS",
        "补充项": "manual note",
    }
    if confirmed:
        sample["manual_confirmed"] = True
    return sample


def test_manual_quality_profile_ignores_unconfirmed_candidate_drift() -> None:
    trusted = _sample(case_id="TC-1", module="本周课程模块", priority="P0")
    candidate = _sample(
        case_id="TC-2",
        module="按钮展示逻辑",
        priority="P2",
        confirmed=False,
        candidate=True,
    )

    first = build_manual_quality_profile([trusted, candidate], project_id=1, user_id=2)
    changed_candidate = dict(candidate)
    changed_candidate["source_case_module"] = "另一个未确认模块"
    second = build_manual_quality_profile([trusted, changed_candidate], project_id=1, user_id=2)

    assert first["sample_set_hash"] == second["sample_set_hash"]
    assert first["trusted_sample_count"] == 1
    assert first["priority_distribution"] == {"P0": 1}
    assert first["module_distribution_top"] == {"本周课程模块": 1}
    assert {"ST", "release", "补充项"}.issubset(set(first["execution_lifecycle_fields"]))


def test_manual_quality_profile_version_changes_when_confirmed_sample_changes() -> None:
    first = build_manual_quality_profile(
        [_sample(case_id="TC-1", module="入口", priority="P0")],
        project_id=1,
        user_id=2,
    )
    second = build_manual_quality_profile(
        [
            _sample(case_id="TC-1", module="入口", priority="P0"),
            _sample(case_id="TC-2", module="排课-学习计划-第1步", priority="P1"),
        ],
        project_id=1,
        user_id=2,
        existing_profile=first,
    )

    assert first["sample_set_hash"] != second["sample_set_hash"]
    assert second["trusted_sample_count"] == 2
    assert second["priority_distribution"] == {"P0": 1, "P1": 1}
    assert second["high_priority_ratio"] == 1.0


def test_manual_quality_profile_reads_shared_case_aliases() -> None:
    sample = {
        "caseId": "TC-ALIAS",
        "description": "Create learning plan",
        "testModule": "Learning plan",
        "expectedResult": {"status": "saved", "message": ["success visible"]},
        "expected_priority": "P1",
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "source_type": "manual_pool_input",
        "learning_status": "user_confirmed",
        "manual_confirmed": True,
    }
    profile = build_manual_quality_profile([sample], project_id=1, user_id=2)

    changed_assertion = dict(sample)
    changed_assertion["expectedResult"] = {"status": "failed", "message": ["error visible"]}
    changed_profile = build_manual_quality_profile([changed_assertion], project_id=1, user_id=2)

    assert profile["trusted_sample_count"] == 1
    assert profile["module_distribution_top"] == {"Learning plan": 1}
    assert profile["priority_distribution"] == {"P1": 1}
    assert profile["sample_set_hash"] != changed_profile["sample_set_hash"]
