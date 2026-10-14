"""历史验收副本的显式契约投影；生产读取链不兼容已删除的槽位字段。"""

from copy import deepcopy

from modules.agent_platform.action_journal import action_state_hash


def project_historical_generation_inputs(inputs):
    """只删除已退役字段，其余事实、设计、路由、编号和数量逐字段保持原值。"""
    original_hash = action_state_hash(inputs)
    projected = deepcopy(inputs)
    removed = 0
    for item in projected:
        contract = item["case_fact_contract"]
        if "coverage_slots" in contract:
            del contract["coverage_slots"]
            removed += 1
    assert action_state_hash(inputs) == original_hash
    return projected, {
        "removed_field": "case_fact_contract.coverage_slots",
        "removed_from_input_count": removed,
        "original_input_sha256": original_hash,
        "projected_input_sha256": action_state_hash(projected),
        "all_other_fields_preserved": True,
        "persisted_input_unchanged": True,
        "scope": "explicit_historical_contract_projection_not_same_input_cache_hit",
    }
