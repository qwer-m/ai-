from modules.test_generation_components.judge.judge_duplicate_rules import (
    _is_semantic_duplicate_case,
)
from modules.test_generation_components.postprocess.case_fact_relations import (
    build_case_semantic_identity,
    compare_case_semantic_identity,
    deduplicate_cases_by_semantic_identity,
)
from modules.test_generation_components.postprocess.streaming_case_keys import case_signature
from modules.test_generation_components.postprocess.streaming_review_selection import (
    resolve_review_llm_drop_reason_maps,
)


def _case(
    case_id: str,
    *,
    description: str,
    preconditions: list[str],
    steps: list[str],
    test_input: str,
    expected_result: str,
    fact_ids: list[str] | None = None,
    workflow_stage: str = "",
) -> dict:
    workflow_candidates = []
    if workflow_stage:
        workflow_candidates.append(
            {
                "workflow_id": "resource_access_flow",
                "stage_id": workflow_stage,
                "stage_kind": "action",
                "evidence": [steps[0]],
            }
        )
    return {
        "id": case_id,
        "description": description,
        "test_module": "资源访问控制",
        "preconditions": preconditions,
        "steps": steps,
        "test_input": test_input,
        "expected_result": expected_result,
        "priority": "P1",
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": "resource_access",
                    "module_name": "资源访问控制",
                    "role": "primary",
                    "evidence": [description],
                }
            ],
            "fact_ids": fact_ids or [],
            "interaction_ids": [],
            "workflow_stage_candidates": workflow_candidates,
            "precondition_states": [],
            "produced_states": [],
        },
    }


def _equivalent_access_cases() -> tuple[dict, dict]:
    first = _case(
        "TC-A",
        description="普通账号访问非首个受限资源时被权限锁拦截，点击跳转升级页",
        preconditions=["用户为普通账号", "用户已进入资源列表页"],
        steps=["1. 点击非首个受限资源", "2. 观察锁定状态与页面跳转"],
        test_input="普通账号点击非首个受限资源",
        expected_result="资源显示权限锁定标识，点击后页面跳转至账号升级页",
        fact_ids=["FACT-ACCESS-GATE-A"],
    )
    second = _case(
        "TC-B",
        description="普通账号点击锁住的受限资源跳转账号升级页",
        preconditions=["当前为普通账号", "已打开资源列表页"],
        steps=["1. 查看受限资源的锁定标识", "2. 点击锁住的非首个资源"],
        test_input="普通账号点击锁住的非首个资源",
        expected_result="受限资源展示权限锁定标识；点击后跳转账号升级页",
        fact_ids=["FACT-ACCESS-GATE-B"],
    )
    return first, second


def _different_trigger_cases() -> tuple[dict, dict]:
    processing_failure = _case(
        "TC-PROCESS",
        description="任务执行失败时提示并自动删除任务记录",
        preconditions=["任务已提交并处于处理中", "服务端返回执行失败"],
        steps=["1. 等待任务执行失败", "2. 观察失败提示与记录状态"],
        test_input="任务处理过程中服务端返回失败",
        expected_result="页面提示本次执行失败，并自动删除对应任务记录",
        fact_ids=["FACT-FAILED-RECORD-CLEANUP"],
    )
    click_failed_record = _case(
        "TC-CLICK",
        description="点击失败任务记录时提示并自动删除记录",
        preconditions=["任务列表中已存在一条失败记录"],
        steps=["1. 进入任务列表", "2. 点击失败任务记录", "3. 观察提示与列表"],
        test_input="用户点击已经失败的任务记录",
        expected_result="页面提示本次执行失败，提示结束后自动删除该任务记录",
        fact_ids=["FACT-FAILED-RECORD-CLEANUP"],
    )
    return processing_failure, click_failed_record


def test_identity_reuses_verified_contract_dimensions() -> None:
    case, _ = _equivalent_access_cases()

    identity = build_case_semantic_identity(case)

    assert identity.module_keys == frozenset({"resource_access"})
    assert identity.fact_ids == frozenset({"FACT-ACCESS-GATE-A"})
    assert identity.source_evidence
    assert identity.intent_signature


