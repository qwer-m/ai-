"""共享并发策略的隔离草稿；只写 QA 目录，不改运行中的文件。"""

import ast
import difflib
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
BASE = Path(__file__).resolve().parent
TREE = BASE / "concurrency-draft-tree"
changes = {}


def read(path):
    return (ROOT / path).read_text(encoding="utf-8-sig")


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"并发草稿替换必须唯一命中: {old!r}")
    return source.replace(old, new)


path = "backend/modules/agent_platform/retry_policy.py"
before = read(path)
after = before
index = after.index("def restorable_sdk_state(")
after = after[:index] + (BASE / "adaptive_concurrency_class_draft.py").read_text(encoding="utf-8").rstrip() + "\n\n\n" + after[index:]
changes[path] = (before, after)

path = "backend/modules/agent_platform/runtime.py"
before = read(path)
after = replace_once(before, "    RetryAttemptState,\n", "    AdaptiveConcurrency,\n    RetryAttemptState,\n")
start = after.index("    effective_concurrency = min(\n", after.index("async def _execute_agent_map_parallel_async"))
end = after.index("    concurrency_adjustments = [\n", start)
after = after[:start] + '''    concurrency = AdaptiveConcurrency.restore(
        max_concurrency=max_concurrency, min_concurrency=min_concurrency,
        pressure_threshold=int(settings.AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES),
        recovery_successes=int(settings.AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES),
        checkpoint=previous_parallelism,
    )
    effective_concurrency = concurrency.effective_concurrency
''' + after[end:]
start = after.index("    successful_since_concurrency_adjustment = 0\n", start)
end = after.index("    pending: dict[\n", start)
after = after[:start] + after[end:]
after = replace_once(after, '"pressure_failures": pressure_failures_since_concurrency_adjustment,', '"pressure_failures": concurrency.pressure_failures,\n                    "successful_since_adjustment": concurrency.successful_since_adjustment,')
start = after.index("    def adjust_concurrency(")
end = after.index("    async def cancel_pending_instances(", start)
after = after[:start] + '''    def publish_concurrency_adjustment(adjustment: dict[str, Any]) -> None:
        """共享纯策略决定上限，原 Runtime 仍负责诊断、调度和持久化。"""
        nonlocal effective_concurrency
        effective_concurrency = concurrency.effective_concurrency
        adjustment = {**adjustment, "completed_instances": len(completed_by_index)}
        concurrency_adjustments.append(adjustment)
        del concurrency_adjustments[:-20]
        _event(repo, run, "map_concurrency_adjusted",
               {"node_key": node.node_key, **adjustment}, node_run=node_run)

''' + after[end:]
start = after.index("                    successful_since_concurrency_adjustment += 1\n")
end = after.index("                except Exception as exc:\n", start)
after = after[:start] + '''                    adjustment = concurrency.record_success()
                    if adjustment is not None:
                        publish_concurrency_adjustment(adjustment)
''' + after[end:]
start = after.index("                    if retryable and _is_concurrency_pressure_failure(failure_kind):\n")
end = after.index("                    error_diagnostic = _agent_error_diagnostic(exc)\n", start)
after = after[:start] + '''                    if retryable and _is_concurrency_pressure_failure(failure_kind):
                        adjustment = concurrency.record_failure(failure_kind, retryable=retryable)
                        if adjustment is not None:
                            publish_concurrency_adjustment(adjustment)
''' + after[end:]
changes[path] = (before, after)

path = "backend/modules/agent_platform/action_journal.py"
before = read(path)
after = replace_once(before, "    def save_budget(self, snapshot):\n", (BASE / "journal_parallelism_methods_draft.py").read_text(encoding="utf-8").rstrip() + "\n\n    def save_budget(self, snapshot):\n")
changes[path] = (before, after)

path = "backend/modules/agent_platform/sdk_adapter.py"
before = read(path)
after = replace_once(before, "from contextvars import ContextVar\n", "from contextvars import ContextVar\nfrom contextlib import asynccontextmanager\n")
after = replace_once(after, "from core.ai.usage import normalized_usage\n", "from core.ai.usage import normalized_usage\nfrom core.settings.config import settings\n")
after = replace_once(after, "from .registry import ToolExecutionContext, tool_registry\n", "from .registry import ToolExecutionContext, tool_registry\nfrom .retry_policy import AdaptiveConcurrency, model_transport_failure_kind\n")
index = after.index("async def run_agent_async(\n")
after = after[:index] + (BASE / "managed_session_gate_draft.py").read_text(encoding="utf-8").rstrip() + "\n\n\n" + after[index:]
after = replace_once(after,
    "        execution_context.agent_concurrency = 1 if execution_context.stop_on_failure else managed_concurrency\n",
    "        execution_context.agent_concurrency = (1 if execution_context.stop_on_failure\n            else min(managed_concurrency, int(settings.AGENT_MAP_MAX_CONCURRENCY)))\n")
after = replace_once(after, "        allowed_agents = frozenset(managed_keys)\n", '''        allowed_agents = frozenset(managed_keys)
        session_gates: dict[str, _ManagedSessionConcurrency] = {}

        def managed_session_gate(child):
            from .action_journal import action_state_hash

            scope = _managed_session_scope(client, child)
            scope_key = action_state_hash(scope)
            if scope_key not in session_gates:
                policy = AdaptiveConcurrency.restore(
                    max_concurrency=execution_context.agent_concurrency,
                    min_concurrency=min(execution_context.agent_concurrency, int(settings.AGENT_MAP_MIN_CONCURRENCY)),
                    pressure_threshold=int(settings.AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES),
                    recovery_successes=int(settings.AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES),
                    checkpoint=journal.managed_parallelism(scope_key=scope_key, scope=scope),
                )
                def persist(snapshot, adjustment):
                    journal.save_managed_parallelism(scope_key=scope_key, scope=scope,
                                                    policy=snapshot, adjustment=adjustment)
                session_gates[scope_key] = _ManagedSessionConcurrency(policy=policy, on_change=persist)
            return session_gates[scope_key]
''')
start = after.index("                child_result = await run_agent_async(\n", after.index("        async def invoke_agent(\n"))
end = after.index("            except Exception as error:\n", start)
block = after[start:end]
after = after[:start] + "                # 只准入新会话；上方回执命中已返回，嵌套咨询不重复取得本许可。\n                async with managed_session_gate(child).session():\n" + "".join("    " + line if line.strip() else line for line in block.splitlines(keepends=True)) + after[end:]
changes[path] = (before, after)

changes["backend/tests/agent_platform/test_managed_concurrency_real_data.py"] = (
    "", (BASE / "concurrency_policy_test_draft.py").read_text(encoding="utf-8"),
)

patch = []
manifest = []
for path, (before, after) in changes.items():
    compile(after, path, "exec")
    target = TREE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8")
    patch.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile="a/" + path if before else "/dev/null", tofile="b/" + path))
    manifest.append({"path": path, "before_normalized_sha256": hashlib.sha256(before.encode()).hexdigest(),
                     "before_lines": len(before.splitlines()), "after_lines": len(after.splitlines())})
(BASE / "managed-concurrency-draft.patch").write_text("".join(patch), encoding="utf-8")
(BASE / "managed-concurrency-draft-manifest.json").write_text(json.dumps({"status": "draft_only_not_applied", "files": manifest, "production_or_database_mutations": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"files": len(manifest), "net_line_delta": sum(x["after_lines"] - x["before_lines"] for x in manifest)}, ensure_ascii=False))
