"""人工暂停观测透传草稿；生产运行期间只写 QA 隔离目录。"""

import difflib
import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
changes = {}


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"暂停观测替换必须唯一命中: {old!r}")
    return source.replace(old, new)


path = "backend/modules/agent_platform/sdk_adapter.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
helper = '''def _attach_execution_observations(error: BaseException, *, model_requests,
                                   tool_calls, tool_observations=None) -> None:
    """暂停与普通异常共享真实观测附件，不分类失败或结算预算。"""
    error.tool_calls = deepcopy(tool_calls)
    error.diagnostic = {**dict(getattr(error, "diagnostic", None) or {}),
                        "model_requests": deepcopy(model_requests),
                        **({"tool_observations": deepcopy(tool_observations)}
                           if tool_observations is not None else {})}


'''
after = replace_once(before, "async def run_agent_async(\n", helper + "async def run_agent_async(\n")
after = replace_once(after, '''        if not isinstance(exc, ConflictConfirmationRequired):
            execution_context.record_failure(exc)
''', '''        if isinstance(exc, ConflictConfirmationRequired):
            # 人工暂停是 BaseException 控制流，必须在通用早退前保留本阶段观测。
            _attach_execution_observations(exc, model_requests=request_diagnostics.requests,
                tool_calls=execution_context.tool_calls[tool_call_start:],
                tool_observations=autonomous_loop.events if autonomous_loop is not None else None)
            raise
        execution_context.record_failure(exc)
''')
after = replace_once(after, '''            exc.tool_calls = deepcopy(execution_context.tool_calls[tool_call_start:])
            exc.diagnostic = {
                **dict(getattr(exc, "diagnostic", None) or {}),
                "tool_observations": autonomous_loop.events,
                "budget_usage": budget_hooks.snapshot() if budget_hooks is not None else {},
            }
''', '''            _attach_execution_observations(exc, model_requests=request_diagnostics.requests,
                tool_calls=execution_context.tool_calls[tool_call_start:],
                tool_observations=autonomous_loop.events)
            exc.diagnostic["budget_usage"] = budget_hooks.snapshot() if budget_hooks is not None else {}
''')
changes[path] = (before, after)

path = "backend/modules/agent_platform/runtime.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
after = replace_once(before, "def _exception_chain(exc: Exception)", "def _exception_chain(exc: BaseException)")
after = replace_once(after, "def _agent_error_diagnostic(exc: Exception)", "def _agent_error_diagnostic(exc: BaseException)")
after = replace_once(after, "def _failed_agent_tool_calls(exc: Exception)", "def _failed_agent_tool_calls(exc: BaseException)")
helper = '''def _append_execution_observations(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """同一节点人工恢复时保留前段观测；新节点不继承的边界由恢复入口负责。"""
    values = {}
    for key in ("model_requests", "tool_calls", "tool_observations"):
        if isinstance(current.get(key), list):
            recorded = previous.get(key, [])
            if not isinstance(recorded, list):
                raise ValueError("已保存的执行观测必须是数组")
            values[key] = deepcopy([*recorded, *current[key]])
    return values


def _set_paused_execution_observations(node_run: AgentNodeRun, pause: ConflictConfirmationRequired) -> None:
    """只保存暂停阶段已附带的观测，保留检查点、计量游标和其他控制字段。"""
    state = dict(node_run.sdk_state or {})
    diagnostic = _agent_error_diagnostic(pause) or {}
    observations = {key: diagnostic[key] for key in ("model_requests", "tool_observations")
                    if isinstance(diagnostic.get(key), list)}
    if isinstance(getattr(pause, "tool_calls", None), list):
        observations["tool_calls"] = _failed_agent_tool_calls(pause)
    node_run.sdk_state = {**state, **_append_execution_observations(state, observations)}


'''
after = replace_once(after, "def _agent_failure_kind(exc: Exception)", helper + "def _agent_failure_kind(exc: Exception)")
after = replace_once(after, '''        node_run.status = "waiting_approval"
        node_run.finished_at = _now()
''', '''        _set_paused_execution_observations(node_run, pause)
        node_run.status = "waiting_approval"
        node_run.finished_at = _now()
''')
after = replace_once(after, '''            "tool_calls": result.tool_calls,
            "tool_observations": result.tool_observations,
            "model_requests": result.model_requests,
''', '''            **_append_execution_observations(dict(setup.node_run.sdk_state or {}), {
                "tool_calls": result.tool_calls, "tool_observations": result.tool_observations,
                "model_requests": result.model_requests,
            }),
''')
changes[path] = (before, after)

changes["backend/tests/agent_platform/test_pause_observability_real_data.py"] = (
    "", (BASE / "pause_observability_test_draft.py").read_text(encoding="utf-8"),
)

patch, manifest = [], []
for path, (before, after) in changes.items():
    compile(after, path, "exec")
    target = BASE / "pause-observability-draft-tree" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8")
    patch.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                    fromfile="a/" + path if before else "/dev/null", tofile="b/" + path))
    manifest.append({"path": path, "before_normalized_sha256": hashlib.sha256(before.encode()).hexdigest(),
                     "before_lines": len(before.splitlines()), "after_lines": len(after.splitlines())})
(BASE / "pause-observability-draft.patch").write_text("".join(patch), encoding="utf-8")
(BASE / "pause-observability-draft-manifest.json").write_text(json.dumps({
    "status": "draft_only_not_applied", "production_mutations": 0, "files": manifest,
    "does_not_reconstruct_run61_missing_95_requests": True,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2))
