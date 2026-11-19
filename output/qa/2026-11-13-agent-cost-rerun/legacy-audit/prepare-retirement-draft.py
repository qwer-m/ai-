"""只生成可审查删除草稿，不修改生产文件或数据库。"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).resolve().parent
changes = {}


def change(path, replacements):
    before = (ROOT / path).read_text(encoding="utf-8-sig")
    after = before
    for old, new in replacements:
        if after.count(old) != 1:
            raise ValueError(f"草稿替换需恰好命中一次: {path}: {old!r}")
        after = after.replace(old, new)
    changes[path] = (before, after)


def remove_functions(path, names):
    before = (ROOT / path).read_text(encoding="utf-8-sig")
    lines = before.splitlines(keepends=True)
    nodes = [node for node in ast.parse(before).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise ValueError(f"待删除函数不完整: {path}")
    for node in sorted(nodes, key=lambda node: node.lineno, reverse=True):
        del lines[node.lineno - 1:node.end_lineno]
    after = "".join(lines).rstrip() + "\n"
    changes[path] = (before, after)


remove_functions("backend/modules/agent_platform/checkpoint_storage.py", {"migrate_inline_checkpoint"})
before, after = changes["backend/modules/agent_platform/checkpoint_storage.py"]
changes["backend/modules/agent_platform/checkpoint_storage.py"] = (before, after.replace(
    "旧内嵌检查点必须显式迁移，不能隐式恢复或混合双写",
    "检查点协议不受支持；当前运行仅接受 artifact-journal-v1 的 v3 检查点",
))

for path in (
    "backend/scripts/data_ops/migrations/migrate_action_checkpoint_storage.py",
    "backend/scripts/data_ops/migrations/migrate_workflow_execution_modes.py",
    "backend/scripts/data_ops/migrations/migrate_agent_run_contracts.py",
):
    changes[path] = ((ROOT / path).read_text(encoding="utf-8-sig"), "")

change("backend/scripts/qa/verify/verify_checkpoint_consumers_real_data.py", [
    ('"""真实历史节点显式迁移及预算增量的 MySQL 回滚验证，不发送模型请求。"""', '"""真实 v3 检查点消费者及预算增量的 MySQL 回滚验证，不发送模型请求。"""'),
    ("    # 迁移入口是人工显式调用的离线边界，不能在生产恢复路径里自动升级旧状态。\n    from modules.agent_platform.checkpoint_storage import migrate_inline_checkpoint", "    from modules.agent_platform.checkpoint_storage import compact_checkpoint"),
    ('                compact = migrate_inline_checkpoint(db, run, node)', '                compact = compact_checkpoint(node)'),
    ('checks["explicit_v3_migration"]', 'checks["current_v3_checkpoint"]'),
    ('checks["migration_preserves_usage_and_input_hash"]', 'checks["read_preserves_usage_and_input_hash"]'),
    ('report["node_sdk_state_bytes_after_migration"]', 'report["node_sdk_state_bytes"]'),
    ('checks["rollback_removes_all_migration_artifacts_and_logs"]', 'checks["rollback_removes_all_new_artifacts_and_logs"]'),
    ('仅证明真实历史状态的显式存储迁移和消费者协议；不执行当前业务模型，', '仅证明真实 v3 检查点的消费者协议；不执行当前业务模型，'),
])

change("backend/scripts/qa/verify/verify_checkpoint_history_real_data.py", [
    ('"""用迁移前真实节点备份验证检查点保真；默认迁移后回滚，可只读核验已落库引用。"""', '"""只读比较真实历史备份和已落库 v3 检查点，验证原始证据保真。"""'),
    ('canonical_bytes, content_hash, hydrate_action_checkpoint, hydrate_node_input, migrate_inline_checkpoint,', 'canonical_bytes, content_hash, hydrate_action_checkpoint, hydrate_node_input, compact_checkpoint,'),
    ('    parser.add_argument("--persisted", action="store_true", help="不迁移、不写入，只读核验已提交引用")\n', ''),
    ('report = {"persisted_readback": args.persisted,', 'report = {"persisted_readback": True,'),
    ('            if args.persisted:\n                db.execute(text("SET TRANSACTION READ ONLY"))\n                run = repo.get_run(run_id=original_node["run_id"])\n            else:\n                run = repo.get_run_for_update(run_id=original_node["run_id"])', '            db.execute(text("SET TRANSACTION READ ONLY"))\n            run = repo.get_run(run_id=original_node["run_id"])'),
    ('compact = node.sdk_state["action_checkpoint"] if args.persisted else migrate_inline_checkpoint(db, run, node)', 'compact = compact_checkpoint(node)'),
])

change("backend/scripts/qa/verify/verify_compact_journal_real_data.py", [
    ('    hydrate_action_checkpoint, migrate_inline_checkpoint,', '    hydrate_action_checkpoint,'),
    ('        old = deepcopy(node.sdk_state["action_checkpoint"])\n        compact = migrate_inline_checkpoint(db, run, node)', '        old = hydrate_action_checkpoint(db, node)\n        compact = compact_checkpoint(node)'),
    ('        old = deepcopy(node.sdk_state["action_checkpoint"])\n        source_input =', '        old = hydrate_action_checkpoint(db, node)\n        source_input ='),
    ('        original_child = deepcopy(child.sdk_state["action_checkpoint"])\n        parent_cp = migrate_inline_checkpoint(db, run, parent)', '        original_child = hydrate_action_checkpoint(db, child)\n        parent_cp = compact_checkpoint(parent)'),
])

change("backend/modules/agent_platform/conflict_confirmation.py", [
    ('旧动作检查点需要显式迁移后才能应用确认结果', '动作检查点协议不受支持，不能应用确认结果'),
])
change("backend/modules/agent_platform/serialization.py", [
    ('header["projection"] = {"requires_migration": True}', 'header["projection"] = {"unsupported_checkpoint": True}'),
])
change("frontend/src/components/agent-platform/runPresentation.ts", [
    ("if (projection.requires_migration === true) return '此检查点不属于当前执行契约，需显式迁移';", "if (projection.unsupported_checkpoint === true) return '此检查点协议不受支持，仅保留历史记录';"),
])

changes["backend/tests/agent_platform/test_checkpoint_protocol_real_data.py"] = (
    "", (OUTPUT / "checkpoint_protocol_test_draft.py").read_text(encoding="utf-8"),
)

patch = []
manifest = []
draft_tree = OUTPUT / "draft-tree"
for path, (before, after) in changes.items():
    if before == after:
        raise ValueError(f"草稿没有变化: {path}")
    if after and path.endswith(".py"):
        compile(after, path, "exec")
    if after:
        target = draft_tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
    patch.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile="a/" + path if before else "/dev/null", tofile="b/" + path if after else "/dev/null"))
    manifest.append({"path": path, "operation": "delete" if not after else "rewrite", "before_normalized_sha256": hashlib.sha256(before.encode()).hexdigest(), "before_lines": len(before.splitlines()), "after_lines": len(after.splitlines())})
(OUTPUT / "retire-checkpoint-migration-draft.patch").write_text("".join(patch), encoding="utf-8")
(OUTPUT / "retirement-draft-manifest.json").write_text(json.dumps({"status": "draft_only_not_applied", "files": manifest, "production_or_database_mutations": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"patch": str(OUTPUT / "retire-checkpoint-migration-draft.patch"), "files": len(manifest), "net_removed_lines": sum(item["before_lines"] - item["after_lines"] for item in manifest)}, ensure_ascii=False))
