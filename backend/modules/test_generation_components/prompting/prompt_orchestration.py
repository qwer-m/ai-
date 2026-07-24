import json
from typing import Any, Callable

from ..postprocess.case_access import (
    case_id as case_access_id,
    case_priority,
    case_step_lines,
    case_text_field,
    case_text_list_field,
)
from .prompt_orchestration_split_helpers import (
    build_closed_loop_base_prompt,
)
from ..postprocess.streaming_review_semantics import (
    compact_review_contract_context,
    compact_structured_case_risk,
    compact_verified_case_semantics,
)
from .case_semantic_schema import render_case_semantic_output_contract

def build_append_closed_loop_coverage_instruction(
    *,
    existing_cases: list[dict[str, Any]],
    requirement: str,
    expected_count: int,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
) -> str:
    """追加生成只声明全局增量原则，不从历史分布制造模块配额。"""
    _ = requirement, infer_case_kind_fn
    return f"""
# --- APPEND MODE: GLOBAL INCREMENT ---
Current Case Count: {len(existing_cases)}
Suggested Total (reference only): {expected_count}
1. Use the complete verified workflow/module/interaction contract from the main prompt.
2. Generate only evidence-backed behavior not already covered by the historical baseline.
3. Optimize the whole suite; do not lock generation to one module or force equal module counts.
4. Keep the reference count soft. Return [] when no meaningful global increment remains.
"""


def build_supplement_closed_loop_instruction(
    *,
    all_cases: list[dict[str, Any]],
    requirement: str,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
) -> str:
    """Gap 阶段只声明全局增量原则，不从局部分布推导补齐顺序。"""
    _ = requirement, infer_case_kind_fn
    return f"""
Existing candidate count: {len(all_cases)}
1. Evaluate all verified modules, interactions, workflow stages, states, and explicit risks together.
2. Generate evidence-backed candidates for unresolved structured gaps only.
3. Do not prioritize a module merely because it appears first or currently has fewer cases.
4. Do not force equal module counts; Review will choose the globally best suite.
"""


def _dump_cases_for_prompt(cases: list[dict[str, Any]]) -> str:
    """中文注释：完整交付已有用例，避免尾部模块和交互在 Gap 阶段失去去重依据。"""
    payload = [item for item in cases if isinstance(item, dict)]
    if not payload:
        return "[]"
    import json

    return json.dumps(payload, ensure_ascii=False)


def _case_for_review_prompt(case: dict[str, Any]) -> dict[str, Any]:
    """向全局 Review 交付完整公开行为字段和已核验结构化语义。"""

    semantic_summary = compact_verified_case_semantics(case)
    review_case = {
        "id": case_access_id(case),
        "description": case_text_field(case, "description"),
        "test_module": case_text_field(case, "test_module"),
        "priority": case_priority(case, prefer_final=True),
        "preconditions": case_text_list_field(case, "preconditions", split_lines=True),
        "steps": case_step_lines(case),
        "test_input": case_text_field(case, "test_input"),
        "expected_result": case_text_field(case, "expected_result"),
    }
    if any(semantic_summary.values()):
        review_case["_semantic"] = semantic_summary
    structured_risk = compact_structured_case_risk(case)
    if structured_risk:
        review_case["structured_risk"] = structured_risk
    return review_case


def _dump_review_cases_for_prompt(cases: list[dict[str, Any]]) -> str:
    payload = [
        _case_for_review_prompt(item)
        for item in cases
        if isinstance(item, dict)
    ]
    if not payload:
        return "[]"
    import json

    return json.dumps(payload, ensure_ascii=False)


def _build_coverage_gap_text(
    *,
    coverage_result: dict[str, Any] | None,
    missing_rules: list[str] | None,
) -> str:
    """中文注释：把规则级 coverage 缺口渲染成可读文本供 gap prompt 使用。"""
    coverage = dict(coverage_result or {})
    diagnostics = [item for item in (coverage.get("rule_diagnostics") or []) if isinstance(item, dict)]
    lines: list[str] = []
    for item in diagnostics:
        rule_id = str(item.get("rule_id") or "").strip()
        if not rule_id:
            continue
        missing_types = [str(x).strip() for x in (item.get("missing_types") or []) if str(x).strip()]
        covered = bool(item.get("covered"))
        if covered and not missing_types:
            continue
        biz_key = str(item.get("biz_key") or "unknown").strip() or "unknown"
        rule_text = str(item.get("rule_text") or "").strip()
        type_text = ",".join(missing_types) if missing_types else "happy,boundary,exception,risk"
        lines.append(f"- {rule_id} | biz_key={biz_key} | missing_types={type_text} | rule={rule_text}")

    if not lines:
        fallback = [str(item).strip() for item in (missing_rules or []) if str(item).strip()]
        lines = [f"- {rule}" for rule in fallback]

    if not lines:
        return "- （未识别到明确缺口，请优先补边界/异常/风险用例）"
    return "\n".join(lines)


