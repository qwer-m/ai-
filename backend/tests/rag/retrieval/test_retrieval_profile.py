from modules.knowledge_base_components.retrieval.retrieval_profile import build_retrieval_profile


def test_build_retrieval_profile_contains_required_fields():
    profile = build_retrieval_profile(
        question="How to validate balance limit and permission rule?",
        recall_debug={
            "rewrite_queries": ["validate account balance limit"],
            "lane_counts": {"original_raw": 3, "rewrite_summary": 2},
            "lane_reasons": {"original_raw": "ok", "rewrite_summary": "ok"},
            "lane_topk": {"original_raw": 8, "rewrite_summary": 6},
            "merged_count": 6,
            "deduped_count": 4,
        },
        reranked_chunks=[
            {"final_score": 0.93, "query_source": "original", "chunk_source": "raw", "doc_type": "requirement"},
            {"final_score": 0.82, "query_source": "rewrite", "chunk_source": "summary", "doc_type": "rule"},
        ],
        selected_chunks=[
            {"final_score": 0.93, "query_source": "original", "chunk_source": "raw", "doc_type": "requirement"},
        ],
        raw_chunks=[
            {"fusion_score": 0.66, "score": 0.66, "chunk_text": "A", "doc_type": "requirement"},
            {"fusion_score": 0.52, "score": 0.52, "chunk_text": "B", "doc_type": "rule"},
        ],
        compressor_stats={"input_chars": 300, "output_chars": 120},
        attempts=[
            {"retry_triggered": True, "retry_reason": "retryable_lane_reason:network_error"},
            {"retry_triggered": False, "retry_reason": "ok"},
        ],
        final_status="success",
        final_failure_reason="",
    )

    assert profile["profile_version"] == "2.5"
    assert profile["query_type"] in {"fact", "rule", "process", "boundary", "enum", "unknown"}
    assert profile["query_length"] > 0
    assert isinstance(profile["recall_lane_hits"], dict)
    assert isinstance(profile["raw_topk_scores"], list)
    assert isinstance(profile["rerank_top_scores"], list)
    assert profile["final_chunk_count"] == 1
    assert profile["final_doc_type_distribution"]["requirement"] == 1
    assert profile["compressed_before_chars"] == 300
    assert profile["compressed_after_chars"] == 120
    assert "lane_health" in profile
    assert "score_profile" in profile
    assert "selection_profile" in profile
    assert "stability" in profile
    assert profile["stability"]["attempt_count"] == 2
