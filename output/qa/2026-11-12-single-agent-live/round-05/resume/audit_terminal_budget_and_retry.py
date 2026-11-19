"""真实终态与正规续跑能力的只读审计；不创建 Run，不写业务库，不发送请求。"""

import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from modules.agent_platform.retry_policy import restorable_sdk_state, RetryAttemptState


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


paths = {name: HERE / filename for name, filename in (
    ("final", "final-snapshot.json"), ("resumed", "after-approval.json"),
    ("pause_baseline", "independent-before-baseline.json"), ("samples", "observations.json"),
)}
final, resumed, baseline, observations = [read(paths[name]) for name in ("final", "resumed", "pause_baseline", "samples")]
run = final["run"]
node = next(item for item in final["nodes"] if item["id"] == baseline["node_id"])
cp = final["expanded_checkpoints"][str(node["id"])]
resumed_cp = resumed["expanded_checkpoints"][str(node["id"])]
budget, oldbudget = cp["budget"], baseline["budget"]
diagnostic = node["sdk_state"]["error_diagnostic"]
requests = diagnostic["model_requests"]
reported_delta = {key: budget["reported_usage"][key] - oldbudget["reported_usage"][key]
                  for key in budget["reported_usage"]}
request_usage = {key: sum(item.get("usage", {}).get(key, 0) for item in requests)
                 for key in reported_delta}
batch = diagnostic["batch_progress"]
failed_action = cp["projection"]["current_action"]
current_success_calls = {key: value for key, value in cp["validated_agent_calls"].items()
                         if value["parent_tool_call_id"] == failed_action["tool_call_id"]}
all_indexes = set(range(batch["total_count"]))
partitions = [set(batch[key]) for key in ("validated_indexes", "failed_indexes", "not_started_indexes")]
samples = []
for observed in observations["snapshots"]:
    saved = observed["snapshot"]["latest_node"]["checkpoint"]
    samples.append({"observed_at": observed["observed_at"],
                    "status": observed["snapshot"]["run"]["status"],
                    "charged": saved["budget_charged"], "reported": saved["budget_reported_usage"],
                    "pending": saved["budget_pending_requests"], "completed": saved["budget_completed_requests"],
                    "interrupted": saved["budget_interrupted_requests"]})
