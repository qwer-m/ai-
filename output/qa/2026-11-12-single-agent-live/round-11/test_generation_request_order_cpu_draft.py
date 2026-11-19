"""真实call2110的CPU修复草稿回归；不导入替换生产函数、不构造模型回执。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))

from modules.agent_platform.action_journal import action_state_hash
from generation_request_order_cpu_draft import _generation_request


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("run59_call2110_request_order_real.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    expected = value.pop("data_sha256")
    assert action_state_hash(value) == expected
    assert value["provenance"]["validated_call_id"] == 2110
    return value


def managed_request(item, real):
    return {**_generation_request(item, ""), "_user_confirmations": deepcopy(real["confirmed_context"])}


def test_real_evidence_distinguishes_historical_validated_hash_from_restored_map_order(real):
    actual = real["exact_original_request_reconstructed"]
    restored = real["restored_map_iteration_request_cpu"]
    assert action_state_hash(actual) == real["actual_call_record"]["identity"]["input_hash"]
    assert action_state_hash(actual) == "1be001982d9625d05d7f4b612f3326177a5de1d0f015ed182575d835f62fefe2"
    assert action_state_hash(restored) == "d908f97cc113d35f3c41aa12e43475697850760f8182252ce2a7b9b2dfd4ff3b"
    assert len(real["input_order_diff"]) == 2
    assert actual["case_fact_contract"]["fact_design_item_ids"] == restored["case_fact_contract"]["fact_design_item_ids"]


@pytest.mark.parametrize("order", ["captured_hydration", "reverse", "sorted_json"])
def test_same_real_business_input_always_reconstructs_exact_validated_request(real, order):
    item = deepcopy(real["current_batch_input"])
    if order == "reverse":
        routes = item["case_fact_contract"]["fact_design_item_ids"]
        item["case_fact_contract"]["fact_design_item_ids"] = dict(reversed(list(routes.items())))
    elif order == "sorted_json":
        item = json.loads(json.dumps(item, ensure_ascii=False, sort_keys=True))
    assert action_state_hash(item) == action_state_hash(real["current_batch_input"])
    before = deepcopy(item)
    request = managed_request(item, real)
    assert request == real["exact_original_request_reconstructed"]
    assert action_state_hash(request) == real["actual_call_record"]["identity"]["input_hash"]
    assert item == before


def test_outside_batch_real_fact_route_is_rejected_instead_of_silently_dropped(real):
    item = deepcopy(real["current_batch_input"])
    outside = real["external_real_fact_id"]
    assert outside == real["external_real_fact_provenance"]["fact"]["fact_id"]
    assert outside not in item["case_fact_contract"]["available_fact_ids"]
    # 只破坏真实契约的引用边界，不编造业务事实或模型结果。
    item["case_fact_contract"]["fact_design_item_ids"][outside] = item["case_fact_contract"]["required_test_design_item_ids"][:1]
    before = deepcopy(item)
    with pytest.raises(ValueError, match="当前批次事实目录不一致"):
        managed_request(item, real)
    assert item == before


def test_missing_real_fact_route_is_rejected_at_request_preparation(real):
    item = deepcopy(real["current_batch_input"])
    item["case_fact_contract"]["fact_design_item_ids"].pop(item["case_fact_contract"]["available_fact_ids"][0])
    with pytest.raises(ValueError, match="当前批次事实目录不一致"):
        managed_request(item, real)


def test_declared_fact_order_cannot_disagree_with_actual_fact_array(real):
    item = deepcopy(real["current_batch_input"])
    item["case_fact_contract"]["available_fact_ids"] = list(reversed(item["case_fact_contract"]["available_fact_ids"]))
    with pytest.raises(ValueError, match="当前批次事实目录不一致"):
        managed_request(item, real)
