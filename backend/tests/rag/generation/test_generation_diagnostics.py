from modules.testing.test_generation_components.prompting.generation_diagnostics import (
    build_context_compression_diagnostics,
    build_context_source_log,
    build_coverage_diagnostics,
    build_final_context_trace,
    build_gate_reason_chain,
    build_prompt_context_intake_diagnostics,
)


def test_build_gate_reason_chain_contains_wait_result():
    chain = build_gate_reason_chain(
        {
            "snapshot_gate_enabled": True,
            "snapshot_status_before_generation": "pending",
            "snapshot_status_after_wait": "building",
            "snapshot_wait_result": "timeout_fallback_rag",
            "snapshot_wait_queue_reason": "already_pending",
        }
    )
    assert "snapshot_wait_result:timeout_fallback_rag" in chain
    assert "snapshot_queue_reason:already_pending" in chain


def test_build_coverage_diagnostics_contains_required_metrics():
    diag = build_coverage_diagnostics(
        requirement="User login must check permission and enforce amount range 0-500 within 15 days.",
        generated_cases=[
            {
                "description": "Positive login and permission check",
                "test_module": "Auth",
                "test_input": "valid username and password",
                "expected_result": "login success",
                "preconditions": ["user exists"],
                "steps": ["open login", "submit credentials"],
                "priority": "P0",
            },
            {
                "description": "Boundary amount max 500",
                "test_module": "Payment",
                "test_input": "amount=500",
                "expected_result": "accepted",
                "steps": ["set amount", "submit order"],
                "priority": "P1",
            },
            {
                "description": "Invalid credential should fail",
                "test_module": "Auth",
                "test_input": "invalid password",
                "expected_result": "error message",
                "steps": ["open login", "submit invalid credentials"],
                "priority": "P1",
            },
        ],
        kb_context="amount range is 0-500 and timeout is 15 days",
        fusion_debug={"final_chunks": [{"chunk_text": "amount range is 0-500"}]},
        expected_count=5,
    )
    assert diag["diag_version"] == "2.5"
    assert diag["expected_count"] == 5
    assert diag["generated_count"] == 3
    assert diag["missing_count"] == 2
    assert diag["edge_count"] >= 1
    assert diag["negative_count"] >= 1
    assert isinstance(diag["possible_gap_reasons"], list)
    assert "priority_distribution" in diag
    assert 0.0 <= diag["requirement_keyword_coverage"] <= 1.0


def test_build_context_source_log_has_snapshot_and_rag_sections():
    payload = build_context_source_log(
        context_result={
            "context_source": "snapshot+rag",
            "fusion_debug": {"final_decision": "proceed_with_generation", "reason_chain": ["x"]},
            "snapshot_result": {
                "snapshot_version": 3,
                "snapshot_fingerprint": "abc",
                "rebuild_reason": "incremental_merge",
                "build_latency_ms": 123.4,
                "snapshot_status": "success",
            },
            "rag_result": {"debug": {"attempt_count": 1, "final_status": "success"}},
        },
        gate_debug={"snapshot_wait_result": "ready"},
        doc_type="requirement",
        compress=False,
        requirement_length=120,
    )
    assert payload["kind"] == "gen_context_source"
    assert payload["snapshot"]["version"] == 3
    assert payload["rag"]["attempt_count"] == 1


def test_build_context_compression_diagnostics_reports_core_metrics():
    payload = build_context_compression_diagnostics(
        context_result={
            "context_source": "rag_only",
            "snapshot_result": {
                "snapshot_id": "snap-001",
                "corpus_hash": "corpus-abc",
            },
            "rag_result": {
                "debug": {
                    "compressed_count": 2,
                    "compressor_stats": {
                        "input_chars": 1000,
                        "output_chars": 400,
                        "deduped_count": 6,
                    },
                    "final_chunks": [
                        {"final_score": 0.86},
                        {"final_score": 0.58},
                    ],
                    "retrieval_profile": {"query": "login", "strategy": "hybrid"},
                }
            },
        }
    )
    assert payload["context_source"] == "rag_only"
    assert payload["compression_ratio"] == 0.4
    assert payload["compression_rate"] == 0.6
    assert payload["retained_chunk_count"] == 2
    assert payload["input_chunk_count"] == 6
    assert payload["chunk_retention_ratio"] == round(2 / 6, 4)
    assert payload["relevance_distribution"]["sample_size"] == 2
    assert payload["relevance_distribution"]["high_count"] == 1
    assert payload["relevance_distribution"]["medium_count"] == 1
    assert payload["snapshot_id"] == "snap-001"
    assert payload["corpus_hash"] == "corpus-abc"
    assert isinstance(payload["retrieval_hash"], str) and len(payload["retrieval_hash"]) > 0


