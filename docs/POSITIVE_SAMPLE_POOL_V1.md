# Positive Sample Pool V1 (Minimal Plan)

## Goal

Upgrade from one-channel negative feedback to two channels:

- Negative channel: avoid known bad patterns
- Positive channel: reuse high-quality patterns

Main path:

`positive sample -> pattern -> control_state.preferred_patterns -> prompt injection`

## Data Contract (Backward Compatible)

Add optional fields on each `priority_sample_pool.samples[]` item:

- `signal_type`: `positive | negative`
- `pattern_usage`: `prefer | avoid`

Fallback rules:

- if `signal_type` missing: default `negative`
- if `pattern_usage` missing:
  - `signal_type=positive` -> `prefer`
  - otherwise -> `avoid`

Example:

```json
{
  "case_id": "TC-901",
  "title": "stable settlement ordering",
  "reason_category": "core_flow",
  "expected_priority": "P1",
  "pattern_summary": "deterministic settlement assertion chain",
  "signal_type": "positive",
  "pattern_usage": "prefer",
  "user_comment": "golden path with deterministic assertion"
}
```

## Control State Extension

`FeedbackControlState` now includes:

- `preferred_patterns: list[str]`

Meaning:

- `forbidden_patterns`: hard block
- `soft_constraints`: negative bias
- `preferred_patterns`: positive bias

## Prompt Injection Strategy

`structured_context` now renders:

- `### PREFERRED PATTERNS`

Execution priority:

1. satisfy `MUST COVER RULES` and `RULE QUOTA`
2. do not violate `FORBIDDEN PATTERNS`
3. under 1/2, prefer `PREFERRED PATTERNS`

Optional A/B strong mode (variant B):

- enforce preferred quota: at least N flow/state cases from preferred patterns
- enforce UI soft cap: UI-only cases <= cap ratio

## Prompt Templates

### Template A: Positive Pattern Extraction

```text
You are a test case structure analyzer. Extract reusable patterns from high-quality samples.

Input:
- sample title
- sample steps/assertions
- sample comments

Output JSON:
{
  "pattern_summary": "max 80 chars, abstract and reusable, no concrete case id",
  "signal_type": "positive",
  "pattern_usage": "prefer",
  "reason_category": "core_flow|exception_path|boundary_condition|state_transition|other",
  "confidence": 0.0-1.0
}

Constraints:
- avoid UI copy/style details
- prefer "step structure + assertion structure + state consistency checks"
- if not confident, return confidence <= 0.4
```

### Template B: Generation-Time Injection

```text
Under MUST COVER RULES and RULE QUOTA, prefer these high-quality patterns:
{{preferred_patterns}}

Rules:
1) Reuse step/assertion structure, not business text copy.
2) For each reused pattern, generate at least 1 case with clear coverage gain.
3) If conflict with FORBIDDEN PATTERNS, FORBIDDEN PATTERNS win.
```

## Code Integration (Already Implemented)

- `backend/modules/testing/priority_sample_pool_store.py`
  - normalize and persist `signal_type/pattern_usage`
  - include both fields in vector index metadata and retrieval output
- `backend/modules/test_generation_components/control/feedback_control_state.py`
  - add `preferred_patterns` field
  - support serialize/deserialize/merge/has_signals
- `backend/modules/test_generation_components/control/build_feedback_control_state.py`
  - map positive samples into `preferred_patterns`
  - add diagnostics: `positive_selected_count`, `negative_selected_count`
- `backend/modules/test_generation_components/prompting/structured_context.py`
  - inject `PREFERRED PATTERNS` in control context
  - expose `preferred_patterns_count` in control summary
  - support A/B strong mode injection (`PREFERRED PATTERN QUOTA (AB)`)

## A/B Config

- `TESTGEN_ENABLE_STRONG_PREFERRED_QUOTA_AB`
  - default: `true`
  - `true` => variant `B`
- `TESTGEN_PREFERRED_FLOW_CASE_QUOTA`
  - default: `2`
  - range: `1..6`
- `TESTGEN_UI_CASE_RATIO_CAP`
  - default: `0.40`
  - range: `0.20..0.60`

Diagnostic fields in `control_summary`:

- `preferred_quota_variant`: `A | B`
- `preferred_flow_case_quota`
- `ui_case_ratio_cap`

## Validation Checklist

1. Save pool with at least one `signal_type=positive` sample.
2. Run generation and verify `control_summary.preferred_patterns_count > 0`.
3. Confirm prompt includes `### PREFERRED PATTERNS`.
4. Spot-check generated cases to verify preferred pattern reuse.
