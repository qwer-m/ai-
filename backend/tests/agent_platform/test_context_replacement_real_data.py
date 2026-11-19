"""真实 Run 64/65 持久回执的只读上下文回归；不伪造成功动作或模型响应。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture(scope="module")
def receipts():
    path = Path(__file__).parent / "fixtures/run64_65_context_replacement_real.json.gz"
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert hashlib.sha256(_json(data["runs"]).encode()).hexdigest() == data["data_sha256"]
    return data["runs"]


@pytest.fixture(scope="module")
def run67():
    path = Path(__file__).parent / "fixtures/run67_rejected_proposals_real.json.gz"
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert hashlib.sha256(_json(data["receipts"]).encode()).hexdigest() == data["data_sha256"]
    return data["receipts"]


class SavedReceiptReader:
    """只读真实展开快照；把已验证的内容摘要映射为当前 Journal 的引用头。"""

    def __init__(self, rows):
        self.rows = {row["tool_call_id"]: deepcopy(row) for row in rows}
        for row in self.rows.values():
            for field in ("arguments", "error", "result"):
                if field in row:
                    assert action_state_hash(row[field]) == row[field + "_hash"]

    def receipt_by_id(self, call_id, *, include_payloads=True):
        row = deepcopy(self.rows.get(call_id))
        if row is not None and not include_payloads:
            for field in ("arguments", "error", "result"):
                if field in row:
                    row.pop(field)
                    row[field + "_ref"] = {"sha256": row[field + "_hash"]}
        return row


def _history(rows):
    items = []
    for row in rows:
        # 这是持久回执的协议视图，不是伪造模型响应；成功输出逐字使用真实保存结果。
        output = row["result"]["output"] if row["status"] == "completed" else _json({
            "status": "rejected", "feedback": row["error"]["feedback"],
        })
        items.extend([
            {"type": "function_call", "call_id": row["tool_call_id"], "name": row["tool_key"],
             "arguments": row["arguments"]["arguments_json"]},
            {"type": "function_call_output", "call_id": row["tool_call_id"], "output": output},
        ])
    return items


def _filter(rows):
    # 本回归只验证历史替换，不执行领域观察、模型调用或数据库加载。
    state_filter = object.__new__(_PersistentStateInputFilter)
    state_filter._context = SimpleNamespace(action_journal=SavedReceiptReader(rows))
    state_filter._resolved_failure_scopes = {
        "commit_coverage_plan": {"items": "items", "id": "coverage_id", "version": "expected_version"},
    }
    state_filter._superseded_proposal_tools = set()
    return state_filter


def _calls(items):
    calls = {item["call_id"] for item in items if item.get("type") == "function_call"}
    outputs = [item["call_id"] for item in items if item.get("type") == "function_call_output"]
    assert calls == set(outputs)
    assert len(outputs) == len(calls)
    return calls


def test_run65_resolved_failures_leave_small_receipts_and_preserve_raw_history(receipts):
    rows = receipts["65"]
    items = _history(rows)
    before_items, before_rows = action_state_hash(items), action_state_hash(rows)
    result = _filter(rows)._compact_resolved_failures(items)
    assert _calls(result) == {rows[-1]["tool_call_id"]}
    summaries = [json.loads(item["content"].split("\n", 1)[1])
                 for item in result if item.get("role") == "user"]
    assert [entry["tool_call_id"] for group in summaries for entry in group] == [
        row["tool_call_id"] for row in rows[:-1]
    ]
    assert all(entry["resolved_by"] == rows[-1]["tool_call_id"] for group in summaries for entry in group)
    assert len(_json(result).encode()) < len(_json(items).encode()) / 2
    assert action_state_hash(items) == before_items
    assert action_state_hash(rows) == before_rows


def test_latest_unresolved_run65_failures_keep_arguments_for_correction(receipts):
    rows = receipts["65"][:-1]
    items = _history(rows)
    assert _filter(rows)._compact_resolved_failures(items) == items


def test_run64_different_ids_and_versions_are_not_inferred_as_resolved(receipts):
    rows = receipts["64"]
    items = _history(rows)
    result = _filter(rows)._compact_resolved_failures(items)
    assert _calls(result) == {row["tool_call_id"] for row in rows if row["started_sequence"] != 3}
    # seq 2 的 COV-001 与成功 COV-0001 不同，保留完整失败；seq 8 仅写一个目标且版本不同。
    single_success_rows = [row for row in rows if row["started_sequence"] in {3, 8}]
    unresolved = _history(single_success_rows)
    assert _filter(single_success_rows)._compact_resolved_failures(unresolved) == unresolved


@pytest.mark.parametrize("corruption", ["arguments", "feedback", "success_output", "missing_output"])
def test_real_history_tampering_cannot_authorize_compaction(receipts, corruption):
    rows = receipts["65"]
    items = _history(rows)
    if corruption == "arguments":
        items[0]["arguments"] += " "
    elif corruption == "feedback":
        output = json.loads(items[1]["output"])
        output["feedback"]["message"] += "（篡改控制）"
        items[1]["output"] = _json(output)
    elif corruption == "success_output":
        items[-1]["output"] += " "
    else:
        del items[1]
    with pytest.raises(ValueError, match="不可变"):
        _filter(rows)._compact_resolved_failures(items)


def test_undeclared_tools_keep_all_real_failure_pairs(receipts):
    rows = receipts["65"]
    state_filter = _filter(rows)
    state_filter._resolved_failure_scopes = {}
    items = _history(rows)
    assert state_filter._compact_resolved_failures(items) == items


def test_run67_old_rejected_proposals_shrink_without_claiming_business_resolution(run67):
    state_filter = _filter(run67)
    state_filter._superseded_proposal_tools = {"append_cases", "patch_cases"}
    items = _history(run67)
    before = action_state_hash([items, state_filter._context.action_journal.rows])
    result = state_filter._compact_resolved_failures(items)
    removed = {row["tool_call_id"] for row in run67 if row["started_sequence"] in {1, 7}}
    assert _calls(result) == {row["tool_call_id"] for row in run67} - removed
    summaries = [json.loads(item["content"].split("\n", 1)[1])
                 for item in result if item.get("role") == "user"]
    entries = [entry for group in summaries for entry in group]
    assert {entry["tool_call_id"] for entry in entries} == removed
    for entry in entries:
        original = next(row for row in run67 if row["tool_call_id"] == entry["tool_call_id"])
        assert entry["status"] == "rejected_proposal_not_applied"
        assert entry["error"] == original["error"]
        assert "resolved_by" not in entry
    assert action_state_hash([items, state_filter._context.action_journal.rows]) == before
    # 第 11 次失败之后没有成功推进，保留整批失败参数供修正。
    assert items[-2] in result and items[-1] in result


def test_run67_current_failure_stays_until_same_prestate_success(run67):
    state_filter = _filter(run67)
    state_filter._superseded_proposal_tools = {"append_cases"}
    for sequences in ({1}, {1, 3}, {7}, {7, 9}, {11}):
        # seq 3/9 是真实后续成功，但它们的 pre_state 不等于前一失败版本，不能据此压缩。
        rows = [row for row in run67 if row["started_sequence"] in sequences]
        items = _history(rows)
        assert state_filter._compact_resolved_failures(items) == items


def test_run67_without_explicit_state_only_proposal_configuration_keeps_body(run67):
    items = _history(run67)
    assert _filter(run67)._compact_resolved_failures(items) == items
