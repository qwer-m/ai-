"""将旧单项/RF单元样本改成当前完整ID批次形态，保留支持协议的并行改动。"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
path = ROOT / "backend/tests/agent_platform/test_agent_platform_contracts.py"
source = path.read_text(encoding="utf-8-sig")
source = source.replace("    PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,\n", "")
tree = ast.parse(source)
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
replacements = []


def patch_function(node, text):
    if node.name == "test_scope_routes_merge_fact_level_primary_and_shared_assignments":
        text = text.replace('"fact_ref": "RF-001"', '"fact_id": "F-001"', 1)
        text = text.replace('"fact_ref": "RF-001"', '"fact_id": "F-002"', 1)
        text = text.replace("PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA", "PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA")
        return text.replace('{"fact_ref", "fact_id", "assertion"}', '{"fact_id", "assertion"}')

    if node.name == "test_scope_route_postprocessor_normalizes_primary_route_first":
        text = text.replace('"fact_ref": "RF-001",', '"fact_id": "DOC259-P0028-fact2",', 1)
        text = text.replace('                "fact_ref": "RF-001",\n', '', 1)
        text = text.replace("PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA", "PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA")

    local_tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def bounds(value):
        start_column = len(lines[value.lineno - 1].encode("utf-8")[:value.col_offset].decode("utf-8"))
        end_column = len(lines[value.end_lineno - 1].encode("utf-8")[:value.end_col_offset].decode("utf-8"))
        return offsets[value.lineno - 1] + start_column, offsets[value.end_lineno - 1] + end_column

    def segment(value):
        start, end = bounds(value)
        return text[start:end]

    edits = []
    if 'prepared["items"]' in text:
        for value in ast.walk(local_tree):
            if not isinstance(value, ast.Dict):
                continue
            fields = {key.value: item for key, item in zip(value.keys, value.values)
                      if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            if "item_index" not in fields or "output" not in fields:
                continue
            output = fields["output"]
            is_scope = isinstance(output, ast.Dict) and any(isinstance(key, ast.Constant) and key.value == "scope_id" for key in output.keys)
            if is_scope or isinstance(output, ast.Name) and output.id == "output":
                start, end = bounds(output)
                edits.append((start, end, '{"routes": [' + segment(output) + ']}'))
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        return text.replace('prepared["items"]', 'prepared["batch_items"]')

    if node.name.startswith("test_scope_route_postprocessor_"):
        for value in ast.walk(local_tree):
            if isinstance(value, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "item_input" for target in value.targets):
                fields = {key.value: item for key, item in zip(value.value.keys, value.value.values)}
                replacement = ('{"scopes": [{"scope_id": ' + segment(fields["scope_id"])
                    + ', "facts": ' + segment(fields["facts"]) + '}], "business_modules": '
                    + segment(fields["business_modules"]) + '}')
                edits.append((*bounds(value.value), replacement))
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "postprocess_planning_scope_routing_item":
                for argument in value.args:
                    if not isinstance(argument, ast.Dict):
                        continue
                    for key, item in zip(argument.keys, argument.values):
                        if isinstance(key, ast.Constant) and key.value == "item_output":
                            edits.append((*bounds(item), '{"routes": [' + segment(item) + ']}'))
            if isinstance(value, ast.Assign) and isinstance(value.value, ast.Call) and isinstance(value.value.func, ast.Name) and value.value.func.id == "postprocess_planning_scope_routing_item":
                _, end = bounds(value.value)
                edits.append((end, end, '["routes"][0]'))
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
    return text


lines = source.splitlines(keepends=True)
for node in functions:
    if node.name.startswith("test_scope_route"):
        text = "".join(lines[node.lineno - 1:node.end_lineno])
        modified = patch_function(node, text)
        if modified != text:
            replacements.append((node.lineno - 1, node.end_lineno, modified))
for start, end, text in sorted(replacements, reverse=True):
    lines[start:end] = [text]
result = "".join(lines)
compile(result, str(path), "exec")
assert "PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA" not in result
assert "postprocess_planning_support_decisions" in result
path.write_text(result, encoding="utf-8")
print("updated_rf_tests", len(replacements))
