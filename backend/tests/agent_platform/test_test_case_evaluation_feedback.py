"""验证差异转换的空值边界和人工确认契约，不制造业务评测或人工定稿数据。"""

import pytest

from modules.agent_platform.test_case_evaluation_feedback import difference_candidate_contents
from modules.knowledge_base_components.context.context_requests import ContextRecordConfirmationRequest


def test_no_differences_do_not_create_memory() -> None:
    assert difference_candidate_contents({}, {
        "defect_analysis": {"missing_points": [], "hallucinations": [], "modifications": []},
    }) == []


@pytest.mark.parametrize("analysis", [None, {}, {"missing_points": ""}])
def test_incomplete_evaluation_cannot_become_memory(analysis) -> None:
    with pytest.raises(ValueError):
        difference_candidate_contents({}, {"defect_analysis": analysis})


def test_blank_difference_cannot_become_memory() -> None:
    with pytest.raises(ValueError, match="差异必须是非空文本"):
        difference_candidate_contents({}, {
            "defect_analysis": {"missing_points": ["  "], "hallucinations": [], "modifications": []},
        })


@pytest.mark.parametrize("payload", [
    {},
    {"revision": "", "text": "", "applicability": "", "invalidation_condition": ""},
    {"revision": " ", "text": " ", "applicability": " ", "invalidation_condition": " "},
])
def test_confirmation_requires_version_conclusion_and_conditions(payload) -> None:
    with pytest.raises(ValueError):
        ContextRecordConfirmationRequest.model_validate(payload)
