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
) -> str:
    next_case_id = int(current_id) + int(generated_in_batch)
    return f"""
                {base_prompt}

                {coverage_instruction}

                {history_context}

                {coverage_plan_lite}

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
                - Generate UI/display verification only after the main workflow and state-transition cases for that module.
                - Treat visual/layout checks as the UI/display independent suite, not as the first main-chain case.
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

                If no meaningful incremental cases remain, return [].

                Return ONLY the JSON array.
                """
