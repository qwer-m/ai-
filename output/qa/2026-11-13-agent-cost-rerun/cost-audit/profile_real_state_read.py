"""对真实输入和不可变快照做一次只读 CPU/SQL 剖析，不派发模型。"""

import argparse
import cProfile
from copy import deepcopy
import io
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import event, text
from core.db.database import SessionLocal
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_agent import initialize_generation_state, observe_generation_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["initialize", "observe_final"])
    args = parser.parse_args()
    original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json"
    snapshot = json.loads(original.read_text(encoding="utf-8"))
    run = snapshot["run"]
    created = json.loads((ROOT / "output/qa/2026-11-12-single-agent-live/round-11/created-run.json").read_text(encoding="utf-8"))["run"]
    statements = []
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        context = ToolExecutionContext(db=db, user_id=run["user_id"], project_id=run["project_id"],
            run_id=run["id"], node_key="agent_network", run_input=deepcopy(run["input_payload"]),
            artifacts=deepcopy(created["run_context"]["artifacts"]))
        if args.operation == "observe_final":
            context.action_input = deepcopy(run["input_payload"])
            context.action_state = snapshot["expanded_checkpoints"]["404"]["state"]
            context.confirmed_conflicts = deepcopy(context.action_state["conflict_confirmations"])
        engine = db.get_bind()

        def before(conn, cursor, statement, parameters, context, executemany):
            context._qa_started_at = perf_counter()

        def after(conn, cursor, statement, parameters, context, executemany):
            statements.append({"kind": statement.lstrip().split()[0],
                "elapsed_seconds": perf_counter() - context._qa_started_at})

        event.listen(engine, "before_cursor_execute", before)
        event.listen(engine, "after_cursor_execute", after)
        profiler = cProfile.Profile()
        started = perf_counter()
        profiler.enable()
        try:
            result = (initialize_generation_state(context, deepcopy(run["input_payload"]))
                if args.operation == "initialize" else observe_generation_state(context))
        finally:
            profiler.disable()
            elapsed = perf_counter() - started
            event.remove(engine, "before_cursor_execute", before)
            event.remove(engine, "after_cursor_execute", after)
        assert context.invoke_agent is None and context.action_journal is None
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    stats = pstats.Stats(profiler).sort_stats("cumulative")
    top = [{"file": key[0], "line": key[1], "name": key[2],
        "primitive_calls": value[0], "calls": value[1], "self_seconds": value[2], "cumulative_seconds": value[3]}
        for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:65]]
    report = {"run_id": 59, "operation": args.operation, "wall_seconds_with_profiler": elapsed,
        "sql_count": len(statements), "sql_seconds_sum": sum(item["elapsed_seconds"] for item in statements),
        "sql_verbs": sorted({item["kind"] for item in statements}),
        "model_requests_sent": 0, "business_database_writes": 0,
        "result_json_bytes": len(json.dumps(result, ensure_ascii=False).encode()), "top_functions": top,
        "limits": "一次只读CPU剖析，cProfile本身有开销；真实历史输入，当前代码。没有把剖析时间当作线上精确耗时。"}
    output = Path(__file__).with_name(f"profile-{args.operation}.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "top_functions"}, ensure_ascii=False))
    for row in top[:20]:
        print(f"{row['cumulative_seconds']:.3f}s {row['self_seconds']:.3f}s {row['calls']} {row['file']}:{row['line']} {row['name']}")


if __name__ == "__main__":
    main()
