import sys
from pathlib import Path

# 兼容 pytest 从任意工作目录执行，确保可导入 backend 下的模块。
sys.path.append(str(Path(__file__).resolve().parents[2]))

from routers.orchestration.evaluation_history_routes import _extract_quality_metrics


def test_extract_quality_metrics_from_json_block() -> None:
    raw = """
```json
{
  "metrics": {
    "precision": 0.88,
    "recall": 1.0,
    "f1_score": 0.94,
    "semantic_similarity": 0.89
  }
}
```
"""
    metrics = _extract_quality_metrics(raw)
    assert metrics["precision"] == 0.88
    assert metrics["recall"] == 1.0
    assert metrics["f1_score"] == 0.94
    assert metrics["semantic_similarity"] == 0.89


def test_extract_quality_metrics_from_flat_json() -> None:
    raw = '{"precision": "88%", "recall": "100%", "f1": 0.94, "similarity": 0.9}'
    metrics = _extract_quality_metrics(raw)
    assert metrics["precision"] == 0.88
    assert metrics["recall"] == 1.0
    assert metrics["f1_score"] == 0.94
    assert metrics["semantic_similarity"] == 0.9


def test_extract_quality_metrics_from_plain_text_fallback() -> None:
    raw = "Precision: 0.75, Recall: 0.5, F1 Score: 0.6, 语义相似度: 82%"
    metrics = _extract_quality_metrics(raw)
    assert metrics["precision"] == 0.75
    assert metrics["recall"] == 0.5
    assert metrics["f1_score"] == 0.6
    assert metrics["semantic_similarity"] == 0.82
