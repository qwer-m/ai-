"""在独立模块验证只读视图草稿，不改运行中服务的源码。"""

from copy import deepcopy
from difflib import unified_diff
import json
from pathlib import Path
import sys
from time import perf_counter
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_agent as original_generation


def blocked(*args, **kwargs):
    raise AssertionError("只读观察尝试修改真实输入或状态")


class ReadOnlyDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = blocked

    def __deepcopy__(self, memo):
        return {key: deepcopy(value, memo) for key, value in self.items()}


class ReadOnlyList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = blocked

    def __deepcopy__(self, memo):
        return [deepcopy(value, memo) for value in self]


def readonly(value):
    if isinstance(value, dict):
        result = ReadOnlyDict()
        for key, item in value.items():
            dict.__setitem__(result, key, readonly(item))
        return result
    if isinstance(value, list):
        result = ReadOnlyList()
        for item in value:
            list.append(result, readonly(item))
        return result
    return value


def module_from_text(name, filename, source):
    module = ModuleType(name)
    module.__file__ = str(filename)
    sys.modules[name] = module
    exec(compile(source, str(filename), "exec"), module.__dict__)
    return module


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def source_draft():
    source_path = ROOT / "backend/modules/agent_platform/test_generation_source_agent.py"
    original = source_path.read_text(encoding="utf-8")
    begin = original.index("def hydrate_source_context(")
    end = original.index("\n\ndef initialize_source(", begin)
    replacement = '''def source_context_view(context, source: dict[str, Any] | None = None):
    """只读来源视图保留完整验证；调用方不能修改共享的原文和领域状态。"""
    current = source if source is not None else context.action_state.get("source")
    artifacts = dict(context.artifacts)
    if current is not None:
        _validate(current)
        artifacts.update(current["artifacts"])
    return replace(context, artifacts=artifacts, executed_tools=[], tool_calls=[])


def hydrate_source_context(context, source: dict[str, Any] | None = None):
    """修改领域动作使用独立副本；合并完实际来源后只复制一次。"""
    view = source_context_view(context, source)
    return replace(view, artifacts=deepcopy(view.artifacts))
'''
    return source_path, original, original[:begin] + replacement + original[end:]


def generation_draft():
    path = ROOT / "backend/modules/agent_platform/test_generation_agent.py"
    original = path.read_text(encoding="utf-8")
    changed = replace_once(original,
        "return replace(source_context, artifacts=deepcopy(source_context.artifacts), executed_tools=[], tool_calls=[],",
        "return replace(source_context, executed_tools=[], tool_calls=[],")
    marker = "\n\ndef _state_snapshot(context):"
    helper = '''

def _planning_observation_context(context, state):
    """观察沿共享只读视图验证；修改行动仍使用 _planning_context 的独立副本。"""
    from .test_generation_source_agent import source_context_view

    view = source_context_view(context)
    return replace(view, action_journal=None, action_state=state["planning"],
                   action_input=state["prepared_input"])
'''
    changed = replace_once(changed, marker, helper + marker)
    changed = replace_once(changed,
        'plan = planning.observe_planning_state(_planning_context(context, state)) if state["planning"] is not None else None',
        'plan = planning.observe_planning_state(_planning_observation_context(context, state)) if state["planning"] is not None else None')
    return path, original, changed


def main():
    changes = [source_draft(), generation_draft()]
    patch = "".join("".join(unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path.relative_to(ROOT)), tofile=str(path.relative_to(ROOT)))) for path, old, new in changes)
    Path(__file__).with_name("read-path-draft.patch").write_text(patch, encoding="utf-8")
    source = module_from_text("modules.agent_platform._qa_readpath_source", changes[0][0], changes[0][2])
    generation_text = changes[1][2].replace("from .test_generation_source_agent import", "from ._qa_readpath_source import")
    generation = module_from_text("modules.agent_platform._qa_readpath_generation", changes[1][0], generation_text)

    snapshot = json.loads((ROOT / "output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json").read_text(encoding="utf-8"))
    run = snapshot["run"]
    created = json.loads((ROOT / "output/qa/2026-11-12-single-agent-live/round-11/created-run.json").read_text(encoding="utf-8"))["run"]
    state = snapshot["expanded_checkpoints"]["404"]["state"]
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        context = ToolExecutionContext(db=db, user_id=run["user_id"], project_id=run["project_id"],
            run_id=run["id"], node_key="agent_network", run_input=run["input_payload"],
            action_input=run["input_payload"], action_state=state,
            artifacts=created["run_context"]["artifacts"], confirmed_conflicts=state["conflict_confirmations"])
        original_digest = action_state_hash([context.action_state, context.action_input, context.artifacts])
        started = perf_counter()
        previous = original_generation.observe_generation_state(context)
        old_seconds = perf_counter() - started
        started = perf_counter()
        revised = generation.observe_generation_state(context)
        new_seconds = perf_counter() - started
        assert previous == revised
        assert original_digest == action_state_hash([context.action_state, context.action_input, context.artifacts])
        # 冻结嵌套容器时仍可观察，证明当前真实分支没有依赖共享输入上的写操作。
        frozen = ToolExecutionContext(db=db, user_id=run["user_id"], project_id=run["project_id"],
            run_id=run["id"], node_key="agent_network", run_input=readonly(run["input_payload"]),
            action_input=readonly(run["input_payload"]), action_state=readonly(state),
            artifacts=readonly(created["run_context"]["artifacts"]),
            confirmed_conflicts=readonly(state["conflict_confirmations"]))
        assert generation.observe_generation_state(frozen) == previous
        # 修改路径仍持有副本，改变其中的真实嵌套结构不能影响父领域和原文。
        isolated = generation._planning_context(context, state)
        assert isolated.action_state is not state["planning"]
        assert isolated.action_input is not state["prepared_input"]
        assert isolated.artifacts is not context.artifacts
        assert isolated.artifacts["requirement_evidence"] is not state["source"]["artifacts"]["requirement_evidence"]
        isolated.action_state["planning_records"][0]["output"]["batch_summary"] = "仅用于隔离性核验的本地变更"
        assert original_digest == action_state_hash([context.action_state, context.action_input, context.artifacts])
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    report = {"source_run_id": 59, "scope": "成功末态只读观察，未覆盖所有中间领域状态",
        "production_code_modified": False, "artifact_scope": "created_run_initial_artifacts", "previous_seconds": old_seconds, "draft_seconds": new_seconds,
        "speedup_ratio": old_seconds / new_seconds, "result_exact": True,
        "result_sha256": action_state_hash(previous), "source_unchanged": True,
        "mutating_context_still_independent": True, "nested_write_guard_observation_passed": True,
        "model_requests_sent": 0, "business_database_writes": 0}
    Path(__file__).with_name("read-path-draft-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
