"""对真实历史内容寻址状态验证一次物化草稿，不修改产物或生产代码。"""

from difflib import unified_diff
import json
from pathlib import Path
import sys
from time import perf_counter

from prepare_read_path_draft import ROOT, module_from_text, replace_once
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform import checkpoint_storage as original
from modules.agent_platform.action_journal import action_state_hash


def draft_source():
    path = ROOT / "backend/modules/agent_platform/checkpoint_storage.py"
    source = path.read_text(encoding="utf-8")
    begin, end = source.index("    def get(self, ref:"), source.index("\n\ndef store_for_node")
    body = source[begin:end]
    changed = replace_once(body, "return deepcopy(decoded[identity])", "return decoded[identity]")
    changed = replace_once(changed, 'return deepcopy(entry["value"])', 'return entry["value"]')
    changed = replace_once(changed, 'value = deepcopy(payload["value"])', 'value = payload["value"]')
    changed = replace_once(changed, "            return deepcopy(value)\n        return expand(ref)", '''            return value

        def materialize(value):
            # 展开缓存只在本次 get 内共享；交付时每个出现位置各自拥有可变容器。
            # 不使用 deepcopy 的 memo，防止相同内容引用在不同路径上形成可变别名。
            if isinstance(value, dict):
                return {key: materialize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [materialize(item) for item in value]
            return value

        return materialize(expand(ref))''')
    return path, source, source[:begin] + changed + source[end:]


def mutable_aliases(value):
    locations, duplicate = {}, []

    def walk(item, path):
        if isinstance(item, (dict, list)):
            if id(item) in locations:
                duplicate.append([locations[id(item)], path])
            else:
                locations[id(item)] = path
            for key, child in (item.items() if isinstance(item, dict) else enumerate(item)):
                walk(child, path + [key])
    walk(value, [])
    return duplicate


def main():
    path, old, new = draft_source()
    Path(__file__).with_name("artifact-decode-draft.patch").write_text("".join(unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path.relative_to(ROOT)), tofile=str(path.relative_to(ROOT)))), encoding="utf-8")
    revised = module_from_text("modules.agent_platform._qa_artifact_decode", path, new)
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        node_id = db.execute(text("SELECT MAX(id) FROM agent_node_runs WHERE run_id=53 AND node_key='agent_network'")).scalar_one()
        packed = db.execute(text("SELECT JSON_EXTRACT(sdk_state,'$.action_checkpoint') FROM agent_node_runs WHERE id=:node_id"), {"node_id": node_id}).scalar_one()
        checkpoint = json.loads(packed) if isinstance(packed, str) else packed
        ref = checkpoint["state_ref"]
        started = perf_counter()
        baseline = original.ArtifactStore(db, user_id=1, project_id=8).get(ref)
        previous_seconds = perf_counter() - started
        started = perf_counter()
        result = revised.ArtifactStore(db, user_id=1, project_id=8).get(ref)
        draft_seconds = perf_counter() - started
        assert baseline == result
        assert action_state_hash(result) == ref["sha256"]
        aliases = mutable_aliases(result)
        assert not aliases
        # 多次读取也不能共享上次返回的可变图。
        started = perf_counter()
        second = revised.ArtifactStore(db, user_id=1, project_id=8).get(ref)
        second_seconds = perf_counter() - started
        before = action_state_hash(second)
        result["source"]["artifacts"]["requirement_evidence"]["requirement"] = "仅用于本地隔离性核验的变更"
        assert before == action_state_hash(second) == ref["sha256"]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    report = {"run_id": 53, "node_id": node_id, "state_sha256": ref["sha256"],
        "previous_seconds": previous_seconds, "draft_seconds": draft_seconds,
        "draft_second_seconds": second_seconds, "speedup_ratio": previous_seconds / draft_seconds,
        "result_exact": True, "mutable_alias_count": len(aliases), "independent_reads": True,
        "production_code_modified": False, "model_requests_sent": 0, "business_database_writes": 0,
        "limits": "单一真实大状态CPU比较，保留所有get校验分支；错误分支及完整回归尚未执行。"}
    Path(__file__).with_name("artifact-decode-draft-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
