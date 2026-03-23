from modules.knowledge_base_components.retrieval_retry import calc_low_relevance


def test_calc_low_relevance_prefers_fusion_final_and_relaxes_on_strong_hits():
    warning, reason, info = calc_low_relevance(
        [
            {
                # 不给 final_score，验证会优先使用 fusion_score，而非 vector_score。
                "fusion_score": 0.52,
                "vector_score": 0.08,
                "title_score": 0.78,
                "keyword_score": 0.72,
                "title_hit_terms": ["流程"],
                "content_hit_terms": ["流程", "校验", "权限"],
                "chunk_text": "流程校验包含权限、额度和状态检查。",
            },
            {
                "final_score": 0.41,
                "title_score": 0.10,
                "keyword_score": 0.15,
                "chunk_text": "补充说明。",
            },
        ]
    )

    assert warning is True
    assert "low_relevance_score" in reason
    assert float(info.get("top1_score") or 0.0) == 0.52
    assert bool(info.get("title_keyword_relaxed")) is True
    assert float(info.get("effective_top1_threshold") or 0.0) < float(info.get("top1_threshold") or 0.0)
    assert float(info.get("effective_topk_avg_threshold") or 0.0) < float(info.get("topk_avg_threshold") or 0.0)
