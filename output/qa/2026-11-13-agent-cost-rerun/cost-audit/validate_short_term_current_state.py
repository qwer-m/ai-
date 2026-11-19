"""回放四次真实短期状态查询，比较完整恢复与当前已校验快照副本。"""

from copy import deepcopy
from difflib import unified_diff
import json
from pathlib import Path
from time import perf_counter

from prepare_read_path_draft import ROOT, module_from_text, replace_once
from prepare_artifact_decode_draft import draft_source
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.knowledge_base_components.context.context_provider import OnDemandContextProvider, ShortTermStateContextSourcePort
from modules.knowledge_base_components.ports.context_memory_port import ContextBudget, ContextQuery


def prepare_patch():
    path = ROOT / "backend/modules/agent_platform/action_journal.py"
    old = path.read_text(encoding="utf-8")
    marker = "    def load(self):\n"
    addition = '''    def load_current_state(self, *, state, user_id, project_id):
        """在原租约和完整内容边界内读取当前状态，避免重复解码同一持久快照。"""
        with self._transaction() as node:
            if (self._run.user_id, self._run.project_id) != (user_id, project_id):
                raise ActionStateConflict("当前短期状态的用户或项目与真实运行不一致")
            checkpoint = self._checkpoint(node)
            if (checkpoint is None or not isinstance(state, dict)
                    or action_state_hash(state) != checkpoint["state_hash"]):
                raise ActionStateConflict("当前短期状态与持久检查点完整内容不一致")
            return deepcopy(state)

'''
    new = replace_once(old, marker, addition + marker)
    patches = [(path, old, new)]
    path = ROOT / "backend/modules/agent_platform/context_agent_tools.py"
    old = path.read_text(encoding="utf-8")
    new = replace_once(old, "state = journal.load_state()", '''state = journal.load_current_state(
                    state=context.action_state, user_id=context.user_id, project_id=context.project_id,
                )''')
    patches.append((path, old, new))
    Path(__file__).with_name("short-term-current-state-draft.patch").write_text("".join(
        "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=str(filename.relative_to(ROOT)), tofile=str(filename.relative_to(ROOT))))
        for filename, before, after in patches), encoding="utf-8")


def main():
    prepare_patch()
    path, _, code = draft_source()
    storage = module_from_text("modules.agent_platform._qa_artifact_decode", path, code)
    snapshot = json.loads((ROOT / "output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json").read_text(encoding="utf-8"))
    checkpoint = snapshot["expanded_checkpoints"]["404"]
    receipts = checkpoint["receipts"]
    cases = [tool for tool in snapshot["nodes"][-1]["sdk_state"]["tool_calls"]
        if tool.get("tool_key") == "retrieve_context" and tool["arguments"]["source_kinds"] == ["short_term_state"]]
    results = []
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        for tool in cases:
            receipt = receipts[tool["tool_call_id"]]
            ref = storage.ArtifactStore(db, user_id=1, project_id=8)._ref(receipt["pre_state_hash"])
            current = storage.ArtifactStore(db, user_id=1, project_id=8).get(ref)
            arguments = tool["arguments"]
            query = ContextQuery(text=arguments["query"], user_id=1, project_id=8,
                source_kinds=tuple(arguments["source_kinds"]), max_candidates=arguments["max_candidates"])
            budget = ContextBudget(max_candidates=arguments["max_candidates"], max_chars=arguments["max_chars"])

            def restored_loader():
                return ArtifactStore(db, user_id=1, project_id=8).get(ref)

            def current_loader():
                # 与草稿日志方法相同的完整内容校验和独立副本，未执行历史终态租约事务。
                assert action_state_hash(current) == ref["sha256"]
                return deepcopy(current)

            def provider(loader):
                port = ShortTermStateContextSourcePort(loader, run_id=59, node_key="agent_network", user_id=1, project_id=8)
                return OnDemandContextProvider({"short_term_state": port})

            before = action_state_hash(current)
            started = perf_counter()
            original_bundle = provider(restored_loader).retrieve(query=query, budget=budget)
            old_seconds = perf_counter() - started
            started = perf_counter()
            revised_bundle = provider(current_loader).retrieve(query=query, budget=budget)
            new_seconds = perf_counter() - started
            assert original_bundle.manifest.to_dict() == revised_bundle.manifest.to_dict()
            assert original_bundle.rendered_text == revised_bundle.rendered_text
            saved = tool["result"]
            assert original_bundle.manifest.to_dict() == saved["manifest"]
            assert original_bundle.rendered_text == saved["context_text"]
            assert before == action_state_hash(current)
            results.append({"action_sequence": receipt["started_sequence"], "pre_state_sha256": ref["sha256"],
                "query": arguments["query"], "previous_seconds": old_seconds, "current_copy_seconds": new_seconds,
                "speedup_ratio": old_seconds / new_seconds, "saved_manifest_and_text_exact": True,
                "source_unchanged": True, "selected_source_count": len(saved["manifest"]["selected"])})
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    report = {"run_id": 59, "queries": results, "production_code_modified": False,
        "model_requests_sent": 0, "business_database_writes": 0,
        "bounds": "真实历史四次查询及行动前完整状态；比较内容恢复与校验副本的实际provider路径。历史Run已终止，未借用或修改其租约执行新journal方法；草稿保留原_transaction fencing/branch并增加tenant/fullhash校验，但该事务方法尚需正式回归。"}
    output = Path(__file__).with_name("short-term-current-state-validation.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
