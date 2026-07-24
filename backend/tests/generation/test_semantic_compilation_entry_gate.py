import json

import modules.test_generation_components.legacy.json_generation_impl as json_impl
import modules.test_generation_components.legacy.stream.prepare as stream_prepare
from modules.test_generation_components.control.current_requirement_blueprint import (
    extract_current_requirement_blueprints,
)
from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.legacy.json_generation_runtime import JsonGenerationRuntimeState
from modules.test_generation_components.legacy.stream.prepare_runtime import (
    AppendExistingState,
    PrepareRuntimeState,
)


REQUIREMENT_TEXT = "用户在官方区点击发布入口后提交帖子。"

_FACT_STAGE_INPUT_TYPE = "current_requirement_atomic_fact_compile"
_SCOPE_BOUNDARY_SELECTION_STAGE_INPUT_TYPE = (
    "current_requirement_scope_boundary_selection_compile"
)
_SCOPE_MEMBERSHIP_STAGE_INPUT_TYPE = (
    "current_requirement_scope_membership_compile"
)
_SCOPE_BINDING_STAGE_INPUT_TYPE = "current_requirement_scope_binding_compile"
_GRAPH_STAGE_INPUT_TYPE = "current_requirement_graph_compile"
_FACT_ID = "F_CURRENT_REQUIREMENT"
_SCOPE_ID = "SCOPE_CURRENT_REQUIREMENT"
_CAPABILITY_ID = "C_CURRENT_REQUIREMENT"


def _request_payload(requirement: object) -> dict:
    try:
        payload = json.loads(str(requirement or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _request_scope_facts(request_payload: dict) -> list[dict]:
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
    if any(
        not isinstance(row, list) or len(row) != len(schema)
        for row in rows
    ):
        return []
    return [dict(zip(schema, row)) for row in rows]


def _valid_fact_stage_payload(request_payload: dict) -> dict:
    target_catalog = request_payload["target_source_evidence_catalog"]
    assert len(target_catalog) == 1
    evidence_ref = target_catalog[0]["ref"]
    statement = str(target_catalog[0]["quote"]).rstrip("。")
    return {
        "source_evidence_records": [
            {
                "evidence_ref": evidence_ref,
                "owned_facts": [
                    {
                        "fact_id": _FACT_ID,
                        "fact_kind": "action",
                        "statement": statement,
                        "requirement_level": "required",
                        "priority": "unspecified",
                        "testability": "testable",
                        "evidence": [evidence_ref],
                        "confidence": 0.95,
                    }
                ],
            }
        ],
    }


def _valid_scope_boundaries(request_payload: dict) -> list[dict]:
    frozen_facts = _request_scope_facts(request_payload)
    assert len(frozen_facts) == 1
    fact_ref = frozen_facts[0]["fact_ref"]
    return [
            {
                "boundary_id": _SCOPE_ID,
                "label": "当前需求职责",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "",
                "membership_relation_refs": [],
                "membership_fact_refs": [],
                "support": [
                    {
                        "signal": "purpose",
                        "fact_refs": [fact_ref],
                    }
                ],
            }
        ]


def _valid_scope_boundary_selection_stage_payload(
    request_payload: dict,
) -> dict:
    records: list[dict] = []
    for boundary in _valid_scope_boundaries(request_payload):
        records.append(
            {
                "boundary_id": boundary["boundary_id"],
                "label": boundary["label"],
                "decision": "in_scope",
                "parent_boundary_id": boundary["parent_boundary_id"],
                "support": boundary["support"],
            }
        )
    return {"boundary_records": records}


def _valid_scope_membership_stage_payload(request_payload: dict) -> dict:
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
    return {"membership_assignments": assignments}


def _valid_scope_binding_stage_payload(request_payload: dict) -> dict:
    frozen_facts = _request_scope_facts(request_payload)
    assert len(frozen_facts) == 1
    fact_ref = frozen_facts[0]["fact_ref"]
    assert request_payload["target_fact_refs"] == [fact_ref]
    assert request_payload["frozen_boundary_manifest"]["boundaries"] == (
        _valid_scope_boundaries(request_payload)
    )
    assert request_payload["frozen_source_outline"]["fingerprint"]
    assert request_payload["target_topology_usage"] == [
        {
            "fact_ref": fact_ref,
            "explicit_membership_edges": [],
            "support_scope_ids": [_SCOPE_ID],
        }
    ]
    return {
        "fact_bindings": [
            {
                "fact_ref": fact_ref,
                "scope_ids": [_SCOPE_ID],
                "role": "owned_requirement",
            }
        ]
    }


def _valid_graph_stage_payload(request_payload: dict) -> dict:
    frozen_context = request_payload["frozen_context"]
    projection = frozen_context["ledger_projection"]
    assert projection["active_scope_ids"] == [_SCOPE_ID]
    frozen_facts = frozen_context["evidence_facts"]
    assert len(frozen_facts) == 1
    stable_fact_id = frozen_facts[0]["fact_id"]
    return {
        "confidence": 0.95,
        "semantic_graph": {
            "graph_version": "requirement-semantic-graph-v1",
            "nodes": [
                {
                    "node_id": _SCOPE_ID,
                    "kind": "scope",
                    "name": "当前需求职责",
                    "aliases": [],
                    "scope_status": "in_scope",
                    "boundary_status": "resolved",
                    "fact_ids": [stable_fact_id],
                    "confidence": 0.95,
                },
                {
                    "node_id": _CAPABILITY_ID,
                    "kind": "capability",
                    "name": "执行当前需求行为",
                    "aliases": [],
                    "scope_status": "in_scope",
                    "boundary_status": "resolved",
                    "fact_ids": [stable_fact_id],
                    "confidence": 0.95,
                },
            ],
            "edges": [
                {
                    "edge_id": "E_SCOPE_OWNS_CAPABILITY",
                    "type": "owns",
                    "source_node_id": _SCOPE_ID,
                    "target_node_id": _CAPABILITY_ID,
                    "fact_ids": [stable_fact_id],
                    "ownership_role": "primary",
                    "trigger": "",
                    "result_state": "",
                    "transferred_entity_node_ids": [],
                    "confidence": 0.95,
                }
            ],
            "primary_flow": {"node_ids": [], "edge_ids": []},
            "fact_dispositions": [],
        },
        "workflow_blueprints": [],
    }


def _invalid_semantic_payload() -> dict:
    return {
        "semantic_contract_version": "requirement-semantic-v1",
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "official",
                    "module_name": "官方区",
                    "scope_status": "in_scope",
                    "evidence": ["用户在官方区点击发布入口后提交帖子"],
                    "confidence": 0.95,
                }
            ],
            "module_interactions": [],
        },
        "workflow_blueprints": [
            {
                "workflow_id": "publish_post",
                "name": "发布帖子",
                "primary": True,
                "initial_state": "official_ready",
                "required_stage_ids": ["open_publish"],
                "terminal_states": ["editor_opened"],
                "steps": [
                    {
                        "id": "open_publish",
                        "label": "点击发布入口",
                        "action": "点击发布入口",
                        "stage_kind": "entry",
                        "actor": "business_user",
                        "state_in": "official_ready",
                        "required": True,
                        "terminal": True,
                        "critical": True,
                        "blocking": True,
                        "destructive": False,
                        "module_candidates": [
                            {
                                "module_key": "official",
                                "role": "primary",
                                "confidence": 0.95,
                                "evidence": ["用户在官方区点击发布入口后提交帖子"],
                            }
                        ],
                        "interaction_ids": [],
                        "evidence": ["点击发布入口"],
                    }
                ],
            }
        ],
    }


