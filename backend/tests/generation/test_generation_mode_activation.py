from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from modules.test_generation_components.control.generation_mode_activation import (
    build_generation_mode_control_state,
    infer_generation_coverage_profile,
    merge_generation_mode_control_state,
    resolve_linked_final_case_signal,
)
from modules.test_generation_components.control.current_requirement_blueprint import (
    extract_current_requirement_blueprints,
)
from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import stream_postprocess_cases
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    normalize_final_case_priorities,
    strip_case_meta_fields,
)
from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context


def _coverage_strategy(mode: str, expected_count: int) -> dict[str, object]:
    return {
        "coverage_targets": {
            "mode": mode,
            "target_case_range": {"min": expected_count, "max": expected_count},
        }
    }


class _NoopClient:
    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs):  # noqa: ANN001, ARG002
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        yield "[]"


class _SupplementClient(_NoopClient):
    def __init__(self, cases):
        self.cases = cases
        self.prompts: list[str] = []

    def generate_response(self, user_input: str, system_prompt: str = "", db=None, **kwargs):  # noqa: ANN001, ARG002
        self.prompts.append(user_input)
        return __import__("json").dumps(self.cases, ensure_ascii=False)


class _StreamingSupplementClient(_NoopClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.stream_prompts: list[str] = []

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        self.stream_prompts.append(prompt)
        response = self.responses.pop(0) if self.responses else []
        yield json.dumps(response, ensure_ascii=False)


_FACT_CORE_FIELDS = (
    "fact_kind",
    "statement",
    "requirement_level",
    "priority",
    "testability",
    "evidence",
)


def _fact_core(
    fact: object,
    *,
    source_evidence_catalog: list[dict[str, object]],
) -> tuple[object, ...] | None:
    if not isinstance(fact, dict):
        return None
    quote_by_ref = {
        str(item.get("ref") or ""): str(item.get("quote") or "")
        for item in source_evidence_catalog
        if isinstance(item, dict) and item.get("ref") and item.get("quote")
    }
    return tuple(
        tuple(
            sorted(
                {
                    quote_by_ref.get(str(item or ""), str(item or ""))
                    for item in fact.get(field) or []
                    if str(item or "")
                }
            )
        )
        if field == "evidence"
        else str(fact.get(field) or "")
        for field in _FACT_CORE_FIELDS
    )


def _request_scope_facts(request_payload: dict[str, object]) -> list[dict[str, object]]:
    schema = [
        "fact_ref",
        "fact_kind",
        "statement",
        "requirement_level",
        "priority",
        "testability",
        "confidence",
    ]
    table = request_payload.get("frozen_fact_table")
    if not isinstance(table, dict) or table.get("schema") != schema:
        return []
    rows = table.get("rows")
    if not isinstance(rows, list):
        return []
    output: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(schema):
            return []
        output.append(
            {
                field: copy.deepcopy(value)
                for field, value in zip(schema, row)
            }
        )
    return output


def _local_to_frozen_fact_ids(
    raw_facts: list[dict[str, object]],
    frozen_facts: object,
    *,
    source_evidence_catalog: list[dict[str, object]],
) -> dict[str, str]:
    if not isinstance(frozen_facts, list):
        return {}
    stable_ids_by_core: dict[tuple[object, ...], set[str]] = {}
    for frozen_fact in frozen_facts:
        core = _fact_core(
            frozen_fact,
            source_evidence_catalog=source_evidence_catalog,
        )
        stable_id = (
            str(frozen_fact.get("fact_id") or "")
            if isinstance(frozen_fact, dict)
            else ""
        )
        if core is not None and stable_id:
            stable_ids_by_core.setdefault(core, set()).add(stable_id)

    mapping: dict[str, str] = {}
    for raw_fact in raw_facts:
        core = _fact_core(
            raw_fact,
            source_evidence_catalog=source_evidence_catalog,
        )
        local_id = str(raw_fact.get("fact_id") or "")
        stable_ids = stable_ids_by_core.get(core, set()) if core is not None else set()
        if local_id and len(stable_ids) == 1:
            mapping[local_id] = next(iter(stable_ids))
    return mapping


def _rewrite_known_fact_references(
    value: object,
    local_to_frozen: dict[str, str],
) -> object:
    rewritten = copy.deepcopy(value)

    def _rewrite(current: object) -> None:
        if isinstance(current, dict):
            for key, nested in list(current.items()):
                if key == "fact_id":
                    raw_id = str(nested or "")
                    if raw_id in local_to_frozen:
                        current[key] = local_to_frozen[raw_id]
                    continue
                if key == "fact_ids" and isinstance(nested, list):
                    current[key] = [
                        local_to_frozen.get(str(item or ""), item)
                        for item in nested
                    ]
                    continue
                _rewrite(nested)
        elif isinstance(current, list):
            for nested in current:
                _rewrite(nested)

    _rewrite(rewritten)
    return rewritten


class _BlueprintExtractionClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.source_evidence_catalog: list[dict[str, object]] = []
        self.raw_facts: list[dict[str, object]] = []

    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs):  # noqa: ANN001, ARG002
        request_payload = json.loads(requirement)
        input_type = request_payload.get("input_type")
        self.calls.append(
            {
                "requirement": requirement,
                "prompt": prompt,
                "input_type": input_type,
                "kwargs": dict(kwargs),
            }
        )
        if input_type == "current_requirement_atomic_fact_compile":
            target_source_evidence_catalog = [
                copy.deepcopy(item)
                for item in request_payload["target_source_evidence_catalog"]
            ]
            self.source_evidence_catalog = [
                copy.deepcopy(item)
                for key in (
                    "target_source_evidence_catalog",
                    "context_source_evidence_catalog",
                )
                for item in request_payload[key]
            ]
            evidence_refs = [
                item["ref"] for item in self.source_evidence_catalog
            ]
            target_evidence_refs = [
                item["ref"] for item in target_source_evidence_catalog
            ]
            anchor_evidence_ref = target_evidence_refs[0]
            self.raw_facts = [
                {
                    "fact_id": "f_publish",
                    "fact_kind": "action",
                    "statement": "A user publishes a forum post",
                    "requirement_level": "required",
                    "priority": "unspecified",
                    "testability": "testable",
                    "evidence": evidence_refs,
                    "anchor_evidence_ref": anchor_evidence_ref,
                    "confidence": 0.9,
                },
                {
                    "fact_id": "f_open_detail",
                    "fact_kind": "action",
                    "statement": "The user opens the post detail page",
                    "requirement_level": "required",
                    "priority": "unspecified",
                    "testability": "testable",
                    "evidence": evidence_refs,
                    "anchor_evidence_ref": anchor_evidence_ref,
                    "confidence": 0.9,
                },
            ]
            return json.dumps(
                {
                    "source_evidence_records": [
                        {
                            "evidence_ref": evidence_ref,
                            "owned_facts": (
                                [
                                    {
                                        str(key): copy.deepcopy(value)
                                        for key, value in fact.items()
                                        if key != "anchor_evidence_ref"
                                    }
                                    for fact in self.raw_facts
                                ]
                                if evidence_ref == anchor_evidence_ref
                                else []
                            ),
                        }
                        for evidence_ref in target_evidence_refs
                    ]
                },
                ensure_ascii=False,
            )
        if input_type == "current_requirement_scope_boundary_selection_compile":
            frozen_facts = _request_scope_facts(request_payload)
            fact_refs = [
                item["fact_ref"] for item in frozen_facts
            ]
            return json.dumps(
                {
                    "boundary_records": [
                        {
                            "boundary_id": "forum",
                            "label": "Forum",
                            "decision": "in_scope",
                            "parent_boundary_id": "",
                            "support": [
                                {
                                    "signal": "purpose",
                                    "fact_refs": fact_refs,
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if input_type == "current_requirement_scope_membership_compile":
            selection = request_payload["frozen_boundary_selection"]
            assert selection["boundaries"]
            assert request_payload["frozen_source_outline"]["fingerprint"]
            assignments = [
                {
                    "boundary_id": boundary["boundary_id"],
                    "membership_kind": "none",
                    "membership_ref": "",
                }
                for boundary in selection["boundaries"]
                if boundary.get("parent_boundary_id")
            ]
            assert {item["boundary_id"] for item in assignments} == {
                boundary["boundary_id"]
                for boundary in selection["boundaries"]
                if boundary.get("parent_boundary_id")
            }
            return json.dumps(
                {"membership_assignments": assignments},
                ensure_ascii=False,
            )
        if input_type == "current_requirement_scope_binding_compile":
            target_fact_refs = list(request_payload["target_fact_refs"])
            assert _request_scope_facts(request_payload)
            assert request_payload["frozen_boundary_manifest"]["boundaries"]
            assert request_payload["frozen_source_outline"]["fingerprint"]
            assert request_payload["target_topology_usage"] == [
                {
                    "fact_ref": fact_ref,
                    "explicit_membership_edges": [],
                    "support_scope_ids": ["forum"],
                }
                for fact_ref in target_fact_refs
            ]
            return json.dumps(
                {
                    "fact_bindings": [
                        {
                            "fact_ref": fact_ref,
                            "scope_ids": ["forum"],
                            "role": "owned_requirement",
                        }
                        for fact_ref in target_fact_refs
                    ],
                },
                ensure_ascii=False,
            )
        assert input_type == "current_requirement_graph_compile"
        graph_payload = {
                "confidence": 0.9,
                "semantic_graph": {
                    "graph_version": "requirement-semantic-graph-v1",
                    "nodes": [
                        {
                            "node_id": "forum",
                            "kind": "scope",
                            "name": "Forum",
                            "aliases": [],
                            "scope_status": "in_scope",
                            "boundary_status": "resolved",
                            "fact_ids": ["f_publish", "f_open_detail"],
                            "confidence": 0.9,
                        },
                        {
                            "node_id": "publish_post",
                            "kind": "capability",
                            "name": "Publish post",
                            "aliases": [],
                            "scope_status": "",
                            "boundary_status": "resolved",
                            "fact_ids": ["f_publish"],
                            "confidence": 0.9,
                        },
                        {
                            "node_id": "open_detail",
                            "kind": "capability",
                            "name": "Open detail page",
                            "aliases": [],
                            "scope_status": "",
                            "boundary_status": "resolved",
                            "fact_ids": ["f_open_detail"],
                            "confidence": 0.9,
                        },
                    ],
                    "edges": [
                        {
                            "edge_id": "forum_owns_publish",
                            "type": "owns",
                            "source_node_id": "forum",
                            "target_node_id": "publish_post",
                            "fact_ids": ["f_publish"],
                            "ownership_role": "primary",
                            "trigger": "",
                            "result_state": "",
                            "transferred_entity_node_ids": [],
                            "confidence": 0.9,
                        },
                        {
                            "edge_id": "forum_owns_detail",
                            "type": "owns",
                            "source_node_id": "forum",
                            "target_node_id": "open_detail",
                            "fact_ids": ["f_open_detail"],
                            "ownership_role": "primary",
                            "trigger": "",
                            "result_state": "",
                            "transferred_entity_node_ids": [],
                            "confidence": 0.9,
                        },
                        {
                            "edge_id": "publish_to_detail",
                            "type": "transitions",
                            "source_node_id": "publish_post",
                            "target_node_id": "open_detail",
                            "fact_ids": ["f_open_detail"],
                            "ownership_role": "none",
                            "trigger": "Post published",
                            "result_state": "detail_visible",
                            "transferred_entity_node_ids": [],
                            "confidence": 0.9,
                        },
                    ],
                    "primary_flow": {
                        "node_ids": ["publish_post", "open_detail"],
                        "edge_ids": ["publish_to_detail"],
                    },
                    "fact_dispositions": [],
                },
                "workflow_blueprints": [
                    {
                        "workflow_id": "forum_publish_flow",
                        "name": "Forum publish flow",
                        "primary": True,
                        "confidence": 0.8,
                        "initial_state": "initial",
                        "required_stage_ids": ["publish_post", "open_detail"],
                        "terminal_states": ["detail_visible"],
                        "fact_ids": ["f_publish", "f_open_detail"],
                        "steps": [
                            {
                                "id": "publish_post",
                                "label": "Publish a forum post",
                                "action": "Publish a forum post",
                                "stage_kind": "commit",
                                "actor": "business_user",
                                "state_in": "initial",
                                "state_out": "post_published",
                                "required": True,
                                "terminal": False,
                                "critical": True,
                                "blocking": True,
                                "destructive": False,
                                "scope_candidates": [
                                    {
                                        "scope_id": "forum",
                                        "role": "primary",
                                        "confidence": 0.9,
                                        "fact_ids": ["f_publish"],
                                    }
                                ],
                                "relation_ids": ["forum_owns_publish"],
                                "required_states": [],
                                "produced_states": [],
                                "fact_ids": ["f_publish"],
                            },
                            {
                                "id": "open_detail",
                                "label": "Open the post detail page",
                                "action": "Open the post detail page",
                                "stage_kind": "consume",
                                "actor": "business_user",
                                "state_in": "post_published",
                                "state_out": "detail_visible",
                                "required": True,
                                "terminal": True,
                                "critical": False,
                                "blocking": False,
                                "destructive": False,
                                "scope_candidates": [
                                    {
                                        "scope_id": "forum",
                                        "role": "primary",
                                        "confidence": 0.9,
                                        "fact_ids": ["f_open_detail"],
                                    }
                                ],
                                "relation_ids": [
                                    "forum_owns_detail",
                                    "publish_to_detail",
                                ],
                                "required_states": [],
                                "produced_states": [],
                                "fact_ids": ["f_open_detail"],
                            },
                        ],
                    }
                ],
            }
        frozen_context = request_payload["frozen_context"]
        local_to_frozen = _local_to_frozen_fact_ids(
            self.raw_facts,
            frozen_context["evidence_facts"],
            source_evidence_catalog=self.source_evidence_catalog,
        )
        return json.dumps(
            _rewrite_known_fact_references(graph_payload, local_to_frozen),
            ensure_ascii=False,
        )


class _LinkedSignalQuery:
    def __init__(self, db):
        self.db = db
        self.filters: list[str] = []

    def filter(self, *criteria):
        text = " ".join(str(item) for item in criteria)
        self.filters.append(text)
        if "content_hash" in text:
            self.db.hash_filter_count += 1
        elif "knowledge_documents.content =" in text:
            self.db.content_eq_filter_count += 1
        elif " LIKE " in text:
            self.db.content_like_filter_count += 1
        return self

    def order_by(self, *args):  # noqa: ANN002, ARG002
        return self

    def limit(self, value):  # noqa: ANN001, ARG002
        return self

    def all(self):
        if self.db.hash_filter_count > 0:
            return [SimpleNamespace(id=11)]
        return []


class _LinkedSignalDb:
    def __init__(self) -> None:
        self.hash_filter_count = 0
        self.content_eq_filter_count = 0
        self.content_like_filter_count = 0

    def query(self, model):  # noqa: ANN001, ARG002
        return _LinkedSignalQuery(self)


class _LinkedSignalRepo:
    def __init__(self, db):  # noqa: ANN001
        self.db = db

    def list_linked_test_cases_for_sources(self, *, project_id: int, source_doc_ids):  # noqa: ANN001, ARG002
        self.db.linked_source_doc_ids = [int(value) for value in source_doc_ids]
        return [
            SimpleNamespace(
                id=21,
                content=json.dumps([{"id": "TC-001"}, {"id": "TC-002"}], ensure_ascii=False),
            )
        ]


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def _strict_case_semantic(
    case: dict[str, object],
    *,
    module_key: str,
    workflow_id: str = "",
    stage_id: str = "",
    stage_kind: str = "",
) -> dict[str, object]:
    """为模型新产用例构造最小、完整且证据可核验的语义契约。"""
    evidence = str(case.get("description") or "").strip()
    workflow_candidates: list[dict[str, object]] = []
    if workflow_id and stage_id and stage_kind:
        workflow_candidates.append(
            {
                "workflow_id": workflow_id,
                "stage_id": stage_id,
                "stage_kind": stage_kind,
                "confidence": 0.95,
                "evidence": [evidence],
                "evidence_verified": True,
            }
        )
    return {
        "version": "case-semantic-v1",
        "module_candidates": [
            {
                "module_key": module_key,
                "module_name": str(case.get("test_module") or module_key),
                "role": "primary",
                "confidence": 0.95,
                "evidence": [evidence],
                "evidence_verified": True,
            }
        ],
        "interaction_ids": [],
        "workflow_stage_candidates": workflow_candidates,
        "precondition_states": [],
        "produced_states": [],
    }


def _attach_independent_case_semantics(
    cases: list[dict[str, object]],
    *,
    module_key: str,
) -> list[dict[str, object]]:
    for case in cases:
        case["_semantic"] = _strict_case_semantic(case, module_key=module_key)
    return cases


def _complete_primary_workflow_fixture(
    blueprint: dict[str, object],
    *,
    module_key: str,
) -> dict[str, object]:
    """补齐通用工作流声明；测试不再依赖正文关键词派生阶段。"""
    blueprint["primary"] = True
    blueprint.setdefault("name", str(blueprint.get("workflow_id") or blueprint.get("id") or "workflow"))
    blueprint.setdefault("confidence", 0.95)
    for raw_step in blueprint.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        label = str(raw_step.get("label") or raw_step.get("action") or raw_step.get("id") or "")
        raw_step["path_type"] = "positive"
        raw_step["critical"] = bool(raw_step.get("critical", False))
        raw_step["blocking"] = bool(raw_step.get("blocking", False))
        raw_step["destructive"] = bool(raw_step.get("destructive", False))
        raw_step["can_advance_main_flow"] = True
        raw_step["module_candidates"] = [
            {
                "module_key": module_key,
                "role": "primary",
                "confidence": 0.95,
                "evidence": [label],
                "evidence_verified": True,
            }
        ]
        raw_step["interaction_ids"] = []
        raw_step["required_states"] = []
        raw_step["produced_states"] = []
        raw_step["evidence"] = [label]
        raw_step["evidence_verified"] = True
        raw_step.pop("match_keywords", None)
    return blueprint


def _attach_workflow_case_semantics(
    cases: list[dict[str, object]],
    *,
    blueprint: dict[str, object],
    module_key: str,
    stage_by_case_id: dict[str, str],
) -> list[dict[str, object]]:
    workflow_id = str(blueprint.get("workflow_id") or blueprint.get("id") or "")
    stage_kind_by_id = {
        str(step.get("id") or ""): str(step.get("stage_kind") or "")
        for step in (blueprint.get("steps") or [])
        if isinstance(step, dict)
    }
    for case in cases:
        stage_id = stage_by_case_id.get(str(case.get("id") or ""), "")
        case["_semantic"] = _strict_case_semantic(
            case,
            module_key=module_key,
            workflow_id=workflow_id if stage_id else "",
            stage_id=stage_id,
            stage_kind=stage_kind_by_id.get(stage_id, ""),
        )
    return cases


def test_linked_final_case_signal_prefers_content_hash_lookup(monkeypatch) -> None:
    import modules.knowledge_base_components.repositories.knowledge_document_repository as repo_mod

    db = _LinkedSignalDb()
    monkeypatch.setattr(repo_mod, "KnowledgeDocumentRepository", _LinkedSignalRepo)

    result = resolve_linked_final_case_signal(
        db=db,
        project_id=2,
        user_id=9,
        requirement_text="论坛发布成功后进入详情页",
    )

    assert result["source_doc_ids"] == [11]
    assert result["linked_final_case_doc_ids"] == [21]
    assert result["linked_final_case_count"] == 2
    assert db.hash_filter_count == 1
    assert db.content_eq_filter_count == 0
    assert db.content_like_filter_count == 0
    assert db.linked_source_doc_ids == [11]


def test_current_requirement_blueprint_extraction_uses_default_token_budget(monkeypatch) -> None:
    monkeypatch.delenv("GENERATION_CURRENT_REQUIREMENT_BLUEPRINT_MAX_TOKENS", raising=False)
    client = _BlueprintExtractionClient()

    blueprints, diagnostics = extract_current_requirement_blueprints(
        client=client,
        requirement_text="A user publishes a forum post and opens its detail page.",
        project_id=2,
        user_id=9,
    )

    assert len(blueprints) == 1
    assert [call["input_type"] for call in client.calls] == [
        "current_requirement_atomic_fact_compile",
        "current_requirement_scope_boundary_selection_compile",
        "current_requirement_scope_membership_compile",
        "current_requirement_scope_binding_compile",
        "current_requirement_graph_compile",
    ]
    assert [call["kwargs"]["max_tokens"] for call in client.calls] == [8192] * 5
    assert diagnostics["current_requirement_blueprint_max_tokens"] == 8192
    assert diagnostics["current_requirement_blueprint_status"] == "applied_with_workflows"


def test_current_requirement_blueprint_extraction_allows_env_token_budget(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_CURRENT_REQUIREMENT_BLUEPRINT_MAX_TOKENS", "1200")
    client = _BlueprintExtractionClient()

    _, diagnostics = extract_current_requirement_blueprints(
        client=client,
        requirement_text="A user publishes a forum post and opens its detail page.",
    )

    assert client.calls[0]["kwargs"]["max_tokens"] == 1200
    assert diagnostics["current_requirement_blueprint_max_tokens"] == 1200


def test_current_requirement_blueprint_extraction_clamps_too_small_budget(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_CURRENT_REQUIREMENT_BLUEPRINT_MAX_TOKENS", "100")
    client = _BlueprintExtractionClient()

    _, diagnostics = extract_current_requirement_blueprints(
        client=client,
        requirement_text="A user publishes a forum post and opens its detail page.",
    )

    assert client.calls[0]["kwargs"]["max_tokens"] == 1200
    assert diagnostics["current_requirement_blueprint_max_tokens"] == 1200


def test_explicit_expected_count_is_not_mapped_to_a_fixed_mode_tier() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="近期课程和排课功能调整",
        expected_count=100,
    )

    assert profile["coverage_mode"] == ""
    assert profile["case_density"] == ""
    assert profile["target_case_range"] == {"min": 80, "max": 100}
    assert profile["coverage_layers"] == []
    assert profile["coverage_source"] == "explicit_expected_count"
    assert profile["activation_evidence"]["requirement_text_parsed_for_mode"] is False


def test_document_intent_does_not_activate_a_keyword_mode() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="本次改版需要做标准回归，确保原有模块不受影响",
        expected_count=20,
    )

    assert profile["coverage_mode"] == ""
    assert profile["target_case_range"] == {"min": 16, "max": 20}
    assert profile["coverage_layers"] == []


def test_strategy_plan_explicit_coverage_targets_are_preserved() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="writing workflow regression",
        expected_count=70,
        strategy_plan={
            "coverage_targets": {
                "mode": "release_acceptance",
                "case_density": "plan_defined",
                "target_case_range": {"min": 12, "max": 18},
                "layers": ["覆盖显式发布闭环"],
            }
        },
    )

    assert profile["coverage_mode"] == "release_acceptance"
    assert profile["case_density"] == "plan_defined"
    assert profile["target_case_range"] == {"min": 56, "max": 70}
    assert profile["coverage_layers"] == ["覆盖显式发布闭环"]


def test_merge_uses_existing_control_and_functional_architecture_without_domain_scenarios() -> None:
    state = merge_generation_mode_control_state(
        {
            "must_cover_rules": ["REQ-1: 保存后状态为已发布"],
            "rule_quota": {"REQ-1": 1},
            "source_meta": {
                "project_profile": {
                    "functional_architecture": {
                        "functional_modules": [{"module_name": "发布区"}],
                        "module_interactions": [
                            {"source_module": "发布区", "target_module": "消息区"}
                        ],
                    }
                }
            },
        },
        requirement_text="任意领域正文",
        expected_count=17,
    )

    profile = dict(state.source_meta.get("generation_coverage_profile") or {})
    evidence = dict(profile.get("activation_evidence") or {})
    assert state.must_cover_rules == ["REQ-1: 保存后状态为已发布"]
    assert state.must_have_scenarios == []
    assert profile["target_case_range"] == {"min": 14, "max": 17}
    assert evidence["control_signal_count"] == 2
    assert evidence["functional_module_count"] == 1
    assert evidence["module_interaction_count"] == 1


def test_generation_mode_control_reaches_structured_prompt_context() -> None:
    state = build_generation_mode_control_state(
        requirement_text="全功能测试：覆盖入口、核心流程、异常和跨模块回归",
        expected_count=100,
    )
    prompt_context = build_structured_prompt_context(
        requirement="全功能测试：覆盖入口、核心流程、异常和跨模块回归",
        feedback_control_state=state,
    )

    summary = dict(prompt_context.get("control_summary") or {})
    control_text = str(prompt_context.get("control_context") or "")
    assert summary["generation_coverage_mode"] == ""
    assert summary["generation_target_case_range"] == {"min": 80, "max": 100}
    assert "GENERATION COVERAGE MODE" not in control_text
    assert "不从正文关键词猜测固定场景" in "\n".join(state.quality_fix_hints)


def test_full_functional_mode_prevents_review_gate_compressing_dense_case_set() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for a management workflow",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate management workflow checkpoint {idx}",
            "test_module": f"module-{idx:03d}",
            "preconditions": ["User is logged in", f"Dataset {idx} exists"],
            "steps": [
                f"1. Open workflow area {idx}",
                f"2. Execute action for checkpoint {idx}",
                f"3. Refresh and re-open checkpoint {idx}",
            ],
            "test_input": f"checkpoint-{idx}",
            "expected_result": (
                f"Checkpoint {idx} shows saved state {idx} after refresh, "
                f"and module {idx} displays the updated record."
            ),
            "priority": "P1" if idx % 3 else "P2",
        }
        for idx in range(1, 41)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="full functional regression for a management workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    assert len(result.get("cases") or []) >= 30
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["recommended_range"] == "80-100"
    debug = dict(result.get("feedback_control_debug") or {})
    assert debug["generation_coverage_mode"] == "full_functional_regression"


def test_expanded_regression_mode_keeps_broad_case_set_after_review_gate() -> None:
    state = build_generation_mode_control_state(
        requirement_text="expanded regression for a writing workflow",
        expected_count=70,
        strategy_plan=_coverage_strategy("expanded_regression", 70),
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate writing workflow scenario {idx}",
            "test_module": f"module-{idx:03d}",
            "preconditions": ["User is logged in", f"Writing asset {idx} exists"],
            "steps": [
                f"1. Open writing area {idx}",
                f"2. Execute business action for scenario {idx}",
                f"3. Verify state, message, and linked list update for scenario {idx}",
            ],
            "test_input": f"scenario-{idx}",
            "expected_result": (
                f"Scenario {idx} keeps the expected business state, "
                f"updates module {idx}, and does not expose stale requirement behavior."
            ),
            "priority": "P1" if idx % 4 else "P2",
        }
        for idx in range(1, 69)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="expanded regression for a writing workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=70,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    assert len(result.get("cases") or []) >= 56
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "expanded_regression"
    assert summary["recommended_range"] == "56-70"
    assert summary["target_final_count"] == 70
    assert summary["soft_min_count"] == 56
    assert summary["hard_min_count"] == 49
    assert summary["underfilled"] is False


def test_explicit_expected_count_accepts_recovered_soft_floor_after_pruning() -> None:
    state = build_generation_mode_control_state(
        requirement_text="expanded regression for a writing workflow",
        expected_count=70,
        strategy_plan=_coverage_strategy("expanded_regression", 70),
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate writing workflow scenario {idx}",
            "test_module": f"module-{idx % 10}",
            "preconditions": ["User is logged in", f"Writing asset {idx} exists"],
            "steps": [
                f"1. Open writing area {idx % 10}",
                f"2. Execute business action for scenario {idx}",
                f"3. Verify state, message, and linked list update for scenario {idx}",
            ],
            "test_input": f"scenario-{idx}",
            "expected_result": (
                f"Scenario {idx} keeps the expected business state, "
                f"updates module {idx % 10}, and does not expose stale requirement behavior."
            ),
            "priority": "P1",
        }
        for idx in range(1, 69)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="expanded regression for a writing workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=70,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    debug = dict(result.get("convergence_debug") or {})
    assert summary["generation_coverage_mode"] == "expanded_regression"
    assert summary["target_final_count"] == 70
    assert summary["underfilled"] is False
    assert summary["status"] != "completed_underfilled"
    assert debug["valid_unique_candidate_count"] >= 63
    assert debug["target_satisfaction_ratio"] >= 0.8


def test_full_mode_uses_mode_aware_final_duplicate_caps_for_generic_scenarios() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for dashboard and course workflow",
        expected_count=120,
        strategy_plan=_coverage_strategy("full_functional_regression", 120),
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Dashboard statistics show total submitted count",
            "test_module": "Dashboard",
            "preconditions": ["User is logged in"],
            "steps": ["Open Dashboard"],
            "test_input": "submitted records exist",
            "expected_result": "Dashboard total submitted count matches submitted records.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Dashboard statistics show failed review count",
            "test_module": "Dashboard",
            "preconditions": ["User is logged in"],
            "steps": ["Open Dashboard"],
            "test_input": "failed records exist",
            "expected_result": "Dashboard failed review count matches failed records.",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "Course workflow step navigation opens the first lesson",
            "test_module": "Course",
            "preconditions": ["User is logged in"],
            "steps": ["Open Course", "Click next step to first lesson"],
            "test_input": "first lesson",
            "expected_result": "First lesson page opens and shows lesson content.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Course workflow step navigation opens the second lesson",
            "test_module": "Course",
            "preconditions": ["User is logged in"],
            "steps": ["Open Course", "Click next step to second lesson"],
            "test_input": "second lesson",
            "expected_result": "Second lesson page opens and shows lesson content.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement=(
                "1. Dashboard: users view statistics.\n"
                "2. Course: users navigate lesson workflow."
            ),
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=120,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    final_cases = result.get("cases") or []
    final_descriptions = " ".join(str(item.get("description") or "") for item in final_cases)
    assert len(final_cases) >= 3
    assert "total submitted count" in final_descriptions or "failed review count" in final_descriptions
    assert "first lesson" in final_descriptions or "second lesson" in final_descriptions
    review_summary = dict(result.get("review_decision_summary") or {})
    assert review_summary["scenario_duplicate_pruned_count"] <= 1
    assert review_summary["flow_governance_applied"] is True


def test_full_mode_does_not_invent_p0_from_unstructured_business_words() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submit, review, and permission workflow",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Upload file and submit task generates the final result",
            "test_module": "Submission Workflow",
            "preconditions": ["User is logged in", "A valid file exists"],
            "steps": ["Open submission page", "Upload file", "Submit task"],
            "test_input": "valid file",
            "expected_result": "The task is submitted and the generated result is displayed.",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Result page copy button shows success toast",
            "test_module": "Result Page",
            "preconditions": ["A generated result exists"],
            "steps": ["Open result page", "Click copy"],
            "test_input": "copy",
            "expected_result": "A success toast is shown.",
            "priority": "P2",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload files, submit tasks, and view generated results.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities = [str(item.get("priority") or "").upper() for item in (result.get("cases") or [])]
    assert "P0" not in priorities


def test_full_mode_does_not_invent_a_fixed_p0_quota_from_case_count() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submit, approval, permission, and community workflow",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    payload = state.to_dict()
    seed_cases = [
        {
            "id": "TC-001",
            "description": "Existing P0 first lesson permission anchor",
            "test_module": "Permission",
            "preconditions": ["A normal user is logged in"],
            "steps": ["Open first lesson"],
            "test_input": "normal user",
            "expected_result": "The first lesson is enterable.",
            "priority": "P0",
        }
    ]
    generated_cases = [
        {
            "id": f"TC-{index + 2:03d}",
            "description": f"{family} main path case {index}",
            "test_module": f"{family.title()} Module {index}",
            "preconditions": ["User is logged in"],
            "steps": [f"Open {family}", f"Run {family} action {index}"],
            "test_input": family,
            "expected_result": f"The {family} flow completes and result {index} is displayed.",
            "priority": "P1",
        }
        for index, family in enumerate(
            [
                "upload submit",
                "generate result",
                "review approved",
                "member permission",
                "community comment like",
                "publish workflow",
                "approval passed",
                "all courses member",
                "upload image",
                "submit draft",
                "generated report",
                "locked paywall",
                "comment reply",
                "like cancel",
                "publish approved",
                "permission vip",
                "result detail",
                "upload retry",
                "submit success",
                "review pass notice",
                "member all courses",
                "community detail",
                "approval state",
                "generate final result",
                "upload attachment",
                "submit form",
                "publish article",
                "review approved list",
                "permission locked course",
                "comment audit",
                "like article",
                "all courses access",
                "upload camera",
                "submit review",
                "generated feedback",
                "approval notification",
                "member course access",
                "community work detail",
                "publish circle",
                "review pass reward",
                "permission first lesson",
                "upload album",
                "submit audit",
                "generate correction",
                "community comment entry",
            ]
        )
    ]
    cases = [*seed_cases, *generated_cases]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload content, submit it, receive approval, pass permission gates, and use community features.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=payload,
        )
    )

    priorities = [str(item.get("priority") or "").upper() for item in (result.get("cases") or [])]
    assert priorities.count("P0") < 8
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    persisted_cases = strip_case_meta_fields(
        normalize_final_case_priorities(
            result.get("cases") or [],
            requirement_text="Users upload content, submit it, receive approval, pass permission gates, and use community features.",
        )
    )
    persisted_priorities = [str(item.get("priority") or "").upper() for item in persisted_cases]
    assert persisted_priorities.count("P0") < 8


def test_public_normalization_does_not_recreate_removed_text_anchor_floor() -> None:
    anchor_cases = [
        {
            "id": "TC-001",
            "description": "批改反馈-完整四部分生成：校验综合点评、分句点评、提升思路、全文润色全部正确显示",
            "test_module": "作文批改-批改结果页",
            "expected_result": "四部分内容均正确生成，综合点评、分句点评、提升思路、全文润色完整展示",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "投稿页-提交投稿：校验提交后显示投稿成功弹窗，点击我知道了返回批改详情页，按钮状态变为审核中",
            "test_module": "作文批改-投稿页",
            "expected_result": "提交后弹出投稿成功弹窗；点击后返回批改结果页，投稿按钮文案变为审核中且不可点击",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "后台审核通过投稿作品",
            "test_module": "作文审核后台",
            "expected_result": "后台审核通过成功，作品状态变为已发布",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "普通用户第一课免费试学，其余课程锁住并点击跳转会员中心",
            "test_module": "课程列表页 - 权限",
            "expected_result": "第一课可免费进入学习；第二课及以后显示锁图标，点击后跳转至会员中心页面",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "会员用户进入同步作文课程列表页可看到所有课程均可学习",
            "test_module": "课程列表页 - 权限",
            "expected_result": "所有课程均显示为可学习状态，无锁图标或可点击进入",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "删除已发布作品后恢复未投稿",
            "test_module": "我的作文",
            "expected_result": "作品从作文圈列表移除，我的作文中该作品恢复为未投稿状态",
            "priority": "P1",
        },
        {
            "id": "TC-007",
            "description": "上传图片成功后点击去批改生成批改结果",
            "test_module": "作文批改",
            "expected_result": "上传成功后点击去批改，系统生成批改结果并进入结果页",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "审核通过后作文圈可见作品详情",
            "test_module": "作文圈",
            "expected_result": "审核通过后作品在作文圈可见，打开作品详情展示正文",
            "priority": "P1",
        },
    ]
    filler_cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"展示型补充用例 {idx}",
            "test_module": "展示",
            "expected_result": "页面展示正确",
            "priority": "P2",
        }
        for idx in range(9, 90)
    ]

    normalized = strip_case_meta_fields(
        normalize_final_case_priorities(
            [*anchor_cases, *filler_cases],
            requirement_text="小学同步作文 full functional regression",
        )
    )

    assert sum(1 for item in normalized if str(item.get("priority") or "").upper() == "P0") < 7
    by_description = {str(item.get("description") or ""): item for item in normalized}
    assert str(
        by_description["批改反馈-完整四部分生成：校验综合点评、分句点评、提升思路、全文润色全部正确显示"].get(
            "priority"
        )
        or ""
    ).upper() == "P2"


