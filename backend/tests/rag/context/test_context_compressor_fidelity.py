from modules.knowledge_base_components.context.context_compressor import compress_context


def test_compress_context_emits_enhanced_fidelity_report():
    chunks = [
        {
            "chunk_text": "Amount must be between 0-500 and process within 15 days. Status enum: approved/rejected/expired. RequestID must be unique and no repeat.",
            "score": 0.95,
            "final_score": 0.95,
            "query_source": "original",
            "doc_id": 1,
            "filename": "r1.md",
            "doc_type": "requirement",
        },
        {
            "chunk_text": "Only one submission is allowed; duplicate submission is forbidden. timeout <= 30 seconds.",
            "score": 0.80,
            "final_score": 0.80,
            "query_source": "rewrite",
            "doc_id": 2,
            "filename": "r2.md",
            "doc_type": "rule",
        },
    ]
    result = compress_context(chunks=chunks, max_tokens=140, keep_original_top_n=1)
    stats = result.get("stats") or {}
    fidelity = stats.get("fidelity") or {}

    assert fidelity.get("enabled") is True
    assert fidelity.get("source_constraint_terms", 0) >= fidelity.get("retained_constraint_terms", 0)
    assert "risk_level" in fidelity
    assert "category_retention" in fidelity
    assert "numeric_range" in fidelity["category_retention"]
    assert "input_chars" in stats
    assert "output_chars" in stats
