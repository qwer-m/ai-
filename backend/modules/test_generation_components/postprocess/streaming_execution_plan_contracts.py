from __future__ import annotations

import re
from typing import Any

from ..control.actor_roles import normalize_actor_role as normalize_actor_role_value
from .streaming_postprocess_utils import _clip_text


def is_internal_state_text(value: str) -> bool:
    return bool(re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\b", str(value or "").strip().lower()))


def public_contract_module_label(step_meta: dict[str, Any], label: str) -> str:
    for raw in (
        step_meta.get("module"),
        step_meta.get("domain"),
        step_meta.get("feature"),
        step_meta.get("blueprint_name"),
    ):
        value = str(raw or "").strip()
        if value and not is_internal_state_text(value):
            return _clip_text(value, 80)
    return "业务主链路"


CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX_BY_STAGE = {
    "entry": "完成，目标入口页面可执行后续操作",
    "configure": "完成，已选配置在页面中保留并可进入下一步",
    "preview": "完成，预览内容展示当前配置结果",
    "commit": "完成，保存结果展示成功状态",
    "downstream_visibility": "完成，下游页面展示最新业务结果",
    "consume": "完成，目标页面打开并展示可操作内容",
    "completion_sync": "完成，进度状态更新",
}
DEFAULT_CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX = (
    "完成，业务状态已更新并可继续执行下一步"
)


def contract_materialized_expected_result(label: str, stage_kind: str) -> str:
    stage = str(stage_kind or "").strip().lower()
    suffix = CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX_BY_STAGE.get(
        stage,
        DEFAULT_CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX,
    )
    return f"{label}{suffix}"


def materialize_workflow_contract_case(stage_key: str, step_meta: dict[str, Any]) -> dict[str, Any] | None:
    label = str(step_meta.get("label") or stage_key).strip()
    if not label or is_internal_state_text(label):
        return None
    action = str(step_meta.get("action") or label).strip()
    if not action or is_internal_state_text(action):
        action = label
    test_steps = step_meta.get("test_steps") if isinstance(step_meta.get("test_steps"), list) else []
    public_steps = [
        str(step).strip()
        for step in test_steps
        if str(step).strip() and not is_internal_state_text(str(step))
    ]
    if not public_steps:
        public_steps = [action]
    stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
    assertion = str(step_meta.get("assertion") or step_meta.get("expected_result") or "").strip()
    expected_result = (
        assertion
        if assertion and not is_internal_state_text(assertion)
        else contract_materialized_expected_result(label, stage_kind)
    )
    return {
        "id": f"TC-CONTRACT-{stage_key.upper().replace(':', '-').replace(' ', '-')[:40]}",
        "description": label,
        "test_module": public_contract_module_label(step_meta, label),
        "preconditions": [f"已具备执行“{label}”的前置业务状态"],
        "steps": public_steps,
        "test_input": action,
        "expected_result": expected_result,
        "priority": "P0" if bool(step_meta.get("main_path_step", True)) else "P1",
        "role": normalize_actor_role_value(step_meta.get("actor")),
        "workflow_contract_materialized_case": True,
    }


def workflow_bridge_case(
    stage_key: str,
    *,
    stage_meta_by_key: dict[str, dict[str, Any]],
    main_chain_stages: list[tuple[str, str, tuple[tuple[str, ...], ...]]],
    selected_stage_keys: set[str],
    available_stage_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    step_meta = stage_meta_by_key.get(stage_key) or {}
    if not step_meta or not bool(step_meta.get("allow_bridge")):
        return None
    stage_order = [key for key, _label, _patterns in main_chain_stages]
    try:
        stage_index = stage_order.index(stage_key)
    except ValueError:
        return None
    available = available_stage_keys if available_stage_keys is not None else selected_stage_keys
    if stage_index > 0 and stage_order[stage_index - 1] not in available:
        return None
    label = str(step_meta.get("label") or step_meta.get("action") or stage_key).strip()
    assertion = str(step_meta.get("assertion") or step_meta.get("expected_result") or step_meta.get("state_out") or "").strip()
    test_steps = step_meta.get("test_steps") if isinstance(step_meta.get("test_steps"), list) else []
    return {
        "id": f"TC-BRIDGE-{stage_key.upper().replace(':', '-').replace(' ', '-')[:40]}",
        "description": label or stage_key,
        "test_module": str(step_meta.get("module") or step_meta.get("blueprint_name") or "workflow_blueprint"),
        "preconditions": [str(step_meta.get("state_in") or "previous workflow state")],
        "steps": test_steps or [str(step_meta.get("action") or label or stage_key)],
        "test_input": str(step_meta.get("input") or step_meta.get("state_in") or "workflow state"),
        "expected_result": assertion or f"workflow state reaches {stage_key}",
        "priority": "P0" if bool(step_meta.get("main_path_step", True)) else "P1",
        "role": normalize_actor_role_value(step_meta.get("actor")),
        "generated_bridge_case": True,
        "workflow_blueprint_bridge": True,
    }