class _ThreeStageClient:
    def __init__(self) -> None:
        self.stage_calls = {
            _FACT_STAGE_INPUT_TYPE: 0,
            _SCOPE_BOUNDARY_SELECTION_STAGE_INPUT_TYPE: 0,
            _SCOPE_MEMBERSHIP_STAGE_INPUT_TYPE: 0,
            _SCOPE_BINDING_STAGE_INPUT_TYPE: 0,
            _GRAPH_STAGE_INPUT_TYPE: 0,
        }
        self.case_generation_calls = 0

    def _graph_stage_response(self, request_payload: dict) -> dict:
        return _valid_graph_stage_payload(request_payload)

    def _case_generation_response(self) -> object:
        return []

    def generate_response(
        self,
        requirement,
        prompt=None,  # noqa: ANN001, ARG002
        **_kwargs,
    ) -> str:
        request_payload = _request_payload(requirement)
        input_type = request_payload.get("input_type")
        if input_type == _FACT_STAGE_INPUT_TYPE:
            self.stage_calls[input_type] += 1
            payload = _valid_fact_stage_payload(request_payload)
            return json.dumps(payload, ensure_ascii=False)
        if input_type == _SCOPE_BOUNDARY_SELECTION_STAGE_INPUT_TYPE:
            self.stage_calls[input_type] += 1
            payload = _valid_scope_boundary_selection_stage_payload(
                request_payload
            )
            return json.dumps(payload, ensure_ascii=False)
        if input_type == _SCOPE_MEMBERSHIP_STAGE_INPUT_TYPE:
            self.stage_calls[input_type] += 1
            payload = _valid_scope_membership_stage_payload(request_payload)
            return json.dumps(payload, ensure_ascii=False)
        if input_type == _SCOPE_BINDING_STAGE_INPUT_TYPE:
            self.stage_calls[input_type] += 1
            payload = _valid_scope_binding_stage_payload(request_payload)
            return json.dumps(payload, ensure_ascii=False)
        if input_type == _GRAPH_STAGE_INPUT_TYPE:
            self.stage_calls[input_type] += 1
            payload = self._graph_stage_response(request_payload)
            return json.dumps(payload, ensure_ascii=False)
        self.case_generation_calls += 1
        return json.dumps(self._case_generation_response(), ensure_ascii=False)


