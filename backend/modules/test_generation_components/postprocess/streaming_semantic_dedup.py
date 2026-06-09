from __future__ import annotations

from typing import Any

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
    candidate_tokens = semantic_tokenize(
        " ".join(
            [
                str(candidate.get("description") or ""),
                str(candidate.get("expected_result") or ""),
                str(candidate.get("test_input") or ""),
                " ".join([str(x) for x in (candidate.get("steps") or []) if str(x).strip()])
                if isinstance(candidate.get("steps"), list)
                else "",
            ]
        )
    )
    existed_tokens = semantic_tokenize(
        " ".join(
            [
                str(existed.get("description") or ""),
                str(existed.get("expected_result") or ""),
                str(existed.get("test_input") or ""),
                " ".join([str(x) for x in (existed.get("steps") or []) if str(x).strip()])
                if isinstance(existed.get("steps"), list)
                else "",
            ]
        )
    )
    return jaccard_similarity(candidate_tokens, existed_tokens) >= float(threshold)


def semantic_deduplicate_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    entries = [item for item in cases if isinstance(item, dict)]
    entries.sort(
        key=lambda item: (
            -int(case_priority_score(item)),
            -int(len([x for x in (item.get("steps") or []) if str(x).strip()]) if isinstance(item.get("steps"), list) else 0),
            -int(len(str(item.get("description") or ""))),
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