def test_public_normalization_does_not_restore_removed_floor_source_or_text_anchor() -> None:
    normalized = normalize_final_case_priorities(
        [
            {
                "id": "TC-001",
                "description": "Complete four-part generated result display",
                "test_module": "Result page",
                "expected_result": "Generated result details are fully displayed",
                "priority": "P0",
                "priority_final": "P0",
                "priority_decision_source": "main_path_anchor_floor",
            }
        ],
        requirement_text="ordinary workflow",
    )

    assert normalized[0]["priority"] == "P1"
    assert normalized[0]["priority_decision_source"] == "model_p0_guard_downgrade"


def test_public_normalization_preserves_structured_critical_and_declared_main_priority() -> None:
    normalized = normalize_final_case_priorities(
        [
            {
                "id": "TC-001",
                "description": "Open critical entry",
                "test_module": "Entry",
                "expected_result": "Target page opens",
                "priority": "P0",
                "priority_final": "P0",
                "critical": True,
            },
            {
                "id": "TC-002",
                "description": "Declared ordinary workflow step",
                "test_module": "Flow",
                "expected_result": "State advances",
                "priority": "P1",
                "priority_final": "P1",
                "execution_group": "main_smoke",
            },
        ],
        requirement_text="ordinary workflow",
    )

    assert normalized[0]["priority"] == "P0"
    assert normalized[0]["priority_decision_source"] == "preserved_structured_critical_priority"
    assert normalized[1]["priority"] == "P1"
    assert normalized[1]["priority_decision_source"] == "preserved_execution_plan_priority"


