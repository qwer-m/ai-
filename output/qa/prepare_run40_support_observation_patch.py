"""仅生成 Run40 观察与参数诊断候选补丁，禁止写入生产目录。"""

from pathlib import Path
import difflib
import hashlib
import json


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "run40-support-observation-candidate"
OUT.mkdir(exist_ok=True)
changes = {}


def change(name, old, new):
    relative = f"backend/modules/agent_platform/{name}"
    if relative not in changes:
        original = (ROOT / relative).read_text(encoding="utf-8")
        changes[relative] = [original, original]
    current = changes[relative][1]
    assert current.count(old) == 1, (name, old[:100], current.count(old))
    changes[relative][1] = current.replace(old, new, 1)


change("test_generation_planning_agent.py",
    "def _route_feedback(state: dict[str, Any]) -> list[dict[str, Any]]:\n",
    '''def _current_support_review(state: dict[str, Any], index: int) -> dict[str, Any] | None:
    """缺口和可选目录共用同一份当前裁决，后续定向复核只覆盖实际选中的意图。"""
    record = state["gap_records"][index]
    if record is None:
        return None
    review = deepcopy(record["output"])
    current = {decision["test_design_item_index"]: decision for decision in review["decisions"]}
    module_index = state["gap_inputs"][index]["module_index"]
    for revision in state["route_review_revisions"]:
        if revision["input"]["module_index"] == module_index:
            for decision in revision["output"]["decisions"]:
                current[decision["test_design_item_index"]] = deepcopy(decision)
    review["decisions"] = [current[key] for key in sorted(current)]
    return review


def _reviewable_designs(state: dict[str, Any]) -> list[dict[str, Any]]:
    """可选范围包含已支持项；按真实裁决分组，避免被误读为待办清单。"""
    if _pending(state["gap_records"]):
        return []
    return [{"module_index": item["module_index"],
             "design_indexes_by_disposition": {
                 disposition: [decision["test_design_item_index"] for decision in review["decisions"]
                               if decision["disposition"] == disposition]
                 for disposition in ("supported", "unsupported")}}
            for index, item in enumerate(state["gap_inputs"])
            if (review := _current_support_review(state, index)) is not None]


def _route_feedback(state: dict[str, Any]) -> list[dict[str, Any]]:
''')
change("test_generation_planning_agent.py",
    '''        record = state["gap_records"][index]
        designs = _feedback_designs(item["missing_test_design_items"], origins)
        facts_by_id = {fact["fact_id"]: fact for fact in item["candidate_facts"]}
        review = deepcopy(record["output"]) if record is not None else None
        if review is not None:
            current = {decision["test_design_item_index"]: decision for decision in review["decisions"]}
            for revision in state["route_review_revisions"]:
                if revision["input"]["module_index"] == item["module_index"]:
                    facts_by_id.update({fact["fact_id"]: fact for fact in revision["input"]["candidate_facts"]})
                    for decision in revision["output"]["decisions"]:
                        current[decision["test_design_item_index"]] = deepcopy(decision)
            review["decisions"] = [current[key] for key in sorted(current) if current[key]["disposition"] == "unsupported"]
''',
    '''        designs = _feedback_designs(item["missing_test_design_items"], origins)
        facts_by_id = {fact["fact_id"]: fact for fact in item["candidate_facts"]}
        review = _current_support_review(state, index)
        if review is not None:
            for revision in state["route_review_revisions"]:
                if revision["input"]["module_index"] == item["module_index"]:
                    facts_by_id.update({fact["fact_id"]: fact for fact in revision["input"]["candidate_facts"]})
            review["decisions"] = [decision for decision in review["decisions"] if decision["disposition"] == "unsupported"]
''')
change("test_generation_planning_agent.py",
    '''        "reviewable_designs": [{"module_index": item["module_index"],
                                "test_design_item_indexes": [design["test_design_item_index"] for design in item["missing_test_design_items"]]}
                               for item in state["gap_inputs"]] if not _pending(state["gap_records"]) else [],
''',
    '''        "reviewable_designs": _reviewable_designs(state),
''')
change("test_generation_planning_agent.py",
    '''                "additional_scope_ids": arguments.get("additional_scope_ids", []),
            })["items"]
        except ValueError as error:
            raise OutputValidationError(str(error)) from error
''',
    '''                "additional_scope_ids": arguments.get("additional_scope_ids", []),
            })["items"]
        except OutputValidationError:
            # 选择校验已经提供真实非法位置和合法范围，不能在行动边界丢掉结构化信息。
            raise
        except ValueError as error:
            raise OutputValidationError(str(error)) from error
''')
change("test_generation_agent.py",
    '''actions["review_planning_route_gaps"] = {"design_selections": deepcopy(plan["reviewable_designs"]),''',
    '''actions["review_planning_route_gaps"] = {"available_designs": deepcopy(plan["reviewable_designs"]),''')
