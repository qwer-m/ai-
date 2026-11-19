"""对 output 候选预算类做真实记录回放；无网络、模型、业务数据库或后端写入。

正常结算只使用 Run49 已收到的 reservation/usage 原始配对，不为 Run51
未知请求填造响应。把这些记录装入 Run51 隔离账本属于容量边界回放，
不代表 Run51 或 Run52 实际执行了这些新请求。
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from agents import RunContextWrapper
from agents.usage import Usage


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = HERE.parents[3]
spec = importlib.util.spec_from_file_location("isolated_budget_candidate", HERE / "autonomous_budget_candidate.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
recovery_path = BASE / "round-03/recovery-verification.json"
earlier_path = BASE / "pre-fix-snapshot.json"
recovery = json.loads(recovery_path.read_text(encoding="utf-8-sig"))
earlier = json.loads(earlier_path.read_text(encoding="utf-8-sig"))
initial = recovery["ledgers"]["initial_new_attempt"]
new_requests = recovery["request_diagnostics"]
assert len(new_requests) == 3 and all(item["status"] == "ended_without_model_response" for item in new_requests)
known = max((item for node in earlier["nodes"]
             for item in node.get("sdk_state", {}).get("error_diagnostic", {}).get("model_requests", [])
             if item["status"] == "response_received"), key=lambda item: item["reserved_input_tokens"])
assert known["run_id"] == 49 and known["sequence"] == 18


def reservation(record):
    input_tokens, output_tokens = record["reserved_input_tokens"], record["reserved_output_tokens"]
    return {"attempted_requests": 1, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}


def context(record):
    # 上下文仅携带真实记录身份；不连接模型或假造领域输出。
    return RunContextWrapper(context=SimpleNamespace(run_id=record["run_id"], sequence=record["sequence"],
                                                     input_hash=record["input_hash"]))


def budget(checkpoint=initial):
    return candidate.AutonomousBudgetHooks(checkpoint["limits"], checkpoint=deepcopy(checkpoint))


def assert_unknown_preserved(snapshot, extra_reservations):
    original = recovery["unknown_budget"]["this_kill_retained"]
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        assert snapshot["charged"][key] - snapshot["reported_usage"][key] == (
            original[key] + sum(item[key] for item in extra_reservations))


async def wait_turn():
    # 仅让本地候选协程登记等待，不等待服务或模型。
    await asyncio.sleep(0)


async def pressured_budget():
    hooks = budget()
    contexts = [context(item) for item in new_requests]
    reservations = [reservation(item) for item in new_requests]
    for current, planned in zip(contexts, reservations):
        await hooks._reserve_request(current, planned)
    assert hooks.snapshot()["charged"] == recovery["ledgers"]["new_attempt_final_failure"]["charged"]
    # 第4次真实准入的 input 投影来自原错误；output 复用同一冻结专业 Agent 的实际 max_tokens。
    target = reservation(new_requests[0])
    target["input_tokens"] = 2072563 - hooks.snapshot()["charged"]["input_tokens"]
    target["total_tokens"] = target["input_tokens"] + target["output_tokens"]
    return hooks, contexts, reservations, context(new_requests[0]), target


async def main():
    checks = {}
    evidence = {}

    # 保留 Run51 的6个 unknown，并将两条 Run51 预留和一条 Run49 已知请求装入隔离账本。
    hooks = budget()
    known_context = context(known)
    await hooks._reserve_request(known_context, reservation(known))
    first_two = [context(item) for item in new_requests[:2]]
    for current, record in zip(first_two, new_requests):
        await hooks._reserve_request(current, reservation(record))
    before = hooks.snapshot()
    waiter_context = context(new_requests[2])
    waiter = asyncio.create_task(hooks._reserve_request(waiter_context, reservation(new_requests[2])))
    await wait_turn()
    assert not waiter.done() and hooks.snapshot() == before
    checks["temporary_pressure_waits_without_charge_or_attempt"] = True
    # 此处只能结算有真实响应的 Run49 记录，绝不结算 Run51 的 unknown。
    hooks.settle_request(known_context, Usage(**known["usage"]))
    await asyncio.wait_for(waiter, timeout=1)
    after = hooks.snapshot()
    assert after["charged"]["attempted_requests"] == before["charged"]["attempted_requests"] + 1
    assert after["completed_requests"] == before["completed_requests"] + 1
    assert after["interrupted_requests"] == initial["interrupted_requests"] == 6
    assert all(after["charged"][key] <= after["limits"][key] for key in after["limits"])
    assert_unknown_preserved(after, [reservation(item) for item in new_requests])
    checks["real_known_settlement_then_admission_under_original_limits"] = True
    checks["original_unknown_retained_and_attempts_never_refunded"] = True
    evidence["known_settlement_replay"] = {"record_run": 49, "sequence": known["sequence"],
        "reservation": reservation(known), "reported_usage": known["usage"], "before": before, "after": after}

    # 用 Run51 原失败终态账本验证：unknown不能当 live pending，立即拒绝且计数不动。
    hooks = budget(recovery["ledgers"]["new_attempt_final_failure"])
    before = hooks.snapshot()
    try:
        await hooks._reserve_request(context(new_requests[0]), reservation(new_requests[0]))
    except candidate.AgentExecutionBudgetExceeded:
        pass
    else:
        raise AssertionError("没有 live pending 时不得无限等待或超额准入")
    assert hooks.snapshot() == before
    checks["no_pending_rejects_immediately_without_new_charge"] = True

    # 完整复用真实3条在途预留；中断只改变 pending/interrupted，不制造退款或响应。
    hooks, contexts, planned, waiting_context, target = await pressured_budget()
    before = hooks.snapshot()
    waiter = asyncio.create_task(hooks._reserve_request(waiting_context, target))
    await wait_turn()
    assert not waiter.done()
    for current in contexts:
        hooks.interrupt_requests(execution_context=current.context)
    try:
        await asyncio.wait_for(waiter, timeout=1)
    except candidate.AgentExecutionBudgetExceeded:
        pass
    else:
        raise AssertionError("中断通知后unknown仍占额度，不能凭空准入")
    after = hooks.snapshot()
    assert after["charged"] == before["charged"] and after["reported_usage"] == before["reported_usage"]
    assert after["pending_requests"] == 0 and after["interrupted_requests"] == 9
    checks["interruption_wakes_waiter_without_refunding_unknown"] = True
    evidence["exact_run51_unknown_replay"] = {"before": before, "after": after, "rejected_reservation": target}

    # 本地等待被硬截止取消，尚未准入的请求不会留下 attempted/pending。
    hooks, contexts, planned, waiting_context, target = await pressured_budget()
    before = hooks.snapshot()
    try:
        await asyncio.wait_for(hooks._reserve_request(waiting_context, target), timeout=0.01)
    except TimeoutError:
        pass
    else:
        raise AssertionError("硬截止应能终止预算等待")
    assert hooks.snapshot() == before
    checks["deadline_cancels_wait_without_charge"] = True

    hooks, contexts, planned, waiting_context, target = await pressured_budget()
    before = hooks.snapshot()
    waiter = asyncio.create_task(hooks._reserve_request(waiting_context, target))
    await wait_turn()
    waiter.cancel()
    try:
        await waiter
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("取消应能终止预算等待")
    assert hooks.snapshot() == before
    checks["cancellation_cancels_wait_without_charge"] = True

    manifest = json.loads((HERE / "proposal-manifest.json").read_text(encoding="utf-8"))
    for filename, metadata in manifest["files"].items():
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == metadata["original_sha256"]
    checks["backend_sources_unchanged"] = True
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "isolated_real_record_replay_passed/draft_not_applied",
        "checks": checks, "passed": sum(checks.values()), "total": len(checks),
        "real_evidence": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                          for path in (recovery_path, earlier_path)],
        "details": evidence,
        "not_verified": ["SDK ObservedRunHooks真实claim等待后校验", "registry TaskGroup真实兄弟验收收敛", "Run52结果", "真实模型请求准入等待"],
        "side_effects": {"model_requests": 0, "business_db_access": 0, "backend_writes": 0,
                         "service_control": 0, "new_business_outputs": 0},
        "interpretation": "仅输出候选预算类的真实元数据隔离回放。正常回调使用Run49已记录配对，不是Run51未知请求的虚构返回；原Run51失败与unknown证据保持不变。",
    }
    (HERE / "isolated-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "total": result["total"], "status": result["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
