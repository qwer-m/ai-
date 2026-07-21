import json
from typing import Any

from ..postprocess.streaming_execution_plan_ordering import execution_side_suite_order_text


def _render_structured_strategy(plan: dict[str, Any]) -> str:
    """只透传元分析已有的结构化策略，不在提示词层补默认配额。"""
    payload = {
        key: plan.get(key)
        for key in (
            "system_type",
            "impact_scope",
            "complexity",
            "coverage_targets",
        )
        if plan.get(key) not in (None, "", [], {})
    }
    if not payload:
        return "(none; derive coverage from confirmed requirement/control evidence)"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)

def build_closed_loop_base_prompt(
    strategy_plan: dict[str, Any] | None,
    *,
    requirement_context: str = "",
    requirement_semantics_context: str = "",
    testcase_context: str = "",
    supplement_context: str = "",
    control_context: str = "",
    current_biz_key: str = "",
    doc_type: str = "requirement",
    pretty_json: bool = False,
) -> str:
    """Layered workflow-first base prompt used by JSON/stream generation."""
    plan = strategy_plan or {}
    structured_strategy = _render_structured_strategy(plan)
    requirement_context = (requirement_context or "").strip() or "(empty)"
    requirement_semantics_context = (requirement_semantics_context or "").strip() or "(empty)"
    testcase_context = (testcase_context or "").strip() or "(empty)"
    supplement_context = (supplement_context or "").strip() or "(empty)"
    control_context = (control_context or "").strip()
    current_biz_key = (current_biz_key or "").strip() or "unknown"
    side_suite_order = execution_side_suite_order_text()
    control_block = ""
    if control_context and control_context.lower() != "(empty)":
        control_block = f"""

[GENERATION OBJECTIVE - MUST FOLLOW]
Primary objective: prioritize core business workflow coverage and high-risk behavior paths over UI-detail coverage.

{control_context}

OBJECTIVE RULES:
1. Prioritize the complete workflow explicitly supported by requirement/control evidence; do not invent missing domain stages.
2. Prioritize key state transitions (loading, switching, interruption, recovery, exception).
3. Prioritize cross-page / cross-module behavior chains.
4. Prioritize real user paths over single-widget checks.
5. If a case cannot map to business workflow or state transition, lower its priority.

GENERATION STRATEGY (STRICT ORDER):
1) Workflow-contract cases first:
- Follow explicit workflow blueprints, flow order, module interactions, and confirmed state transitions.
- Keep entry, action, resulting state, and downstream visibility in an executable order when the evidence defines them.
2) Rule/state cases second:
- Cover distinct confirmed rules and observable before/after state differences.
- Treat explicitly documented scope, role, version, page, and data boundaries as inputs instead of inventing a generic matrix.
3) Evidence-backed exception/risk cases third:
- Generate only failures, recovery behavior, validation boundaries, and risks supported by requirement/control evidence.
- Do not inject a standard exception catalog into every module.
4) Presentation-only UI/display cases last:
- Workflow entries, clickable controls, navigation, and state-changing UI interactions belong to the business workflow.
- Missing, blocked, invisible, or non-clickable core entries are workflow-blocking failures; priority follows business impact.
- Style/color/copy/spacing checks that do not affect workflow are supplemental only.
- Do not over-generate presentation-only cases.

PRIORITY ASSIGNMENT (BUSINESS IMPACT FIRST):
- P1: core workflow break, wrong page jump, requirement-backed business-path abnormality, state/data errors affecting usability
- P2: UI display issues, copy issues, style issues, non-core interaction issues
- Presentation-only cases must NOT be P1 unless the presentation defect blocks the core workflow.
- If any issue meets global P0 criteria in this prompt, escalate it to P0.

NEGATIVE CONSTRAINTS (WEAK, FOR DEDUP ONLY):
- Reduce repeated non-empty validation cases
- Reduce pure UI-display validation cases
- Reduce copy/color/style-only cases
- Reduce micro interactions unrelated to business workflow
- These constraints must NOT override workflow/state generation.

COVERAGE BALANCE:
- Follow explicit coverage targets, rule quotas, functional-module contracts, and workflow blueprints from the structured strategy/control context.
- Allocate cases by distinct confirmed rules, observable state transitions, and business impact; do not apply an implicit percentage or per-scenario quota.
- Remove semantic duplicates while preserving cases with materially different preconditions, actions, state transitions, or expected outcomes.

FINAL CHECK BEFORE OUTPUT:
1. Is the full main workflow covered?
2. Is there cross-page or cross-module behavior?
3. Are state transitions or data changes covered?
4. Are there obvious duplicate cases?
5. Are presentation-only UI/display cases too many?
If not satisfied, revise before output.
"""
    base_prompt = f"""You are the QA Architect Agent.
Generate test cases in STRICT JSON format.

# ================================
# 🧠 CONTEXT (STRUCTURED INPUT)
# ================================

【需求规则（Requirement - SINGLE SOURCE OF TRUTH）】
{requirement_context}

【需求语义结构化（Requirement Semantics - CONFIRMED vs PENDING）】
{requirement_semantics_context}

SEMANTIC RULES (MANDATORY):
1. Generate formal test cases only from Confirmed Facts, Reuse Declarations, and Hard Flow Constraints.
2. Pending / Open Questions are NOT confirmed behavior.
3. For ordinary requirement documents, do NOT output formal test cases that depend on Pending / Open Questions.
4. Use "[Pending Confirmation]" only when the document type is explicitly incomplete and the incomplete-document rules below require inferred assumptions.
5. Reuse Declarations must trigger reuse-adaptation checks, not only generic workflow checks.
{control_block}

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
# 🧩 STRATEGY HIERARCHY
# ================================

STRATEGY ORDER (must obey top-down):

S0 - Workflow / Closed-loop (HIGHEST PRIORITY):
1. 按用户旅程、页面、业务节点顺序拆分模块
2. 当前模块未闭环时，禁止跳到下一个模块
3. 对 control context 明确列出的功能模块，覆盖其显式功能、规则和状态转换；边界、异常和非功能风险仅在需求或控制证据支持时生成，不强制所有模块套用同一分类清单。
4. 必须先模块内闭环，再考虑全局
5. JSON 数组顺序就是执行计划顺序，不是普通列表排序。

EXECUTION ORDER CONTRACT (MANDATORY):
1. If WORKFLOW BLUEPRINTS are present in the control context, generate the first main-chain cases in the exact blueprint step order.
2. Main-chain cases must appear before independent suites and must preserve state transition order: source_state -> target_state.
3. After the main chain, output independent suites in this order:
   {side_suite_order}.
4. Do not interleave UI/display, boundary, or exception cases into the main chain unless they advance the confirmed workflow state.
5. When a case belongs to an independent suite, keep it after the main workflow and write preconditions that prepare its own state.

--------------------------------

S1 - Quality Rules (SECONDARY):

MANDATORY TEST CASE DESIGN PRINCIPLES (5 Pillars):
1. Comprehensive Coverage
2. Clear Purpose（每个用例仅一个验证点）
3. Minimal Workload（避免重复）
4. Clear Classification
5. Independence（零耦合）

说明：
- 同一模块内不同验证点 ≠ 重复 ≠ 耦合
- Focus on high-quality, non-redundant, high-information-gain test cases.
- Do NOT generate cases solely to increase count.
- Avoid low-value, repetitive, or speculative scenarios.
- If additional cases would be redundant or low-value, STOP generating further cases.

--------------------------------

S2 - Global Guidance (REFERENCE ONLY):

STRUCTURED STRATEGY PLAN:
{structured_strategy}

Case Volume Guidance (reference only):
- Use an explicit expected_count or structured coverage target when one is provided by the request/control data.
- If no explicit target exists, determine scope from confirmed rules, functional modules, module interactions, and workflow blueprints; do not assume a default range.
- Do not introduce repeated, low-value, or speculative cases merely to increase count.
- Quality, business closure, and evidence-backed coverage take precedence over quantity.

⚠️ 若与 S0/S1 冲突，必须优先质量与闭环，不得为数量让步

# ================================
# 🎯 TEST CASE PRIORITY CLASSIFICATION (MANDATORY)
# ================================

P0:
- release blocking
- core workflow broken
- severe data/security/permission risk

P1:
- important but non-blocking
- important secondary flows
- obvious defects but system remains usable

P2:
- supplemental / edge / low-risk / long-tail

Constraints:
- Coverage != P0
- Importance != P0
- Workflow closed-loop != P0
- P0 must be a minority
- If uncertain between P0 and P1, prefer P1
- If uncertain between P1 and P2, prefer P2 unless impact is obvious

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

1. Ensure coverage of all explicitly stated Requirement rules.
   Do NOT infer, invent, or extrapolate rules beyond the provided context.
   If a scenario is not supported by explicit evidence, do NOT generate a test case for it.
2. 每个已开始的功能模块是否覆盖其显式规则和可观察状态闭环？是否错误套用了需求未支持的统一分类清单？
3. 是否遗漏关键边界？
4. 是否存在重复验证点？
5. 是否引入了未定义规则？（禁止）
6. 若继续生成是否只会增加重复/低价值内容？若是，必须停止
7. Are all test cases grounded in explicit requirements?
8. Are there redundant or low-value cases that should be removed?
9. Would adding more cases reduce overall quality?
10. If yes → remove or stop, NOT add.

❗若出现杜撰、重复或低信息增益内容 → 必须删除后再输出；允许数量低于参考值

# ================================
# EXPECTED_RESULT ASSERTABILITY (MANDATORY)
# ================================

- expected_result MUST be a concrete, verifiable assertion.
- expected_result MUST describe observable outcome/state/data, not template wording.
- Forbidden placeholder expressions in expected_result include:
  - 正常展示
  - 符合预期
  - 执行成功
  - 返回成功
  - 结果可核对
  - 结果正确
  - 正常 / 成功 / OK (without concrete assertion target)
- If a scenario cannot provide a concrete expected_result assertion, do NOT generate that case.

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