def test_text_only_blocking_words_do_not_create_p0_evidence() -> None:
    normalized = normalize_final_case_priorities(
        [
            {
                "id": "TC-TEXT-RISK",
                "description": "P0 blocking failure stops the entire release",
                "test_module": "Generic flow",
                "preconditions": ["workflow prepared"],
                "steps": ["trigger the failure"],
                "test_input": "blocking failure",
                "expected_result": "release is blocked by a P0 failure",
                "priority": "P0",
                "priority_final": "P0",
                "execution_group": "main_smoke",
            }
        ],
        requirement_text="generic workflow",
    )

    assert normalized[0]["priority"] != "P0"
    assert normalized[0]["priority_decision_source"] == "model_p0_guard_downgrade"


def test_public_normalization_does_not_promote_essay_cases_for_schedule_requirement() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "上传作文图片成功后点击去批改生成批改结果",
            "test_module": "作文批改",
            "expected_result": "上传成功后点击去批改，系统生成批改结果并进入结果页",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "提交投稿后进入审核中",
            "test_module": "作文投稿",
            "expected_result": "投稿提交成功，作品状态变为审核中",
            "priority": "P1",
        },
    ]

    normalized = strip_case_meta_fields(
        normalize_final_case_priorities(
            cases,
            requirement_text="近期课程+排课：本周课程、学习计划、课程时间冲突和顺延规则",
        )
    )

    assert all(str(item.get("priority") or "").upper() != "P0" for item in normalized)


