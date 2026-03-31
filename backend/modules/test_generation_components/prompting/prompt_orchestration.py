from typing import Any, Callable


def build_closed_loop_base_prompt(
    strategy_plan: dict[str, Any] | None,
    *,
    requirement_context: str = "",
    testcase_context: str = "",
    supplement_context: str = "",
    current_biz_key: str = "",
    doc_type: str = "requirement",
    pretty_json: bool = False,
) -> str:
    """Layered workflow-first base prompt used by JSON/stream generation."""
    plan = strategy_plan or {}
    ratios = plan.get("suggested_ratios", {}) or {}
    requirement_context = (requirement_context or "").strip() or "(empty)"
    testcase_context = (testcase_context or "").strip() or "(empty)"
    supplement_context = (supplement_context or "").strip() or "(empty)"
    current_biz_key = (current_biz_key or "").strip() or "unknown"
    base_prompt = f"""You are the QA Architect Agent.
Generate test cases in STRICT JSON format.

# ================================
# 🧠 CONTEXT (STRUCTURED INPUT)
# ================================

【需求规则（Requirement - SINGLE SOURCE OF TRUTH）】
{requirement_context}

【已有测试用例（Testcases - STYLE ONLY）】
{testcase_context}

【补充说明/评估（Supplement - BOUNDARY/DEFECT ONLY）】
{supplement_context}

--------------------------------

USAGE RULES (MANDATORY):
1. Requirement 是唯一业务真源（Single Source of Truth）
2. Testcases 仅用于风格/参考，不可作为新规则来源
3. Supplement 仅用于补充边界/缺陷，不可覆盖 Requirement
4. 若存在冲突：必须以 Requirement 为准
5. 严禁跨 biz_key 混用业务逻辑

# ================================
# 🚨 HALLUCINATION GUARD (STRICT)
# ================================

- 禁止生成 Requirement 中未明确出现的业务规则
- 禁止补充未定义字段 / 流程 / 权限逻辑
- 禁止扩展不存在的业务分支
- 如果信息不足：
  - 使用 "待确认" 标记
  - 不允许自行假设

# ================================
# 🧩 PRIORITY HIERARCHY
# ================================

PRIORITY ORDER (must obey top-down):

P0 - Workflow / Closed-loop (HIGHEST PRIORITY):
1. 按用户旅程、页面、业务节点顺序拆分模块
2. 当前模块未闭环时，禁止跳到下一个模块
3. 每个模块必须包含：
   - Happy Path
   - Boundary / Validation
   - Exception / Error Handling
   - 至少一个关键风险（权限 / 安全 / 性能）
4. 必须先模块内闭环，再考虑全局

--------------------------------

P1 - Quality Rules (SECONDARY):

MANDATORY TEST CASE DESIGN PRINCIPLES (5 Pillars):
1. Comprehensive Coverage
2. Clear Purpose（每个用例仅一个验证点）
3. Minimal Workload（避免重复）
4. Clear Classification
5. Independence（零耦合）

说明：
- 同一模块内不同验证点 ≠ 重复 ≠ 耦合

--------------------------------

P2 - Global Targets (LOW PRIORITY):

SYSTEM TYPE: {plan.get('system_type')}
IMPACT SCOPE: {plan.get('impact_scope')}

Target Ratios (soft):
- Functional: {int(float(ratios.get('functional', 0.6)) * 100)}%
- Regression: {int(float(ratios.get('regression', 0.2)) * 100)}%
- Non-Functional: {int(float(ratios.get('non_functional', 0.2)) * 100)}%

⚠️ 若与 P0 冲突，必须优先 P0

# ================================
# 🔍 TEST DESIGN STRATEGY
# ================================

必须应用：

1. 等价类划分（Equivalence Partitioning）
2. 边界值分析（Boundary Value Analysis）
3. 场景法（Scenario Testing）

# ================================
# 🧠 CONTEXT AWARENESS（关键增强）
# ================================

- 每条 context 具有 doc_type / biz_key / module
- 必须保证：
  1. 用例围绕同一 biz_key 生成
  2. 不得混合不同业务域
  3. 优先覆盖 Requirement 中的规则粒度

# ================================
# 🚧 BUSINESS ISOLATION RULE（MANDATORY）
# ================================

- 当前生成目标 biz_key: {current_biz_key}
- 当前 biz_key 下的 Requirement/Testcases 是唯一业务主依据
- 其他 biz_key 的 testcase/supplement 仅可参考写作风格和表达形式
- 严禁引用其他 biz_key 的业务逻辑、规则、步骤、预期结果
- 若当前 biz_key 信息不足，使用“待确认”标记，不得跨 biz_key 补齐规则

# ================================
# 🧪 FINAL SELF-CHECK（必须执行）
# ================================

在输出前必须自检：

1. 是否覆盖所有 Requirement 规则？
2. 每个模块是否闭环：
   - Happy Path
   - Boundary
   - Exception
3. 是否遗漏关键边界？
4. 是否存在重复验证点？
5. 是否引入了未定义规则？（禁止）

❗如果任何一项不满足 → 必须自行修正后再输出

# ================================
# 📦 OUTPUT REQUIREMENTS（STRICT）
# ================================

- 输出必须是一个 JSON 数组（无任何额外文本）
- 禁止 Markdown / 解释 / 代码块
- 每个元素必须包含 EXACT 字段：

id, description, test_module, preconditions, steps, test_input, expected_result, priority

类型要求：
- id: "TC-001"
- description: string
- test_module: string
- preconditions: array[string]
- steps: array[string]（不能为空）
- test_input: string
- expected_result: string
- priority: "P0" | "P1" | "P2"

--------------------------------

# LANGUAGE REQUIREMENT
所有字段必须使用中文（除技术字段名）

--------------------------------

Return ONLY the JSON array."""
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

输出要求：
- 只返回 JSON 数组，不要输出任何解释。
- 字段必须是：id, description, test_module, preconditions, steps, test_input, expected_result, priority
- priority 仅允许 P0/P1/P2
"""
    if pretty_json:
        prompt += "\n- JSON 请使用 2 空格缩进。\n"
    return prompt


def build_review_select_prompt(
    *,
    requirement_context: str,
    candidate_cases: list[dict[str, Any]],
    target_count: int,
    current_biz_key: str = "",
    pretty_json: bool = False,
) -> str:
    """中文注释：Review 阶段提示词，只筛选不改写内容。"""
    requirement_context = str(requirement_context or "").strip() or "(empty)"
    current_biz_key = str(current_biz_key or "").strip() or "unknown"
    target_count = max(1, int(target_count or 1))
    candidate_text = _dump_cases_for_prompt(candidate_cases, max_items=120)

    prompt = f"""
You are a Senior QA Review Agent.
你现在处于 REVIEW 阶段：只允许筛选，不允许改写。

当前 biz_key: {current_biz_key}

【Requirement（唯一真源）】
{requirement_context}

【候选用例（primary + gap）】
{candidate_text}

任务：
1. 从候选集中筛选出最优 {target_count} 条用例。
2. 去重标准：验证目标相同视为重复，仅保留一条。
3. 优先级策略：P0 > P1 > P2。
4. 优先保留：边界、异常、状态流转相关用例。
5. 严禁修改字段内容，只能选择子集。

输出要求：
- 只返回 JSON 数组，不要输出任何解释。
- 字段必须是：id, description, test_module, preconditions, steps, test_input, expected_result, priority
"""
    if pretty_json:
        prompt += "\n- JSON 请使用 2 空格缩进。\n"
    return prompt
