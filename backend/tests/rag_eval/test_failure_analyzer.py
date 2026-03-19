from modules.rag_eval.rag_failure_analyzer import analyze_failure_reason


def test_failure_no_recall():
    reason, detail = analyze_failure_reason(
        sample={"gold_chunks": ["g1"]},
        retrieved_chunks=[{"chunk_id": "x1"}],
        reranked_chunks=[{"chunk_id": "x1"}],
        answer="",
        metrics={},
        judge_result={},
        rerank_top_n=5,
    )
    assert reason == "no_recall"
    assert "未召回" in detail


def test_failure_low_rank():
    reason, detail = analyze_failure_reason(
        sample={"gold_chunks": ["g1"]},
        retrieved_chunks=[{"chunk_id": "x1"}, {"chunk_id": "x2"}, {"chunk_id": "g1"}],
        reranked_chunks=[{"chunk_id": "x1"}],
        answer="",
        metrics={"first_hit_rank": 9},
        judge_result={},
        rerank_top_n=5,
    )
    assert reason == "low_rank"
    assert "rerank_top_n=5" in detail


def test_failure_hallucination():
    reason, detail = analyze_failure_reason(
        sample={"gold_chunks": ["g1"]},
        retrieved_chunks=[{"chunk_id": "g1"}],
        reranked_chunks=[{"chunk_id": "g1", "doc_version": "v1"}],
        answer="系统支持短信验证码找回",
        metrics={"context_precision": 0.9},
        judge_result={"faithfulness_score": 0.3, "hallucinated_claims": ["支持短信验证码找回"]},
        rerank_top_n=5,
    )
    assert reason == "hallucination"
    assert "未支持" in detail


def test_failure_wrong_version():
    reason, detail = analyze_failure_reason(
        sample={"gold_chunks": ["g1"], "expected_doc_version": "v2"},
        retrieved_chunks=[{"chunk_id": "g1"}],
        reranked_chunks=[{"chunk_id": "g1", "doc_version": "v1"}],
        answer="",
        metrics={"context_precision": 0.9},
        judge_result={"faithfulness_score": 0.9},
        rerank_top_n=5,
    )
    assert reason == "wrong_version"
    assert "v1" in detail and "v2" in detail
