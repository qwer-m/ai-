from modules.rag_eval.analysis.debug_display import resolve_debug_display_fields


def test_resolve_debug_display_fields_keep_original_when_context_and_answer_exist():
    context, output, reason = resolve_debug_display_fields(
        context_text="ctx",
        answer="answer",
        debug={},
    )
    assert context == "ctx"
    assert output == "answer"
    assert reason == ""


def test_resolve_debug_display_fields_fallback_context_from_debug_chunks():
    context, output, reason = resolve_debug_display_fields(
        context_text="",
        answer="",
        debug={
            "final_failure_reason": "low_relevance_filtered",
            "diverse_chunks": [
                {
                    "filename": "a.md",
                    "doc_type": "rule",
                    "chunk_text": "A chunk",
                }
            ],
        },
    )
    assert "A chunk" in context
    assert output == "[LLM skipped] low_relevance_filtered"
    assert reason == "low_relevance_filtered"


def test_resolve_debug_display_fields_no_chunks_still_reports_skip_reason():
    context, output, reason = resolve_debug_display_fields(
        context_text="",
        answer="",
        debug={
            "low_relevance_filtered": True,
            "low_relevance_reason": "low_relevance_score(top1=0.30)",
        },
    )
    assert context == ""
    assert output == "[LLM skipped] low_relevance_score(top1=0.30)"
    assert reason == "low_relevance_score(top1=0.30)"
