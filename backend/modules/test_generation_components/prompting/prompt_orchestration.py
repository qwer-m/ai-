from typing import Any, Callable


def build_closed_loop_base_prompt(
    strategy_plan: dict[str, Any] | None,
    *,
    doc_type: str = "requirement",
    pretty_json: bool = False,
) -> str:
    """Layered workflow-first base prompt used by JSON/stream generation."""
    plan = strategy_plan or {}
    ratios = plan.get("suggested_ratios", {}) or {}
    base_prompt = f"""You are the QA Architect Agent.
Generate test cases in STRICT JSON format.

PRIORITY HIERARCHY (must obey top-down):
1. Workflow / Closed-loop
2. Quality Rules
3. Global Ratios / Batch Size / Exact Count

P0 - Workflow / Closed-loop (highest priority):
1. 按用户旅程、页面、业务节点的自然顺序拆分模块。
2. 当前模块未闭环时，必须继续在当前模块内补齐，禁止跳到下一个模块。
3. 每个模块的最低闭环必须包含：
   - Happy Path (main success flow)
   - Core Validation / Boundary
   - Core Exception / Error Handling
   - At least one key risk case (Permission / Security / Performance), if applicable.
4. 不得为了全局比例、batch size、exact count、coverage matrix 而跨模块跳转。
5. 先模块内闭环，再考虑全局 non-functional / integration。

P1 - Quality Rules (secondary, cannot override P0):
MANDATORY TEST CASE DESIGN PRINCIPLES (The 5 Pillars):
1. Comprehensive Coverage: cover P0/P1/P2 with meaningful scenarios.
2. Clear Purpose: one verification goal per case.
3. Minimal Workload (MECE): avoid redundant duplicates.
4. Clear Classification: correct `test_module` and `priority`.
5. Independence (Zero Coupling): each case must be atomic and self-contained.

Critical guardrail:
- In the SAME module, different verification aspects
  (happy / validation / exception / permission-security / performance)
  are NOT duplicates and NOT coupling.
- 同一模块内不同验证点，不算重复，也不算耦合。

Must apply testing techniques:
1. Equivalence Partitioning
2. Boundary Value Analysis

P2 - Global Targets (soft constraints, lowest priority):
1. SYSTEM TYPE: {plan.get('system_type')}
   - Focus Scenarios: {', '.join(plan.get('device_scenarios', []))}
2. IMPACT SCOPE: {plan.get('impact_scope')}
3. Target Ratios (soft):
   - Functional: {int(float(ratios.get('functional', 0.6)) * 100)}%
   - Regression/Integration: {int(float(ratios.get('regression', 0.2)) * 100)}%
   - Non-Functional (Security/Perf): {int(float(ratios.get('non_functional', 0.2)) * 100)}%
4. Focus Areas: {', '.join(plan.get('focus_areas', []))}
If any P2 target conflicts with P0 closed-loop continuity, follow P0 first.
Batch size, ratio, and exact count are only secondary references and must not override module closure continuity.

IMPORTANT LANGUAGE REQUIREMENT:
All content (description, steps, test_input, expected_result, preconditions, test_module) MUST be in Chinese (Simplified).
Do not output English unless it is a specific technical term or variable name from the requirement.

STRICT OUTPUT REQUIREMENTS (MANDATORY):
- Output MUST be a single valid JSON array (no extra text before/after).
- Do NOT output Markdown, code fences, explanations, or batch headers.
- Each array item MUST be a JSON object with EXACT keys:
  id, description, test_module, preconditions, steps, test_input, expected_result, priority
- No additional keys are allowed.
- preconditions and steps MUST be arrays of strings.
- Types:
  - id: string like "TC-001"
  - description: string
  - test_module: string
  - preconditions: array of strings
  - steps: array of strings (non-empty)
  - test_input: string
  - expected_result: string
  - priority: one of "P0","P1","P2"
"""
    if pretty_json:
        base_prompt += "\n- Format the JSON with indentation (2 spaces) and newlines for readability.\n"

    if doc_type == "prototype":
        base_prompt += """
The input is a UI prototype description (from image/prototype extraction).
Focus on UI elements, layout, interactions, and visual states.
If visual layout/style is specified, include UI verification in related module closed loop.
"""
    elif doc_type == "incomplete":
        base_prompt += """
The input is an incomplete requirement document.
1. Generate test cases for clearly defined parts first.
2. Infer reasonable expectations for ambiguous parts using common software standards.
3. Add tag "[Pending Confirmation]" in description for inferred assumptions.
"""
    return base_prompt


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
Target Total: {expected_count}

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