def test_full_mode_does_not_promote_non_blocking_display_cases_to_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submit, approval, permission, sorting, and sharing workflow",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Category list keeps the promoted course on top",
            "test_module": "Course Category",
            "preconditions": ["User is logged in"],
            "steps": ["Open category list"],
            "test_input": "category",
            "expected_result": "The course is sorted at the top of the category list.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Share article to H5 page and verify card content",
            "test_module": "Community Share",
            "preconditions": ["Article exists"],
            "steps": ["Click share"],
            "test_input": "share",
            "expected_result": "The H5 share page displays title, subtitle, and open app button.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Upload image and submit task successfully generates correction result",
            "test_module": "Correction",
            "preconditions": ["User is logged in"],
            "steps": ["Upload image", "Submit task"],
            "test_input": "valid image",
            "expected_result": "The correction result is successfully generated and displayed.",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "Submit article successfully enters pending review state",
            "test_module": "Submission",
            "preconditions": ["Draft is valid"],
            "steps": ["Submit article"],
            "test_input": "valid draft",
            "expected_result": "Submission succeeds and the article enters pending review.",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "Normal user opens locked lesson and is redirected to membership paywall",
            "test_module": "Permission",
            "preconditions": ["Normal user is logged in"],
            "steps": ["Open locked lesson"],
            "test_input": "locked lesson",
            "expected_result": "The user cannot enter the lesson and is redirected to membership paywall.",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "Approved work appears in community detail page",
            "test_module": "Community",
            "preconditions": ["Work is approved"],
            "steps": ["Open community detail"],
            "test_input": "approved work",
            "expected_result": "The approved work detail page displays the work content.",
            "priority": "P1",
        },
    ] + [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Core generated result checkpoint {idx}",
            "test_module": f"Generated Result {idx}",
            "preconditions": ["User is logged in"],
            "steps": ["Upload input", "Submit task"],
            "test_input": f"valid input {idx}",
            "expected_result": f"The generated result {idx} is displayed after submit success.",
            "priority": "P1",
        }
        for idx in range(7, 47)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload and submit content, pass approval, handle permissions, sorting, and sharing.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities_by_description = {
        str(item.get("description") or ""): str(item.get("priority") or "").upper()
        for item in (result.get("cases") or [])
    }
    assert priorities_by_description["Category list keeps the promoted course on top"] != "P0"
    assert priorities_by_description["Share article to H5 page and verify card content"] != "P0"
    true_anchor_p0_count = sum(
        1
        for description, priority in priorities_by_description.items()
        if priority == "P0"
        and any(token in description.lower() for token in ("upload", "submit", "approved", "permission", "generated result"))
    )
    assert true_anchor_p0_count == 0


