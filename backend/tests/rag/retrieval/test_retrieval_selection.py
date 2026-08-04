from modules.knowledge_base_components.retrieval.retrieval_selection import select_diverse_chunks
from modules.knowledge_base_components.retrieval.reranker import rerank_chunks


def _chunk(
    *,
    chunk_id: str,
    doc_id: int,
    score: float,
    text: str,
    terms: list[str],
) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "filename": f"doc-{doc_id}.md",
        "doc_type": "requirement",
        "chunk_text": text,
        "final_score": score,
        "fusion_score": score,
        "title_hit_terms": terms[:1],
        "content_hit_terms": terms,
    }


def test_select_diverse_chunks_two_round_and_doc_cap():
    chunks = [
        _chunk(chunk_id="d1-1", doc_id=1, score=0.82, text="开户流程第一步校验身份并记录手机号。", terms=["开户", "流程", "身份"]),
        _chunk(chunk_id="d1-2", doc_id=1, score=0.79, text="开户流程第二步校验银行卡并校验额度上限。", terms=["开户", "额度", "银行卡"]),
        _chunk(chunk_id="d1-3", doc_id=1, score=0.76, text="开户流程第三步写入风控状态并返回处理结果。", terms=["风控", "状态", "处理结果"]),
        _chunk(chunk_id="d1-4", doc_id=1, score=0.74, text="开户流程第四步发送通知短信并更新日志。", terms=["通知", "短信", "日志"]),
        _chunk(chunk_id="d2-1", doc_id=2, score=0.80, text="审批规则要求角色权限匹配且审批单据完整。", terms=["审批", "规则", "权限"]),
        _chunk(chunk_id="d2-2", doc_id=2, score=0.75, text="审批异常需要回滚状态并记录错误码。", terms=["异常", "回滚", "错误码"]),
        _chunk(chunk_id="d3-1", doc_id=3, score=0.78, text="额度规则定义日限额和单笔上限。", terms=["额度", "限额", "上限"]),
    ]

    selected, stats = select_diverse_chunks(
        chunks=chunks,
        final_top_n=8,
        max_chunks_per_doc=6,
        min_docs=2,
        redundancy_threshold=0.92,
    )

    assert selected
    assert stats["doc_coverage_triggered"] is True
    assert stats["second_round_mode"] == "score_plus_information_gain"
    assert stats["max_chunks_per_doc_applied"] == 3
    assert any(str(item.get("selection_reason")) == "score_info_gain_round" for item in selected)
    assert max(stats["per_doc_counts"].values()) <= 3


def test_rerank_chunks_only_emits_current_score_contract():
    chunks = [
        {
            "chunk_id": "d1-1",
            "doc_id": 1,
            "score": 0.82,
            "chunk_text": "开户流程需要校验身份信息。",
            "query_source": "original",
        }
    ]

    result = rerank_chunks(chunks=chunks, question="开户身份校验", top_k=1)

    assert result[0]["final_score"] > 0
    assert "rerank_score" not in result[0]