class _SemanticClient(_ThreeStageClient):
    def _graph_stage_response(self, request_payload: dict) -> dict:  # noqa: ARG002
        return _invalid_semantic_payload()


class _ParseableInvalidGraphClient(_ThreeStageClient):
    def _graph_stage_response(self, request_payload: dict) -> dict:
        payload = _valid_graph_stage_payload(request_payload)
        payload["semantic_graph"]["nodes"][1]["confidence"] = 0
        return payload


class _RecordingLog:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _RecordingDb:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0
        self.fail_commit = fail_commit

    def add(self, value) -> None:  # noqa: ANN001
        self.added.append(value)

    def commit(self) -> None:
        self.commit_count += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rollback_count += 1


class _StreamHarness(stream_prepare.LegacyGenerationStreamPrepareMixin):
    def _is_active_db_session(self, db) -> bool:  # noqa: ANN001
        return False


class _JsonHarness(json_impl.LegacyGenerationJsonMixin):
    def _is_active_db_session(self, db) -> bool:  # noqa: ANN001
        return True

    def _run_snapshot_readiness_gate(self, **kwargs):  # noqa: ANN003
        return {"proceed": True, "gate_debug": {"snapshot_wait_result": "ready"}}

    def _resolve_kb_context_with_hybrid(self, **kwargs):  # noqa: ANN003
        return {
            "kb_context": "当前需求上下文",
            "context_source": "current_requirement",
            "fusion_debug": {},
            "abort_generation": False,
        }


class _JsonCaseContractHarness(_JsonHarness):
    def analyze_requirement_context(self, *_args, **_kwargs):
        return {"system_type": "Web", "impact_scope": "module"}

    def _default_strategy_plan(self):
        return {"system_type": "Web", "impact_scope": "module"}

    def _emit_final_context_trace(self, **_kwargs) -> None:
        return None


class _MissingCaseSemanticClient(_ThreeStageClient):
    def _case_generation_response(self) -> object:
        return [
            {
                "id": "TC-001",
                "description": "用户查看论坛帖子详情",
                "test_module": "论坛",
                "preconditions": ["论坛存在已发布帖子"],
                "steps": ["打开论坛", "查看帖子详情"],
                "test_input": "已发布帖子",
                "expected_result": "页面展示该帖子的标题与正文",
                "priority": "P1",
            }
        ]


def _assert_graph_stage_aborted_before_case_generation(
    client: _SemanticClient,
) -> None:
    assert client.stage_calls == {
        _FACT_STAGE_INPUT_TYPE: 1,
        _SCOPE_BOUNDARY_SELECTION_STAGE_INPUT_TYPE: 1,
        _SCOPE_MEMBERSHIP_STAGE_INPUT_TYPE: 1,
        _SCOPE_BINDING_STAGE_INPUT_TYPE: 1,
        _GRAPH_STAGE_INPUT_TYPE: 2,
    }
    assert client.case_generation_calls == 0


def test_stream_entry_aborts_before_test_case_generation(monkeypatch) -> None:
    client = _SemanticClient()
    monkeypatch.setattr(
        stream_prepare,
        "resolve_stream_prepare_runtime",
        lambda **kwargs: PrepareRuntimeState(
            client=client,
            request_id="stream-request",
            original_requirement=REQUIREMENT_TEXT,
            compression_decision={},
            linked_final_case_signal={},
            memory_diag={},
            memory_fabric=None,
            memory_ctx=None,
        ),
    )
    monkeypatch.setattr(
        stream_prepare,
        "resolve_append_existing_state",
        lambda **kwargs: AppendExistingState(
            start_id=1,
            existing_cases=[],
            existing_entry=None,
            existing_unique_count=0,
        ),
    )

    chunks = list(
        _StreamHarness()._stream_prepare_phase(
            client=client,
            request_id="stream-request",
            requirement=REQUIREMENT_TEXT,
            project_id=7,
            db=None,
            user_id=9,
        )
    )

    _assert_graph_stage_aborted_before_case_generation(client)
    assert any("semantic_compilation_abort" in chunk for chunk in chunks)
    assert any("SEMANTIC_COMPILATION_FAILED" in chunk for chunk in chunks)