def test_full_mode_demotes_popup_status_and_limit_cases_from_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for submission, status, and record management",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Submission rule popup appears on first entry",
            "test_module": "Submission Popup",
            "preconditions": ["User is logged in"],
            "steps": ["Open submission page"],
            "test_input": "first entry",
            "expected_result": "The rule popup is displayed.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "My works list shows status badge for failed review",
            "test_module": "My Works",
            "preconditions": ["A failed review work exists"],
            "steps": ["Open my works"],
            "test_input": "failed work",
            "expected_result": "The failed review status badge is displayed.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "My works list keeps maximum records limit at 20",
            "test_module": "My Works",
            "preconditions": ["More than 20 records exist"],
            "steps": ["Open my works"],
            "test_input": "21 records",
            "expected_result": "Only 20 records are retained.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Submit work successfully enters pending review",
            "test_module": "Submission",
            "preconditions": ["Valid work exists"],
            "steps": ["Submit work"],
            "test_input": "valid work",
            "expected_result": "Submit succeeds and the work enters pending review.",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "Upload image and successfully generate review result",
            "test_module": "Review",
            "preconditions": ["Valid image exists"],
            "steps": ["Upload image", "Submit review"],
            "test_input": "valid image",
            "expected_result": "The generated review result is displayed.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users submit work, view status, and manage records.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities_by_description = {
        str(item.get("description") or ""): str(item.get("priority") or "").upper()
        for item in (result.get("cases") or [])
    }
    assert priorities_by_description["Submission rule popup appears on first entry"] != "P0"
    assert priorities_by_description["My works list shows status badge for failed review"] != "P0"
    assert priorities_by_description["My works list keeps maximum records limit at 20"] != "P0"


def test_postprocess_does_not_delete_case_by_template_specific_text_pattern() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for image visibility and correction result",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Image original visibility toggle",
            "test_module": "Correction Upload",
            "preconditions": ["User is logged in"],
            "steps": ["Open uploaded image", "Click toggle original image"],
            "test_input": "uploaded image",
            "expected_result": "The image visibility changes and the current page remains observable.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Upload image and generate correction result",
            "test_module": "Correction Upload",
            "preconditions": ["User is logged in"],
            "steps": ["Upload image", "Submit correction"],
            "test_input": "valid image",
            "expected_result": "The correction result is generated and displays the review details.",
            "priority": "P1",
        },
    ]
    _attach_independent_case_semantics(cases, module_key="correction_upload")

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload images, toggle original image visibility, and generate correction results.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    final_cases = result.get("cases") or []
    assert {str(item.get("description") or "") for item in final_cases} == {
        "Image original visibility toggle",
        "Upload image and generate correction result",
    }
    assert all(isinstance(item.get("_semantic"), dict) for item in final_cases)


def test_full_mode_demotes_media_management_and_time_status_from_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submission, approval, permission, and media management",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Image thumbnails can be deleted and drag sorted",
            "test_module": "Upload Media",
            "preconditions": ["Uploaded images exist"],
            "steps": ["Delete one thumbnail", "Drag sort another thumbnail"],
            "test_input": "uploaded images",
            "expected_result": "The image list updates and keeps the new order.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Force close app keeps uploaded image list",
            "test_module": "Upload Recovery",
            "preconditions": ["Images have been uploaded"],
            "steps": ["Force close app", "Reopen the page"],
            "test_input": "uploaded images",
            "expected_result": "The uploaded image list is retained.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Review remains pending for 48 hours",
            "test_module": "Review Status",
            "preconditions": ["A review item is pending"],
            "steps": ["Wait 48 hours", "Open review status"],
            "test_input": "pending item",
            "expected_result": "The pending status remains visible.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "My works list keeps maximum records limit at 20",
            "test_module": "My Works",
            "preconditions": ["More than 20 records exist"],
            "steps": ["Open my works"],
            "test_input": "21 records",
            "expected_result": "Only 20 records are retained.",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "Upload valid image and successfully generate correction result",
            "test_module": "Correction",
            "preconditions": ["User is logged in"],
            "steps": ["Upload valid image", "Submit correction"],
            "test_input": "valid image",
            "expected_result": "The correction result is generated and the result details are displayed.",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "Correction feedback displays four modules completely",
            "test_module": "Correction Result",
            "preconditions": ["Correction result exists"],
            "steps": ["Open correction result"],
            "test_input": "result id",
            "expected_result": "The four feedback modules are displayed completely.",
            "priority": "P1",
        },
        {
            "id": "TC-007",
            "description": "Submit work successfully enters pending review",
            "test_module": "Submission",
            "preconditions": ["Valid work exists"],
            "steps": ["Submit work"],
            "test_input": "valid work",
            "expected_result": "Submit succeeds and the work enters pending review.",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "Approved work becomes visible in community detail",
            "test_module": "Approval",
            "preconditions": ["The submitted work has been approved"],
            "steps": ["Open community list", "Open the work detail"],
            "test_input": "approved work",
            "expected_result": "The approved work is visible in the community detail page.",
            "priority": "P1",
        },
        {
            "id": "TC-009",
            "description": "Member user can access all courses",
            "test_module": "Permission",
            "preconditions": ["User is a member"],
            "steps": ["Open all courses"],
            "test_input": "member account",
            "expected_result": "All courses are accessible.",
            "priority": "P1",
        },
        {
            "id": "TC-010",
            "description": "Normal user first lesson is available and other lessons are locked",
            "test_module": "Permission",
            "preconditions": ["User is not a member"],
            "steps": ["Open first lesson", "Open another lesson"],
            "test_input": "normal account",
            "expected_result": "The first lesson is available and other lessons are locked by the paywall.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload images, generate correction results, submit work, pass approval, and access courses by permission.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities_by_description = {
        str(item.get("description") or ""): str(item.get("priority") or "").upper()
        for item in (result.get("cases") or [])
    }
    assert priorities_by_description.get("Image thumbnails can be deleted and drag sorted") != "P0"
    assert priorities_by_description.get("Force close app keeps uploaded image list") != "P0"
    assert priorities_by_description.get("Review remains pending for 48 hours") != "P0"
    assert priorities_by_description.get("My works list keeps maximum records limit at 20") != "P0"
    assert priorities_by_description["Upload valid image and successfully generate correction result"] == "P1"
    assert priorities_by_description["Correction feedback displays four modules completely"] == "P1"


