from __future__ import annotations

from typing import Any

from .json_generation_payloads import (
    _build_case_signature as _payload_build_case_signature,
    _normalize_case_steps as _payload_normalize_case_steps,
    _normalize_case_text as _payload_normalize_case_text,
    build_requirement_semantics_payload,
)
from .runtime import (
    LazyAttrProxy as _LazyAttrProxy,
    call_component as _call_component,
    component_attr as _component_attr,
    lazy_attr as _lazy_attr,
)


def get_client_for_user(*args: Any, **kwargs: Any) -> Any:
    return _call_component("core.ai.ai_client", "get_client_for_user", *args, **kwargs)


def TestGeneration(*args: Any, **kwargs: Any) -> Any:
    return _call_component("core.db.models", "TestGeneration", *args, **kwargs)


def LogEntry(*args: Any, **kwargs: Any) -> Any:
    return _call_component("core.db.models", "LogEntry", *args, **kwargs)


STAGE25_SWITCHES = _LazyAttrProxy("modules.domain.stage25_switches", "STAGE25_SWITCHES")
MemoryContext = _LazyAttrProxy("modules.memory_fabric.contracts.memory_context", "MemoryContext")


def init_memory_diag(*args: Any, **kwargs: Any) -> Any:
    return _call_component("modules.memory_fabric.runtime.diagnostics", "init_memory_diag", *args, **kwargs)


def get_memory_fabric(*args: Any, **kwargs: Any) -> Any:
    return _call_component("modules.memory_fabric.runtime.factory", "get_memory_fabric", *args, **kwargs)


def build_context_compression_diagnostics(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..prompting.generation_diagnostics", "build_context_compression_diagnostics", *args, **kwargs)


def build_coverage_diagnostics(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..prompting.generation_diagnostics", "build_coverage_diagnostics", *args, **kwargs)


def build_gate_reason_chain(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..prompting.generation_diagnostics", "build_gate_reason_chain", *args, **kwargs)


def build_prompt_context_intake_diagnostics(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..prompting.generation_diagnostics", "build_prompt_context_intake_diagnostics", *args, **kwargs)


def analyze_coverage(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..coverage.coverage_analyzer", "analyze_coverage", *args, **kwargs)


def build_append_closed_loop_coverage_instruction(*args: Any, **kwargs: Any) -> Any:
    return _call_component(
        "..prompting.prompt_orchestration",
        "build_append_closed_loop_coverage_instruction",
        *args,
        **kwargs,
    )


def build_closed_loop_base_prompt(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..prompting.prompt_orchestration", "build_closed_loop_base_prompt", *args, **kwargs)


def build_supplement_closed_loop_instruction(*args: Any, **kwargs: Any) -> Any:
    return _call_component(
        "..prompting.prompt_orchestration",
        "build_supplement_closed_loop_instruction",
        *args,
        **kwargs,
    )


def build_structured_prompt_context(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..prompting.structured_context", "build_structured_prompt_context", *args, **kwargs)


def build_feedback_control_state(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..control.build_feedback_control_state", "build_feedback_control_state", *args, **kwargs)


def merge_current_requirement_blueprint_control_state(*args: Any, **kwargs: Any) -> Any:
    return _call_component(
        "..control.current_requirement_blueprint",
        "merge_current_requirement_blueprint_control_state",
        *args,
        **kwargs,
    )


def evaluate_current_requirement_semantic_compilation(*args: Any, **kwargs: Any) -> Any:
    return _call_component(
        "..control.current_requirement_blueprint",
        "evaluate_current_requirement_semantic_compilation",
        *args,
        **kwargs,
    )


def merge_generation_mode_control_state(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..control.generation_mode_activation", "merge_generation_mode_control_state", *args, **kwargs)


def resolve_linked_final_case_signal(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..control.generation_mode_activation", "resolve_linked_final_case_signal", *args, **kwargs)


def finalize_generated_cases(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.result_postprocess", "finalize_generated_cases", *args, **kwargs)


def merge_cases_for_append(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.result_postprocess", "merge_cases_for_append", *args, **kwargs)


def normalize_final_case_priorities(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.result_postprocess", "normalize_final_case_priorities", *args, **kwargs)


def prepare_append_existing_cases(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.result_postprocess", "prepare_append_existing_cases", *args, **kwargs)


def stream_postprocess_cases(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.result_postprocess", "stream_postprocess_cases", *args, **kwargs)


def build_persistence_gate_diagnostic(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.persistence_gate", "build_persistence_gate_diagnostic", *args, **kwargs)


def evaluate_persistence_gate(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.persistence_gate", "evaluate_persistence_gate", *args, **kwargs)


def summarize_persistence_case_quality_gate(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.persistence_gate", "summarize_persistence_case_quality_gate", *args, **kwargs)


def merge_contract_quality_gate(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.case_contract", "merge_contract_quality_gate", *args, **kwargs)


def project_persistable_cases(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.case_contract", "project_persistable_cases", *args, **kwargs)


def summarize_persistable_case_contract(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.case_contract", "summarize_persistable_case_contract", *args, **kwargs)


def case_access_id(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.case_access", "case_id", *args, **kwargs)


def case_steps(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.case_access", "case_steps", *args, **kwargs)


def case_text_field(*args: Any, **kwargs: Any) -> Any:
    return _call_component("..postprocess.case_access", "case_text_field", *args, **kwargs)


def _normalize_case_text(value: Any) -> str:
    return _payload_normalize_case_text(value)


def _normalize_case_steps(value: Any) -> str:
    return _payload_normalize_case_steps(value)


def _build_case_signature(case_payload: dict[str, Any]) -> str:
    return _payload_build_case_signature(
        case_payload,
        text_field_getter=case_text_field,
        steps_getter=case_steps,
    )


def requirement_compression_decision(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".compression_policy", "requirement_compression_decision", *args, **kwargs)


def clean_and_parse_json(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "clean_and_parse_json", *args, **kwargs)


def normalize_json_structure(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "normalize_json_structure", *args, **kwargs)


def deduplicate_test_cases(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "deduplicate_test_cases", *args, **kwargs)


def count_unique_test_cases(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "count_unique_test_cases", *args, **kwargs)


def infer_case_kind(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "infer_case_kind", *args, **kwargs)


def reorder_cases_by_closed_loop(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "reorder_cases_by_closed_loop", *args, **kwargs)


def _convert_json_to_excel_adapter(*args: Any, **kwargs: Any) -> Any:
    return _call_component(".adapters", "convert_json_to_excel", *args, **kwargs)