def test_different_fact_ids_do_not_hide_equivalent_behavior() -> None:
    first, second = _equivalent_access_cases()

    comparison = compare_case_semantic_identity(first, second)

    assert comparison.relation == "duplicate"
    assert comparison.confidence >= 0.7
    assert comparison.reasons[:2] == (
        "same_intent_signature",
        "equivalent_trigger_action_outcome",
    )


def test_same_fact_does_not_merge_different_trigger_timing() -> None:
    processing_failure, click_failed_record = _different_trigger_cases()

    comparison = compare_case_semantic_identity(processing_failure, click_failed_record)

    assert comparison.relation == "none"
    assert "different_trigger_context" in comparison.conflicts


def test_same_template_with_different_validation_objects_is_preserved() -> None:
    first = _case(
        "TC-OBJECT-A",
        description="验证对象甲有分组时展示分类名称，无分组时不展示分类名称",
        preconditions=["存在有分组的对象甲数据", "存在无分组的对象甲数据"],
        steps=["1. 查看有分组的对象甲", "2. 查看无分组的对象甲"],
        test_input="有分组和无分组的对象甲数据",
        expected_result="有分组的对象甲展示分类名称，无分组的对象甲不展示分类名称",
        fact_ids=["FACT-OBJECT-A-GROUP"],
    )
    second = _case(
        "TC-OBJECT-B",
        description="验证对象乙有分组时展示分类名称，无分组时不展示分类名称",
        preconditions=["存在有分组的对象乙数据", "存在无分组的对象乙数据"],
        steps=["1. 查看有分组的对象乙", "2. 查看无分组的对象乙"],
        test_input="有分组和无分组的对象乙数据",
        expected_result="有分组的对象乙展示分类名称，无分组的对象乙不展示分类名称",
        fact_ids=["FACT-OBJECT-B-GROUP"],
    )

    comparison = compare_case_semantic_identity(first, second)

    assert comparison.relation == "none"


def test_different_workflow_stages_are_kept_even_when_public_text_is_similar() -> None:
    first, second = _equivalent_access_cases()
    first["_semantic"]["workflow_stage_candidates"] = [
        {
            "workflow_id": "resource_access_flow",
            "stage_id": "before_activation",
            "stage_kind": "action",
            "evidence": [first["steps"][0]],
        }
    ]
    second["_semantic"]["workflow_stage_candidates"] = [
        {
            "workflow_id": "resource_access_flow",
            "stage_id": "after_activation",
            "stage_kind": "action",
            "evidence": [second["steps"][0]],
        }
    ]

    comparison = compare_case_semantic_identity(first, second)

    assert comparison.relation == "none"
    assert "different_workflow_stage" in comparison.conflicts


def test_judge_uses_shared_relation_and_respects_trigger_conflict() -> None:
    duplicate_left, duplicate_right = _equivalent_access_cases()
    trigger_left, trigger_right = _different_trigger_cases()

    duplicate, confidence = _is_semantic_duplicate_case(duplicate_left, duplicate_right)
    distinct, distinct_confidence = _is_semantic_duplicate_case(trigger_left, trigger_right)

    assert duplicate is True
    assert confidence >= 0.7
    assert distinct is False
    assert distinct_confidence == 0.0


def test_review_backfill_reports_shared_semantic_relation_evidence() -> None:
    selected, omitted = _equivalent_access_cases()

    resolved, sources, evidence = resolve_review_llm_drop_reason_maps(
        pool_cases=[selected, omitted],
        selected_cases=[selected],
        raw_drop_reason_map={},
        coverage_context=None,
        rule_diagnostics=None,
    )
    omitted_signature = case_signature(omitted)

    assert resolved[omitted_signature] == "duplicate"
    assert sources[omitted_signature] == "deterministic_backfill"
    assert evidence[omitted_signature]["duplicate_of_case_id"] == "TC-A"
    assert evidence[omitted_signature]["semantic_relation_reasons"][:2] == [
        "same_intent_signature",
        "equivalent_trigger_action_outcome",
    ]


