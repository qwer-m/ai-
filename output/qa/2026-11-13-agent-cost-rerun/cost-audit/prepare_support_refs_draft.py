"""生成当前唯一fact_ref模型协议草稿；领域回执仍使用稳定fact_id。"""

from difflib import unified_diff
from pathlib import Path

from prepare_read_path_draft import ROOT, module_from_text, replace_once


def changes():
    directory = ROOT / "backend/modules/agent_platform"
    result = []
    path = directory / "test_generation_schemas.py"
    old = path.read_text(encoding="utf-8")
    marker = '_support_agent_decision_schema["required"].remove("disposition")'
    addition = '''
# 模型只在本次完整输入内提交整数引用；持久领域协议始终使用原稳定事实ID。
_support_ref_array_schema = {"type": "array", "uniqueItems": True,
                             "items": {"type": "integer", "minimum": 1}}
_support_agent_decision_schema["properties"].pop("fact_ids")
_support_agent_decision_schema["properties"]["fact_refs"] = deepcopy(_support_ref_array_schema)
_support_agent_decision_schema["required"] = [
    "fact_refs" if field == "fact_ids" else field for field in _support_agent_decision_schema["required"]
]
for _support_agent_gap_schema in _support_agent_decision_schema["properties"]["support_gaps"]["items"]["oneOf"]:
    _support_agent_gap_schema["properties"].pop("evidence_fact_ids")
    _support_agent_gap_schema["properties"]["evidence_fact_refs"] = deepcopy(_support_ref_array_schema)
    _support_agent_gap_schema["required"] = [
        "evidence_fact_refs" if field == "evidence_fact_ids" else field
        for field in _support_agent_gap_schema["required"]
    ]
    if "conflict" in _support_agent_gap_schema["properties"]:
        _support_agent_side_schema = _support_agent_gap_schema["properties"]["conflict"]["properties"]["sides"]["items"]
        _support_agent_side_schema["properties"].pop("fact_id")
        _support_agent_side_schema["properties"]["fact_ref"] = {"type": "integer", "minimum": 1}
        _support_agent_side_schema["required"] = ["fact_ref", "quote"]
'''
    new = replace_once(old, marker, marker + addition)
    new = replace_once(new, 'PLANNING_SUPPORT_MODEL_INPUT_PROJECTION_VERSION = "planning-support-v5-shared-source"',
        'PLANNING_SUPPORT_MODEL_INPUT_PROJECTION_VERSION = "planning-support-v6-fact-refs"')
    result.append((path, old, new))

    path = directory / "test_generation_semantics.py"
    old = path.read_text(encoding="utf-8")
    marker = "def planning_support_source_review(facts: list[dict[str, Any]]) -> dict[str, Any]:"
    helper = '''def planning_support_fact_ref_catalog(facts: list[dict[str, Any]]) -> dict[int, str]:
    """当前请求的引用目录只由完整稳定ID排序生成，不依赖字典或事实读取顺序。"""
    if not isinstance(facts, list) or any(not isinstance(fact, dict) for fact in facts):
        raise ValueError("支持复核引用目录缺少完整事实")
    identities = [required_text(fact.get("fact_id"), "fact_id") for fact in facts]
    if (len(set(identities)) != len(identities)
            or any(identity != fact["fact_id"] for identity, fact in zip(identities, facts, strict=True))):
        raise ValueError("支持复核引用目录的稳定事实身份重复或无效")
    return dict(enumerate(sorted(identities), start=1))


'''
    new = replace_once(old, marker, helper + marker)
    new = replace_once(new,
        '    projected = [{key: deepcopy(fact[key]) for key in PLANNING_SUPPORT_FACT_FIELDS if key in fact} for fact in facts]\n    return authority_source_review_view(projected)',
        '''    refs_by_id = {identity: reference for reference, identity in planning_support_fact_ref_catalog(facts).items()}
    projected = [{**{key: deepcopy(fact[key]) for key in PLANNING_SUPPORT_FACT_FIELDS if key in fact},
                  "fact_ref": refs_by_id[fact["fact_id"]]} for fact in facts]
    return authority_source_review_view(projected)''')
    result.append((path, old, new))

    path = directory / "test_generation_planning.py"
    old = path.read_text(encoding="utf-8")
    new = replace_once(old, "def postprocess_planning_route_repair_item(\n", "def postprocess_planning_support_decisions(\n")
    marker = "\n\ndef planning_support_request_hash(item: dict[str, Any]) -> str:"
    decoder = '''

def postprocess_planning_route_repair_item(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """唯一当前模型提交协议：校验可见目录并把整数引用恢复为稳定事实ID。"""
    from jsonschema import ValidationError, validate
    from .test_generation_schemas import PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA
    from .test_generation_semantics import planning_support_fact_ref_catalog, planning_support_source_review

    prepared = dict(arguments.get("item_input") or {})
    facts = prepared.get("candidate_facts")
    catalog = planning_support_fact_ref_catalog(facts)
    if action_state_hash(prepared.get("source_review")) != action_state_hash(planning_support_source_review(facts)):
        raise ValueError("支持复核模型可见事实目录与当前完整输入不一致")
    output = deepcopy(arguments.get("item_output"))
    try:
        validate(instance=output, schema=PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA)
    except ValidationError as error:
        raise OutputValidationError("支持复核必须使用当前fact_ref输出协议",
            details={"field_path": list(error.absolute_path), "validation_message": error.message}) from error

    def fact_id(reference):
        # JSON Schema的integer可能接受1.0；引用身份必须是真正整数，布尔也不能冒充编号。
        if type(reference) is not int or reference not in catalog:
            raise OutputValidationError("支持复核引用不在当前可见fact_ref目录中",
                details={"invalid_fact_ref": reference, "allowed_fact_refs": list(catalog)})
        return catalog[reference]

    def fact_ids(references):
        values = [fact_id(reference) for reference in references]
        if len(set(values)) != len(values):
            raise OutputValidationError("支持复核事实引用不能重复")
        return values

    for decision in output["decisions"]:
        decision["fact_ids"] = fact_ids(decision.pop("fact_refs"))
        for gap in decision["support_gaps"]:
            gap["evidence_fact_ids"] = fact_ids(gap.pop("evidence_fact_refs"))
            if gap["kind"] == "source_conflict":
                for side in gap["conflict"]["sides"]:
                    # 同一事实可以承载两段不同原文；双方唯一性继续按原(fact_id,quote)核对。
                    side["fact_id"] = fact_id(side.pop("fact_ref"))
    return postprocess_planning_support_decisions(context, {"item_input": prepared, "item_output": output})
'''
    new = replace_once(new, marker, decoder + marker)
    result.append((path, old, new))

    path = directory / "test_generation_planning_agent.py"
    old = path.read_text(encoding="utf-8")
    # 已标准化领域回执仅走稳定ID校验，SDK登记的模型后处理键保持原入口且仅收短ref。
    new = old.replace("postprocess_planning_route_repair_item", "postprocess_planning_support_decisions")
    assert old.count("postprocess_planning_route_repair_item") == 5
    new = new.replace('item_postprocessor="testing.postprocess_planning_support_decisions"',
        'item_postprocessor="testing.postprocess_planning_route_repair_item"')
    new = replace_once(new, "    planning_support_source_review, planning_support_model_payload,",
        "    planning_support_source_review, planning_support_model_payload, planning_support_fact_ref_catalog,")
    new = replace_once(new, '        fact_ids = [fact["fact_id"] for fact in item["candidate_facts"]]',
        '''        catalog = planning_support_fact_ref_catalog(item["candidate_facts"])
        refs_by_id = {identity: reference for reference, identity in catalog.items()}
        fact_refs = list(catalog)''')
    new = replace_once(new,
        '        quote_candidates = {(fact["fact_id"], fact["assertion"]): {"fact_id": fact["fact_id"], "quote": fact["assertion"]}',
        '        quote_candidates = {(fact["fact_id"], fact["assertion"]): {"fact_ref": refs_by_id[fact["fact_id"]], "quote": fact["assertion"]}')
    new = replace_once(new, '                             "disposition": "unsupported", "fact_ids": fact_ids,',
        '                             "fact_refs": fact_refs,')
    new = replace_once(new, '                                 "evidence_fact_ids": fact_ids, "reason": design["coverage_intent"],',
        '                                 "evidence_fact_refs": fact_refs, "reason": design["coverage_intent"],')
    result.append((path, old, new))
    return result


def load_draft_modules():
    result = changes()
    for name in ("test_generation_schemas", "test_generation_semantics", "test_generation_planning", "test_generation_planning_agent"):
        path, _, code = next(entry for entry in result if entry[0].stem == name)
        for dependency in ("test_generation_schemas", "test_generation_semantics", "test_generation_planning"):
            code = code.replace(f"from .{dependency} import", f"from ._qa_refs_{dependency} import")
        module_from_text(f"modules.agent_platform._qa_refs_{name}", path, code)
    import sys
    return {entry[0].stem: sys.modules[f"modules.agent_platform._qa_refs_{entry[0].stem}"] for entry in result}


if __name__ == "__main__":
    result = changes()
    patch = "".join("".join(unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path.relative_to(ROOT)), tofile=str(path.relative_to(ROOT)))) for path, old, new in result)
    Path(__file__).with_name("support-fact-refs-draft.patch").write_text(patch, encoding="utf-8")
    load_draft_modules()
    print("4文件fact_ref草稿已生成并通过独立模块编译；尚未写入生产。")
