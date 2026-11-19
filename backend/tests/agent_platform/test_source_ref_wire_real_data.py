"""Run76 原始重复引用键的 CPU 协议回放；旧失败与原始参数保持不变。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

from jsonschema import validate, ValidationError
import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.serialization import DuplicateJSONKeyError, strict_json_loads
from modules.agent_platform.test_generation_schemas import SOURCE_REFS_SCHEMA


@pytest.fixture(scope="module")
def actual():
    path = Path(__file__).parent / "fixtures/run76_source_ref_wire_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    assert fixture["provenance"]["source_run_id"] == 76
    assert fixture["provenance"]["source_status"] == "failed"
    assert fixture["provenance"]["new_model_responses"] is False
    assert action_state_hash(data) == fixture["provenance"]["data_sha256"]
    assert action_state_hash(data["state"]) == fixture["provenance"]["source_state_hash"]
    yield data
    assert path.read_bytes() == original


def references(actual):
    wire = actual["projected_wire"]
    return [item["source_refs"] for item in [*wire["coverage_updates"], *wire["cases"]]]


def test_actual_conflicting_duplicate_keys_are_still_rejected_not_repaired(actual):
    assert len(actual["duplicate_fields"]) == 8
    assert all(item["first"] != item["second"] for item in actual["duplicate_fields"])
    with pytest.raises(DuplicateJSONKeyError):
        strict_json_loads(actual["raw_arguments"])


def test_unviewed_images_remain_discoverable_by_page_without_claimable_evidence_ids(actual):
    state = actual["state"]
    original = action_state_hash(state)
    summary = generation._source_summary(state)
    unseen = [item for item in state["evidence_catalog"]["items"] if item["kind"] == "image"
              and str(item["page_number"]) not in state["image_pages"]]
    assert unseen
    ids = {row["evidence_id"] for row in summary if "evidence_id" in row}
    assert ids.isdisjoint(item["evidence_id"] for item in unseen)
    assert {item["page_number"] for item in unseen} <= {
        row["page_number"] for row in summary if row["kind"] == "image" and not row["reference_available"]}
    assert {item["evidence_id"] for item in state["evidence_catalog"]["items"] if item["kind"] != "image"} <= ids
    assert action_state_hash(state) == original


def test_observed_ids_are_preserved_and_still_checked_against_provided_sources(actual):
    state = actual["state"]
    before = action_state_hash(state)
    assert len(actual["projected_wire"]["coverage_updates"]) == 14
    assert len(actual["projected_wire"]["cases"]) == 10
    assert sum(map(len, references(actual))) == 42
    by_id = {item["evidence_id"]: item for item in state["evidence_catalog"]["items"]}
    rejected = []
    for ids in references(actual):
        assert all(isinstance(identity, str) for identity in ids)
        unavailable = [identity for identity in ids if identity not in by_id or
            by_id[identity]["kind"] == "image" and str(by_id[identity]["page_number"]) not in state["image_pages"]]
        if unavailable:
            # 新协议不代表该非法历史候选已成立；原有未提供图片/未知编号约束仍须拒绝。
            with pytest.raises(OutputValidationError):
                generation._source_refs(state, ids)
            rejected.extend(unavailable)
            continue
        bound = generation._source_refs(state, ids)
        assert generation._model_source_refs(bound) == sorted(ids)
        for item in bound:
            source = by_id[item["evidence_id"]]
            assert item == {"evidence_id": source["evidence_id"],
                            "quote": source.get("resolution", source["text"])}
    assert rejected
    assert action_state_hash(state) == before


def test_duplicate_string_ids_are_losslessly_deduplicated_before_stored_binding(actual):
    ids = references(actual)[0]
    duplicates = [identity for identity in ids for _ in range(2)]
    original = list(duplicates)
    # Schema 不能在领域无损归并前以 uniqueItems 拒绝，否则仍会多花模型纠正回合。
    validate(duplicates, SOURCE_REFS_SCHEMA)
    assert generation._source_refs(actual["state"], duplicates) == generation._source_refs(actual["state"], ids)
    assert duplicates == original


def test_model_wire_schema_and_parser_strictly_reject_legacy_objects(actual):
    identity = references(actual)[0][0]
    for old in ([{"evidence_id": identity}], generation._source_refs(actual["state"], [identity])):
        with pytest.raises(ValidationError):
            validate(old, SOURCE_REFS_SCHEMA)
        with pytest.raises(OutputValidationError, match="字符串数组"):
            generation._source_refs(actual["state"], old)


def test_internal_quote_records_remain_valid_and_unmodified(actual):
    records = next(iter(actual["state"]["cases"]["items"].values()))["content"]["source_refs"]
    original = deepcopy(records)
    assert records and all(set(ref) == {"evidence_id", "quote"} for ref in records)
    assert generation._validate_stored_source_refs(actual["state"], records) == sorted(
        records, key=lambda item: (item["evidence_id"], item["quote"]))
    assert records == original


def test_all_review_model_source_ref_fields_use_id_arrays(actual):
    original = action_state_hash(actual["state"])
    payload = generation._review_input(actual["state"])
    encountered = []

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "source_refs":
                    assert isinstance(item, list) and all(isinstance(identity, str) for identity in item)
                    encountered.append(item)
                else:
                    check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(payload)
    assert len(encountered) >= len(actual["state"]["cases"]["items"]) + len(actual["state"]["coverage_plan"]["items"])
    assert action_state_hash(actual["state"]) == original


def test_unviewed_real_image_source_still_cannot_be_claimed_by_switching_wire(actual):
    state = actual["state"]
    unseen = next(item["evidence_id"] for item in state["evidence_catalog"]["items"]
                  if item["kind"] == "image" and str(item["page_number"]) not in state["image_pages"])
    with pytest.raises(OutputValidationError, match="实际查看"):
        generation._source_refs(state, [unseen])
