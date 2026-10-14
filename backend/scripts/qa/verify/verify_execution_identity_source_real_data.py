"""独立复算真实执行源码身份，并仅在临时副本模拟私有实现变化。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_ROOT))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def text_digest(path: Path) -> str:
    # 独立按文本内容计算，不使用生产代码的 mtime/size 缓存。
    content = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify(run_id: int) -> dict:
    from sqlalchemy import text
    from core.db.database import SessionLocal
    from core.db.model_defs import AgentRun
    from modules.agent_platform import registry
    from modules.agent_platform.service import AgentPlatformService

    callbacks = {key: registry.tool_registry.resolve(key) for key in registry.tool_registry.keys()}
    strategies = {
        key: {name: getattr(registry.tool_registry.resolve_repair_strategy(key), name)
              for name in ("build_context", "feedback")}
        for key in registry.tool_registry.repair_strategy_keys()
    }
    files = set()
    for directory in ("modules/agent_platform", "core/ai", "modules/knowledge_base_components"):
        files.update((BACKEND_ROOT / directory).rglob("*.py"))
    for callback in [*callbacks.values(), *(item for strategy in strategies.values() for item in strategy.values())]:
        files.add(Path(inspect.getsourcefile(inspect.getmodule(callback))).resolve())
    manifest = [(path.relative_to(BACKEND_ROOT).as_posix(), text_digest(path)) for path in sorted(files)]
    source_hash = digest(manifest)
    assert source_hash == registry.execution_source_signature(), "生产源码摘要与独立内容复算不一致"

    def identity(callback):
        return f"{callback.__module__}.{callback.__qualname__}"

    contract = {
        "agents": registry.BUILTIN_AGENT_SPECS, "tools": registry.BUILTIN_TOOL_SPECS,
        "workflows": registry.BUILTIN_WORKFLOW_SPECS,
        "handlers": {key: identity(callback) for key, callback in callbacks.items()},
        "parallel_safe_handlers": registry.tool_registry.parallel_safe_keys(),
        "state_only_handlers": registry.tool_registry.state_only_keys(),
        "repair_strategies": {key: {name: identity(callback) for name, callback in strategy.items()}
                              for key, strategy in strategies.items()},
        "execution_source_signature": source_hash,
    }
    registry_hash = digest(contract)
    assert registry_hash == registry.runtime_registry_signature(), "注册契约未纳入同一源码摘要"
    planning_path = BACKEND_ROOT / "modules/agent_platform/test_generation_planning.py"
    planning_text = planning_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(planning_text)
    functions = {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef)}
    public = functions["validate_business_plan_output"]
    private_name = next(
        call.func.id for call in ast.walk(public)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id.startswith("_") and call.func.id in functions
    )
    helper = functions[private_name]
    original_public = ast.get_source_segment(planning_text, public)
    with TemporaryDirectory(prefix="agent-execution-identity-") as temporary:
        snapshot = Path(temporary)
        for relative, _ in manifest:
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((BACKEND_ROOT / relative).read_bytes())
        assert digest([(name, text_digest(snapshot / name)) for name, _ in manifest]) == source_hash
        changed = snapshot / planning_path.relative_to(BACKEND_ROOT)
        lines = planning_text.splitlines(keepends=True)
        lines.insert(helper.body[0].lineno - 1, "    # 仅临时验证副本：私有实现源码发生变化。\n")
        changed.write_text("".join(lines), encoding="utf-8", newline="\n")
        changed_text = changed.read_text(encoding="utf-8")
        changed_public = next(item for item in ast.parse(changed_text).body
                              if isinstance(item, ast.FunctionDef) and item.name == public.name)
        assert ast.get_source_segment(changed_text, changed_public) == original_public
        changed_manifest = [(name, text_digest(snapshot / name)) for name, _ in manifest]
        changed_files = [name for (name, before), (_, after) in zip(manifest, changed_manifest) if before != after]
        assert changed_files == [planning_path.relative_to(BACKEND_ROOT).as_posix()]
        changed_source_hash = digest(changed_manifest)
        changed_registry_hash = digest({**contract, "execution_source_signature": changed_source_hash})
        assert changed_source_hash != source_hash and changed_registry_hash != registry_hash

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, run_id)
        assert run is not None and run.status == "failed"
        saved_signature = (run.run_context or {}).get("runtime_registry_signature")
        assert saved_signature and saved_signature != registry_hash
        retry, reason = AgentPlatformService(db).retry_run(run_id=run_id, user_id=run.user_id)
        assert retry is None and reason == "run_version_mismatch"
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    assert [(name, text_digest(BACKEND_ROOT / name)) for name, _ in manifest] == manifest, "验证期间生产源码被修改"
    assert registry.runtime_registry_signature() == registry_hash, "验证期间注册身份发生变化"
    return {
        "status": "passed", "verification_scope": "real_source_content_and_temporary_source_copy",
        "source_file_count": len(manifest), "execution_source_signature": source_hash,
        "runtime_registry_signature": registry_hash, "independent_content_hash_matches": True,
        "copy_only_change": {"file": changed_files[0], "private_function": private_name,
                             "public_handler_unchanged": True, "source_signature": changed_source_hash,
                             "runtime_registry_signature": changed_registry_hash},
        "source_run_id": run_id, "source_run_registry_signature": saved_signature,
        "retry_service_result": reason, "production_sources_unchanged": True,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0, "model_requests_sent": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("拒绝覆盖已有验证报告")
    report = verify(options.run_id)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "source_file_count", "retry_service_result", "model_requests_sent")}))


if __name__ == "__main__":
    main()