def test_near_exact_auxiliary_word_variant_is_duplicate() -> None:
    first = _case(
        "TC-039",
        description="提交内容通过审核后才可进入公开列表",
        preconditions=["内容已经提交审核"],
        steps=["1. 完成审核", "2. 打开公开列表"],
        test_input="审核结果为通过",
        expected_result="内容通过审核后才可在公开列表中查看",
    )
    second = _case(
        "TC-040",
        description="提交内容通过审核后才能进入公开列表",
        preconditions=["内容已经提交审核"],
        steps=["1. 完成审核", "2. 打开公开列表"],
        test_input="审核结果为通过",
        expected_result="内容通过审核后才能在公开列表中查看",
    )

    comparison = compare_case_semantic_identity(first, second)

    assert comparison.relation == "duplicate"
    assert comparison.confidence >= 0.8


def test_schema_completeness_and_concrete_value_assertions_are_distinct() -> None:
    schema_case = _case(
        "TC-017",
        description="验证详情页的核心字段完整显示",
        preconditions=["详情数据已成功加载"],
        steps=["1. 打开详情页", "2. 检查名称、主题和等级字段"],
        test_input="任意一条有效详情数据",
        expected_result="名称、主题和等级字段均存在且格式完整",
    )
    concrete_case = _case(
        "TC-051",
        description="验证指定详情数据的名称和主题值正确",
        preconditions=["详情数据已成功加载"],
        steps=["1. 打开编号为 1 的详情页", "2. 核对名称和主题的具体值"],
        test_input="编号 1，名称为 A-01，主题为 B-01",
        expected_result="名称显示 A-01，主题显示 B-01，等级显示 3",
    )

    comparison = compare_case_semantic_identity(schema_case, concrete_case)

    assert comparison.relation == "none"


def test_combined_behavior_is_reported_as_containment_without_fact_ids() -> None:
    atomic = _case(
        "TC-008",
        description="受限账号点击锁定条目后跳转升级页",
        preconditions=["账号处于受限状态", "列表包含锁定条目"],
        steps=["1. 查看锁定条目", "2. 点击锁定条目"],
        test_input="受限账号点击第二个锁定条目",
        expected_result="第二个条目显示锁定标识，点击后跳转升级页",
    )
    combined = _case(
        "TC-078",
        description="首个条目可直接访问，受限账号点击后续锁定条目时跳转升级页",
        preconditions=["账号处于受限状态", "列表包含首个免费条目和后续锁定条目"],
        steps=["1. 点击首个条目并返回列表", "2. 查看锁定条目", "3. 点击第二个锁定条目"],
        test_input="受限账号依次点击首个条目和第二个锁定条目",
        expected_result="首个条目可正常访问；第二个条目显示锁定标识，点击后跳转升级页",
    )

    comparison = compare_case_semantic_identity(atomic, combined)

    assert comparison.relation == "contained_by"
    assert "assertion_scope_contained_by" in comparison.reasons


def test_numbered_input_identifiers_protect_distinct_validation_objects() -> None:
    checkpoint_one = _case(
        "TC-001",
        description="Validate management workflow checkpoint 1",
        preconditions=["Dataset 1 exists"],
        steps=["Open workflow area 1", "Execute checkpoint 1"],
        test_input="checkpoint-1",
        expected_result="Checkpoint 1 shows saved state 1 after refresh",
    )
    checkpoint_ten = _case(
        "TC-010",
        description="Validate management workflow checkpoint 10",
        preconditions=["Dataset 10 exists"],
        steps=["Open workflow area 10", "Execute checkpoint 10"],
        test_input="checkpoint-10",
        expected_result="Checkpoint 10 shows saved state 10 after refresh",
    )

    comparison = compare_case_semantic_identity(checkpoint_one, checkpoint_ten)

    assert comparison.relation == "none"


def test_full_collection_semantic_dedup_scans_non_adjacent_cases() -> None:
    duplicate_left, duplicate_right = _equivalent_access_cases()
    distinct_left, distinct_right = _different_trigger_cases()

    result = deduplicate_cases_by_semantic_identity(
        [duplicate_left, distinct_left, distinct_right, duplicate_right]
    )

    assert result.dropped_count == 1
    assert result.duplicate_count == 1
    assert len(result.cases) == 3
    assert set(result.dropped_case_ids) & {"TC-A", "TC-B"}
    assert {item["action"] for item in result.relation_samples} & {
        "drop_duplicate",
        "replace_with_richer_duplicate",
    }
