from __future__ import annotations

import json
import math
from typing import Any, Callable

from core.settings.config import settings
from ...postprocess.case_fact_relations import verified_case_fact_ids


def resolve_stream_batch_plan(
    *,
    expected_count: int,
    batch_size: int,
    append: bool,
    start_id: int,
    existing_unique_count: int,
) -> dict[str, Any]:
    # start_id 仅保留调用兼容；批次数量只由本轮全局目标和批大小决定。
    _ = start_id
    resolved_expected_count = max(0, int(expected_count or 0))
    resolved_batch_size = max(
        1,
        int(batch_size or settings.TEST_GENERATION_BATCH_SIZE),
    )
    if append:
        needed_to_append = resolved_expected_count - int(existing_unique_count or 0)
        if needed_to_append > 0:
            resolved_batch_size = min(resolved_batch_size, needed_to_append)
    auto_extended = bool(append and resolved_expected_count <= int(existing_unique_count or 0))
    if auto_extended:
        resolved_expected_count = int(existing_unique_count or 0) + resolved_batch_size

    generation_target_count = (
        max(0, resolved_expected_count - int(existing_unique_count or 0))
        if append
        else resolved_expected_count
    )
    total_batches = math.ceil(generation_target_count / resolved_batch_size) if generation_target_count else 0
    return {
        "expected_count": int(resolved_expected_count),
        "batch_size": int(resolved_batch_size),
        "generation_target_count": int(generation_target_count),
        "total_batches": int(total_batches),
        "auto_extended": bool(auto_extended),
    }


def build_public_batch_execution_plan(
    *,
    generation_target_count: int,
    batch_size: int,
    max_workers: int,
) -> list[dict[str, Any]]:
    """构建公共批次与批内子分片的不可变所有权计划。

    公共批次由 batch_size 决定；max_workers 只决定批内同时发起的
    模型子请求数，不得改变公共批次边界。
    """

    target_count = max(0, int(generation_target_count or 0))
    resolved_batch_size = max(1, int(batch_size or 1))
    resolved_workers = max(1, int(max_workers or 1))
    plan: list[dict[str, Any]] = []
    offset = 0
    batch_index = 1
    while offset < target_count:
        public_target = min(resolved_batch_size, target_count - offset)
        shard_count = min(resolved_workers, public_target)
        base_target = public_target // shard_count
        remainder = public_target % shard_count
        shard_targets = [
            int(base_target + (1 if index < remainder else 0))
            for index in range(shard_count)
        ]
        plan.append(
            {
                "batch_index": int(batch_index),
                "target_count": int(public_target),
                "start_offset": int(offset),
                "end_offset": int(offset + public_target),
                "shard_target_counts": shard_targets,
            }
        )
        offset += public_target
        batch_index += 1
    return plan