def test_build_prompt_context_intake_diagnostics_reports_sections_and_sources():
    system_prompt = "system prompt with generation rules"
    base_prompt = "base prompt"
    payload = build_prompt_context_intake_diagnostics(
        prompt_context={
            "requirement_context": "User must click save and then preview the committed result.",
            "requirement_semantics_context": "confirmed save -> preview flow",
            "testcase_context": "(empty)",
            "supplement_context": "reference examples",
            "control_context": "control rules",
            "current_biz_key": "lesson_review",
            "only_current_biz": True,
            "control_summary": {"must_cover_rules_count": 2, "quality_fix_hints_count": 3},
            "feedback_control_state": {
                "workflow_blueprints": [{"name": "main_smoke"}],
                "source_meta": {
                    "fact_profile": {
                        "profile_source": "requirement_semantics",
                        "confidence": 0.8,
                        "confirmed_facts": ["save creates result"],
                        "pending_items": [],
                        "forbidden_facts": ["do not jump to report first"],
                    }
                },
            },
            "biz_key_order": ["lesson_review"],
            "module_order_hint": ["entry", "save", "preview"],
            "module_order_source": "requirement",
            "requirement_semantics_by_biz": {
                "lesson_review": {
                    "confirmed_facts": ["save creates result"],
                    "scoped_rules": ["save required"],
                    "pending_items": ["preview copy"],
                    "forbidden_facts": ["skip save"],
                    "reuse_risks": [],
                }
            },
            "scoped_rules": ["save required"],
            "hard_flow_constraints": ["save before preview"],
            "reuse_risks": [],
        },
        context_result={
            "context_source": "snapshot+rag",
            "fusion_debug": {
                "snapshot_used": True,
                "snapshot_status": "ready",
                "snapshot_version": 4,
                "rag_used": True,
                "rag_chunk_count": 2,
            },
            "snapshot_result": {"snapshot_version": 4},
            "rag_result": {
                "debug": {
                    "final_status": "success",
                    "retrieval_profile": {"query": "save preview"},
                    "final_chunks": [
                        {
                            "doc_id": "doc-1",
                            "chunk_id": "chunk-1",
                            "filename": "requirement.md",
                            "final_score": 0.91,
                            "chunk_text": "\u5f00\u53d1\u9002\u914d\u70b9: \u6570\u636e\u5e93\u8868\u4ec5\u4f5c\u6280\u672f\u8bf4\u660e",
                        },
                        {
                            "doc_id": "doc-2",
                            "chunk_id": "chunk-2",
                            "filename": "flow.md",
                            "final_score": 0.86,
                            "chunk_text": "\u70b9\u51fb\u4fdd\u5b58\u540e\u8fdb\u5165\u9884\u89c8\u9875",
                        },
                    ],
                }
            },
        },
        requirement="User clicks save and previews result.",
        kb_context="snapshot plus rag context",
        base_prompt=base_prompt,
        system_prompt=system_prompt,
        mode="stream",
        doc_type="requirement",
        compress=False,
        project_id=1,
        request_id="req-1",
        batch_index=1,
        total_batches=2,
        attempt=1,
        expected_count=5,
        multi_pass=True,
        generation_mode="multi_pass",
        model="qwen-plus",
        max_output_tokens=10000,
    )

    assert payload["kind"] == "prompt_context_intake"
    assert payload["max_tokens_semantics"] == "output_tokens"
    assert payload["section_sizes"]["system_prompt"]["chars"] == len(system_prompt)
    assert payload["section_sizes"]["full_input"]["chars"] == len(system_prompt + "User clicks save and previews result.")
    assert payload["source_lanes"]["rag"]["chunk_count"] == 2
    assert payload["rag_sources"][0]["doc_id"] == "doc-1"
    assert "dev_adaptation_fragment" in payload["rag_sources"][0]["noise_flags"]
    assert payload["control"]["workflow_blueprint_count"] == 1
    assert payload["control"]["fact_profile_confirmed_count"] == 1
    assert payload["business_scope"]["requirement_semantics_by_biz"][0]["pending_items_count"] == 1
    assert "workflow_blueprint_missing" not in payload["risk_flags"]


