from __future__ import annotations

from modules.test_generation_components.control.feedback_control_pattern_policy import (
    _extract_reuse_risks,
)
from modules.test_generation_components.control.reuse_risk_policy import extract_reuse_risks
from modules.test_generation_components.prompting.structured_context_requirement_semantics import (
    _derive_reuse_risks,
)


def test_reuse_risk_policy_extracts_generic_routing_and_state_leaks() -> None:
    risks = extract_reuse_risks(
        "复用已有模块时返回目标仍指向原地址，并存在上下文污染和状态污染"
    )

    assert any("wrong_return_target_risk" in item for item in risks)
    assert any("shared_flow_residual_risk" in item for item in risks)


def test_reuse_risk_wrappers_share_one_policy_and_keep_context_specific_default() -> None:
    text = "复用共享页面并保留旧版行为"

    assert _extract_reuse_risks(text) == extract_reuse_risks(text)
    assert _derive_reuse_risks([text]) == extract_reuse_risks(
        text,
        default_shared_flow=True,
    )
    assert _extract_reuse_risks("复用一个能力但未声明具体风险") == []
    assert any(
        "shared_flow_residual_risk" in item
        for item in _derive_reuse_risks(["复用一个能力但未声明具体风险"])
    )