def test_full_mode_shortfall_does_not_start_post_review_supplement() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for generated results, audit, permissions, upload failures, and downloads",
        expected_count=120,
        strategy_plan=_coverage_strategy("full_functional_regression", 120),
    )
    base_cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Existing valid workflow checkpoint {idx}",
            "test_module": f"Module {idx}",
            "preconditions": ["User is logged in"],
            "steps": ["Open the module", "Execute the configured workflow"],
            "test_input": f"valid data {idx}",
            "expected_result": f"Workflow checkpoint {idx} completes and the linked state is updated.",
            "priority": "P1" if idx % 2 else "P2",
        }
        for idx in range(1, 65)
    ]
    supplement_topics = [
        "upload validation",
        "audit rejection",
        "permission denied",
        "download retry",
        "notification sync",
        "result export",
        "network timeout",
        "file size boundary",
        "format validation",
        "state rollback",
        "approval reminder",
        "cache refresh",
        "history query",
        "detail navigation",
        "batch operation",
        "role switch",
        "quota limit",
        "manual retry",
        "status recovery",
        "cross device sync",
        "archive export",
        "receipt download",
        "community moderation",
        "appeal submit",
    ]
    supplement_cases = [
        {
            "id": f"S-{idx:03d}",
            "description": f"Supplement {topic} path {idx}",
            "test_module": f"{topic.title()} Module",
            "preconditions": [f"{topic} data is available"],
            "steps": [
                f"Open {topic} area",
                f"Execute {topic} operation",
                "Verify synchronized final state",
            ],
            "test_input": f"{topic} data {idx}",
            "expected_result": (
                f"The {topic} path {idx} completes and the final state remains consistent after refresh."
            ),
            "priority": "P1",
        }
        for idx, topic in enumerate(supplement_topics, start=1)
    ]
    client = _SupplementClient(supplement_cases)

    result = _drain_with_return(
        stream_postprocess_cases(
            client=client,
            requirement="Full regression must cover generated results, upload failures, retry, audit rejection, permission, and download failure.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(base_cases, ensure_ascii=False),
            expected_count=120,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    review_summary = dict(result.get("review_decision_summary") or {})
    assert not any(str(item.get("id") or "").startswith("S-") for item in (result.get("cases") or []))
    assert review_summary["final_scenario_duplicate_case_count"] == 0
    assert summary["underfilled"] is True
    assert not any("FINAL_SHORTFALL_SUPPLEMENT" in prompt for prompt in client.prompts)


def test_standard_expected_count_shortfall_does_not_start_post_review_supplement() -> None:
    state = build_generation_mode_control_state(
        requirement_text="standard regression for activity entry, course visibility, assessment, reports, reward, and purchase flows",
        expected_count=50,
        strategy_plan=_coverage_strategy("standard_regression", 50),
    )
    focus_words = [
        "entry",
        "course",
        "assessment",
        "report",
        "reward",
        "purchase",
        "visibility",
        "progress",
        "timer",
        "member",
        "nonmember",
        "guest",
        "svip",
        "chapter",
        "question",
        "submit",
        "result",
        "download",
        "popup",
        "resume",
        "reset",
        "grade",
        "score",
        "comment",
        "avatar",
        "rank",
        "boundary",
        "exception",
        "sync",
        "cache",
        "device",
        "network",
        "route",
        "homepage",
        "lesson",
        "unlock",
        "complete",
        "history",
        "confirm",
        "cancel",
        "toast",
        "dialog",
        "detail",
        "status",
        "plan",
    ]
    base_cases = []
    for idx in range(1, 46):
        word = focus_words[(idx - 1) % len(focus_words)]
        weak_expected = idx > 37
        base_cases.append(
            {
                "id": f"TC-{idx:03d}",
                "description": f"Activity {word} standard regression checkpoint {idx}",
                "test_module": f"Activity {word} Module",
                "preconditions": ["User has entered the activity"],
                "steps": [
                    "Open the activity page",
                    f"Execute {word} checkpoint {idx}",
                    "Refresh and verify synchronized state",
                ],
                "test_input": f"{word} standard data {idx}",
                "expected_result": (
                    "功能正常，符合预期"
                    if weak_expected
                    else f"The {word} checkpoint {idx} completes and the synchronized business state is visible."
                ),
                "expected_result_quality": "non_assertable" if weak_expected else "assertable",
                "priority": "P1" if idx % 2 else "P2",
            }
        )
    supplement_cases = [
        {
            "id": f"S-{idx:03d}",
            "description": f"Supplement unique standard path {idx}",
            "test_module": f"Supplement Unique Module {idx}",
            "preconditions": ["Activity data is available"],
            "steps": ["Open the under-covered path", "Complete the operation", "Verify the final synchronized state"],
            "test_input": f"supplement standard data {idx}",
            "expected_result": f"Supplement path {idx} completes and the final state is retained after refresh.",
            "priority": "P1",
        }
        for idx in range(1, 8)
    ]
    client = _SupplementClient(supplement_cases)

    result = _drain_with_return(
        stream_postprocess_cases(
            client=client,
            requirement="Standard regression must cover activity entry, assessment, course visibility, report, reward, and purchase paths.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(base_cases, ensure_ascii=False),
            expected_count=50,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    review_summary = dict(result.get("review_decision_summary") or {})
    assert not any(str(item.get("id") or "").startswith("S-") for item in (result.get("cases") or []))
    assert "final_shortfall_supplement_attempted" not in review_summary
    assert not any("FINAL_SHORTFALL_SUPPLEMENT" in prompt for prompt in client.prompts)


def test_standard_expected_count_exposes_matching_final_floor() -> None:
    state = build_generation_mode_control_state(
        requirement_text="Activity operation regression covers entry configuration state permission audit export and failure paths.",
        expected_count=50,
        strategy_plan=_coverage_strategy("standard_regression", 50),
    )
    focus_words = [
        "entry",
        "configure",
        "publish",
        "enroll",
        "sync",
        "archive",
        "restore",
        "audit",
        "export",
        "import",
        "retry",
        "permission",
        "schedule",
        "cancel",
        "notify",
        "lock",
        "unlock",
        "preview",
        "submit",
        "approve",
        "reject",
        "rollback",
        "reopen",
        "close",
        "copy",
        "validate",
        "search",
        "filter",
        "sort",
        "download",
        "upload",
        "assign",
        "unassign",
        "refresh",
        "timeout",
        "offline",
        "quota",
        "limit",
        "draft",
        "history",
        "detail",
        "summary",
        "report",
        "calendar",
        "batch",
        "single",
        "mobile",
        "web",
        "teacher",
        "student",
        "admin",
        "cache",
        "conflict",
        "duplicate",
        "expired",
        "pending",
        "success",
        "failure",
        "empty",
        "overflow",
        "underflow",
        "boundary",
        "state",
        "record",
        "result",
        "message",
        "panel",
        "tab",
        "dialog",
        "form",
    ]
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate activity {word} business path {idx}",
            "test_module": f"Activity {word} module {idx}",
            "preconditions": ["User has valid project permission", f"Activity dataset {idx} exists"],
            "steps": [
                f"Open activity {word} area",
                f"Execute {word} operation with case dataset {idx}",
                "Refresh page and reopen the record",
            ],
            "test_input": f"{word}-payload-{idx}",
            "expected_result": (
                f"The {word} operation for dataset {idx} completes with persisted business status "
                "and visible synchronized result."
            ),
            "priority": "P1" if idx % 3 else "P2",
        }
        for idx, word in enumerate(focus_words, start=1)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Activity operation regression covers entry configuration state permission audit export and failure paths.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=50,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    review_summary = dict(result.get("review_decision_summary") or {})
    assert summary["min_acceptable_final"] == 40
    assert "final_target_floor_count" not in review_summary
    assert summary["underfilled"] is False


def test_full_mode_marks_below_recommended_floor_underfilled_even_when_candidates_are_few() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for a compact candidate set",
        expected_count=100,
        strategy_plan=_coverage_strategy("full_functional_regression", 100),
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Compact full candidate {idx}",
            "test_module": f"Module {idx}",
            "preconditions": ["User is logged in"],
            "steps": ["Open module", "Run action"],
            "test_input": f"input {idx}",
            "expected_result": f"Result {idx} is displayed.",
            "priority": "P1" if idx % 3 else "P2",
        }
        for idx in range(1, 59)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="full functional regression for a compact candidate set",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["hard_min_count"] >= 80
    assert summary["underfilled"] is True
    assert summary["underfill_level"] in {"moderate", "severe"}
    assert summary["underfill_reason"] == "valid_candidate_insufficient"


def test_confirmed_fact_does_not_trigger_text_based_case_deletion() -> None:
    state = build_generation_mode_control_state(
        requirement_text="Course stages are non-linear and have no prerequisites; any stage can be entered initially.",
        expected_count=100,
    )
    payload = state.to_dict()
    payload.setdefault("source_meta", {})["fact_profile"] = {
        "confirmed_facts": [
            "Course stages are non-linear and have no prerequisites; all stages are enterable initially."
        ],
        "confidence": 0.9,
    }
    cases = [
        {
            "id": "TC-001",
            "description": "Course stage initial state allows all stages to be entered",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click each stage"],
            "test_input": "course stage",
            "expected_result": "All stages are enterable without prerequisite prompts.",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Course stage initial state shows locked toast before previous stage is completed",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click a locked stage"],
            "test_input": "locked stage",
            "expected_result": "The stage cannot enter and a previous stage toast is shown.",
            "priority": "P1",
        },
    ]
    _attach_independent_case_semantics(cases, module_key="course_stage")

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Course stages are non-linear and have no prerequisites; any stage can be entered initially.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=payload,
        )
    )

    descriptions = " ".join(str(item.get("description") or "") for item in (result.get("cases") or [])).lower()
    assert "allows all stages" in descriptions
    assert "locked toast" in descriptions


def test_final_set_does_not_infer_semantic_conflict_from_case_text() -> None:
    state = build_generation_mode_control_state(
        requirement_text="Course stage regression with current stage behavior.",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Course stages are non-linear and all stages are enterable initially",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click all stages"],
            "test_input": "course stage",
            "expected_result": "All stages are enterable initially without prerequisite prompts.",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Course stage initial locked toast before completing previous stage",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click a locked stage"],
            "test_input": "locked stage",
            "expected_result": "The stage is locked and a previous stage completion toast is shown.",
            "priority": "P1",
        },
    ]
    _attach_independent_case_semantics(cases, module_key="course_stage")

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Course stage regression with current stage behavior.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    descriptions = " ".join(str(item.get("description") or "") for item in (result.get("cases") or [])).lower()
    assert "all stages are enterable" in descriptions
    assert "locked toast" in descriptions


