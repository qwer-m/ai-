import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.testing.evaluation import _normalize_compare_result_json


def _build_payload(missing_points: list[str], hallucinations: list[str]) -> str:
    return json.dumps(
        {
            "metrics": {
                "precision": 0.5,
                "recall": 0.5,
                "f1_score": 0.5,
                "semantic_similarity": 0.5,
            },
            "defect_analysis": {
                "missing_points": missing_points,
                "hallucinations": hallucinations,
                "modifications": [],
            },
            "summary": "test",
        },
        ensure_ascii=False,
    )


def test_normalizer_swaps_when_direction_is_obviously_reversed() -> None:
    raw = _build_payload(
        missing_points=[
            "断网答题后网络恢复自动同步至词汇档案（TC-002）",
        ],
        hallucinations=[
            "新增“单词本”相关用例，原生成用例未包含单词本模块",
            "新增断网下点击“继续学习”应提示网络异常的用例",
        ],
    )

    normalized = json.loads(_normalize_compare_result_json(raw))
    defect = normalized["defect_analysis"]
    assert defect["missing_points"][0].startswith("新增“单词本”相关用例")
    assert defect["hallucinations"][0].startswith("断网答题后网络恢复自动同步至词汇档案")


def test_normalizer_keeps_correct_orientation() -> None:
    raw = _build_payload(
        missing_points=[
            "未覆盖支付失败后的重试提示",
            "原生成用例未包含弱网重连验证",
        ],
        hallucinations=[
            "多余增加了与需求无关的邮箱绑定场景",
            "重复验证了同一路径，属于冗余步骤",
        ],
    )

    normalized = json.loads(_normalize_compare_result_json(raw))
    defect = normalized["defect_analysis"]
    assert defect["missing_points"][0] == "未覆盖支付失败后的重试提示"
    assert defect["hallucinations"][0] == "多余增加了与需求无关的邮箱绑定场景"


def test_normalizer_does_not_swap_without_enough_signal() -> None:
    raw = _build_payload(
        missing_points=["场景 A", "场景 B"],
        hallucinations=["场景 C", "场景 D"],
    )

    normalized = json.loads(_normalize_compare_result_json(raw))
    defect = normalized["defect_analysis"]
    assert defect["missing_points"] == ["场景 A", "场景 B"]
    assert defect["hallucinations"] == ["场景 C", "场景 D"]
