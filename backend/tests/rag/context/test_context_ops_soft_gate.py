import modules.knowledge_base_components.context.context_ops as context_ops


def test_context_ops_low_relevance_soft_gate_keeps_context(monkeypatch):
    chunks = [
        {
            "chunk_id": "c1",
            "doc_id": 1,
            "filename": "doc1.md",
            "doc_type": "rule",
            "chunk_text": "流程第一步先校验用户角色权限与额度条件。",
            "final_score": 0.34,
            "fusion_score": 0.34,
            "title_score": 0.72,
            "keyword_score": 0.68,
            "title_hit_terms": ["流程"],
            "content_hit_terms": ["流程", "权限", "额度"],
        },
        {
            "chunk_id": "c2",
            "doc_id": 2,
            "filename": "doc2.md",
            "doc_type": "requirement",
            "chunk_text": "流程第二步提交审批并记录状态流转。",
            "final_score": 0.31,
            "fusion_score": 0.31,
            "title_score": 0.60,
            "keyword_score": 0.56,
            "title_hit_terms": ["审批"],
            "content_hit_terms": ["审批", "状态", "流转"],
        },
        {
            "chunk_id": "c3",
            "doc_id": 1,
            "filename": "doc1.md",
            "doc_type": "rule",
            "chunk_text": "流程第三步补充异常回滚策略与通知机制。",
            "final_score": 0.29,
            "fusion_score": 0.29,
            "title_score": 0.40,
            "keyword_score": 0.45,
            "content_hit_terms": ["异常", "回滚", "通知"],
        },
    ]

    monkeypatch.setattr(
        context_ops,
        "recall_chunks",
        lambda **kwargs: {
            "chunks": list(chunks),
            "debug": {
                "original_query": kwargs.get("question") or "",
                "rewrite_queries": [],
                "lane_counts": {"original_raw": len(chunks)},
                "lane_reasons": {"original_raw": "ok"},
                "lane_topk": {"original_raw": 10},
                "merged_count": len(chunks),
                "deduped_count": len(chunks),
            },
        },
    )
    monkeypatch.setattr(context_ops, "rerank_chunks", lambda **kwargs: list(chunks))

    result = context_ops.get_relevant_context_impl(
        module=None,
        query="开户流程和审批规则有哪些",
        project_id=1,
        limit=3,
        db=None,
        user_id=None,
        debug=True,
        max_tokens=1800,
        retrieval_options={"min_docs": 1, "max_chunks_per_doc": 6},
    )

    assert isinstance(result, dict)
    assert str(result.get("context") or "").strip()

    debug = result.get("debug") or {}
    assert debug.get("low_relevance_warning") is True
    assert int(debug.get("gate_before_candidate_count") or 0) >= 1
    assert int(debug.get("gate_after_candidate_count") or 0) >= 1
    assert bool(debug.get("doc_coverage_triggered")) is True

    per_doc_counts = debug.get("per_doc_selected_chunk_counts") or {}
    assert per_doc_counts
    assert max(int(v) for v in per_doc_counts.values()) <= 3
    assert int((debug.get("retrieval_tuning") or {}).get("min_docs_effective") or 0) >= 2
