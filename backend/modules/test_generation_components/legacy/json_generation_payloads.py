from typing import Any, Callable


_REQUIREMENT_SEMANTICS_FIELDS = (
    "confirmed_facts",
    "scoped_rules",
    "pending_items",
    "reuse_declarations",
    "hard_flow_constraints",
    "reuse_risks",
)


def _clean_payload_items(items: Any) -> list[str]:
    return [str(item).strip() for item in (items or []) if str(item).strip()]


def build_requirement_semantics_payload(prompt_context: Any) -> dict[str, list[str]]:
    return {
        field_name: _clean_payload_items(prompt_context.get(field_name))
        for field_name in _REQUIREMENT_SEMANTICS_FIELDS
    }


def _normalize_case_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_case_steps(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(_normalize_case_text(item) for item in value if str(item or "").strip())
    return _normalize_case_text(value)


def _build_case_signature(
    case_payload: dict[str, Any],
    *,
    text_field_getter: Callable[[dict[str, Any], str], Any],
    steps_getter: Callable[[dict[str, Any]], Any],
) -> str:
    if not isinstance(case_payload, dict):
        return ""
    return "||".join(
        [
            _normalize_case_text(text_field_getter(case_payload, "test_module")),
            _normalize_case_text(text_field_getter(case_payload, "description")),
            _normalize_case_steps(steps_getter(case_payload)),
            _normalize_case_text(text_field_getter(case_payload, "test_input")),
            _normalize_case_text(text_field_getter(case_payload, "expected_result")),
        ]
    )
