"""真实请求账本/已验收专业结果的隔离回放；不调用模型，不写业务数据库。

Run51/52 未收到响应的请求只转未知，正常结算只使用 Run49 已记录的
reservation/usage 配对。并发测试回放真实专业结果，不生成虚构业务答案。
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import RunContextWrapper
from agents.usage import Usage

from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks, AgentExecutionBudgetExceeded
from modules.agent_platform.lifecycle import assert_run_claim, RunLeaseLost
from modules.agent_platform.registry import ToolExecutionContext


@pytest.fixture(scope="module")
def real_records():
    return json.loads((Path(__file__).parent / "fixtures/live_budget_concurrency_real.json").read_text(encoding="utf-8"))


def reservation(record):
    input_tokens, output_tokens = record["reserved_input_tokens"], record["reserved_output_tokens"]
    return {"attempted_requests": 1, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}


def context(record):
    return RunContextWrapper(context=SimpleNamespace(run_id=record["run_id"], sequence=record["sequence"],
                                                     input_hash=record["input_hash"]))


def budget(checkpoint):
    return AutonomousBudgetHooks(checkpoint["limits"], checkpoint=deepcopy(checkpoint))


async def pressured_run52(records):
    real = records["run52"]
    hooks = budget(real["before_last_action"])
    contexts = [context(item) for item in real["last_requests"]]
    for current, record in zip(contexts, real["last_requests"], strict=True):
        await hooks._reserve_request(current, reservation(record))
    assert hooks.snapshot()["charged"] == real["final"]["charged"]
    # 第4次实际拒绝的 input 取自错误投影；max_tokens 属于同一冻结专业定义。
    target = reservation(real["last_requests"][0])
    target["input_tokens"] = real["projected_rejected_input"] - hooks.snapshot()["charged"]["input_tokens"]
    target["total_tokens"] = target["input_tokens"] + target["output_tokens"]
    return hooks, contexts, context(real["last_requests"][0]), target


def test_real_known_settlement_releases_only_known_difference_before_admission(real_records):
    async def check():
        initial = real_records["run51"]["initial"]
        known = real_records["known_response"]
        requests = real_records["run51"]["new_requests"]
        hooks = budget(initial)
        known_context = context(known)
        await hooks._reserve_request(known_context, reservation(known))
        for record in requests[:2]:
            await hooks._reserve_request(context(record), reservation(record))
        before = hooks.snapshot()
        waiting = asyncio.create_task(hooks._reserve_request(context(requests[2]), reservation(requests[2])))
        await asyncio.sleep(0)
        assert not waiting.done() and hooks.snapshot() == before
        hooks.settle_request(known_context, Usage(**known["usage"]))
        await asyncio.wait_for(waiting, timeout=1)
        after = hooks.snapshot()
        assert after["charged"]["attempted_requests"] == before["charged"]["attempted_requests"] + 1
        assert after["completed_requests"] == before["completed_requests"] + 1
        assert after["interrupted_requests"] == 6
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            assert after["charged"][key] <= initial["limits"][key]
            assert after["charged"][key] - after["reported_usage"][key] == (
                real_records["run51"]["unknown_budget"]["this_kill_retained"][key]
                + sum(reservation(record)[key] for record in requests))
    asyncio.run(check())


def test_real_run52_pending_pressure_waits_then_unknowns_still_fail_without_refund(real_records):
    async def check():
        hooks, contexts, waiting_context, target = await pressured_run52(real_records)
        before = hooks.snapshot()
        assert before["pending_requests"] == 3
        assert before["charged"]["input_tokens"] == 1973231
        assert target["input_tokens"] == 51578
        waiting = asyncio.create_task(hooks._reserve_request(waiting_context, target))
        await asyncio.sleep(0)
        assert not waiting.done() and hooks.snapshot() == before
        for current in contexts:
            hooks.interrupt_requests(execution_context=current.context)
        with pytest.raises(AgentExecutionBudgetExceeded):
            await asyncio.wait_for(waiting, timeout=1)
        assert hooks.snapshot() == real_records["run52"]["final"]
    asyncio.run(check())


def test_real_unknown_only_checkpoint_rejects_without_wait_or_new_attempt(real_records):
    async def check():
        hooks = budget(real_records["run51"]["final"])
        before = hooks.snapshot()
        record = real_records["run51"]["new_requests"][0]
        with pytest.raises(AgentExecutionBudgetExceeded):
            await asyncio.wait_for(hooks._reserve_request(context(record), reservation(record)), timeout=1)
        assert hooks.snapshot() == before
    asyncio.run(check())


@pytest.mark.parametrize("termination", ["deadline", "cancel"])
def test_waiting_admission_terminates_without_charging_unsent_request(real_records, termination):
    async def check():
        hooks, _, waiting_context, target = await pressured_run52(real_records)
        before = hooks.snapshot()
        waiting = asyncio.create_task(hooks._reserve_request(waiting_context, target))
        await asyncio.sleep(0)
        assert not waiting.done()
        if termination == "deadline":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(waiting, timeout=0.01)
        else:
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        assert hooks.snapshot() == before
    asyncio.run(check())


def test_waiting_admission_rechecks_claim_after_wakeup_before_charging(real_records):
    async def check():
        hooks, contexts, waiting_context, target = await pressured_run52(real_records)
        identity = real_records["run52"]["identity"]
        # 仅在隔离执行元数据中注入代际变化；两个标识均来自真实投递记录。
        own = identity["task_id"]
        row = SimpleNamespace(id=identity["id"], status="running", claim_token=own,
                              lease_expires_at=datetime.utcnow() + timedelta(minutes=1))
        before = hooks.snapshot()
        checks = []

        def guard():
            assert_run_claim(row, own, now=datetime.utcnow())
            checks.append("valid")

        waiting = asyncio.create_task(hooks._reserve_request(waiting_context, target, before_admission=guard))
        await asyncio.sleep(0)
        assert not waiting.done() and checks == ["valid"]
        row.claim_token = real_records["run51"]["identity"]["old_task_id"]
        hooks.interrupt_requests(execution_context=contexts[0].context)
        with pytest.raises(RunLeaseLost):
            await asyncio.wait_for(waiting, timeout=1)
        after = hooks.snapshot()
        assert after["charged"] == before["charged"]
        assert after["pending_requests"] == 2 and after["interrupted_requests"] == 1
    asyncio.run(check())


def test_attempt_count_exhaustion_does_not_wait_for_token_settlement(real_records):
    async def check():
        hooks, _, waiting_context, target = await pressured_run52(real_records)
        # 只注入执行计数边界，不伪称实际产生了这些模型请求或新的业务数据。
        hooks._charged["attempted_requests"] = hooks._limits["attempted_requests"]
        before = hooks.snapshot()
        with pytest.raises(AgentExecutionBudgetExceeded, match="attempted_requests"):
            await asyncio.wait_for(hooks._reserve_request(waiting_context, target), timeout=1)
        assert hooks.snapshot() == before
    asyncio.run(check())


@pytest.mark.parametrize("return_subtree_failures", [False, True])
def test_global_budget_denial_drains_real_receipt_replay_and_skips_queued_items(real_records, return_subtree_failures):
    async def check():
        real = real_records["run52"]
        identity, calls = real["identity"], real["validated_calls"]
        inputs = [deepcopy(item["identity"]) for item in calls]
        release, rejected = asyncio.Event(), asyncio.Event()
        invoked, saved = [], []
        original_error = AgentExecutionBudgetExceeded(real["error_message"])

        async def replay_or_reject(*, agent_key, input_payload, **_):
            index = inputs.index(input_payload)
            invoked.append(index)
            if index == 1:
                rejected.set()
                raise original_error
            assert index == 0, "预算拒绝之后排队输入不应被启动"
            await release.wait()
            # 与真实 SDK 入口相同，回执先保存，再向父批次返回；输出原样取自真实验收结果。
            output = deepcopy(calls[index]["output"])
            saved.append(output)
            return output

        execution = ToolExecutionContext(
            db=None, user_id=identity["user_id"], project_id=identity["project_id"], run_id=identity["id"],
            node_key="agent_network", run_input=deepcopy(identity["input_payload"]),
            invoke_agent=replay_or_reject, agent_concurrency=2,
        )
        batch = asyncio.create_task(execution.invoke_agents(
            agent_key=calls[0]["identity"]["agent_key"], inputs=inputs,
            return_subtree_failures=return_subtree_failures,
        ))
        await asyncio.wait_for(rejected.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not batch.done() and not saved
        release.set()
        with pytest.raises(AgentExecutionBudgetExceeded) as caught:
            await asyncio.wait_for(batch, timeout=1)
        assert caught.value is original_error
        assert invoked == [0, 1] and saved == [calls[0]["output"]]
        assert caught.value.diagnostic["batch_progress"] == {
            "total_count": 3, "failed_indexes": [1], "not_started_indexes": [2], "validated_indexes": [0],
        }
    asyncio.run(check())
