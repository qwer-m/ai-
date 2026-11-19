"""仅生成旧 RF / 单项路由兼容删除草稿，不修改并行编辑的生产文件。"""

import difflib
import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
changes = {}


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"路由草稿替换必须唯一命中: {old!r}")
    return source.replace(old, new)


path = "backend/modules/agent_platform/test_generation_planning.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
after = replace_once(before, '''                {
                    **_planning_fact_model_view(dict(fact)),
                    "fact_ref": f"RF-{fact_index + 1:03d}",
                }
                for fact_index, fact in enumerate(item["facts"])
''', '''                _planning_fact_model_view(dict(fact))
                for fact in item["facts"]
''')
after = replace_once(after, '"model_fact_fields": ["fact_ref", "fact_id", "assertion"],', '"model_fact_fields": ["fact_id", "assertion"],')
start = after.index("    prepared_fact_refs = [\n")
end = after.index('    assignments = output.get("assignments")\n', start)
after = after[:start] + after[end:]
start = after.index('        fact_ref = str(assignment.get("fact_ref") or "").strip()\n')
end = after.index('        assignment["fact_id"] = fact_id\n', start)
after = after[:start] + '''        if set(assignment) != {"fact_id", "module_routes"}:
            raise ValueError("规划索引投影只接受完整 fact_id 与模块映射")
        fact_id = assignment.get("fact_id")
        if not isinstance(fact_id, str) or not fact_id or fact_id in assignments_by_fact_id:
            raise ValueError(
                "规划路由必须逐条且仅路由当前 scope 的全部 fact_id: "
                f"scope_id={expected_scope_id}"
            )
''' + after[end:]
start = after.index('    if "scopes" in prepared:\n', after.index("def postprocess_planning_scope_routing_item("))
end = after.index("\n\ndef _expand_planning_scope_route_batches(", start)
after = after[:start] + '''    return _normalize_planning_scope_route_batch(
        prepared=prepared,
        raw_output=raw_output,
    )
''' + after[end:]
start = after.index('    if not prepared_items or "scopes" not in dict(prepared_items[0] or {}):\n')
end = after.index('        raise ValueError("规划路由批次结果数量不一致")\n', start)
after = after[:start] + '    if not prepared_items or len(prepared_items) != len(route_records):\n' + after[end:]
after = replace_once(after, '        if int(record.get("item_index", batch_index)) != batch_index:\n',
    '        if type(record.get("item_index")) is not int or record["item_index"] != batch_index:\n')
after = replace_once(after, '        if int(record.get("item_index", item_index)) != item_index:\n',
    '        if type(record.get("item_index")) is not int or record["item_index"] != item_index:\n')
after = after.replace('"""在单个路由实例完成时规范化输出并校验其真实输入边界。"""',
    '"""校验当前完整批次的索引投影，不解释旧单项或短引用协议。"""')
after = after.replace('"""把模型批次恢复为既有的逐 scope 路由数据流。"""',
    '"""把显式编号的当前批次展开为逐 scope 索引，供共享完整性校验。"""')
changes[path] = (before, after)

path = "backend/modules/agent_platform/test_generation_schemas.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
start = before.index("PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA = deepcopy(\n")
end = before.index("# 归属只由业务边界决定", start)
after = before[:start] + before[end:]
changes[path] = (before, after)

path = "backend/modules/agent_platform/test_generation_workflow.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
after = replace_once(before, "\n    postprocess_planning_scope_routing_item,\n", "\n")
after = replace_once(after, '''    registry.register(
        "testing.postprocess_planning_scope_routing_item",
        postprocess_planning_scope_routing_item,
        cache_safe=True,
    )
''', "")
changes[path] = (before, after)

patch = []
manifest = []
for path, (before, after) in changes.items():
    compile(after, path, "exec")
    target = BASE / "rf-retirement-draft-tree" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8")
    patch.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                    fromfile="a/" + path, tofile="b/" + path))
    manifest.append({"path": path, "before_normalized_sha256": hashlib.sha256(before.encode()).hexdigest(),
                     "after_normalized_sha256": hashlib.sha256(after.encode()).hexdigest(),
                     "before_lines": len(before.splitlines()), "after_lines": len(after.splitlines())})
(BASE / "rf-routing-retirement-draft.patch").write_text("".join(patch), encoding="utf-8")
(BASE / "rf-routing-retirement-manifest.json").write_text(json.dumps({
    "status": "draft_only_not_applied", "files": manifest,
    "net_removed_lines": sum(row["before_lines"] - row["after_lines"] for row in manifest),
    "production_mutations": 0, "support_integer_refs_changed": False,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2))