def test_external_workflow_blueprint_disconnected_states_do_not_publish_main_smoke_chain() -> None:
    blueprint = {
        "id": "schedule_flow",
        "workflow_id": "schedule_flow",
        "source_type": "human_reviewed",
        "repository_source": "workflow_blueprint_repository",
        "trusted": True,
        "initial_state": "initial",
        "required_stage_ids": ["entry", "configure", "preview", "commit", "downstream", "consume"],
        "terminal_states": ["course_learning_opened"],
        "steps": [
            {
                "id": "entry",
                "label": "Open schedule creation",
                "action": "open schedule creation",
                "state_in": "initial",
                "state_out": "schedule_create_started",
                "required": True,
                "terminal": False,
                "stage_kind": "entry",
                "actor": "supervisor",
                "match_keywords": ["open schedule creation"],
            },
            {
                "id": "configure",
                "label": "Select courses and time",
                "action": "select courses and time",
                "state_in": "courses_selected",
                "state_out": "schedule_configured",
                "required": True,
                "terminal": False,
                "stage_kind": "configure",
                "actor": "supervisor",
                "match_keywords": ["select courses and time"],
            },
            {
                "id": "preview",
                "label": "Preview schedule plan",
                "action": "preview schedule plan",
                "state_in": "schedule_configured",
                "state_out": "schedule_preview_ready",
                "required": True,
                "terminal": False,
                "stage_kind": "preview",
                "actor": "supervisor",
                "match_keywords": ["preview schedule plan"],
            },
            {
                "id": "commit",
                "label": "Save schedule plan",
                "action": "save schedule plan",
                "state_in": "schedule_preview_ready",
                "state_out": "schedule_committed",
                "required": True,
                "terminal": False,
                "stage_kind": "commit",
                "actor": "supervisor",
                "match_keywords": ["save schedule plan"],
            },
            {
                "id": "downstream",
                "label": "Student home displays saved schedule",
                "action": "display saved schedule on student home",
                "state_in": "schedule_committed",
                "state_out": "student_home_visible",
                "required": True,
                "terminal": False,
                "stage_kind": "downstream_visibility",
                "actor": "student",
                "match_keywords": ["student home displays saved schedule"],
            },
            {
                "id": "consume",
                "label": "Student opens visible course",
                "action": "open visible course",
                "state_in": "student_home_visible",
                "state_out": "course_learning_opened",
                "required": True,
                "terminal": True,
                "stage_kind": "consume",
                "actor": "student",
                "match_keywords": ["student opens visible course"],
            },
        ],
    }
    _complete_primary_workflow_fixture(blueprint, module_key="schedule")
    cases = [
        {
            "id": "TC-001",
            "description": "Open schedule creation entry",
            "test_module": "Schedule Entry",
            "preconditions": ["Supervisor is logged in"],
            "steps": ["Open schedule creation"],
            "test_input": "schedule entry",
            "expected_result": "Schedule creation page is displayed.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Select courses and time",
            "test_module": "Schedule Configure",
            "preconditions": ["Schedule creation page is open"],
            "steps": ["Select courses", "Select time slots"],
            "test_input": "courses and time slots",
            "expected_result": "Selected courses and time slots are retained.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Preview schedule plan",
            "test_module": "Schedule Preview",
            "preconditions": ["Courses and time slots are selected"],
            "steps": ["Preview schedule plan"],
            "test_input": "configured plan",
            "expected_result": "Preview displays selected dates and course count.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Save schedule plan",
            "test_module": "Schedule Save",
            "preconditions": ["Preview is ready"],
            "steps": ["Save schedule plan"],
            "test_input": "previewed plan",
            "expected_result": "Plan is saved and success message is shown.",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "Student home displays saved schedule",
            "test_module": "Student Home",
            "preconditions": ["Plan is saved"],
            "steps": ["Open student home"],
            "test_input": "saved plan",
            "expected_result": "Saved schedule is visible on student home.",
            "priority": "P0",
        },
        {
            "id": "TC-006",
            "description": "Student opens visible course",
            "test_module": "Student Course",
            "preconditions": ["Saved schedule is visible"],
            "steps": ["Open visible course"],
            "test_input": "visible course",
            "expected_result": "Course learning page opens.",
            "priority": "P0",
        },
    ]
    _attach_workflow_case_semantics(
        cases,
        blueprint=blueprint,
        module_key="schedule",
        stage_by_case_id={
            "TC-001": "entry",
            "TC-002": "configure",
            "TC-003": "preview",
            "TC-004": "commit",
            "TC-005": "downstream",
            "TC-006": "consume",
        },
    )

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Schedule workflow from creation to student learning.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=6,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state={"workflow_blueprints": [blueprint]},
        )
    )

    final_cases = result.get("cases") or []
    assert final_cases
    assert not [item for item in final_cases if str(item.get("execution_group") or "") == "main_smoke"]
    summary = dict(result.get("review_decision_summary") or {})
    execution_plan = dict(summary.get("execution_plan") or {})
    assert execution_plan["workflow_blueprint_source"] == "feedback_control_state"
    assert execution_plan["main_chain_case_count"] == 0
    assert execution_plan["main_chain_incomplete_reason"] == "state_chain_conflict"
    assert execution_plan["selected_stage_state_conflicts"][0]["reason"] == "state_not_connected"


