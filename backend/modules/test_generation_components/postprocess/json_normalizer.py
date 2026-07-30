"""JSON normalization helpers for test generation postprocessing."""

from __future__ import annotations

import re
from typing import Any

from .case_access import case_value
from .json_validator import (
    _CASE_KIND_ORDER,
    extract_module_order_from_cases,
    infer_case_kind,
    reorder_cases_by_closed_loop,
)
from .module_contract import FUNCTIONAL_PHASE_FIELDS
from ..control.semantic_contract import (
    normalize_case_semantic,
    validate_case_semantic_contract,
)


def normalize_json_structure(
    data: Any,
    *,
    require_case_semantic_contract: bool = False,
    requirement_semantic_contract: dict[str, Any] | None = None,
    semantic_rejections: list[dict[str, Any]] | None = None,
    semantic_source_stage: str = "generated_case",
) -> Any:
    """Normalize generated case structures without changing non-list inputs."""
    if not isinstance(data, list):
        return data

    normalized = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue

        def normalize_list(v: Any) -> list[str]:
            if v is None:
                return []
            if isinstance(v, list):
                out: list[str] = []
                for x in v:
                    if isinstance(x, dict):
                        val = (
                            x.get("text")
                            or x.get("desc")
                            or x.get("step")
                            or x.get("name")
                            or x.get("内容")
                            or x.get("描述")
                            or x.get("步骤")
                        )
                        if val is not None:
                            out.append(str(val).strip())
                        else:
                            out.append(str(x).strip())
                    else:
                        out.append(str(x).strip())
                return [s for s in out if s]
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return []
                if "\n" in s:
                    return [line.strip() for line in s.splitlines() if line.strip()]
                if "，" in s:
                    return [seg.strip() for seg in s.split("，") if seg.strip()]
                if ";" in s:
                    return [seg.strip() for seg in s.split(";") if seg.strip()]
                return [s]
            return [str(v).strip()] if str(v).strip() else []

        raw_id = case_value(item, "id", None)
        raw_id_s = str(raw_id).strip() if raw_id is not None else ""
        if re.fullmatch(r"TC-\d{3,}", raw_id_s):
            final_id = raw_id_s
        elif re.fullmatch(r"\d+", raw_id_s):
            final_id = f"TC-{int(raw_id_s):03d}"
        else:
            final_id = f"TC-{i + 1:03d}"

        description = str(case_value(item, "description", "") or "").strip()
        test_module = str(case_value(item, "test_module", "") or "").strip()
        preconditions = normalize_list(case_value(item, "preconditions", []))
        steps = normalize_list(case_value(item, "steps", []))
        test_input = str(case_value(item, "test_input", "") or "").strip()
        expected_result = str(case_value(item, "expected_result", "") or "").strip()
        priority = str(case_value(item, "priority", "") or "").strip()
        p = priority.upper()
        if p not in ["P0", "P1", "P2"]:
            if p in ["高", "HIGH"]:
                p = "P0"
            elif p in ["中", "MEDIUM"]:
                p = "P1"
            elif p in ["低", "LOW"]:
                p = "P2"
            else:
                p = ""

        normalized_case = {
            "id": final_id,
            "description": description,
            "test_module": test_module,
            "preconditions": preconditions,
            "steps": steps,
            "test_input": test_input,
            "expected_result": expected_result,
            "priority": p,
        }
        for field in FUNCTIONAL_PHASE_FIELDS:
            value = item.get(field)
            if value is not None and value != "" and value != []:
                normalized_case[field] = value
        case_text = "\n".join(
            [
                description,
                test_module,
                *preconditions,
                *steps,
                test_input,
                expected_result,
            ]
        )
        semantic_validation = validate_case_semantic_contract(
            item.get("_semantic"),
            case_text=case_text,
            case_test_module=test_module,
            requirement_contract=requirement_semantic_contract,
        )
        if require_case_semantic_contract and not semantic_validation.get("valid"):
            if semantic_rejections is not None:
                semantic_rejections.append(
                    {
                        "source_stage": str(semantic_source_stage or "generated_case"),
                        "case_index": int(i + 1),
                        "case_id": final_id,
                        "description": description[:160],
                        "rejection_reasons": list(
                            semantic_validation.get("rejection_reasons") or []
                        ),
                        "rejected_semantic_items": list(
                            semantic_validation.get("rejected_semantic_items") or []
                        )[:16],
                    }
                )
            continue
        semantic = (
            semantic_validation.get("semantic")
            if require_case_semantic_contract
            else normalize_case_semantic(item.get("_semantic"), case_text=case_text)
        )
        if semantic:
            normalized_case["_semantic"] = semantic
        normalized.append(normalized_case)

    return normalized