def build_public_owned_shard_plan(
    coverage_shard_units: list[dict[str, Any]],
    *,
    public_batch_plan: list[dict[str, Any]],
    max_workers: int,
    main_chain_target: int = 0,
    reserved_fact_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """将覆盖单元分配给公共批次，保证子分片不跨批次。"""

    normalized_units = [
        {
            "rule_id": str(item.get("rule_id") or f"RULE-{index:03d}").strip(),
            "rule_text": str(item.get("rule_text") or "").strip(),
            "facts": [
                {
                    "fact_id": str(fact.get("fact_id") or "").strip(),
                    "statement": str(fact.get("statement") or "").strip(),
                }
                for fact in (item.get("facts") or [])
                if isinstance(fact, dict)
                and str(fact.get("fact_id") or "").strip()
            ],
        }
        for index, item in enumerate(coverage_shard_units, start=1)
        if isinstance(item, dict) and str(item.get("rule_text") or "").strip()
    ]
    if not normalized_units or not public_batch_plan:
        return []

    descriptors: list[dict[str, Any]] = []
    remaining_main_chain = max(0, int(main_chain_target or 0))
    for public_batch in public_batch_plan:
        batch_index = int(public_batch.get("batch_index") or 0)
        batch_target = max(0, int(public_batch.get("target_count") or 0))
        owned_main_chain = (
            min(batch_target, remaining_main_chain) if batch_index == 1 else 0
        )
        if owned_main_chain:
            descriptors.append(
                {
                    "batch_index": batch_index,
                    "batch_target": batch_target,
                    "shard_kind": "main_chain",
                    "target_count": int(owned_main_chain),
                }
            )
            remaining_main_chain -= owned_main_chain
        independent_target = max(0, batch_target - owned_main_chain)
        if independent_target <= 0:
            continue
        independent_plan = build_public_batch_execution_plan(
            generation_target_count=independent_target,
            batch_size=independent_target,
            max_workers=max_workers,
        )
        descriptors.extend(
            {
                "batch_index": batch_index,
                "batch_target": batch_target,
                "shard_kind": "independent",
                "target_count": int(target),
            }
            for target in (
                independent_plan[0].get("shard_target_counts")
                if independent_plan
                else []
            )
            if int(target or 0) > 0
        )

    independent_descriptors = [
        item for item in descriptors if item.get("shard_kind") == "independent"
    ]
    all_rule_ids = [item["rule_id"] for item in normalized_units]
    independent_units: dict[int, list[dict[str, Any]]] = {}
    independent_count = len(independent_descriptors)
    for independent_index, descriptor in enumerate(independent_descriptors):
        start = (independent_index * len(normalized_units)) // max(1, independent_count)
        end = ((independent_index + 1) * len(normalized_units)) // max(
            1, independent_count
        )
        assigned = normalized_units[start:end]
        if not assigned:
            assigned = [normalized_units[independent_index % len(normalized_units)]]
        independent_units[id(descriptor)] = assigned

    # fact_id 是原子需求行为的全局语义身份，不能同时交给多个并发分片。
    # 重叠事实按目标负载均衡到一个所有者，避免模型从不同模块视图
    # 重复生成同一原子行为，再在批内合并时丢失数量。
    reserved_facts = {
        str(fact_id or "").strip()
        for fact_id in (reserved_fact_ids or set())
        if str(fact_id or "").strip()
    }
    fact_candidates_by_descriptor: dict[int, dict[str, dict[str, str]]] = {}
    eligible_descriptor_indexes: dict[str, list[int]] = {}
    for descriptor_index, descriptor in enumerate(independent_descriptors):
        candidates: dict[str, dict[str, str]] = {}
        for unit in independent_units.get(id(descriptor), []):
            for fact in unit.get("facts") or []:
                fact_id = str(fact.get("fact_id") or "").strip()
                if not fact_id or fact_id in reserved_facts:
                    continue
                candidates.setdefault(fact_id, dict(fact))
        fact_candidates_by_descriptor[id(descriptor)] = candidates
        for fact_id in candidates:
            eligible_descriptor_indexes.setdefault(fact_id, []).append(
                descriptor_index
            )

    owned_facts_by_descriptor: dict[int, list[dict[str, str]]] = {
        id(descriptor): [] for descriptor in independent_descriptors
    }
    for fact_id, eligible_indexes in sorted(
        eligible_descriptor_indexes.items(),
        key=lambda item: (len(item[1]), item[0]),
    ):
        owner_index = min(
            eligible_indexes,
            key=lambda index: (
                len(owned_facts_by_descriptor[id(independent_descriptors[index])])
                / max(
                    1,
                    int(independent_descriptors[index].get("target_count") or 0),
                ),
                len(owned_facts_by_descriptor[id(independent_descriptors[index])]),
                index,
            ),
        )
        owner = independent_descriptors[owner_index]
        owned_facts_by_descriptor[id(owner)].append(
            dict(fact_candidates_by_descriptor[id(owner)][fact_id])
        )

    shard_plan: list[dict[str, Any]] = []
    per_batch_indexes: dict[int, int] = {}
    for descriptor in descriptors:
        batch_index = int(descriptor["batch_index"])
        per_batch_indexes[batch_index] = per_batch_indexes.get(batch_index, 0) + 1
        batch_shard_index = per_batch_indexes[batch_index]
        if descriptor.get("shard_kind") == "main_chain":
            shard = {
                "shard_kind": "main_chain",
                "target_count": int(descriptor["target_count"]),
                "rule_ids": [],
                "rule_texts": [],
                "facts": [],
                "reserved_fact_ids": sorted(reserved_facts),
                "excluded_rule_ids": list(all_rule_ids),
            }
        else:
            assigned = independent_units.get(id(descriptor), [])
            owned_rule_ids = [item["rule_id"] for item in assigned]
            facts = list(owned_facts_by_descriptor.get(id(descriptor), []))
            candidate_fact_count = len(
                fact_candidates_by_descriptor.get(id(descriptor), {})
            )
            shard = {
                "shard_kind": "independent",
                "target_count": int(descriptor["target_count"]),
                "rule_ids": owned_rule_ids,
                "rule_texts": [item["rule_text"] for item in assigned],
                "facts": facts,
                "candidate_fact_count": int(candidate_fact_count),
                "shared_fact_excluded_count": max(
                    0,
                    int(candidate_fact_count) - len(facts),
                ),
                "reserved_fact_excluded_count": int(
                    len(
                        {
                            str(fact.get("fact_id") or "").strip()
                            for unit in assigned
                            for fact in (unit.get("facts") or [])
                            if isinstance(fact, dict)
                            and str(fact.get("fact_id") or "").strip()
                            in reserved_facts
                        }
                    )
                ),
                "excluded_rule_ids": [
                    rule_id
                    for rule_id in all_rule_ids
                    if rule_id not in set(owned_rule_ids)
                ],
            }
        shard.update(
            {
                "public_batch_index": batch_index,
                "public_batch_target_count": int(descriptor["batch_target"]),
                "public_batch_shard_index": int(batch_shard_index),
            }
        )
        shard_plan.append(shard)

    per_batch_counts: dict[int, int] = {}
    for shard in shard_plan:
        batch_index = int(shard.get("public_batch_index") or 0)
        per_batch_counts[batch_index] = per_batch_counts.get(batch_index, 0) + 1
    for shard_index, shard in enumerate(shard_plan, start=1):
        batch_index = int(shard.get("public_batch_index") or 0)
        batch_shard_index = int(shard.get("public_batch_shard_index") or 0)
        shard["shard_id"] = (
            "B01-MAIN-CHAIN"
            if str(shard.get("shard_kind") or "") == "main_chain"
            else f"B{batch_index:02d}-SHARD-{batch_shard_index:02d}"
        )
        shard["shard_index"] = int(shard_index)
        shard["total_shards"] = int(len(shard_plan))
        shard["public_batch_shard_count"] = int(per_batch_counts.get(batch_index) or 0)
        shard["merge_order"] = int(shard_index)
    return shard_plan


