from modules.rag_eval.metrics_retrieval import (
    context_precision,
    context_recall,
    first_hit_rank,
    hit_at_k,
    mrr,
    recall_at_k,
)


def test_recall_and_hit_at_k():
    retrieved = ["c1", "c2", "c3", "c4"]
    gold = ["c3", "c8"]
    assert recall_at_k(retrieved, gold, 1) == 0.0
    assert recall_at_k(retrieved, gold, 3) == 0.5
    assert hit_at_k(retrieved, gold, 1) == 0.0
    assert hit_at_k(retrieved, gold, 3) == 1.0


def test_mrr_and_first_hit_rank():
    retrieved = ["x1", "x2", "x3"]
    gold = ["x3"]
    assert first_hit_rank(retrieved, gold) == 3
    assert abs(mrr(retrieved, gold) - (1 / 3)) < 1e-9
    assert first_hit_rank(retrieved, ["na"]) is None
    assert mrr(retrieved, ["na"]) == 0.0


def test_context_precision_recall():
    retrieved = ["g1", "n1", "g2", "n2"]
    gold = ["g1", "g2", "g3"]
    assert context_precision(retrieved, gold, 3) == 2 / 3
    assert context_recall(retrieved, gold, 3) == 2 / 3