def build_gap_fill_prompt(
    *,
    requirement_context: str,
    existing_cases: list[dict[str, Any]],
    coverage_result: dict[str, Any] | None = None,
    missing_rules: list[str] | None = None,
    missing_workflow_stages: list[dict[str, Any]] | None = None,
    review_contract_context: dict[str, Any] | None = None,
    current_biz_key: str = "",
    pretty_json: bool = False,
) -> str:
    """中文注释：Gap 阶段专用提示词，只补缺失，不重写历史。"""
    requirement_context = str(requirement_context or "").strip() or "(empty)"
    current_biz_key = str(current_biz_key or "").strip() or "unknown"
    missing_rules = [str(item).strip() for item in (missing_rules or []) if str(item).strip()]
    missing_workflow_stages = [
        {
            "workflow_id": str(item.get("workflow_id") or "").strip(),
            "stage_id": str(item.get("stage_id") or "").strip(),
            "stage_kind": str(item.get("stage_kind") or "").strip(),
            "stage_order": int(item.get("stage_order") or 0),
        }
        for item in (missing_workflow_stages or [])
        if isinstance(item, dict)
        and str(item.get("workflow_id") or "").strip()
        and str(item.get("stage_id") or "").strip()
    ]
    coverage_payload = dict(coverage_result or {})
    generic_gap_present = bool(missing_rules) or any(
        isinstance(item, dict)
        and (
            bool(item.get("missing_types"))
            or item.get("covered") is False
        )
        for item in (coverage_payload.get("rule_diagnostics") or [])
    )
    missing_text = _build_coverage_gap_text(
        coverage_result=coverage_payload,
        missing_rules=missing_rules,
    )
    if missing_workflow_stages and not generic_gap_present:
        missing_text = (
            "- Generic rule/type gaps: none. Generate only the exact required "
            "workflow-stage candidates listed below; do not add generic boundary, "
            "exception, or risk cases in this attempt."
        )
    missing_workflow_stage_text = json.dumps(
        missing_workflow_stages,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    existing_cases_text = _dump_cases_for_prompt(existing_cases)
    verified_contract_text = json.dumps(
        compact_review_contract_context(review_contract_context),
        ensure_ascii=False,
    )
    case_semantic_contract = render_case_semantic_output_contract()

    prompt = f"""
You are the QA Architect Agent.
你现在处于 GAP FILL 阶段：只补缺失，不允许重写历史用例。

当前 biz_key: {current_biz_key}

【已核验全局契约（唯一允许引用的模块、交互、工作流、阶段和状态 ID）】
{verified_contract_text}

【Requirement（唯一真源）】
{requirement_context}

【已有用例（不可修改）】
{existing_cases_text}

【待补缺口】
{missing_text}

[EXACT REQUIRED WORKFLOW STAGE GAPS]
{missing_workflow_stage_text}

When this array is non-empty, generate at least one separate executable candidate for each listed stage in stage_order. Copy the exact workflow_id, stage_id, stage_kind and the matching module/interaction/state declarations from the verified contract. Do not infer stage IDs from prose and do not bypass a matching stage with an empty workflow_stage_candidates array.

These are structured coverage gaps, not per-module quotas.

强约束（必须遵守）：
1. 从全局角度同时评估全部待补缺口；输出所有有需求证据、能增加规则/类型/状态/风险覆盖的候选，交由后续统一 Review 选择。
2. 不要求一条缺口固定对应一条用例，也不按模块或 biz_key 等额分配；允许一条用例覆盖多个相关缺口，也允许一个复杂缺口由多条用例覆盖。
3. 新用例的 description、steps、expected_result 必须包含对应缺口的业务对象、动作和可断言结果，不能只写泛化描述。
4. 只允许生成新增候选，不能重写已有用例；不得生成与已有用例验证目标完全相同的重复项。
5. `_semantic` 只能引用【已核验全局契约】中的精确 ID。无法由契约和 Requirement 共同支持时，不得猜测模块、交互或阶段。
6. 当前 biz_key 仅是诊断标签，不得用它锁定模块顺序或隐藏其他已核验的全局交互。
7. 不得因为单条候选不能立即清空聚合缺口就放弃它；只要它能增加一个可核验的覆盖维度，就应进入候选集。
8. 若不存在任何有证据的新覆盖候选，直接返回空数组。

输出要求：
- 只返回 JSON 数组，不要输出任何解释。
- priority 仅允许 P0/P1/P2
- 数量为参考结果，不得为了凑数输出低价值或重复用例

{case_semantic_contract}
"""
    if pretty_json:
        prompt += "\n- JSON 请使用 2 空格缩进。\n"
    return prompt


def build_review_select_prompt(
    *,
    requirement_context: str,
    candidate_cases: list[dict[str, Any]],
    target_count: int,
    target_min_count: int | None = None,
    target_max_count: int | None = None,
    coverage_constraints: dict[str, Any] | None = None,
    review_contract_context: dict[str, Any] | None = None,
    current_biz_key: str = "",
    pretty_json: bool = False,
) -> str:
    # 中文注释：本次仅通过 prompt 调整 review_llm 选留导向，强调覆盖优先。
    # 不修改 review_llm 调用、map、gate、quality stop 等其它逻辑。
    requirement_context = str(requirement_context or "").strip() or "(empty)"
    current_biz_key = str(current_biz_key or "").strip() or "unknown"
    target_count = max(1, int(target_count or 1))
    target_min_count = max(1, int(target_min_count or target_count))
    target_max_count = max(target_min_count, int(target_max_count or target_count))
    candidate_text = _dump_review_cases_for_prompt(candidate_cases)
    candidate_ids: list[str] = []
    for item in candidate_cases:
        if not isinstance(item, dict):
            continue
        candidate_id = case_access_id(item)
        if candidate_id:
            candidate_ids.append(candidate_id)
    candidate_ids_text = json.dumps(candidate_ids, ensure_ascii=False)
    review_contract_text = json.dumps(
        compact_review_contract_context(review_contract_context),
        ensure_ascii=False,
    )
    constraints = dict(coverage_constraints or {})
    priority_min = {
        str(key).strip().upper(): int(value)
        for key, value in dict(constraints.get("priority_min") or {}).items()
        if str(key).strip() and int(value or 0) > 0
    }
    scenario_min = {
        str(key).strip().lower(): int(value)
        for key, value in dict(constraints.get("scenario_min") or {}).items()
        if str(key).strip() and int(value or 0) > 0
    }
    domain_min = {
        str(key).strip().lower(): int(value)
        for key, value in dict(constraints.get("domain_min") or {}).items()
        if str(key).strip() and int(value or 0) > 0
    }
    constraint_lines: list[str] = []
    if priority_min:
        constraint_lines.append(
            "- Priority minima: " + ", ".join([f"{key}>={value}" for key, value in sorted(priority_min.items())])
        )
    if scenario_min:
        constraint_lines.append(
            "- Scenario minima: " + ", ".join([f"{key}>={value}" for key, value in sorted(scenario_min.items())])
        )
    if domain_min:
        constraint_lines.append(
            "- Domain minima: " + ", ".join([f"{key}>={value}" for key, value in sorted(domain_min.items())])
        )
    if not constraint_lines:
        constraint_lines.append("- No additional bucket minima.")
    constraints_text = "\n".join(constraint_lines)

    prompt = f"""
You are a Senior QA Review Agent.
You are in REVIEW stage: select a subset only, do NOT rewrite any case content.

Current biz_key: {current_biz_key}

Requirement (single source of truth):
{requirement_context}

Verified requirement contract (global workflow/module/interaction/state objective):
{review_contract_text}

Candidate cases (primary + gap):
{candidate_text}

Primary objective:
- Select a subset that best preserves coverage of missing rules, core rules, and unresolved coverage types/buckets.
- Coverage completeness is more important than aggressive deduplication.
- Before any compression, satisfy coverage/bucket minima. Do NOT trade coverage for brevity.

Selection priorities (in order):
1. Preserve all required workflow stages, critical entry points, and declared cross-module interaction closures.
2. Preserve structured high-risk cases and broad functional/rule coverage.
3. Deduplicate only when two cases contribute exactly the same validation and structured coverage value.
4. Treat the output window and target count as soft references after the whole-suite objectives above are satisfied.
5. Do NOT remove a case only because wording is similar if its module, interaction, workflow stage, state, risk, or rule coverage differs.
6. Soft output window: keep between {target_min_count} and {target_max_count} cases whenever possible; {target_count} is not a hard upper bound.

Hard coverage constraints (must satisfy before dedup/compression):
{constraints_text}

Stop condition:
- Stop only when additional retained cases do NOT improve missing-rule coverage, missing-type coverage,
  core-rule preservation, or risk diversity, and are clearly repetitive/low-value.

Output constraints:
- Return JSON only, no prose.
- Do NOT rewrite fields.
- Output schema (MANDATORY):
  {{
    "kept_case_ids": ["TC-001", "TC-002"],
    "dropped": [
      {{"case_id": "TC-010", "reason": "duplicate"}},
      {{"case_id": "TC-011", "reason": "coverage_redundant"}}
    ]
  }}
- `kept_case_ids` and `dropped[*].case_id` MUST come from this candidate list:
{candidate_ids_text}
- Allowed reasons (canonical only):
  ["coverage_redundant","duplicate","low_value","coverage_protected_omitted","high_signal_omitted","selection_tradeoff_omitted","fallback_unspecified"]
- Do not return legacy array mode. Always return the object schema above.
"""
    if pretty_json:
        prompt += "\n- JSON should use 2-space indentation.\n"
    return prompt
