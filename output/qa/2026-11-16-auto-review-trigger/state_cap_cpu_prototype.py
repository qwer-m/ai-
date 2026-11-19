"""仅CPU提案验证；不加载为生产代码、不发模型请求、不改Run77。"""

from copy import deepcopy

def _scope_tool_schema(schema, scope):
    """只在模型请求副本中应用行动范围，注册契约和真实工具校验保持原样。"""
    result = deepcopy(schema)
    for key in ("properties", "$defs", "definitions", "patternProperties"):
        if isinstance(result.get(key), dict):
            result[key] = {name: _scope_tool_schema(child, scope) for name, child in result[key].items()}
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else"):
        if isinstance(result.get(key), dict):
            result[key] = _scope_tool_schema(result[key], scope)
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [_scope_tool_schema(child, scope) for child in result[key]]
    if "x-state-max-items" in result:
        name = result.pop("x-state-max-items")
        bound = scope.get(name) if isinstance(name, str) else None
        if result.get("type") != "array" or type(bound) is not int or bound < result.get("minItems", 0):
            raise ValueError("数组容量注解必须绑定当前行动范围中的有效整数上限")
        result["maxItems"] = min(result.get("maxItems", bound), bound)
    return result


