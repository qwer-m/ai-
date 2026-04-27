import json
from typing import Any, Callable

from modules.test_generation_components.prompting.prompt_orchestration_split_helpers import (
    build_closed_loop_base_prompt,
)

def _build_closed_loop_snapshot(
    cases: list[dict[str, Any]],
    requirement: str,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
) -> tuple[str, list[str], str, str]:
    module_stats: dict[str, dict[str, int]] = {}
    module_order: list[str] = []
    requirement_lower = str(requirement or "").lower()
    risk_keywords = [
        "permission",
        "security",
        "auth",
        "performance",
        "perf",
        "权限",
        "安全",
        "鉴权",
        "性能",
    ]
    risk_required_globally = any(k in requirement_lower for k in risk_keywords)

    for case in cases:
        if not isinstance(case, dict):
            continue
        module = str(case.get("test_module") or "").strip() or "General"
        if module not in module_stats:
            module_order.append(module)
            module_stats[module] = {
                "total": 0,
                "happy": 0,
                "validation": 0,
                "exception": 0,
                "risk": 0,
                "integration": 0,
            }
        module_stats[module]["total"] += 1
        kind = infer_case_kind_fn(case)
        if kind == "happy_path":
            module_stats[module]["happy"] += 1
        elif kind == "validation_boundary":
            module_stats[module]["validation"] += 1
        elif kind == "exception_error":
            module_stats[module]["exception"] += 1
        elif kind in ("permission_security", "performance_stability_compat"):
            module_stats[module]["risk"] += 1
        elif kind == "integration_cross_module":
            module_stats[module]["integration"] += 1

    unfinished_modules: list[str] = []
    unfinished_details: list[str] = []
    for module in module_order:
        stat = module_stats[module]
        missing_parts: list[str] = []
        if stat["happy"] <= 0:
            missing_parts.append("Happy Path")
        if stat["validation"] <= 0:
            missing_parts.append("Validation/Boundary")
        if stat["exception"] <= 0:
            missing_parts.append("Exception/Error")
        if risk_required_globally and stat["risk"] <= 0:
            missing_parts.append("Key Risk(Security/Permission/Performance)")
        if missing_parts:
            unfinished_modules.append(module)
            unfinished_details.append(f"   - {module}: missing -> {', '.join(missing_parts)}")

    stats_str = "\n".join(
        [
            f"   - {m}: total={v['total']}, happy={v['happy']}, validation={v['validation']}, exception={v['exception']}, risk={v['risk']}, integration={v['integration']}"
            for m, v in module_stats.items()
        ]
    )
    unfinished_list = ", ".join(unfinished_modules) if unfinished_modules else "None"
    unfinished_detail_str = (
        "\n".join(unfinished_details)
        if unfinished_details
        else "   - All started modules already satisfy minimum closed-loop."
    )
    current_target_module = unfinished_modules[0] if unfinished_modules else "None"
    return stats_str, unfinished_modules, unfinished_detail_str, current_target_module


def build_append_closed_loop_coverage_instruction(
    *,
    existing_cases: list[dict[str, Any]],
    requirement: str,
    expected_count: int,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
) -> str:
    """Append-mode coverage instruction that prioritizes unfinished module closure."""
    stats_str, unfinished_modules, unfinished_detail_str, current_target_module = _build_closed_loop_snapshot(
        existing_cases,
        requirement,
        infer_case_kind_fn,
    )
    unfinished_list = ", ".join(unfinished_modules) if unfinished_modules else "None"
    return f"""
# --- APPEND / GAP MODE: 未闭环模块优先补齐 ---
Current Case Count: {len(existing_cases)}
Suggested Total (reference only): {expected_count}

MODULE SNAPSHOT:
{stats_str}

UNFINISHED MODULES (journey order, prioritize filling first):
{unfinished_list}
{unfinished_detail_str}

EXECUTION RULES (priority order):
1. If any unfinished module exists, generate cases ONLY for the earliest unfinished module: {current_target_module}.
2. In that module, fill order: Happy -> Validation/Boundary -> Exception/Error -> Key Risk.
3. 未闭环模块优先补齐；先模块内闭环，再全局 non-functional / integration，不要为了 global ratio、matrix gap、batch count 或 exact count 跨模块跳转。
4. Only after all started modules are closed-loop complete, open the next module in journey order.
5. Only after main journey modules are closed-loop complete, add global non-functional or cross-module integration.
6. Same module but different verification aspects are valid and should NOT be treated as duplicate/coupling.
7. If count pressure conflicts with closure continuity, treat count as a secondary constraint and keep the current module closed-loop first.
"""


