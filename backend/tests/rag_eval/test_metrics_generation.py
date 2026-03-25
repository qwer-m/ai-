from modules.rag_eval.metrics.metrics_generation import evaluate_answer_by_points_rule


def test_rule_points_text_number_enum():
    answer = "系统会锁定15分钟，支持状态A和状态B，重试次数为3次。"
    points = [
        "锁定15分钟",
        {"type": "number", "label": "重试次数", "exact_value": "3"},
        {"type": "enum", "label": "支持状态", "values": ["状态A", "状态B"]},
    ]
    result = evaluate_answer_by_points_rule(answer, points)
    assert result["is_correct"] is True
    assert result["score"] == 1.0
    assert result["missing_points"] == []


def test_rule_points_missing():
    answer = "系统支持状态A。"
    points = [
        "锁定15分钟",
        {"type": "enum", "label": "支持状态", "values": ["状态A", "状态B"]},
    ]
    result = evaluate_answer_by_points_rule(answer, points)
    assert result["is_correct"] is False
    assert result["score"] < 1.0
    assert len(result["missing_points"]) >= 1