def test_build_final_context_trace_rag_only_success():
    trace = build_final_context_trace(
        project_id=1001,
        request_id="req-rag-only",
        context_result={
            "context_source": "rag_only",
            "fusion_debug": {
                "snapshot_status": "stale",
                "snapshot_used": False,
                "rag_used": True,
                "rag_mode": "rag_only",
                "rag_chunk_count": 5,
                "reason_chain": ["snapshot_fallback:snapshot_async_rebuild_skip", "final_decision:proceed_with_generation"],
            },
            "snapshot_result": {"snapshot_version": 0},
            "rag_result": {"debug": {"rerank_top": [{"score": 0.91}, {"score": 0.85}]}},
        },
        gate_debug={"snapshot_wait_result": "ready_then_proceed"},
        fallback_reason="",
        abort_code="",
        compressed_chars=2048,
    )
    assert trace["kind"] == "final_context_trace"
    assert trace["project_id"] == 1001
    assert trace["request_id"] == "req-rag-only"
    assert trace["snapshot_used"] is False
    assert trace["rag_used"] is True
    assert trace["context_source_mode"] == "rag_only"
    assert trace["gate_result"] == "ready_then_proceed"
    assert trace["rerank_top_k"] == 2


def test_build_final_context_trace_snapshot_ready_success():
    trace = build_final_context_trace(
        project_id=1002,
        request_id="req-snapshot",
        context_result={
            "context_source": "snapshot_only",
            "fusion_debug": {
                "snapshot_status": "ready",
                "snapshot_used": True,
                "rag_used": False,
                "rag_mode": "snapshot_only",
                "rag_chunk_count": 0,
                "reason_chain": ["snapshot:reuse_or_rebuild_success", "final_decision:proceed_with_generation"],
            },
            "snapshot_result": {"snapshot_version": 9},
            "rag_result": {"debug": {}},
        },
        gate_debug={"snapshot_wait_result": "ready_then_proceed"},
        fallback_reason="",
        abort_code="",
        compressed_chars=512,
    )
    assert trace["snapshot_used"] is True
    assert trace["snapshot_version"] == 9
    assert trace["rag_used"] is False
    assert trace["context_source_mode"] == "snapshot_only"
    assert trace["gate_result"] == "ready_then_proceed"


def test_build_final_context_trace_snapshot_timeout_failure_reason_chain():
    trace = build_final_context_trace(
        project_id=1003,
        request_id="req-timeout",
        context_result={
            "context_source": "none",
            "fusion_debug": {
                "snapshot_status": "pending",
                "snapshot_used": False,
                "rag_used": False,
                "rag_chunk_count": 0,
                "reason_chain": [
                    "snapshot_gate_timeout",
                    "hybrid_context_not_built",
                    "generation_aborted_before_model_call",
                ],
            },
        },
        gate_debug={"snapshot_wait_result": "timeout_fail_fast"},
        fallback_reason="snapshot_wait_gate_abort",
        abort_code="SNAPSHOT_NOT_READY_TIMEOUT",
        compressed_chars=0,
    )
    assert trace["abort_code"] == "SNAPSHOT_NOT_READY_TIMEOUT"
    assert trace["gate_result"] == "timeout_fail_fast"
    assert "snapshot_gate_timeout" in trace["reason_chain"]
    assert "hybrid_context_not_built" in trace["reason_chain"]
    assert "generation_aborted_before_model_call" in trace["reason_chain"]
