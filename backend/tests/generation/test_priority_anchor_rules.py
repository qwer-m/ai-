from __future__ import annotations

from modules.testing.test_generation_components.postprocess.priority_anchor_rules import (
    p0_configured_anchor_family,
    p0_cross_domain_essay_case,
    p0_has_low_value_signal,
    p0_main_path_anchor,
)


def test_schedule_requirement_rejects_essay_correction_p0_anchor() -> None:
    case = {
        "description": "\u4e0a\u4f20\u4f5c\u6587\u56fe\u7247\u540e\u751f\u6210\u6279\u6539\u7ed3\u679c",
        "test_module": "\u4f5c\u6587\u6279\u6539",
        "expected_result": "\u8fdb\u5165\u6279\u6539\u7ed3\u679c\u9875",
        "priority": "P1",
    }
    requirement = "\u8fd1\u671f\u8bfe\u7a0b+\u6392\u8bfe\uff1a\u8bfe\u7a0b\u65f6\u95f4\u51b2\u7a81\u548c\u987a\u5ef6\u89c4\u5219"

    assert p0_cross_domain_essay_case(case, requirement_text=requirement) is True
    assert p0_main_path_anchor(case, requirement_text=requirement) is False


def test_essay_requirement_accepts_complete_correction_result_anchor() -> None:
    case = {
        "description": "\u4e0a\u4f20\u56fe\u7247\u540e\u70b9\u51fb\u53bb\u6279\u6539\u6210\u529f\u751f\u6210\u6279\u6539\u7ed3\u679c",
        "test_module": "\u4f5c\u6587\u6279\u6539",
        "expected_result": "\u6279\u6539\u7ed3\u679c\u9875\u5c55\u793a\u7efc\u5408\u70b9\u8bc4\u3001\u5206\u53e5\u70b9\u8bc4\u3001\u5168\u6587\u6da6\u8272\u548c\u4f18\u5316\u5efa\u8bae\u56db\u90e8\u5206\u5185\u5bb9",
    }
    requirement = "\u4f5c\u6587\u6279\u6539 full regression"

    assert p0_cross_domain_essay_case(case, requirement_text=requirement) is False
    assert p0_configured_anchor_family(case, requirement_text=requirement) in {
        "generation_result",
        "result_display",
    }
    assert p0_main_path_anchor(case, requirement_text=requirement) is True


def test_low_value_result_detail_is_not_public_p0_anchor() -> None:
    case = {
        "description": "\u7efc\u5408\u70b9\u8bc4\u661f\u661f\u8bc4\u5206\u5c55\u793a",
        "test_module": "\u6279\u6539\u7ed3\u679c",
        "expected_result": "\u661f\u661f\u6570\u91cf\u4e0e\u7efc\u5408\u8bc4\u5206\u503c\u5339\u914d",
    }

    assert p0_has_low_value_signal(case) is True
    assert p0_main_path_anchor(case, requirement_text="\u4f5c\u6587\u6279\u6539") is False


def test_course_permission_anchor_survives_non_essay_requirement() -> None:
    case = {
        "description": "Normal user first lesson is available and other lessons are locked",
        "test_module": "Permission",
        "expected_result": "The first lesson is available and other lessons are locked by the paywall.",
    }

    assert p0_configured_anchor_family(case, requirement_text="course permission regression") == "permission"
    assert p0_main_path_anchor(case, requirement_text="course permission regression") is True


def test_course_permission_anchor_accepts_alias_fields() -> None:
    case = {
        "title": "Normal user first lesson is available and other lessons are locked",
        "testModule": "Permission",
        "expectedResult": "The first lesson is available and other lessons are locked by the paywall.",
        "testSteps": ["open course list", "open locked lesson"],
    }

    assert p0_configured_anchor_family(case, requirement_text="course permission regression") == "permission"
    assert p0_main_path_anchor(case, requirement_text="course permission regression") is True
