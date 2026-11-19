"""真实Run65失败引文的表示层回归；不生成业务数据或新的模型/评审批次。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform import test_generation_agent as generation


@pytest.fixture(scope="module")
def actual_quotes():
    path = Path(__file__).with_name("fixtures") / "run65_quote_layout_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: item for key, item in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert value["provenance"]["run_id"] == 65
    assert value["provenance"]["run_status"] == "failed"
    assert value["provenance"]["candidate_cases"] == value["provenance"]["delivered_cases"] == 0
    assert len(value["examples"]) == 13
    assert sum(item["expected_accept"] for item in value["examples"]) == 12
    return value


@pytest.mark.parametrize("position", range(13))
def test_historical_thirteen_raw_references_still_distinguish_layout_from_changed_word(actual_quotes, position):
    example = actual_quotes["examples"][position]
    state = deepcopy(actual_quotes["source_state_projection"])
    reference = deepcopy(example["reference"])
    before = action_state_hash(state)
    if example["expected_accept"]:
        # 12条原始模型引文仅去掉了中文词中版面空白，仍返回真实引文原值。
        assert generation._validate_stored_source_refs(state, [reference]) == [reference]
        assert generation._validate_stored_source_refs(state, [reference], require_read=False) == [reference]
    else:
        # 实际AP15“学前自测”被模型改成“学前前测”，不是可归一的排版差异。
        assert "学前前测" in reference["quote"]
        for require_read in (True, False):
            with pytest.raises(OutputValidationError, match="引用原话不在真实来源"):
                generation._validate_stored_source_refs(state, [reference], require_read=require_read)
    assert action_state_hash(state) == before
    assert reference == example["reference"]


@pytest.mark.parametrize("position", range(13))
def test_model_selects_real_evidence_id_instead_of_retyping_historical_quote(actual_quotes, position):
    example = actual_quotes["examples"][position]
    state = deepcopy(actual_quotes["source_state_projection"])
    identity = example["reference"]["evidence_id"]
    evidence = next(item for item in state["evidence_catalog"]["items"] if item["evidence_id"] == identity)
    expected = evidence["resolution"] if evidence["kind"] == "approved_decision" else evidence["text"]
    before = action_state_hash(state)
    assert generation._source_refs(state, [identity]) == [{"evidence_id": identity, "quote": expected}]
    # 已存在的失败引文只作历史证据，不能再通过新工具合同交给模型重写。
    with pytest.raises(OutputValidationError, match="来源引用|证据编号"):
        generation._source_refs(state, [deepcopy(example["reference"])])
    assert action_state_hash(state) == before


@pytest.mark.parametrize("restriction", ["not_read", "disjoint_ranges", "changed_raw_text"])
def test_layout_acceptance_does_not_bypass_actual_read_range_or_raw_hash(actual_quotes, restriction):
    state = deepcopy(actual_quotes["source_state_projection"])
    reference = deepcopy(next(item["reference"] for item in actual_quotes["examples"] if item["expected_accept"]))
    identity = reference["evidence_id"]
    original = next(item for item in state["evidence_catalog"]["items"] if item["evidence_id"] == identity)
    assert generation._validate_stored_source_refs(state, [reference]) == [reference]
    # 只在隔离CPU副本收窄元数据，不将此控制范围写成真实读取回执。
    if restriction == "not_read":
        state["evidence_reads"].pop(identity)
    elif restriction == "disjoint_ranges":
        state["evidence_reads"][identity]["ranges"] = [[0, 1], [len(original["text"]) - 1, len(original["text"])]]
    else:
        original["text"] += "\n"
    with pytest.raises(OutputValidationError, match="必须先读取引用所指的实际原文范围"):
        generation._source_refs(state, [identity])


def test_actual_quote_cannot_be_rebound_to_a_different_real_evidence_id(actual_quotes):
    state = deepcopy(actual_quotes["source_state_projection"])
    reference = deepcopy(next(item["reference"] for item in actual_quotes["examples"] if item["expected_accept"]))
    quote = generation._quote_presentation(reference["quote"])
    other = next(item for item in state["evidence_catalog"]["items"]
                 if item["kind"] == "text" and quote not in generation._quote_presentation(item["text"]))
    reference["evidence_id"] = other["evidence_id"]
    with pytest.raises(OutputValidationError, match="引用原话不在真实来源"):
        generation._validate_stored_source_refs(state, [reference])


def test_actual_approval_answer_remains_distinct_from_original_unresolved_question(actual_quotes):
    state = deepcopy(actual_quotes["source_state_projection"])
    approved = next(item for item in state["evidence_catalog"]["items"]
                    if item["kind"] == "approved_decision" and item["approval_id"] == 15)
    reference = {"evidence_id": approved["evidence_id"], "quote": approved["resolution"]}
    assert generation._source_refs(state, [approved["evidence_id"]]) == [reference]
    question = approved["question"]["reason"]
    assert generation._quote_presentation(question) in generation._quote_presentation(approved["text"])
    assert generation._quote_presentation(question) not in generation._quote_presentation(approved["resolution"])
    with pytest.raises(OutputValidationError, match="人工批准引用必须引用真实答复"):
        generation._validate_stored_source_refs(state, [{**reference, "quote": question}])


@pytest.mark.parametrize("layout,continuous", [
    ("甲\n乙", "甲乙"),
    ("甲\u3000乙", "甲乙"),
    ("⻚\n面", "⻚面"),
    ("学習\n結果", "学習結果"),
    ("规则 ——", "规则——"),
    ("red\ncar", "red car"),
])
def test_unicode_layout_controls_without_business_claims(layout, continuous):
    assert generation._quote_presentation(layout) == generation._quote_presentation(continuous)


@pytest.mark.parametrize("separated,joined", [
    ("red car", "redcar"),
    ("1 0", "10"),
    ("第1 0项", "第10项"),
    ("a < = b", "a <= b"),
    ("甲\n\n乙", "甲乙"),
    ("甲\t乙", "甲乙"),
    ("甲\f乙", "甲乙"),
    ("가 방", "가방"),
    ("不支持", "支持"),
])
def test_representation_controls_keep_word_number_operator_and_hard_boundaries(separated, joined):
    # 这些只验证字符表示，不虚构任何项目规则、账号或业务用例。
    assert generation._quote_presentation(separated) != generation._quote_presentation(joined)


def test_quote_layout_policy_does_not_change_case_title_deduplication():
    assert generation._presentation("甲\n乙") == "甲 乙"
    assert generation._presentation("甲乙") == "甲乙"
    assert generation._quote_presentation("甲\n乙") == "甲乙"