def test_external_workflow_blueprint_reports_missing_stage_without_manufacturing_case() -> None:
    blueprint = {
        "id": "course_schedule_flow",
        "workflow_id": "course_schedule_flow",
        "source_type": "human_reviewed",
        "repository_source": "workflow_blueprint_repository",
        "trusted": True,
        "initial_state": "initial",
        "required_stage_ids": ["entry", "choose_course", "configure_time", "save_plan", "student_visible"],
        "terminal_states": ["student_visible"],
        "steps": [
            {
                "id": "entry",
                "label": "Open schedule creation",
                "action": "open schedule creation",
                "state_in": "initial",
                "state_out": "creation_opened",
                "required": True,
                "terminal": False,
                "stage_kind": "entry",
                "actor": "supervisor",
                "match_keywords": ["open schedule creation"],
            },
            {
                "id": "choose_course",
                "label": "Choose recent course",
                "action": "choose recent course",
                "state_in": "creation_opened",
                "state_out": "course_chosen",
                "required": True,
                "terminal": False,
                "stage_kind": "configure",
                "actor": "supervisor",
                "match_keywords": ["choose recent course"],
            },
            {
                "id": "configure_time",
                "label": "Configure schedule time",
                "action": "configure schedule time",
                "state_in": "course_chosen",
                "state_out": "time_configured",
                "required": True,
                "terminal": False,
                "stage_kind": "configure",
                "actor": "supervisor",
                "match_keywords": ["configure schedule time"],
            },
            {
                "id": "save_plan",
                "label": "Save schedule plan",
                "action": "save schedule plan",
                "state_in": "time_configured",
                "state_out": "plan_saved",
                "required": True,
                "terminal": False,
                "stage_kind": "commit",
                "actor": "supervisor",
                "match_keywords": ["save schedule plan"],
            },
            {
                "id": "student_visible",
                "label": "Student home displays schedule",
                "action": "student home displays schedule",
                "state_in": "plan_saved",
                "state_out": "student_visible",
                "required": True,
                "terminal": True,
                "stage_kind": "downstream_visibility",
                "actor": "student",
                "match_keywords": ["student home displays schedule"],
            },
        ],
    }
    _complete_primary_workflow_fixture(blueprint, module_key="schedule")
    cases = [
        {
            "id": "TC-001",
            "description": "Open schedule creation entry",
            "test_module": "Schedule Entry",
            "preconditions": ["Supervisor is logged in"],
            "steps": ["Open schedule creation"],
            "test_input": "schedule entry",
            "expected_result": "Schedule creation page is displayed.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Configure schedule time",
            "test_module": "Schedule Configure",
            "preconditions": ["A course has been chosen"],
            "steps": ["Configure schedule time"],
            "test_input": "weekday time slot",
            "expected_result": "Schedule time is retained and the next step is available.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Save schedule plan",
            "test_module": "Schedule Save",
            "preconditions": ["Schedule time is configured"],
            "steps": ["Save schedule plan"],
            "test_input": "configured schedule",
            "expected_result": "Plan is saved and success message is shown.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Student home displays schedule",
            "test_module": "Student Home",
            "preconditions": ["Plan is saved"],
            "steps": ["Open student home"],
            "test_input": "saved schedule",
            "expected_result": "Saved schedule is visible on student home.",
            "priority": "P0",
        },
    ]
    _attach_workflow_case_semantics(
        cases,
        blueprint=blueprint,
        module_key="schedule",
        stage_by_case_id={
            "TC-001": "entry",
            "TC-002": "configure_time",
            "TC-003": "save_plan",
            "TC-004": "student_visible",
        },
    )

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Schedule workflow from creation to student home visibility.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=5,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state={"workflow_blueprints": [blueprint]},
        )
    )

    summary = dict(result.get("review_decision_summary") or {})
    execution_plan = dict(summary.get("execution_plan") or {})
    assert execution_plan["linear_executable"] is False
    assert execution_plan["publishable_main_chain"] is False
    assert execution_plan["main_chain_incomplete_reason"] == "required_stage_gap"
    assert "choose_course" in execution_plan["missing_required_stage_ids"]
    main_cases = [
        item
        for item in (result.get("cases") or [])
        if str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert main_cases == []
    assert not any(
        item.get("description") == "Choose recent course"
        for item in (result.get("cases") or [])
    )


def test_single_pass_repairs_only_exact_required_stage_gap() -> None:
    blueprint = _complete_primary_workflow_fixture(
        {
            "id": "publish_flow",
            "workflow_id": "publish_flow",
            "initial_state": "draft",
            "required_stage_ids": ["entry", "visible"],
            "terminal_states": ["visible"],
            "steps": [
                {
                    "id": "entry",
                    "label": "Open publish entry",
                    "action": "open publish entry",
                    "state_in": "draft",
                    "state_out": "editing",
                    "stage_kind": "entry",
                    "required": True,
                    "terminal": False,
                },
                {
                    "id": "visible",
                    "label": "View published post",
                    "action": "view published post",
                    "state_in": "editing",
                    "state_out": "visible",
                    "stage_kind": "downstream_visibility",
                    "required": True,
                    "terminal": True,
                },
            ],
        },
        module_key="forum",
    )
    initial_cases = [
        {
            "id": "TC-001",
            "description": "Open publish entry",
            "test_module": "Forum",
            "preconditions": ["User is logged in"],
            "steps": ["Open publish entry"],
            "test_input": "forum entry",
            "expected_result": "The publish editor is visible.",
            "priority": "P1",
        }
    ]
    supplement_cases = [
        {
            "id": "TC-002",
            "description": "View published post",
            "test_module": "Forum",
            "preconditions": ["The publish editor is open"],
            "steps": ["Publish content", "Open published post"],
            "test_input": "valid post content",
            "expected_result": "The published post detail is visible.",
            "priority": "P1",
        }
    ]
    _attach_workflow_case_semantics(
        initial_cases,
        blueprint=blueprint,
        module_key="forum",
        stage_by_case_id={"TC-001": "entry"},
    )
    _attach_workflow_case_semantics(
        supplement_cases,
        blueprint=blueprint,
        module_key="forum",
        stage_by_case_id={"TC-002": "visible"},
    )
    client = _StreamingSupplementClient([supplement_cases])

    result = _drain_with_return(
        stream_postprocess_cases(
            client=client,
            requirement="Publish a forum post and open its detail.",
            base_prompt="BASE",
            kb_context="",
            full_content=json.dumps(initial_cases, ensure_ascii=False),
            expected_count=2,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state={"workflow_blueprints": [blueprint]},
        )
    )

    execution_plan = dict(
        (result.get("review_decision_summary") or {}).get("execution_plan") or {}
    )
    assert execution_plan["publishable_main_chain"] is True
    assert [
        item.get("main_chain_stage")
        for item in (result.get("cases") or [])
        if item.get("execution_group") == "main_smoke"
    ] == ["entry", "visible"]
    assert len(client.stream_prompts) == 1
    assert "Generic rule/type gaps: none" in client.stream_prompts[0]
    assert '"stage_id":"visible"' in client.stream_prompts[0]


def test_current_requirement_blueprint_preserves_explicit_actor_sessions() -> None:
    blueprint = {
        "id": "activity_flow",
        "workflow_id": "activity_flow",
        "source_type": "current_requirement_extracted",
        "repository_source": "current_requirement_blueprint",
        "trusted": False,
        "initial_state": "initial",
        "required_stage_ids": ["entry", "configure", "preview", "commit", "free_visible", "member_complete"],
        "terminal_states": ["member_progress_complete"],
        "steps": [
            {
                "id": "entry",
                "label": "Admin opens activity setup entry",
                "action": "open activity setup entry",
                "state_in": "initial",
                "state_out": "entry_opened",
                "stage_kind": "entry",
                "actor": "后台运营",
                "required": True,
                "terminal": False,
                "match_keywords": ["Admin opens activity setup entry"],
            },
            {
                "id": "configure",
                "label": "Teacher configures activity rules",
                "action": "configure activity rules",
                "state_in": "entry_opened",
                "state_out": "rules_configured",
                "stage_kind": "configure",
                "actor": "老师端用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Teacher configures activity rules"],
            },
            {
                "id": "preview",
                "label": "Teacher previews activity content",
                "action": "preview activity content",
                "state_in": "rules_configured",
                "state_out": "preview_ready",
                "stage_kind": "preview",
                "actor": "教师用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Teacher previews activity content"],
            },
            {
                "id": "commit",
                "label": "Teacher saves activity plan",
                "action": "save activity plan",
                "state_in": "preview_ready",
                "state_out": "plan_saved",
                "stage_kind": "commit",
                "actor": "老师端用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Teacher saves activity plan"],
            },
            {
                "id": "free_visible",
                "label": "Non-member user sees saved activity visible",
                "action": "display saved activity to non-member user",
                "state_in": "plan_saved",
                "state_out": "free_user_visible",
                "stage_kind": "downstream_visibility",
                "actor": "非会员用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Non-member user sees saved activity visible"],
            },
            {
                "id": "member_complete",
                "label": "Member user opens activity and progress becomes complete",
                "action": "open activity and complete progress",
                "state_in": "free_user_visible",
                "state_out": "member_progress_complete",
                "stage_kind": "completion_sync",
                "actor": "会员用户",
                "required": True,
                "terminal": True,
                "match_keywords": ["Member user opens activity and progress becomes complete"],
            },
        ],
    }
    _complete_primary_workflow_fixture(blueprint, module_key="activity")
    cases = [
        {
            "id": "TC-001",
            "description": "Admin opens activity setup entry",
            "test_module": "Activity Setup",
            "preconditions": ["Admin is logged in"],
            "steps": ["Open activity setup entry"],
            "test_input": "activity setup",
            "expected_result": "Activity setup entry page is opened successfully.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Teacher configures activity rules",
            "test_module": "Activity Rules",
            "preconditions": ["Activity setup entry is opened"],
            "steps": ["Configure activity rules"],
            "test_input": "activity rule set",
            "expected_result": "Activity rules are configured and retained.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Teacher previews activity content",
            "test_module": "Activity Preview",
            "preconditions": ["Activity rules are configured"],
            "steps": ["Preview activity content"],
            "test_input": "configured activity",
            "expected_result": "Activity preview displays the configured content.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Teacher saves activity plan",
            "test_module": "Activity Save",
            "preconditions": ["Activity preview is ready"],
            "steps": ["Save activity plan"],
            "test_input": "previewed activity",
            "expected_result": "Activity plan is saved and success status is shown.",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "Non-member user sees saved activity visible",
            "test_module": "Activity Visibility",
            "preconditions": ["Activity plan is saved"],
            "steps": ["Open non-member activity page"],
            "test_input": "saved activity",
            "expected_result": "Saved activity is visible and displayed to the non-member user.",
            "priority": "P0",
        },
        {
            "id": "TC-006",
            "description": "Member user opens activity and progress becomes complete",
            "test_module": "Activity Progress",
            "preconditions": ["Saved activity is visible"],
            "steps": ["Open activity", "Complete activity progress"],
            "test_input": "member activity access",
            "expected_result": "Member progress status becomes complete after opening the activity.",
            "priority": "P0",
        },
    ]
    _attach_workflow_case_semantics(
        cases,
        blueprint=blueprint,
        module_key="activity",
        stage_by_case_id={
            "TC-001": "entry",
            "TC-002": "configure",
            "TC-003": "preview",
            "TC-004": "commit",
            "TC-005": "free_visible",
            "TC-006": "member_complete",
        },
    )

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Activity workflow from setup to member completion.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=6,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state={"workflow_blueprints": [blueprint]},
        )
    )

    summary = dict(result.get("review_decision_summary") or {})
    execution_plan = dict(summary.get("execution_plan") or {})
    assert execution_plan["workflow_blueprint_source"] == "current_requirement_blueprint"
    assert execution_plan["linear_executable"] is True
    assert execution_plan["state_conflict_count"] == 0

    main_cases = [
        item
        for item in (result.get("cases") or [])
        if str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert [item.get("role") for item in main_cases] == [
        "后台运营",
        "老师端用户",
        "教师用户",
        "老师端用户",
        "非会员用户",
        "会员用户",
    ]
    assert [item.get("session_key") for item in main_cases] == [
        "后台运营_session",
        "老师端用户_session",
        "教师用户_session",
        "老师端用户_session",
        "非会员用户_session",
        "会员用户_session",
    ]
    assert [item.get("role_switch_strategy") for item in main_cases] == [
        "reuse_role_session",
        "switch_to_dedicated_role_session",
        "switch_to_dedicated_role_session",
        "switch_to_dedicated_role_session",
        "switch_to_dedicated_role_session",
        "switch_to_dedicated_role_session",
    ]


def test_current_requirement_blueprint_preserves_explicit_generic_actor() -> None:
    blueprint = {
        "id": "order_fulfillment_flow",
        "workflow_id": "order_fulfillment_flow",
        "source_type": "current_requirement_extracted",
        "repository_source": "current_requirement_blueprint",
        "trusted": False,
        "initial_state": "initial",
        "required_stage_ids": ["entry", "configure", "preview", "commit", "visible", "complete"],
        "terminal_states": ["fulfillment_status_synced"],
        "steps": [
            {
                "id": "entry",
                "label": "Customer opens order checkout entry",
                "action": "open order checkout entry",
                "state_in": "initial",
                "state_out": "checkout_opened",
                "stage_kind": "entry",
                "actor": "用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Customer opens order checkout entry"],
            },
            {
                "id": "configure",
                "label": "Customer configures order items and delivery address",
                "action": "configure order items and delivery address",
                "state_in": "checkout_opened",
                "state_out": "order_configured",
                "stage_kind": "configure",
                "actor": "用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Customer configures order items and delivery address"],
            },
            {
                "id": "preview",
                "label": "Customer reviews order confirmation preview",
                "action": "review order confirmation preview",
                "state_in": "order_configured",
                "state_out": "order_preview_ready",
                "stage_kind": "preview",
                "actor": "用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Customer reviews order confirmation preview"],
            },
            {
                "id": "commit",
                "label": "Customer submits order",
                "action": "submit order",
                "state_in": "order_preview_ready",
                "state_out": "order_submitted",
                "stage_kind": "commit",
                "actor": "用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Customer submits order"],
            },
            {
                "id": "visible",
                "label": "Order center displays latest saved order status",
                "action": "display latest saved order status",
                "state_in": "order_submitted",
                "state_out": "order_status_visible",
                "stage_kind": "downstream_visibility",
                "actor": "用户",
                "required": True,
                "terminal": False,
                "match_keywords": ["Order center displays latest saved order status"],
            },
            {
                "id": "complete",
                "label": "Fulfillment status sync completes after payment",
                "action": "sync fulfillment status after payment completes",
                "state_in": "order_status_visible",
                "state_out": "fulfillment_status_synced",
                "stage_kind": "completion_sync",
                "actor": "用户",
                "required": True,
                "terminal": True,
                "match_keywords": ["Fulfillment status sync completes after payment"],
            },
        ],
    }
    _complete_primary_workflow_fixture(blueprint, module_key="order")
    cases = [
        {
            "id": "TC-001",
            "description": "Customer opens order checkout entry",
            "test_module": "Order Checkout",
            "preconditions": ["Customer has items in cart"],
            "steps": ["Open order checkout entry"],
            "test_input": "cart with purchasable items",
            "expected_result": "Order checkout entry page is opened successfully.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Customer configures order items and delivery address",
            "test_module": "Order Configuration",
            "preconditions": ["Checkout entry is opened"],
            "steps": ["Configure item quantity", "Configure delivery address"],
            "test_input": "order item and delivery address",
            "expected_result": "Order items and delivery address are configured and retained.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Customer reviews order confirmation preview",
            "test_module": "Order Preview",
            "preconditions": ["Order information is configured"],
            "steps": ["Review order confirmation preview"],
            "test_input": "configured order",
            "expected_result": "Order confirmation preview displays the current order information.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Customer submits order",
            "test_module": "Order Submit",
            "preconditions": ["Order preview is ready"],
            "steps": ["Submit order"],
            "test_input": "previewed order",
            "expected_result": "Order is submitted and saved with success status.",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "Order center displays latest saved order status",
            "test_module": "Order Center",
            "preconditions": ["Order has been submitted"],
            "steps": ["Open order center"],
            "test_input": "submitted order",
            "expected_result": "Order center displays the latest saved order status.",
            "priority": "P0",
        },
        {
            "id": "TC-006",
            "description": "Fulfillment status sync completes after payment",
            "test_module": "Fulfillment Sync",
            "preconditions": ["Submitted order is visible"],
            "steps": ["Complete payment", "Refresh fulfillment status"],
            "test_input": "paid submitted order",
            "expected_result": "Fulfillment status sync completes and status is updated.",
            "priority": "P0",
        },
    ]
    _attach_workflow_case_semantics(
        cases,
        blueprint=blueprint,
        module_key="order",
        stage_by_case_id={
            "TC-001": "entry",
            "TC-002": "configure",
            "TC-003": "preview",
            "TC-004": "commit",
            "TC-005": "visible",
            "TC-006": "complete",
        },
    )

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Order workflow from checkout to fulfillment status sync.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=6,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state={"workflow_blueprints": [blueprint]},
        )
    )

    summary = dict(result.get("review_decision_summary") or {})
    execution_plan = dict(summary.get("execution_plan") or {})
    assert execution_plan["workflow_blueprint_source"] == "current_requirement_blueprint"
    assert execution_plan["linear_executable"] is True

    main_cases = [
        item
        for item in (result.get("cases") or [])
        if str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert [item.get("role") for item in main_cases] == ["用户"] * 6
    assert [item.get("session_key") for item in main_cases] == ["用户_session"] * 6
    assert all(item.get("source_actor_role") == "用户" for item in main_cases)
    assert "student" not in {str(item.get("role") or "") for item in main_cases}
