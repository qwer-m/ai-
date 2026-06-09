from __future__ import annotations

import json
import re
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from core.db.models import TestGenerationComparison
from modules.test_generation_components.repositories.comparison_repository import (
    TestGenerationComparisonRepository,
)


def normalize_case_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    text = "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    except Exception:
        collapsed = re.sub(r"\s+", "", text)
        return collapsed or text


def infer_compare_filename(modified_text: str) -> str:
    text = (modified_text or "").strip()
    lower = text.lower()
    if "<table" in lower and "</table>" in lower:
        return "history_compare.xlsx"
    first_line = text.splitlines()[0] if text else ""
    if "," in first_line and len(text.splitlines()) >= 2:
        return "history_compare.csv"
    if re.search(r"\[image content:|\[image ocr failed:", lower):
        return "history_compare_image.txt"
    return "history_compare.txt"


def extract_history_title(requirement_text: str) -> str:
    text = (requirement_text or "").strip()
    if not text:
        return "未命名需求"
    first = re.split(r"[\n|]", text)[0].strip()
    return first or "未命名需求"


def build_history_key(requirement_text: str) -> str:
    title = extract_history_title(requirement_text)
    return re.sub(r"\s+", " ", title).strip().lower()


def find_matching_comparison(
    *,
    project_id: int,
    user_id: int,
    generated_result: str,
    generation_created_at: datetime | None,
    db: Session,
) -> TestGenerationComparison | None:
    raw_generated_compact = re.sub(r"\s+", "", (generated_result or ""))
    normalized_generated = normalize_case_text(generated_result or "")
    compact_generated = re.sub(r"\s+", "", normalized_generated)
    candidates = TestGenerationComparisonRepository(db).list_recent_project_comparisons(
        project_id=project_id,
        user_id=user_id,
        limit=200,
    )
    if not candidates:
        return None

    for item in candidates:
        raw_candidate_compact = re.sub(r"\s+", "", (item.generated_test_case or ""))
        if raw_generated_compact and raw_candidate_compact:
            min_len = min(len(raw_generated_compact), len(raw_candidate_compact))
            max_len = max(len(raw_generated_compact), len(raw_candidate_compact))
            if min_len >= 1000 and max_len > 0:
                if raw_generated_compact[:1000] == raw_candidate_compact[:1000]:
                    return item
                shorter = (
                    raw_generated_compact
                    if len(raw_generated_compact) <= len(raw_candidate_compact)
                    else raw_candidate_compact
                )
                longer = (
                    raw_candidate_compact
                    if len(raw_generated_compact) <= len(raw_candidate_compact)
                    else raw_generated_compact
                )
                if len(shorter) >= 1000 and shorter in longer:
                    return item

    for item in candidates:
        candidate_normalized = normalize_case_text(item.generated_test_case or "")
        if candidate_normalized == normalized_generated:
            return item

        compact_candidate = re.sub(r"\s+", "", candidate_normalized)
        if compact_generated and compact_candidate:
            shorter = compact_generated if len(compact_generated) <= len(compact_candidate) else compact_candidate
            longer = compact_candidate if len(compact_generated) <= len(compact_candidate) else compact_generated
            if len(shorter) >= 1000 and shorter in longer:
                return item

    for item in candidates:
        candidate_normalized = normalize_case_text(item.generated_test_case or "")
        compact_candidate = re.sub(r"\s+", "", candidate_normalized)
        if compact_generated and compact_candidate:
            min_len = min(len(compact_generated), len(compact_candidate))
            max_len = max(len(compact_generated), len(compact_candidate))
            if min_len >= 1000 and max_len > 0 and (min_len / max_len) >= 0.8:
                if compact_generated[:1000] == compact_candidate[:1000]:
                    return item

    head = normalized_generated[:5000]
    best_item: TestGenerationComparison | None = None
    best_score = 0.0
    if head:
        for item in candidates:
            candidate_head = normalize_case_text(item.generated_test_case or "")[:5000]
            if not candidate_head:
                continue
            score = SequenceMatcher(None, head, candidate_head).ratio()
            if score > best_score:
                best_score = score
                best_item = item
        if best_item is not None and best_score >= 0.9:
            return best_item

    if generation_created_at is not None:
        nearest = sorted(
            candidates,
            key=lambda x: abs(((x.created_at or generation_created_at) - generation_created_at).total_seconds()),
        )
        if nearest:
            first = nearest[0]
            delta = abs(((first.created_at or generation_created_at) - generation_created_at).total_seconds())
            if delta <= 24 * 3600:
                return first
    return None