def _run_json_abort(monkeypatch, db: _RecordingDb) -> tuple[dict, _SemanticClient]:
    client = _SemanticClient()
    monkeypatch.setattr(
        json_impl,
        "resolve_json_generation_runtime",
        lambda **kwargs: JsonGenerationRuntimeState(
            client=client,
            request_id="json-request",
            original_requirement=REQUIREMENT_TEXT,
            linked_final_case_signal={},
            memory_diag={},
            memory_fabric=None,
            memory_ctx=None,
        ),
    )
    monkeypatch.setattr(
        json_impl,
        "build_feedback_control_state",
        lambda **kwargs: FeedbackControlState.empty(),
    )
    monkeypatch.setattr(
        json_impl,
        "merge_generation_mode_control_state",
        lambda state, **kwargs: FeedbackControlState.from_any(state),
    )
    monkeypatch.setattr(json_impl, "LogEntry", _RecordingLog)

    result = _JsonHarness().generate_test_cases_json(
        requirement=REQUIREMENT_TEXT,
        project_id=7,
        db=db,
        user_id=9,
    )
    return result, client


def test_json_entry_persists_semantic_abort_before_return(monkeypatch) -> None:
    db = _RecordingDb()

    result, client = _run_json_abort(monkeypatch, db)

    assert result["error"] == "SEMANTIC_COMPILATION_FAILED"
    _assert_graph_stage_aborted_before_case_generation(client)
    assert db.commit_count == 1
    payload = json.loads(db.added[0].message.removeprefix("GEN_DIAG:"))
    assert payload["kind"] == "semantic_compilation_abort"
    assert payload["request_id"] == "json-request"
    assert payload["semantic_pipeline_failed_stage"] == "graph"
    assert payload["workflow_declaration_status"] == "response_contract_invalid"
    assert payload["fact_ledger_compile_success"] is True
    assert payload["scope_ledger_compile_success"] is True
    assert payload["workflow_rejection_reasons"] == [
        "semantic_pipeline_failed_stage:graph"
    ]


def test_json_diagnostic_commit_failure_does_not_hide_abort(monkeypatch) -> None:
    db = _RecordingDb(fail_commit=True)

    result, client = _run_json_abort(monkeypatch, db)

    assert result["error"] == "SEMANTIC_COMPILATION_FAILED"
    _assert_graph_stage_aborted_before_case_generation(client)
    assert db.rollback_count == 1


def test_failed_graph_candidate_is_not_propagated_into_source_meta_contract() -> None:
    client = _ParseableInvalidGraphClient()

    _, diagnostics = extract_current_requirement_blueprints(
        client=client,
        requirement_text=REQUIREMENT_TEXT,
        db=None,
        project_id=7,
        user_id=9,
    )

    assert diagnostics["semantic_compile_success"] is False
    assert diagnostics["semantic_pipeline_failed_stage"] == "graph"
    contract = diagnostics["requirement_semantic_contract"]
    assert contract["semantic_compile_success"] is False
    assert contract["evidence_facts"] == []
    assert contract["functional_architecture"]["functional_modules"] == []
    assert contract["semantic_graph"]["nodes"] == []


def test_json_primary_generation_missing_case_semantic_aborts_before_review(monkeypatch) -> None:
    client = _MissingCaseSemanticClient()
    monkeypatch.setattr(
        json_impl,
        "resolve_json_generation_runtime",
        lambda **kwargs: JsonGenerationRuntimeState(
            client=client,
            request_id="json-case-contract-request",
            original_requirement="用户在论坛查看帖子。",
            linked_final_case_signal={},
            memory_diag={},
            memory_fabric=None,
            memory_ctx=None,
        ),
    )

    result = _JsonCaseContractHarness().generate_test_cases_json(
        requirement="用户在论坛查看帖子。",
        project_id=7,
        db=None,
        user_id=9,
        expected_count=1,
        batch_size=1,
        multi_pass=False,
        generation_mode="",
    )

    assert result["error"] == "CASE_SEMANTIC_CONTRACT_FAILED"
    assert result["reason_chain"][-1] == "generation_aborted_before_review"
    assert result["diagnostic"]["rejected_count"] == 1
    assert client.stage_calls == {
        _FACT_STAGE_INPUT_TYPE: 1,
        _SCOPE_BOUNDARY_SELECTION_STAGE_INPUT_TYPE: 1,
        _SCOPE_MEMBERSHIP_STAGE_INPUT_TYPE: 1,
        _SCOPE_BINDING_STAGE_INPUT_TYPE: 1,
        _GRAPH_STAGE_INPUT_TYPE: 1,
    }
    assert client.case_generation_calls == 1
