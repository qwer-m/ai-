from __future__ import annotations

from typing import Any

from .case_access import case_focus_text, case_steps, case_text_field
from .streaming_case_keys import case_priority_score
from .streaming_rule_keys import extract_rule_keys
from .streaming_semantic_text import jaccard_similarity, semantic_signature, semantic_tokenize


def is_semantic_duplicate_case(
    *,
    candidate: dict[str, Any],
    existed: dict[str, Any],
    threshold: float = 0.76,
) -> bool:
    candidate_sig = semantic_signature(candidate, list(extract_rule_keys(candidate)))
    existed_sig = semantic_signature(existed, list(extract_rule_keys(existed)))
    if candidate_sig and candidate_sig == existed_sig:
        return True
    candidate_tokens = semantic_tokenize(case_focus_text(candidate))
    existed_tokens = semantic_tokenize(case_focus_text(existed))
    return jaccard_similarity(candidate_tokens, existed_tokens) >= float(threshold)


def semantic_deduplicate_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    entries = [item for item in cases if isinstance(item, dict)]
    entries.sort(
        key=lambda item: (
            -int(case_priority_score(item)),
            -int(len(case_steps(item))),
            -int(len(case_text_field(item, "description"))),
        )
    )
    kept: list[dict[str, Any]] = []
    dropped = 0
    for candidate in entries:
        if any(is_semantic_duplicate_case(candidate=candidate, existed=existed) for existed in kept):
            dropped += 1
            continue
        kept.append(candidate)
    return kept, dropped