change("test_generation_planning.py",
    '''        for selection in selected:
            index = selection.get("module_index")
            indexes = selection.get("test_design_item_indexes")
''',
    '''        for selection_position, selection in enumerate(selected):
            available_module_indexes = [index for index in range(len(modules)) if state["facts_by_module"][index]]
            if not isinstance(selection, dict):
                raise OutputValidationError("支持复核选择必须是模块及意图索引对象", details={
                    "selection_position": selection_position, "invalid_selection": deepcopy(selection),
                    "available_module_indexes": available_module_indexes,
                })
            index = selection.get("module_index")
            indexes = selection.get("test_design_item_indexes")
''')
change("test_generation_planning.py",
    '''                raise ValueError("支持复核选择了无效或重复的模块及覆盖意图")
            missing_by_module[index] = sorted(indexes)
''',
    '''                available_indexes = list(range(len(_planning_module_design_catalog(modules[index])))) if (
                    type(index) is int and index in available_module_indexes) else []
                requested_indexes = indexes if isinstance(indexes, list) else []
                raise OutputValidationError("支持复核选择了无效或重复的模块及覆盖意图", details={
                    "selection_position": selection_position, "module_index": deepcopy(index),
                    "requested_test_design_item_indexes": deepcopy(indexes),
                    "invalid_test_design_item_indexes": [deepcopy(value) for value in requested_indexes
                        if type(value) is not int or value not in available_indexes],
                    "duplicate_test_design_item_indexes": sorted({value for value in requested_indexes
                        if type(value) is int and requested_indexes.count(value) > 1}),
                    "module_already_selected": type(index) is int and index in missing_by_module,
                    "available_module_indexes": available_module_indexes,
                    "available_test_design_item_indexes": available_indexes,
                })
            missing_by_module[index] = sorted(indexes)
''')
change("test_generation_builtin_specs.py",
    '''            "已有支持边错误或缺失时可按 reviewable_designs 定向选择意图并提供 question 重新复核；支持版本变化会使下游产物失效。"
            "unresolved_design_selections 精确列出未决目标；support_gaps 给出原意图片段、缺口类型和真实证据。"
''',
    '''            "已有支持边错误或缺失时可按 available_actions 中的 available_designs 定向选择意图并提供 question 重新复核；"
            "该可选目录按当前 supported/unsupported 裁决分组，不是待执行参数或未决清单。"
            "支持版本变化只使下游产物失效，其他意图已有支持裁决仍由当前状态保留。"
            "unresolved_design_selections 精确列出未决目标；support_gaps 给出原意图片段、缺口类型和真实证据。"
''')

patch = []
manifest = {"production_files_written": False, "model_requests_sent": 0, "online_database_writes": 0, "files": []}
for relative, (old, new) in changes.items():
    compile(new, str(OUT / Path(relative).name), "exec")
    (OUT / Path(relative).name).write_text(new, encoding="utf-8")
    patch.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                   fromfile="a/" + relative, tofile="b/" + relative))
    manifest["files"].append({"path": relative,
        "production_sha256": hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(new.encode()).hexdigest()})
target = OUT.parent / "2026-09-28-run40-support-observation-candidate.patch"
target.write_text("".join(patch), encoding="utf-8")
(OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(target)