def build_supplement_closed_loop_instruction(
    *,
    all_cases: list[dict[str, Any]],
    requirement: str,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
) -> str:
    """Supplement-mode closed-loop guidance for append-only 补齐."""
    _, unfinished_modules, unfinished_detail_str, current_target_module = _build_closed_loop_snapshot(
        all_cases,
        requirement,
        infer_case_kind_fn,
    )
    unfinished_list = ", ".join(unfinished_modules) if unfinished_modules else "None"
    return f"""
未闭环模块（按旅程顺序，优先补齐）: {unfinished_list}
{unfinished_detail_str}
当前目标模块: {current_target_module}
Rules:
1. If unfinished modules exist, generate cases ONLY for current target module until closed-loop complete.
2. Current module must be closed-loop before moving to the next module.
3. In-module order: Happy -> Validation/Boundary -> Exception/Error -> Key Risk.
4. 未闭环模块优先补齐；先模块内闭环，再全局 non-functional / integration，不要因为 global matrix gap、ratio balancing 或 exact count 而跨模块补齐。
5. Only after all main modules are closed-loop complete, generate global non-functional/integration supplements.
6. Keep de-dup strict, but do not treat different aspects in the same module as duplicates.
7. 数量缺口只作为次级约束，优先保证当前模块闭环连续性。
"""


def _dump_cases_for_prompt(cases: list[dict[str, Any]], max_items: int = 60) -> str:
    """中文注释：把已有用例压缩为 JSON 文本，控制提示词体积。"""
    payload = [item for item in cases if isinstance(item, dict)][:max(1, int(max_items))]
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
        if len(lines) >= 40:
            break

    if not lines:
        fallback = [str(item).strip() for item in (missing_rules or []) if str(item).strip()]
        lines = [f"- {rule}" for rule in fallback[:40]]

    if not lines:
        return "- （未识别到明确缺口，请优先补边界/异常/风险用例）"
    return "\n".join(lines)


def build_gap_fill_prompt(
    *,
    requirement_context: str,
    existing_cases: list[dict[str, Any]],
    coverage_result: dict[str, Any] | None = None,
    missing_rules: list[str] | None = None,
    current_biz_key: str = "",
    pretty_json: bool = False,
) -> str:
    """中文注释：Gap 阶段专用提示词，只补缺失，不重写历史。"""
    requirement_context = str(requirement_context or "").strip() or "(empty)"
    current_biz_key = str(current_biz_key or "").strip() or "unknown"
    missing_rules = [str(item).strip() for item in (missing_rules or []) if str(item).strip()]
    missing_text = _build_coverage_gap_text(coverage_result=coverage_result, missing_rules=missing_rules)
    existing_cases_text = _dump_cases_for_prompt(existing_cases, max_items=80)

    prompt = f"""
You are the QA Architect Agent.
你现在处于 GAP FILL 阶段：只补缺失，不允许重写历史用例。

当前 biz_key: {current_biz_key}

【Requirement（唯一真源）】
{requirement_context}

【已有用例（不可修改）】
{existing_cases_text}

【待补缺口】
{missing_text}

强约束（必须遵守）：
1. 只允许生成“新增补齐用例”，不能重写已有用例。
2. 只补上述 coverage 缺口，不要新增无关规则。
3. 不允许生成与已有用例验证目标相同的重复项。
4. 不允许跨 biz_key 引入其他业务逻辑。
5. 若信息不足，使用“待确认”，不要自行杜撰。
6. 缺口优先级：exception/risk > boundary > happy。
7. 若继续生成无法带来新的覆盖增益（规则/类型/风险），直接返回空数组。

输出要求：
- 只返回 JSON 数组，不要输出任何解释。
- 字段必须是：id, description, test_module, preconditions, steps, test_input, expected_result, priority
- priority 仅允许 P0/P1/P2
- 数量为参考结果，不得为了凑数输出低价值或重复用例
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
    candidate_text = _dump_cases_for_prompt(candidate_cases, max_items=120)
    candidate_ids = [
        str(item.get("id") or item.get("case_id") or "").strip()
        for item in candidate_cases
        if isinstance(item, dict) and str(item.get("id") or item.get("case_id") or "").strip()
    ]
    candidate_ids = candidate_ids[:200]
    candidate_ids_text = json.dumps(candidate_ids, ensure_ascii=False)
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

Candidate cases (primary + gap):
{candidate_text}

Primary objective:
- Select a subset that best preserves coverage of missing rules, core rules, and unresolved coverage types/buckets.
- Coverage completeness is more important than aggressive deduplication.
- Before any compression, satisfy coverage/bucket minima. Do NOT trade coverage for brevity.

Selection priorities (in order):
1. Preserve cases that help close missing-rule gaps or unresolved coverage types/buckets.
2. Preserve cases that hit core rules or high-risk paths (boundary, exception, state transition, key workflow, failure path).
3. Deduplicate only when two cases contribute essentially the same coverage value.
4. Do NOT remove a case only because wording is similar if it contributes different rule/type coverage.
5. Soft output window: keep between {target_min_count} and {target_max_count} cases whenever possible.
6. Target count reference is {target_count}, but it is NOT a hard upper bound. If coverage is still unresolved, keep more.

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