checks = {
    "failed_at_frozen_3m_budget_not_at_recovery": run["status"] == "failed" and node["sdk_state"]["last_failure_kind"] == "agent_budget_exhausted" and "input_tokens=3019269/3000000" in run["error_message"],
    "frozen_limits_never_changed": budget["limits"] == oldbudget["limits"] == resumed_cp["budget"]["limits"],
    "all_66_resumed_requests_have_real_responses": len(requests) == 66 and all(item["status"] == "response_received" and "usage" in item for item in requests),
    "all_incremental_reported_usage_matches_responses": request_usage == reported_delta,
    "charged_equals_reported_actual_tokens": all(budget["charged"][key] == budget["reported_usage"][key] for key in ("input_tokens", "output_tokens", "total_tokens")),
    "all_199_attempts_completed_without_unknown": budget["charged"]["attempted_requests"] == budget["completed_requests"] == budget["reported_usage"]["requests"] == 199 and budget["pending_requests"] == budget["interrupted_requests"] == 0,
    "terminal_spending_under_frozen_limits": all(value <= budget["limits"][key] for key, value in budget["charged"].items()),
    "blocked_request_not_added_to_attempts": budget["charged"]["attempted_requests"] == oldbudget["charged"]["attempted_requests"] + len(requests),
    "paused_109_professional_records_unchanged": all(key in cp["validated_agent_calls"] and digest(cp["validated_agent_calls"][key]) == expected for key, expected in baseline["validated_call_digests"].items()),
    "resumed_110_professional_records_unchanged": all(cp["validated_agent_calls"].get(key) == value for key, value in resumed_cp["validated_agent_calls"].items()),
    "paused_24_completed_actions_unchanged": all(key in cp["receipts"] and digest(cp["receipts"][key]) == expected for key, expected in baseline["receipt_digests"].items()),
    "final_171_professional_results_saved": len(cp["validated_agent_calls"]) == 171,
    "failed_batch_keeps_all_14_validated_results": len(current_success_calls) == len(batch["validated_indexes"]) == 14,
    "batch_distinguishes_4_rejected_23_unstarted_14_validated": [len(part) for part in partitions] == [14, 4, 23] and set.union(*partitions) == all_indexes and not any(partitions[i] & partitions[j] for i in range(3) for j in range(i)),
    "all_observed_recovery_samples_have_zero_unknown": all(item["interrupted"] == 0 for item in samples),
}
evidence = [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths.values()]
terminal = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "status": "budget_guard_and_complete_drain_verified/full_generation_failed" if all(checks.values()) else "terminal_budget_verification_failed",
    "run_id": 53, "node_id": node["id"], "checks": checks, "passed": sum(checks.values()), "total": len(checks),
    "budget_before_human_resume": oldbudget, "budget_at_terminal": budget,
    "resumed_request_status_counts": dict(Counter(item["status"] for item in requests)),
    "resumed_request_agent_counts": dict(Counter(item["agent_key"] for item in requests)),
    "resumed_reported_usage_delta": reported_delta, "resumed_response_usage_sum": request_usage,
    "denied_next_request": {"projected_input": 3019269, "charged_input": budget["charged"]["input_tokens"],
                            "reserved_input": 3019269 - budget["charged"]["input_tokens"],
                            "input_limit": budget["limits"]["input_tokens"],
                            "remaining_input": budget["limits"]["input_tokens"] - budget["charged"]["input_tokens"],
                            "interpretation": "最终在0pending时下一请求需142060输入预留，但只余122791，永久不足而确定拒绝；未超额计账。"},
    "batch_progress": batch, "current_failed_action": failed_action,
    "saved_success_call_keys_in_failed_batch": sorted(current_success_calls),
    "recovery_samples": samples,
    "wait_path_evidence_boundary": "线上直接证明所有已发66请求完整结算、原109/110成功记录保留、当前14结果在批次失败前保存且0unknown。当前预算钩子没有等待入队/唤醒事件或计数，不能凭0unknown伪造确切wait次数或时长；最终拒绝在0pending且预算真正不足的边界符合当前实现。",
    "remaining_unverified": ["完整测试用例生成和交付", "交付幂等", "每次预算等待的确切开始/唤醒时间"],
    "evidence_files": evidence,
    "side_effects": {"business_table_reads": 0, "business_db_writes": 0, "api_calls": 0, "model_requests": 0, "source_changes": 0, "service_control": 0,
                     "import_time_database_probe": "compact_checkpoint依赖导入core.db.database，该模块按既有启动逻辑连接MySQL并立即关闭；本审计未创建Session或查询/修改业务表。"},
}
(HERE / "budget-terminal-verification.json").write_text(json.dumps(terminal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# 仅恢复实际SDK元数据对象，不创建ORM Run/业务会话或分配新执行ID。
# compact_checkpoint依赖导入会按既有模块行为做连接探测，但没有业务表读写。
restored = restorable_sdk_state(SimpleNamespace(**node))
configured_attempts = run["run_context"]["execution_contract"]["workflow"]["definition"]["max_attempts"]
policy_failure = None
try:
    RetryAttemptState.restore(restored).require_retry_budget(configured_max_attempts=configured_attempts)
except RuntimeError as error:
    policy_failure = {"error_type": type(error).__name__, "message": str(error)}

sources = {
    "router": ROOT / "backend/routers/orchestration/agents.py",
    "service": ROOT / "backend/modules/agent_platform/service.py",
    "retry_policy": ROOT / "backend/modules/agent_platform/retry_policy.py",
    "runtime": ROOT / "backend/modules/agent_platform/runtime.py",
    "budget": ROOT / "backend/modules/agent_platform/autonomous_budget.py",
    "usage": ROOT / "backend/modules/agent_platform/usage.py",
}
router_tree, service_tree = [ast.parse(sources[key].read_text(encoding="utf-8-sig")) for key in ("router", "service")]
router_method = next(item for item in router_tree.body if isinstance(item, ast.FunctionDef) and item.name == "retry_run")
service_class = next(item for item in service_tree.body if isinstance(item, ast.ClassDef) and item.name == "AgentPlatformService")
service_method = next(item for item in service_class.body if isinstance(item, ast.FunctionDef) and item.name == "retry_run")
capability = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "status": "unsupported/current_formal_retry_cannot_grant_larger_budget",
    "question": "不改任何生产源码签名、不改Run53，能否通过现有正规续跑接口给新attempt更高额度并保留状态/消费？",
    "answer": "不能。当前retry不接收预算变更；它保留原冻结限额、原共享账本和不可重试失败闩锁。没有现成的授权扩额度续跑入口。",
    "current_retry_failure_behavior": {"kind": "source_derived_not_an_actual_new_run", "finding": "若源码签名仍一致且其他前置条件满足，当前service.retry_run会创建复制旧预算/失败闩锁的子Run；worker在_prepare_node_execution读取继承retry_exhausted=True后拒绝，不能继续模型请求。缺少在入口拒绝无效续跑或接收显式增额授权的产品/服务边界。该结论未通过创建新Run在线重演。"},
    "current_endpoint": {"path": "/api/agents/runs/{run_id}/retry", "http_method": "POST",
                         "router_args": [item.arg for item in router_method.args.args],
                         "service_args": [item.arg for item in (*service_method.args.args, *service_method.args.kwonlyargs)],
                         "execution_limits_request_body": False},
    "actual_restore_policy_check": {"configured_max_attempts": configured_attempts,
        "retry_exhausted_retained": restored["retry_exhausted"], "last_failure_kind_retained": restored["last_failure_kind"],
        "checkpoint_budget_unchanged": restored["action_checkpoint"]["budget"] == budget,
        "require_retry_budget_result": policy_failure,
        "interpretation": "实际Node396的纯元数据回放已经命中原候选重试耗尽；不是在线创建过的新attempt结果。"},
    "blocking_contracts": [
        {"path": "backend/routers/orchestration/agents.py", "line": 580, "finding": "retry路由只收run_id和依赖，不存在execution_limits请求体。"},
        {"path": "backend/modules/agent_platform/service.py", "line": 668, "finding": "重试入口先校验原runtime_registry_signature；不允许换代码后静默续旧版本。"},
        {"path": "backend/modules/agent_platform/service.py", "line": 701, "finding": "execution_limits逐值复制父Run；提高服务进程环境上限不会给此重试重新解析额度。"},
        {"path": "backend/modules/agent_platform/retry_policy.py", "line": 19, "finding": "restorable_sdk_state保留action_checkpoint.budget和retry_exhausted，只剔除历史大诊断/跨Run的action_usage_recorded。"},
        {"path": "backend/modules/agent_platform/retry_policy.py", "line": 123, "finding": "require_retry_budget见retry_exhausted=True立即拒绝；Run53最后类别为agent_budget_exhausted，不是暂态故障。"},
        {"path": "backend/modules/agent_platform/runtime.py", "line": 1051, "finding": "若有saved_budget，实际request reservation直接取旧saved_budget.limits；只改新Run.execution_limits仍不会改共享3m cap。"},
        {"path": "backend/modules/agent_platform/autonomous_budget.py", "line": 167, "finding": "恢复预算要求checkpoint.limits与传入reservation完全一致；不能只把更高reservation塞入旧账本。"},
        {"path": "backend/modules/agent_platform/service.py", "line": 833, "finding": "reset-attempt只改变展示基线，不重新授予次数/Token，也不解除失败闩锁。"},
    ],
    "required_explicit_new_attempt_migration_fields": [
        {"field": "新Run.parent_run_id / logical_task_id / attempt_id / run_attempt", "rule": "新物理Run指向53、沿同一logical_task_id分配下一attempt；旧Run53及其3m/199消费保持原样。"},
        {"field": "新Run.execution_limits与checkpoint.budget.limits", "rule": "必须由明确授权定义同一份新累计硬上限，同时提升input/total等相关分项并校验平台上限；先说明是累计cap还是追加额度，不能暗中发一套新的空白预算。"},
        {"field": "checkpoint.budget.charged / reported_usage / completed / pending / interrupted", "rule": "原199次、input2877209、output120083、total2997292、pending0/interrupted0完整继承，不退款、不清零。"},
        {"field": "checkpoint.quota_base_usage 与新Run.agent_instance_quota_usage / quota_usage", "rule": "保留共享预算建立前的原基线；新Run按base+新cap一次预留，不能复制旧3m预留后又追加导致重复占用，也不能用旧quota计数和新cap制造不一致。"},
        {"field": "sdk.action_usage / action_usage_recorded 与新Run.usage", "rule": "新Run消费游标绑定新run_id、原input_hash，以原199实际用量为baseline；新Run本身只统计续跑增量，累计消费仍在共享checkpoint与父Run中，不重复入账。"},
        {"field": "retry_exhausted / last_failure_kind / content_failure_counts / transient计数", "rule": "仅在明确授权改变原预算约束后解除该预算耗尽的执行闩锁；原失败原因及其他内容/暂态重试计数必须可追溯，禁止通用清空所有失败历史。"},
        {"field": "input_ref/input_hash、state_ref/state_hash、journal_ref及171专业回执", "rule": "保持来源、用户、项目和同逻辑任务；新节点分支按正式存储边界继承并复验原专业结果，不复制虚假成功或丢弃已验收结果。"},
        {"field": "conflict_confirmations / Approval13原始请求与决定", "rule": "人工两项原话与原来源证据一起保留；追加预算不等于新的业务批准或跳过验收。"},
        {"field": "新attempt deadline与confirmation_wait_seconds", "rule": "给新attempt确定执行时限，区分父attempt已排除的5477.658710秒和新attempt实际人工等待；旧等待不得重复授时或误扣。"},
        {"field": "runtime_registry_signature / execution_contract / execution_semantic_hash /专业identity", "rule": "若源码/模型/定义完全不变可按原语义继承；若为新增接口改了签名范围内源码，必须显式版本迁移和结果复验，禁止只重写签名让旧状态看似当前。"},
        {"field": "新attempt额度授权审计与派发记录", "rule": "记录操作者、原/新cap、父消费、理由、原始授权和预期检查点指纹；与新Run及正式dispatch原子提交。"},
    ],
    "non_solutions": ["仅提高环境变量或平台配置", "给现有retry HTTP随意附加未声明body字段", "改旧Run53额度/消费", "清空saved budget或retry_exhausted冒充普通重试", "只改execution_limits而保留旧checkpoint.budget.limits", "修改签名字符串绕过执行版本校验"],
    "source_files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sources.values()],
    "side_effects": {"new_runs_created": 0, "business_table_reads": 0, "business_db_writes": 0, "api_calls": 0, "source_changes": 0, "service_control": 0, "model_requests": 0,
                     "import_time_database_probe": "恢复纯函数导入既有数据库模块触发一次连接探测，无业务表读写；控制状态和共享账本仅在内存复制。"},
}
(HERE / "budget-continuation-capability.json").write_text(json.dumps(capability, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"terminal_checks_passed": terminal["passed"], "terminal_checks_total": terminal["total"],
                  "false_checks": [key for key, value in checks.items() if not value],
                  "formal_retry_supports_budget_grant": False, "restore_policy_block": policy_failure}, ensure_ascii=False))
