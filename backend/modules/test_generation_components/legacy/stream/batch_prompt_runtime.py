from __future__ import annotations


def build_recent_history_context(history_summaries: list[str]) -> str:
    if not history_summaries:
        return ""
    recent_history = history_summaries[-50:]
    history_list_str = "\n".join([f"- {item}" for item in recent_history])
    return f"""
                    IMPORTANT - DE-DUPLICATION INSTRUCTION:
                    The following test scenarios have ALREADY been generated.
                    DO NOT generate duplicates or very similar cases to these:
                    {history_list_str}

                    Focus on NEW scenarios in the current module closed loop first.
                    """


def append_history_to_testcase_context(testcase_context: str, history_summaries: list[str]) -> str:
    if not history_summaries:
        return testcase_context
    recent_history_style = "\n".join(history_summaries[-50:])
    return f"{testcase_context}\n\n[本轮已生成摘要]\n{recent_history_style}"


def build_stream_batch_system_prompt(
    *,
    base_prompt: str,
    coverage_instruction: str,
    history_context: str,
    coverage_plan_lite: str,
    side_suite_order: str,
    batch_index: int,
    total_batches: int,
    current_id: int,
    generated_in_batch: int,
    need: int,
    shard_instruction: str = "",
    module_instruction: str = "",
) -> str:
    next_case_id = int(current_id) + int(generated_in_batch)
    return f"""
                {base_prompt}

                {coverage_instruction}

                {history_context}

                {coverage_plan_lite}

                {shard_instruction}

                {module_instruction}

                # --- GENERATION STRATEGY ---
                1. ANALYZE the User's Requirement (provided in the next message) step-by-step.
                2. IDENTIFY the specific functionality, logic, and constraints in the User's Requirement.
                3. APPLY Testing Techniques:
                   - Equivalence Partitioning: Identify valid/invalid inputs.
                   - Boundary Value Analysis: Test edges (min, max, null, overflow).
                   - Scenario Testing: Cover happy paths and error paths.
                4. GENERATE new test cases that target the User's Requirement.
                   - Do NOT generate generic cases unrelated to the specific logic.
                   - Do NOT repeat test cases found in Reference Knowledge unless necessary.
                5. FINAL CHECK: Ensure the first test case corresponds to the *first step* of the User's Requirement (e.g., Entry Point).

                # --- VISUAL/LAYOUT TESTING RULE ---
                If the Requirement mentions UI layout, styles, or specific visual elements:
                - Workflow entries and state-changing UI interactions stay in the main workflow.
                - Missing or non-clickable core entries are workflow-blocking failures, not presentation-only UI issues.
                - Generate presentation-only visual/layout verification after workflow and state-transition cases for that module.
                - Treat presentation-only visual/layout checks as the UI/display independent suite.
                - Keep independent suites in this order: {side_suite_order}.
                - Verify the visual appearance matches the description/image.
                - Do NOT skip visual details just because they are not "functional actions".

                BATCH GENERATION INSTRUCTION (quality-first):
                This is batch {batch_index + 1} of {total_batches}.
                Start the Test Case IDs from {next_case_id} (e.g., TC-{next_case_id:03d}).
                Reference count: about {need} cases. This is NOT a quota.
                Generate fewer cases if additional cases would be:
                - duplicate of existing validation goals
                - weakly grounded in requirement evidence
                - non-assertable
                - only generic UI/database/permission checks
                - not adding new module, flow, rule, or scenario coverage

                Every case must pass these gates:
                1. It targets a specific business rule or workflow step.
                2. Its expected_result is concrete and verifiable.
                3. It adds new coverage compared with existing cases.
                4. Keep closed-loop continuity in current module first; do not jump modules only to match count.
                5. description, test_module, preconditions, steps, test_input, expected_result, and priority must all be present and non-empty.
                6. Do not use placeholders such as "as configured", "符合预期", "执行成功", TBD, or TODO.
                7. Derive every field from the current Requirement; the backend will reject incomplete cases instead of filling them with templates.

                If no meaningful incremental cases remain, return [].

                Return ONLY the JSON array.
                """


def build_functional_module_instruction(
    *,
    project_profile: dict,
    phase: dict,
) -> str:
    architecture = dict(project_profile.get("functional_architecture") or {})
    modules = [
        dict(item)
        for item in (architecture.get("functional_modules") or [])
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    ]
    allowed = [str(item.get("module_name") or "").strip() for item in modules]
    if not allowed or not isinstance(phase, dict):
        return ""
    allowed_text = ", ".join(allowed)
    if str(phase.get("phase") or "") == "cross_module":
        interaction_lines = []
        for item in (phase.get("interactions") or [])[:16]:
            if not isinstance(item, dict):
                continue
            interaction_lines.append(
                f"- {str(item.get('source_module') or '')} -> {str(item.get('target_module') or '')}: "
                f"{str(item.get('trigger') or '')}"
            )
        evidence = "\n".join(interaction_lines) if interaction_lines else "- Derive only interactions explicitly stated in Requirement."
        return f"""
# --- FUNCTIONAL MODULE PHASE: CROSS-MODULE INTERACTIONS ---
ALLOWED test_module VALUES: {allowed_text}
{evidence}
RULES:
1. Generate only evidence-grounded interactions between allowed modules.
2. test_module must be the primary affected module from ALLOWED values; never output '跨模块交互' as a module.
3. Include source entry/state, trigger, transferred state/data, and observable target result.
4. If no explicit meaningful interaction remains, return [].
"""
    target = str(phase.get("module_name") or "").strip()
    features = [str(item).strip() for item in (phase.get("features") or []) if str(item).strip()]
    feature_text = " | ".join(features[:8]) if features else "Use explicit Requirement evidence for this module."
    return f"""
# --- FUNCTIONAL MODULE PHASE: MODULE-INTERNAL CLOSED LOOP ---
ALLOWED test_module VALUES: {allowed_text}
CURRENT TARGET MODULE: {target}
EXPLICIT MODULE FEATURES: {feature_text}
RULES:
1. Generate cases ONLY for CURRENT TARGET MODULE and set every test_module exactly to '{target}'.
2. Pages, buttons, actions, states, risks, and test types are features/aspects, not new modules.
3. Cover workflow entries and state-changing UI interactions in business-flow order.
4. Treat missing or non-clickable core entries as workflow-blocking failures; priority follows business impact.
5. Put only presentation-only visual checks after functional, state, boundary, exception, and permission behavior.
6. Do not generate out-of-scope or future-phase features.
"""