def group_shard_requests_by_public_batch(
    requests: list[dict[str, Any]],
    *,
    public_batch_plan: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """按公共批次顺序封装子分片请求，供执行器逐批推进。"""

    grouped: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for public_batch in public_batch_plan:
        batch_index = int(public_batch.get("batch_index") or 0)
        batch_requests = [
            request
            for request in requests
            if isinstance(request, dict)
            and int(
                (request.get("shard") or {}).get("public_batch_index") or 0
            )
            == batch_index
        ]
        if batch_requests:
            grouped.append((dict(public_batch), batch_requests))
    return grouped


def assign_public_batch_merge_gap_repair_targets(
    shard_results: list[dict[str, Any]],
    *,
    merge_result: dict[str, Any],
    gap_count: int,
    accepted_batch_cases: list[dict[str, Any]],
    history_summaries: list[str],
    accepted_history_cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """将批内合并去重产生的缺口归还给当前批次的来源分片。"""

    output: list[dict[str, Any]] = []
    for item in shard_results:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied["shard"] = dict(item.get("shard") or {})
        output.append(copied)
    remaining = max(0, int(gap_count or 0))
    if not output or remaining <= 0:
        return output

    counts_by_shard = {
        str(item.get("shard_id") or ""): dict(item)
        for item in (merge_result.get("per_shard_counts") or [])
        if isinstance(item, dict) and str(item.get("shard_id") or "")
    }
    accepted_fact_ids = {
        fact_id
        for case in [
            *(accepted_history_cases or []),
            *accepted_batch_cases,
        ]
        if isinstance(case, dict)
        for fact_id in verified_case_fact_ids(case)
    }
    relation_samples_by_shard: dict[str, list[dict[str, Any]]] = {}
    all_drop_samples: list[dict[str, Any]] = []
    for sample in merge_result.get("semantic_relation_samples") or []:
        if not isinstance(sample, dict):
            continue
        action = str(sample.get("action") or "").strip()
        if action not in {
            "drop_duplicate",
            "replace_with_richer_duplicate",
            "drop_contained_case",
            "replace_with_containing_case",
            "drop_protected_history_conflict",
        }:
            continue
        compact_sample = {
            "relation": str(sample.get("relation") or ""),
            "action": action,
            "reasons": [
                str(reason)
                for reason in (sample.get("reasons") or [])
                if str(reason or "").strip()
            ][:6],
            "dropped_case_id": str(sample.get("dropped_case_id") or ""),
            "retained_case_id": str(sample.get("retained_case_id") or ""),
            "dropped_fact_ids": [
                str(fact_id)
                for fact_id in (sample.get("dropped_fact_ids") or [])
                if str(fact_id or "").strip()
            ],
            "retained_fact_ids": [
                str(fact_id)
                for fact_id in (sample.get("retained_fact_ids") or [])
                if str(fact_id or "").strip()
            ],
        }
        dropped_shard_id = str(
            sample.get("dropped_shard_id")
            or sample.get("source_shard_id")
            or ""
        ).strip()
        if dropped_shard_id:
            relation_samples_by_shard.setdefault(dropped_shard_id, []).append(
                compact_sample
            )
        all_drop_samples.append(compact_sample)

    ranked: list[dict[str, Any]] = []
    for index, result in enumerate(output):
        result["repair_target_count"] = 0
        result["public_batch_merge_gap_target"] = 0
        shard_id = str((result.get("shard") or {}).get("shard_id") or "")
        counts = counts_by_shard.get(shard_id, {})
        lost_count = max(
            0,
            int(counts.get("input_case_count") or 0)
            - int(counts.get("unique_case_count") or 0),
        )
        assigned_facts = [
            dict(fact)
            for fact in ((result.get("shard") or {}).get("facts") or [])
            if isinstance(fact, dict)
            and str(fact.get("fact_id") or "").strip()
        ]
        unused_facts = [
            fact
            for fact in assigned_facts
            if str(fact.get("fact_id") or "").strip() not in accepted_fact_ids
        ]
        base_instruction = str(
            result.get("base_repair_instruction")
            or result.get("repair_instruction")
            or ""
        ).strip()
        result["base_repair_instruction"] = base_instruction
        result["repair_unused_fact_ids"] = [
            str(fact.get("fact_id") or "") for fact in unused_facts
        ]
        result["repair_semantic_relation_samples"] = list(
            relation_samples_by_shard.get(shard_id, [])[-8:]
        )
        ranked.append(
            {
                "index": int(index),
                "result": result,
                "shard_id": shard_id,
                "lost_count": int(lost_count),
                "unused_facts": unused_facts,
            }
        )

    losing_shard_ids = [
        str(item["shard_id"])
        for item in ranked
        if int(item["lost_count"]) > 0 and str(item["shard_id"])
    ]
    has_assigned_fact_catalog = any(
        list((item["result"].get("shard") or {}).get("facts") or [])
        for item in ranked
    )
    # 原丢失分片仍有未覆盖事实时优先修复；额度只能转给同批
    # 仍有未覆盖事实的分片。事实空间耗尽后继续做文本变体，在统一
    # 语义去重口径下不可能带来净新增，因此不再发起无效模型请求。
    allocation_order = sorted(
        ranked,
        key=lambda item: (
            0
            if int(item["lost_count"]) > 0 and item["unused_facts"]
            else 1
            if item["unused_facts"]
            else 2,
            -int(item["lost_count"]),
            -len(item["unused_facts"]),
            int(item["index"]),
        ),
    )

    allocations: list[tuple[dict[str, Any], int, str]] = []
    for item in allocation_order:
        if remaining <= 0:
            break
        lost_count = int(item["lost_count"])
        unused_count = len(item["unused_facts"])
        capacity = unused_count
        allocation = min(remaining, max(0, capacity))
        if allocation <= 0:
            continue
        allocation_reason = (
            "losing_shard_with_unused_facts"
            if lost_count > 0 and unused_count > 0
            else "reassigned_to_unused_fact_capacity"
            if unused_count > 0
            else "reassigned_to_unused_fact_capacity"
        )
        allocations.append((item["result"], allocation, allocation_reason))
        remaining -= allocation
    if remaining > 0 and not has_assigned_fact_catalog:
        # 旧数据或非语义生成没有 fact catalog，只能依据行为字段判重；
        # 此时不存在“已覆盖事实子集必然被删除”的不可达条件，仍允许
        # 将缺口返还给丢失分片生成真正不同的行为。
        fallback_results = [
            item["result"]
            for item in allocation_order
            if int(item["lost_count"]) > 0
        ] or [item["result"] for item in allocation_order]
        fallback_index = 0
        while remaining > 0 and fallback_results:
            result = fallback_results[fallback_index % len(fallback_results)]
            for allocation_index, (
                allocated_result,
                allocated_count,
                allocation_reason,
            ) in enumerate(allocations):
                if allocated_result is result:
                    allocations[allocation_index] = (
                        allocated_result,
                        allocated_count + 1,
                        allocation_reason,
                    )
                    break
            else:
                allocations.append(
                    (result, 1, "behavior_variation_without_fact_catalog")
                )
            remaining -= 1
            fallback_index += 1

    accepted_summaries = [
        f"- {case.get('id') or ''}: {case.get('description') or ''}"
        for case in accepted_batch_cases
        if isinstance(case, dict)
    ]
    prior_history = [
        f"- {item}" for item in history_summaries if str(item or "").strip()
    ]
    for result, allocation, allocation_reason in allocations:
        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
        shard_id = str(shard.get("shard_id") or "")
        unused_fact_ids = list(result.get("repair_unused_fact_ids") or [])
        unused_fact_lines = [
            "- "
            + str(fact.get("fact_id") or "")
            + (f": {str(fact.get('statement') or '').strip()}" if str(fact.get("statement") or "").strip() else "")
            for fact in (shard.get("facts") or [])
            if isinstance(fact, dict)
            and str(fact.get("fact_id") or "").strip() in set(unused_fact_ids)
        ]
        shard_relation_samples = list(
            result.get("repair_semantic_relation_samples") or []
        )
        feedback_samples = shard_relation_samples or all_drop_samples[-8:]
        result["repair_allocation_reason"] = allocation_reason
        result["repair_source_shard_ids"] = list(losing_shard_ids)
        result["repair_target_count"] = int(allocation)
        result["public_batch_merge_gap_target"] = int(allocation)
        latest_instruction = (
            "The current public batch became underfilled only after its shard results were "
            "merged and de-duplicated. Generate genuinely different grounded behaviors owned "
            "by the allocated shard. Do not rename, paraphrase, broaden, or narrow any rejected "
            "or accepted behavior.\n"
            f"Allocated shard: {shard_id}. Allocation reason: {allocation_reason}.\n"
            f"Original losing shards: {losing_shard_ids or ['(unknown)']}.\n"
            "Latest semantic merge feedback (these identities must not be repeated):\n"
            + (
                json.dumps(feedback_samples, ensure_ascii=False, separators=(",", ":"))
                if feedback_samples
                else "- (no structured relation sample; avoid every previously generated candidate)"
            )
            + "\nUnused active facts in the allocated shard (prefer these first):\n"
            + ("\n".join(unused_fact_lines) or "- (none)")
            + "\n"
            "Use a different unused fact as the primary validation goal for every replacement. "
            "Keep each fact set minimal: do not combine unrelated atomic facts, and do not return "
            "a fact-id set equal to, contained by, or containing a retained case's fact-id set.\n"
            "Accepted cases in the current public batch:\n"
            + ("\n".join(accepted_summaries[-50:]) or "- (none)")
            + "\nPreviously accepted public-batch history:\n"
            + ("\n".join(prior_history[-50:]) or "- (none)")
        )
        base_instruction = str(result.get("base_repair_instruction") or "").strip()
        result["repair_instruction"] = "\n\n".join(
            item for item in (base_instruction, latest_instruction) if item
        )
    return output


def select_complete_generated_cases(
    cases: list[dict[str, Any]],
    *,
    limit: int,
    start_id: int,
    is_placeholder_expected_result_fn: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """只接收模型完整产出的用例，缺字段用例留给下一次模型生成补足。"""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    max_count = max(0, int(limit or 0))

    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case = dict(raw_case)
        missing_fields: list[str] = []
        if len(str(case.get("description") or "").strip()) < 4:
            missing_fields.append("description")
        if not str(case.get("test_module") or "").strip():
            missing_fields.append("test_module")
        if not any(str(item or "").strip() for item in (case.get("preconditions") or [])):
            missing_fields.append("preconditions")
        if not any(str(item or "").strip() for item in (case.get("steps") or [])):
            missing_fields.append("steps")
        if not str(case.get("test_input") or "").strip():
            missing_fields.append("test_input")
        expected_result = str(case.get("expected_result") or "").strip()
        if not expected_result or is_placeholder_expected_result_fn(expected_result):
            missing_fields.append("expected_result")
        if str(case.get("priority") or "").strip().upper() not in {"P0", "P1", "P2"}:
            missing_fields.append("priority")

        if missing_fields:
            rejected.append(
                {
                    "case_id": str(case.get("id") or "").strip(),
                    "missing_fields": missing_fields,
                }
            )
            continue
        if len(accepted) >= max_count:
            break
        case["id"] = f"TC-{int(start_id) + len(accepted):03d}"
        accepted.append(case)

    return accepted, rejected


def build_existing_case_history(
    existing_cases: Any,
    *,
    append: bool,
    build_case_signature_fn: Callable[[dict[str, Any]], str],
) -> tuple[list[str], set[str]]:
    history_summaries: list[str] = []
    seen_case_signatures: set[str] = set()
    if append and isinstance(existing_cases, list):
        for case in existing_cases:
            if isinstance(case, dict):
                history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                signature = build_case_signature_fn(case)
                if signature:
                    seen_case_signatures.add(signature)
    return history_summaries, seen_case_signatures


def build_stream_batch_quality_metric(
    *,
    parsed_batch_cases: list[dict[str, Any]],
    seen_case_signatures: set[str],
    batch_index: int,
    build_case_signature_fn: Callable[[dict[str, Any]], str],
    is_non_assertable_expected_result_fn: Callable[[str], bool],
    previous_low_gain_streak: int,
) -> tuple[dict[str, Any], int]:
    new_valid_cases_count = int(len(parsed_batch_cases))
    unique_increment = 0
    non_assertable_count = 0
    for case in parsed_batch_cases:
        signature = build_case_signature_fn(case)
        if signature and signature not in seen_case_signatures:
            seen_case_signatures.add(signature)
            unique_increment += 1
        if is_non_assertable_expected_result_fn(str(case.get("expected_result") or "")):
            non_assertable_count += 1

    duplicate_count = max(0, new_valid_cases_count - unique_increment)
    duplicate_rate = float(duplicate_count) / float(new_valid_cases_count) if new_valid_cases_count > 0 else 1.0
    coverage_gain_count = int(unique_increment)
    low_quality_filtered_count = int(non_assertable_count)
    low_gain_detected = bool(
        (coverage_gain_count <= 1)
        or (duplicate_rate >= 0.6)
        or (new_valid_cases_count > 0 and (float(non_assertable_count) / float(new_valid_cases_count)) >= 0.5)
    )
    low_gain_streak = int(previous_low_gain_streak or 0) + 1 if low_gain_detected else 0
    return (
        {
            "batch_index": int(batch_index),
            "new_valid_cases_count": int(new_valid_cases_count),
            "duplicate_rate": round(float(duplicate_rate), 4),
            "non_assertable_count": int(non_assertable_count),
            "low_quality_filtered_count": int(low_quality_filtered_count),
            "coverage_gain_count": int(coverage_gain_count),
            "low_gain_detected": bool(low_gain_detected),
            "low_gain_streak": int(low_gain_streak),
        },
        int(low_gain_streak),
    )
