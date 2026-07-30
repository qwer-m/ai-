from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import time
from typing import Any, Callable, Mapping

from modules.orchestration.background_task_governance import (
    iter_governed_threadpool_map,
)
from ...postprocess.case_fact_relations import (
    deduplicate_cases_by_semantic_identity,
    verified_case_fact_ids,
)


TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled"}
_ACCEPTED_PUBLIC_BATCH_HISTORY_SHARD_ID = "__ACCEPTED_PUBLIC_BATCH_HISTORY__"


@dataclass(frozen=True)
class ParallelShardConfig:
    enabled: bool
    max_workers: int
    min_expected_count: int
    min_coverage_rules: int
    duplicate_rate_abort: float
    min_unique_ratio: float


def _env_bool(name: str, default: bool, env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    raw = str(source.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in TRUE_VALUES


def _env_int(
    name: str,
    default: int,
    *,
    min_value: int,
    max_value: int,
    env: Mapping[str, str] | None = None,
) -> int:
    source = env if env is not None else os.environ
    try:
        parsed = int(str(source.get(name, str(default))).strip())
    except Exception:
        parsed = int(default)
    return max(int(min_value), min(int(max_value), int(parsed)))


def _env_float(
    name: str,
    default: float,
    *,
    min_value: float,
    max_value: float,
    env: Mapping[str, str] | None = None,
) -> float:
    source = env if env is not None else os.environ
    try:
        parsed = float(str(source.get(name, str(default))).strip())
    except Exception:
        parsed = float(default)
    return max(float(min_value), min(float(max_value), float(parsed)))


def parallel_shard_config_from_env(env: Mapping[str, str] | None = None) -> ParallelShardConfig:
    source = env if env is not None else os.environ
    default_batch_size = _env_int(
        "TEST_GENERATION_BATCH_SIZE",
        25,
        min_value=1,
        max_value=200,
        env=source,
    )
    return ParallelShardConfig(
        enabled=_env_bool("GENERATION_STREAM_COVERAGE_SHARDS_ENABLED", True, source),
        max_workers=_env_int(
            "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS",
            2,
            min_value=1,
            max_value=4,
            env=env,
        ),
        min_expected_count=_env_int(
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT",
            default_batch_size,
            min_value=1,
            max_value=500,
            env=source,
        ),
        min_coverage_rules=_env_int(
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES",
            8,
            min_value=1,
            max_value=100,
            env=source,
        ),
        duplicate_rate_abort=_env_float(
            "GENERATION_STREAM_COVERAGE_SHARD_DUPLICATE_RATE_ABORT",
            0.25,
            min_value=0.0,
            max_value=1.0,
            env=source,
        ),
        min_unique_ratio=_env_float(
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_UNIQUE_RATIO",
            0.45,
            min_value=0.0,
            max_value=1.0,
            env=source,
        ),
    )


def parallel_shard_config_from_settings(settings_obj: Any) -> ParallelShardConfig:
    default_batch_size = max(
        1,
        min(200, int(getattr(settings_obj, "TEST_GENERATION_BATCH_SIZE", 25) or 25)),
    )
    return ParallelShardConfig(
        enabled=bool(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARDS_ENABLED", True)),
        max_workers=max(1, min(4, int(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS", 2) or 2))),
        min_expected_count=max(
            1,
            min(
                500,
                int(
                    getattr(
                        settings_obj,
                        "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT",
                        default_batch_size,
                    )
                    or default_batch_size
                ),
            ),
        ),
        min_coverage_rules=max(
            1,
            min(100, int(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES", 8) or 8)),
        ),
        duplicate_rate_abort=max(
            0.0,
            min(
                1.0,
                float(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARD_DUPLICATE_RATE_ABORT", 0.25) or 0.25),
            ),
        ),
        min_unique_ratio=max(
            0.0,
            min(
                1.0,
                float(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARD_MIN_UNIQUE_RATIO", 0.45) or 0.45),
            ),
        ),
    )


def should_use_parallel_shards(
    *,
    expected_count: int,
    append: bool,
    multi_pass: bool,
    total_batches: int,
    coverage_rule_count: int,
    config: ParallelShardConfig | None = None,
) -> tuple[bool, str]:
    cfg = config or parallel_shard_config_from_env()
    if not cfg.enabled:
        return False, "disabled_by_flag"
    if append:
        return False, "append_mode"
    if not multi_pass:
        return False, "single_pass"
    if int(expected_count or 0) < int(cfg.min_expected_count):
        return False, "expected_count_below_min"
    if int(total_batches or 0) <= 0:
        return False, "no_batch"
    if int(coverage_rule_count or 0) < int(cfg.min_coverage_rules or 1):
        return False, "insufficient_coverage_rules"
    if int(cfg.max_workers or 0) < 2:
        return False, "max_workers_below_parallel"
    return True, "enabled"


def _normalize_rules(coverage_plan_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(coverage_plan_rules or [], start=1):
        if not isinstance(item, dict):
            continue
        rule_text = str(item.get("rule_text") or "").strip()
        if not rule_text:
            continue
        rule_id = str(item.get("rule_id") or f"RULE-{index:03d}").strip() or f"RULE-{index:03d}"
        facts = [
            {
                "fact_id": str(fact.get("fact_id") or "").strip(),
                "statement": str(fact.get("statement") or "").strip(),
            }
            for fact in (item.get("facts") or [])
            if isinstance(fact, dict)
            and str(fact.get("fact_id") or "").strip()
        ]
        normalized.append(
            {"rule_id": rule_id, "rule_text": rule_text, "facts": facts}
        )
    return normalized


def build_coverage_shard_plan(
    coverage_plan_rules: list[dict[str, Any]],
    *,
    expected_count: int,
    max_workers: int,
    max_cases_per_worker: int = 25,
    max_shards: int | None = None,
) -> list[dict[str, Any]]:
    rules = _normalize_rules(coverage_plan_rules)
    if not rules:
        return []
    by_count = max(1, math.ceil(max(1, int(expected_count or 0)) / max(1, int(max_cases_per_worker or 1))))
    shard_limit = (
        max(1, int(max_shards))
        if max_shards not in (None, 0)
        else max(1, int(max_workers or 1))
    )
    # 分片数与并发 worker 数解耦：worker 控制并发压力，分片数控制单次模型输出上限。
    shard_count = min(shard_limit, len(rules), by_count)
    target_total = max(1, int(expected_count or 0))
    base_target = target_total // shard_count
    remainder = target_total % shard_count
    all_rule_ids = [rule["rule_id"] for rule in rules]
    shards: list[dict[str, Any]] = []
    for shard_index in range(shard_count):
        start = math.floor((shard_index * len(rules)) / shard_count)
        end = math.floor(((shard_index + 1) * len(rules)) / shard_count)
        assigned = rules[start:end] or [rules[min(shard_index, len(rules) - 1)]]
        rule_ids = [rule["rule_id"] for rule in assigned]
        assigned_facts: list[dict[str, str]] = []
        seen_fact_ids: set[str] = set()
        for rule in assigned:
            for fact in rule.get("facts") or []:
                fact_id = str(fact.get("fact_id") or "").strip()
                if not fact_id or fact_id in seen_fact_ids:
                    continue
                seen_fact_ids.add(fact_id)
                assigned_facts.append(
                    {
                        "fact_id": fact_id,
                        "statement": str(fact.get("statement") or "").strip(),
                    }
                )
        shards.append(
            {
                "shard_id": f"SHARD-{shard_index + 1:02d}",
                "shard_index": int(shard_index + 1),
                "total_shards": int(shard_count),
                "target_count": int(base_target + (1 if shard_index < remainder else 0)),
                "rule_ids": rule_ids,
                "rule_texts": [rule["rule_text"] for rule in assigned],
                "facts": assigned_facts,
                "excluded_rule_ids": [rule_id for rule_id in all_rule_ids if rule_id not in set(rule_ids)],
                "merge_order": int(shard_index + 1),
            }
        )
    return shards


def build_parallel_shard_instruction(shard: dict[str, Any]) -> str:
    shard_kind = str(shard.get("shard_kind") or "").strip()
    workflow_stage_candidates = (
        '[{"workflow_id":"<exact active workflow id>",'
        '"stage_id":"<exact active stage id>",'
        '"stage_kind":"<exact active stage kind>",'
        '"evidence":["<exact quote from this case public fields>"],'
        '"confidence":0.8}]'
        if shard_kind == "main_chain"
        else "[]"
    )
    semantic_output_contract = f"""
FINAL SHARD OUTPUT CONTRACT (MANDATORY):
- Every returned case object MUST contain `_semantic` with all six array fields shown below.
- `_semantic.module_candidates` MUST be non-empty and use an active module's exact module_key, module_name, and role.
- For module and workflow-stage evidence, copy one complete public-field value from that same case verbatim; prefer description, steps, or expected_result. Do not shorten, summarize, or paraphrase it.
- Minimal shape (placeholder values must be replaced with active-contract values and current-case evidence):
{{"_semantic":{{"module_candidates":[{{"module_key":"<exact active module key>","module_name":"<exact active module name>","role":"<exact active role>","evidence":["<complete verbatim value of this case description, steps, or expected_result>"],"confidence":0.8}}],"fact_ids":[],"interaction_ids":[],"workflow_stage_candidates":{workflow_stage_candidates},"precondition_states":[],"produced_states":[]}}}}
- Copy every directly verified active fact ID into fact_ids. Reuse the same fact ID for the same atomic behavior across shards; a combined case lists the union of its verified fact IDs. Never invent an ID.
- All six fields are arrays. Independent coverage cases may use [] for fact_ids, interaction_ids, workflow_stage_candidates, precondition_states, and produced_states only when the active contract does not apply; module_candidates may never be empty.
- Typed states are optional case-level refinements. Prefer precondition_states=[] and produced_states=[] when active fact IDs or an exact workflow stage already identify the goal. Add a state only when one complete current-case public field states it verbatim; never combine or paraphrase fragments as state evidence.
- Check every case object separately before returning the JSON array. Never output a case with `_semantic` missing or with any of the six arrays omitted.
""".strip()

    if shard_kind == "main_chain":
        return f"""
# --- PRIMARY WORKFLOW SHARD (internal planning, do not output this section) ---
Shard: {shard.get('shard_id') or ''} ({int(shard.get('shard_index') or 1)}/{int(shard.get('total_shards') or 1)})
Target count: {int(shard.get('target_count') or 0)} complete cases.

Shard contract:
- This shard exclusively owns the confirmed primary workflow.
- Generate at least one contract-valid main-chain case for every required stage, in exact stage_order.
- Copy workflow_id, stage_id, stage_kind, module_key, module_name, role, fact_ids, and interaction_ids exactly from the active workflow catalog. For workflow-stage evidence, copy one complete value of this case's description, steps, or expected_result verbatim; never use a summary or paraphrase.
- `_semantic.precondition_states` and `_semantic.produced_states` may be empty because the execution plan inherits authoritative states from the exactly matched workflow step. Do not copy the catalog's typed-state arrays into the case.
- Declare an additional typed state only when the current case's public fields provide exact evidence for it, and do not conflict with the matched workflow step's authoritative states.
- Complete the initial-state to terminal-state closure before adding any supported main-flow boundary or recovery case.
- Do not generate independent permission, display, generic exception, or unrelated module cases in this shard.
- Case IDs are provisional; the merge step will renumber final IDs.

{semantic_output_contract}
""".strip()

    rule_ids = [str(item) for item in (shard.get("rule_ids") or []) if str(item or "").strip()]
    rule_texts = [str(item).strip() for item in (shard.get("rule_texts") or []) if str(item or "").strip()]
    rule_lines = []
    for index, rule_text in enumerate(rule_texts, start=1):
        rule_id = rule_ids[index - 1] if index - 1 < len(rule_ids) else f"RULE-{index:03d}"
        rule_lines.append(f"{index}. {rule_id}: {rule_text[:220]}")
    excluded = ", ".join(str(item) for item in (shard.get("excluded_rule_ids") or []) if str(item or "").strip())
    if not excluded:
        excluded = "(none)"
    assigned_facts = [
        {
            "fact_id": str(item.get("fact_id") or "").strip(),
            "statement": str(item.get("statement") or "").strip(),
        }
        for item in (shard.get("facts") or [])
        if isinstance(item, dict) and str(item.get("fact_id") or "").strip()
    ]
    return f"""
# --- PARALLEL COVERAGE SHARD (internal planning, do not output this section) ---
Shard: {shard.get("shard_id") or ""} ({int(shard.get("shard_index") or 1)}/{int(shard.get("total_shards") or 1)})
Shard output target: exactly {int(shard.get("target_count") or 0)} additional contract-valid, non-duplicate cases. This is the authoritative count for this shard request.
Assigned validation rules:
{chr(10).join(rule_lines) if rule_lines else "- No assigned rules; return []"}
Out-of-scope rule IDs for validation goals: {excluded}
Assigned active fact catalog (the only allowed non-empty `_semantic.fact_ids` values for this shard):
{json.dumps(assigned_facts, ensure_ascii=False, separators=(",", ":")) if assigned_facts else "[]"}

Shard contract:
- Generate validation goals only for the assigned rules above.
- Rule IDs such as `RULE-001` and `MODULE::...` are planning labels, never fact IDs. Never copy a rule ID into `_semantic.fact_ids`.
- For every case, copy all directly verified fact IDs from the assigned active fact catalog. If that catalog is empty, use fact_ids=[] instead of inventing an ID.
- Give every case exactly one primary validation goal. Start from a different assigned atomic fact for each case, keep the fact set minimal, and combine multiple facts only when one executable action necessarily verifies all of them.
- Do not consume the entire fact catalog by copying loosely related facts into broad cases. Preserve unused grounded facts for merge-gap repair.
- Other rule IDs may appear only as setup or prerequisite context, not as the primary validation goal.
- Do not compensate for other shards and do not broaden the module scope to fill count.
- Return the exact shard target when enough grounded assigned-rule goals remain. If fewer meaningful goals remain, return all grounded cases without padding.
- Case IDs are provisional; the merge step will deduplicate, order, and renumber final IDs.

{semantic_output_contract}
""".strip()


def _parse_shard_cases(
    *,
    content: str,
    clean_and_parse_json_fn: Callable[[str], Any],
) -> list[dict[str, Any]]:
    """工作线程只解析模型返回的 JSON 数组。

    字段归一化、语义契约和功能模块契约都由主线程按分片顺序执行，
    避免共享诊断状态在并发线程中交叉写入。
    """
    parsed = clean_and_parse_json_fn(content)
    if not isinstance(parsed, list):
        raise ValueError("parallel shard response is not a JSON array")
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _run_single_shard_request(
    *,
    request: dict[str, Any],
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    shard = dict(request.get("shard") or {})
    client = request.get("client")
    system_prompt = str(request.get("system_prompt") or "")
    if client is None or not hasattr(client, "generate_response_stream"):
        return {
            "shard": shard,
            "status": "client_unavailable",
            "error": "parallel shard client does not support generate_response_stream",
            "error_codes": ["client_unavailable"],
            "cases": [],
            "duration_ms": 0,
            "metadata": {},
            "raw_response_chars": 0,
            "raw_parsed_case_count": 0,
            "normalized_case_count": 0,
            "semantic_rejection_count": 0,
            "semantic_rejection_codes": [],
            "repair_attempt": int(request.get("repair_attempt") or 0),
        }
    content_text = ""
    try:
        stream = client.generate_response_stream(
            requirement,
            system_prompt,
            task_type="generation",
            request_timeout_seconds=request.get("request_timeout_seconds"),
            heartbeat_interval_seconds=request.get("heartbeat_interval_seconds"),
            reasoning_effort="low",
            disable_thinking=True,
        )
        chunks: list[str] = []
        is_heartbeat = getattr(client, "is_stream_heartbeat", None)
        for chunk in stream:
            if callable(is_heartbeat) and is_heartbeat(chunk):
                continue
            chunks.append(str(chunk or ""))
        content_text = "".join(chunks)
        if content_text.startswith("Error:") or content_text.startswith("Exception"):
            status = "provider_error"
            cases: list[dict[str, Any]] = []
            error = content_text[:300]
            error_codes = ["provider_error"]
        else:
            cases = _parse_shard_cases(
                content=content_text,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
            )
            status = "parsed"
            error = ""
            error_codes = []
    except ValueError as exc:
        status = "json_parse_error"
        cases = []
        error = str(exc)[:300]
        error_codes = ["json_parse_error"]
    except Exception as exc:
        status = "exception"
        cases = []
        error = str(exc)[:300]
        error_codes = ["worker_exception"]
    return {
        "shard": shard,
        "status": status,
        "error": error,
        "error_codes": error_codes,
        "cases": cases,
        "duration_ms": max(0, int(round((time.perf_counter() - started) * 1000))),
        "metadata": dict(getattr(client, "last_response_metadata", {}) or {}),
        "response_case_count": int(len(cases)),
        "raw_response_chars": int(len(content_text)),
        "raw_parsed_case_count": int(len(cases)),
        "normalized_case_count": 0,
        "semantic_rejection_count": 0,
        "semantic_rejection_codes": [],
        "repair_attempt": int(request.get("repair_attempt") or 0),
    }


def execute_parallel_shard_requests(
    *,
    requests: list[dict[str, Any]],
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    max_workers: int,
) -> list[dict[str, Any]]:
    stream = stream_parallel_shard_requests(
        requests=requests,
        requirement=requirement,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        max_workers=max_workers,
        heartbeat_interval_seconds=0,
    )
    while True:
        try:
            next(stream)
        except StopIteration as stop:
            return list(stop.value or [])


def stream_parallel_shard_requests(
    *,
    requests: list[dict[str, Any]],
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    max_workers: int,
    heartbeat_interval_seconds: float,
):
    """等待并行分片时持续产出进度，避免外层流因长时间无字节而空闲超时。"""

    if not requests:
        return []
    worker_count = min(max(1, int(max_workers or 1)), len(requests))
    governed_results = []
    for update in iter_governed_threadpool_map(
        profile_key="test_generation_coverage_shard_threadpool",
        items=requests,
        worker=lambda request: _run_single_shard_request(
            request=request,
            requirement=requirement,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
        ),
        max_workers=worker_count,
        thread_name_prefix="testgen-shard",
        business_id=str(requests[0].get("request_id") or "")
        if isinstance(requests[0], dict)
        else None,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    ):
        if update.kind == "heartbeat":
            yield {
                "kind": "heartbeat",
                "completed_count": int(update.completed_count),
                "total_count": int(update.total_count),
            }
            continue
        if update.item_result is not None:
            governed_results.append(update.item_result)
            yield {
                "kind": "item_completed",
                "completed_count": int(update.completed_count),
                "total_count": int(update.total_count),
            }

    results: list[dict[str, Any]] = []
    for item in governed_results:
        if item.exception is not None:
            shard = item.item.get("shard") if isinstance(item.item, dict) else {}
            results.append(
                {
                    "shard": shard if isinstance(shard, dict) else {},
                    "status": "exception",
                    "error": str(item.exception)[:300],
                    "error_codes": ["executor_exception"],
                    "cases": [],
                    "duration_ms": 0,
                    "metadata": {},
                    "raw_response_chars": 0,
                    "raw_parsed_case_count": 0,
                    "normalized_case_count": 0,
                    "semantic_rejection_count": 0,
                    "semantic_rejection_codes": [],
                    "repair_attempt": int(item.item.get("repair_attempt") or 0)
                    if isinstance(item.item, dict)
                    else 0,
                }
            )
        else:
            results.append(dict(item.result or {}))
    return sorted(
        results,
        key=lambda item: int(
            (item.get("shard") or {}).get("merge_order")
            or (item.get("shard") or {}).get("shard_index")
            or 0
        ),
    )


def build_parallel_gap_repair_requests(
    *,
    requests: list[dict[str, Any]],
    accepted_results: list[dict[str, Any]],
    repair_attempt: int = 1,
) -> list[dict[str, Any]]:
    """仅为失败或验收不足的分片构造局部补生成请求。"""

    result_by_shard_id = {
        str((item.get("shard") or {}).get("shard_id") or ""): item
        for item in accepted_results
        if isinstance(item, dict)
    }
    repairs: list[dict[str, Any]] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        shard = dict(request.get("shard") or {})
        shard_id = str(shard.get("shard_id") or "")
        result = result_by_shard_id.get(shard_id, {})
        accepted_cases = [
            dict(item)
            for item in (result.get("cases") or [])
            if isinstance(item, dict)
        ]
        target_count = max(0, int(shard.get("target_count") or 0))
        count_gap = max(0, target_count - len(accepted_cases))
        contract_gap = max(0, int(result.get("repair_target_count") or 0))
        gap_count = max(count_gap, contract_gap)
        if gap_count <= 0:
            continue
        repair_shard = dict(shard)
        repair_shard["target_count"] = int(gap_count)
        repair_shard["repair_of_shard_id"] = shard_id
        prior_candidate_summaries = [
            f"- {case.get('id') or ''}: {case.get('description') or ''}"
            for case in accepted_cases
        ]
        repair_prompt = (
            f"{str(request.get('system_prompt') or '')}\n\n"
            "# --- LOCAL SHARD GAP REPAIR ---\n"
            "This section overrides the earlier batch count for this repair request.\n"
            f"Repair attempt: {max(1, int(repair_attempt or 1))}.\n"
            f"Only repair shard {shard_id}. Return exactly {gap_count} additional cases.\n"
            "Do not regenerate any previous candidate from this shard and do not cover another shard.\n"
            "Previously generated in this shard (including candidates later rejected by merge):\n"
            + (
                "\n".join(prior_candidate_summaries)
                if prior_candidate_summaries
                else "- (none)"
            )
        )
        repair_instruction = str(result.get("repair_instruction") or "").strip()
        if repair_instruction:
            repair_prompt = f"{repair_prompt}\n\n{repair_instruction}"
        repair_request = dict(request)
        repair_request.update(
            {
                "shard": repair_shard,
                "system_prompt": repair_prompt,
                "repair_attempt": max(1, int(repair_attempt or 1)),
            }
        )
        repairs.append(repair_request)
    return repairs


def merge_parallel_shard_attempts(
    initial_results: list[dict[str, Any]],
    repair_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """保留首轮成功数据，将局部补生成结果合并回原分片。"""

    combined = [dict(item) for item in initial_results if isinstance(item, dict)]
    by_shard_id = {
        str((item.get("shard") or {}).get("shard_id") or ""): item
        for item in combined
    }
    for repair in repair_results:
        if not isinstance(repair, dict):
            continue
        repair_shard = dict(repair.get("shard") or {})
        shard_id = str(
            repair_shard.get("repair_of_shard_id")
            or repair_shard.get("shard_id")
            or ""
        )
        target = by_shard_id.get(shard_id)
        if target is None:
            target = dict(repair)
            target["shard"] = repair_shard
            combined.append(target)
            by_shard_id[shard_id] = target
            continue
        target["cases"] = [
            *[dict(item) for item in (target.get("cases") or []) if isinstance(item, dict)],
            *[dict(item) for item in (repair.get("cases") or []) if isinstance(item, dict)],
        ]
        for metric_name in (
            "duration_ms",
            "raw_response_chars",
            "raw_parsed_case_count",
            "normalized_case_count",
            "semantic_rejection_count",
        ):
            target[metric_name] = int(target.get(metric_name) or 0) + int(
                repair.get(metric_name) or 0
            )
        target["response_case_count"] = int(len(target["cases"]))
        target["error_codes"] = list(
            dict.fromkeys(
                [
                    str(item)
                    for item in [
                        *(target.get("error_codes") or []),
                        *(repair.get("error_codes") or []),
                    ]
                    if str(item or "").strip()
                ]
            )
        )
        target["semantic_rejection_codes"] = list(
            dict.fromkeys(
                [
                    str(item)
                    for item in [
                        *(target.get("semantic_rejection_codes") or []),
                        *(repair.get("semantic_rejection_codes") or []),
                    ]
                    if str(item or "").strip()
                ]
            )
        )
        target["repair_status"] = str(repair.get("status") or "")
        target["repair_attempt_count"] = int(target.get("repair_attempt_count") or 0) + 1
    return sorted(
        combined,
        key=lambda item: _result_sort_key(item, 0),
    )


def normalize_and_accept_parallel_shard_results(
    shard_results: list[dict[str, Any]],
    *,
    normalize_json_structure_fn: Callable[[Any], Any],
    accept_candidates_fn: Callable[..., Any],
    semantic_rejections: list[dict[str, Any]],
    start_id: int,
) -> list[dict[str, Any]]:
    """主线程按 merge_order 串行执行归一化和契约验收。"""

    ordered = sorted(
        [dict(item) for item in shard_results if isinstance(item, dict)],
        key=lambda item: _result_sort_key(item, 0),
    )
    next_start_id = int(start_id or 1)
    for result in ordered:
        shard = dict(result.get("shard") or {})
        target_count = max(0, int(shard.get("target_count") or 0))
        error_codes = [
            str(item)
            for item in (result.get("error_codes") or [])
            if str(item or "").strip()
        ]
        raw_cases = [
            dict(item)
            for item in (result.get("cases") or [])
            if isinstance(item, dict)
        ]
        result["raw_parsed_case_count"] = int(
            result.get("raw_parsed_case_count") or len(raw_cases)
        )
        result["normalized_case_count"] = 0
        result["semantic_rejection_count"] = 0
        result["incomplete_rows"] = []
        result["module_contract_summary"] = {}
        if str(result.get("status") or "") != "parsed":
            result["cases"] = []
            result["accepted_case_count"] = 0
            result["gap_count"] = int(target_count)
            result["error_codes"] = list(dict.fromkeys(error_codes))
            next_start_id += target_count
            continue

        semantic_rejection_before = int(len(semantic_rejections))
        try:
            normalized = normalize_json_structure_fn(raw_cases)
            normalized_cases = [
                dict(item)
                for item in (normalized if isinstance(normalized, list) else [])
                if isinstance(item, dict)
            ]
        except Exception as exc:
            normalized_cases = []
            result["error"] = str(exc)[:300]
            error_codes.append("normalization_error")

        # 独立分片已经获得了语义图中的事实所有权目录，在这里封闭引用边界。
        # 全局契约只能证明 fact_id 存在，不能证明它属于当前分片。
        if str(shard.get("shard_kind") or "independent") != "main_chain":
            assigned_fact_ids = {
                str(item.get("fact_id") or "").strip()
                for item in (shard.get("facts") or [])
                if isinstance(item, dict)
                and str(item.get("fact_id") or "").strip()
            }
            owned_cases: list[dict[str, Any]] = []
            for case in normalized_cases:
                case_fact_ids = verified_case_fact_ids(case)
                rejection_reasons: list[str] = []
                if assigned_fact_ids and not case_fact_ids:
                    rejection_reasons.append("fact_ids:assigned_fact_required")
                outside_fact_ids = sorted(case_fact_ids - assigned_fact_ids)
                if outside_fact_ids:
                    rejection_reasons.append("fact_ids:outside_shard_catalog")
                if rejection_reasons:
                    semantic_rejections.append(
                        {
                            "case_id": _case_old_id(case),
                            "rejection_reasons": rejection_reasons,
                            "outside_fact_ids": outside_fact_ids,
                            "assigned_fact_id_samples": sorted(assigned_fact_ids)[:8],
                        }
                    )
                    continue
                owned_cases.append(case)
            normalized_cases = owned_cases
        semantic_rejection_count = max(
            0,
            int(len(semantic_rejections)) - semantic_rejection_before,
        )
        new_semantic_rejections = semantic_rejections[semantic_rejection_before:]
        semantic_rejection_codes: list[str] = []
        for rejection in new_semantic_rejections:
            if not isinstance(rejection, dict):
                continue
            rejection.setdefault("source_shard_id", str(shard.get("shard_id") or ""))
            rejection.setdefault(
                "source_shard_kind",
                str(shard.get("shard_kind") or "independent"),
            )
            rejection.setdefault(
                "source_shard_attempt",
                int(result.get("repair_attempt") or 0) + 1,
            )
            semantic_rejection_codes.extend(
                str(item).strip()
                for item in (rejection.get("rejection_reasons") or [])
                if str(item or "").strip()
            )
        result["normalized_case_count"] = int(len(normalized_cases))
        result["semantic_rejection_count"] = int(semantic_rejection_count)
        result["semantic_rejection_codes"] = list(
            dict.fromkeys(semantic_rejection_codes)
        )
        result["semantic_rejections"] = [
            dict(item) for item in new_semantic_rejections if isinstance(item, dict)
        ]
        if semantic_rejection_count:
            error_codes.append("semantic_contract_rejected")

        try:
            acceptance = accept_candidates_fn(
                normalized_cases,
                limit=target_count,
                start_id=next_start_id,
            )
            accepted_cases = [
                dict(item)
                for item in (getattr(acceptance, "cases", None) or [])
                if isinstance(item, dict)
            ]
            incomplete_rows = [
                dict(item)
                for item in (getattr(acceptance, "incomplete_rows", None) or [])
                if isinstance(item, dict)
            ]
            module_summary = dict(
                getattr(acceptance, "module_contract_summary", None) or {}
            )
        except Exception as exc:
            accepted_cases = []
            incomplete_rows = []
            module_summary = {}
            result["error"] = str(exc)[:300]
            error_codes.append("candidate_acceptance_error")

        if incomplete_rows:
            error_codes.append("incomplete_case_schema")
        if int(module_summary.get("module_rejected_case_count") or 0) > 0:
            error_codes.append("functional_module_contract_rejected")
        gap_count = max(0, target_count - len(accepted_cases))
        result["cases"] = accepted_cases
        result["accepted_case_count"] = int(len(accepted_cases))
        result["gap_count"] = int(gap_count)
        result["incomplete_rows"] = incomplete_rows
        result["module_contract_summary"] = module_summary
        result["status"] = "accepted" if gap_count == 0 else (
            "underfilled" if accepted_cases else "rejected"
        )
        result["error_codes"] = list(dict.fromkeys(error_codes))
        next_start_id += target_count
    return ordered


def _result_sort_key(result: dict[str, Any], index: int) -> int:
    shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
    return int(shard.get("merge_order") or shard.get("shard_index") or result.get("shard_index") or index + 1)


def _case_old_id(case: dict[str, Any]) -> str:
    return str(case.get("id") or case.get("caseId") or "").strip()


def _rewrite_dependency_refs(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [id_map.get(str(item), str(item)) for item in value if str(item or "").strip()]
    if isinstance(value, str):
        return id_map.get(value, value)
    return value


def merge_parallel_shard_cases(
    shard_results: list[dict[str, Any]],
    *,
    build_case_signature_fn: Callable[[dict[str, Any]], str],
    start_id: int,
    expected_count: int | None = None,
    protected_shard_ids: set[str] | None = None,
) -> dict[str, Any]:
    protected_sources = {
        str(shard_id or "").strip()
        for shard_id in (protected_shard_ids or set())
        if str(shard_id or "").strip()
    }
    ordered_results = sorted(
        [item for item in (shard_results or []) if isinstance(item, dict)],
        key=lambda item: _result_sort_key(item, 0),
    )
    seen: set[str] = set()
    unique_cases: list[tuple[str, dict[str, Any], str]] = []
    input_case_count = 0
    duplicate_count = 0
    exact_duplicate_count = 0
    semantic_duplicate_count = 0
    containment_count = 0
    semantic_containment_dropped_count = 0
    relation_samples: list[dict[str, Any]] = []
    per_shard_counts: list[dict[str, Any]] = []
    replacement_id_map: dict[str, str] = {}
    signature_owners: dict[str, tuple[str, dict[str, Any], str]] = {}
    for result_index, result in enumerate(ordered_results):
        cases = result.get("cases") if isinstance(result.get("cases"), list) else []
        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
        shard_input_count = 0
        shard_unique_count = 0
        shard_semantic_duplicate_count = 0
        shard_containment_count = 0
        shard_id = str(shard.get("shard_id") or f"SHARD-{result_index + 1:02d}")
        for case in cases:
            if not isinstance(case, dict):
                continue
            shard_input_count += 1
            input_case_count += 1
            signature = str(build_case_signature_fn(case) or "").strip()
            if signature and signature in seen:
                duplicate_count += 1
                exact_duplicate_count += 1
                owner = signature_owners.get(signature)
                if owner is not None and len(relation_samples) < 32:
                    relation_samples.append(
                        {
                            "relation": "duplicate",
                            "confidence": 1.0,
                            "reasons": ["exact_public_signature_match"],
                            "action": "drop_duplicate",
                            "case_id": _case_old_id(case),
                            "source_shard_id": shard_id,
                            "related_case_id": owner[0],
                            "related_shard_id": owner[2],
                            "dropped_case_id": _case_old_id(case),
                            "dropped_shard_id": shard_id,
                            "dropped_fact_ids": sorted(verified_case_fact_ids(case)),
                            "retained_case_id": owner[0],
                            "retained_shard_id": owner[2],
                            "retained_fact_ids": sorted(
                                verified_case_fact_ids(owner[1])
                            ),
                            "fact_ids": sorted(verified_case_fact_ids(case)),
                        }
                    )
                continue
            semantic_resolution: tuple[
                int,
                tuple[str, dict[str, Any], str],
                dict[str, Any],
            ] | None = None
            unresolved_containment: list[
                tuple[tuple[str, dict[str, Any], str], dict[str, Any]]
            ] = []
            for kept_index, kept in enumerate(unique_cases):
                pair_result = deduplicate_cases_by_semantic_identity(
                    [kept[1], case],
                    sample_limit=1,
                )
                pair_sample = dict(
                    (pair_result.relation_samples or [{}])[0]
                )
                if pair_result.duplicate_count > 0 and pair_result.dropped_count > 0:
                    semantic_resolution = (kept_index, kept, pair_sample)
                    break
                if pair_result.containment_count <= 0:
                    continue
                if pair_result.dropped_count > 0:
                    semantic_resolution = (kept_index, kept, pair_sample)
                    break
                unresolved_containment.append((kept, pair_sample))

            if semantic_resolution is not None:
                kept_index, kept, pair_sample = semantic_resolution
                relation = str(pair_sample.get("relation") or "")
                action = str(pair_sample.get("action") or "")
                candidate_replaces_kept = action in {
                    "replace_with_richer_duplicate",
                    "replace_with_containing_case",
                }
                if candidate_replaces_kept and kept[2] in protected_sources:
                    candidate_replaces_kept = False
                    action = "drop_protected_history_conflict"
                    pair_sample["action"] = action
                    pair_sample["reasons"] = list(
                        dict.fromkeys(
                            [
                                *(pair_sample.get("reasons") or []),
                                "accepted_public_batch_history_protected",
                            ]
                        )
                    )
                if relation == "duplicate":
                    duplicate_count += 1
                    semantic_duplicate_count += 1
                    if not candidate_replaces_kept or kept[2] == shard_id:
                        shard_semantic_duplicate_count += 1
                    else:
                        for row in per_shard_counts:
                            if str(row.get("shard_id") or "") == kept[2]:
                                row["semantic_duplicate_count"] = int(
                                    row.get("semantic_duplicate_count") or 0
                                ) + 1
                                break
                else:
                    containment_count += 1
                    semantic_containment_dropped_count += 1
                    if not candidate_replaces_kept or kept[2] == shard_id:
                        shard_containment_count += 1
                    else:
                        for row in per_shard_counts:
                            if str(row.get("shard_id") or "") == kept[2]:
                                row["containment_count"] = int(
                                    row.get("containment_count") or 0
                                ) + 1
                                break

                candidate_id = _case_old_id(case)
                retained_id = candidate_id if candidate_replaces_kept else kept[0]
                retained_shard_id = shard_id if candidate_replaces_kept else kept[2]
                dropped_id = kept[0] if candidate_replaces_kept else candidate_id
                dropped_shard_id = kept[2] if candidate_replaces_kept else shard_id
                if dropped_id and retained_id:
                    replacement_id_map[dropped_id] = retained_id
                if candidate_replaces_kept:
                    previous_signature = str(
                        build_case_signature_fn(kept[1]) or ""
                    ).strip()
                    if previous_signature:
                        seen.discard(previous_signature)
                        signature_owners.pop(previous_signature, None)
                    unique_cases[kept_index] = (candidate_id, dict(case), shard_id)
                    if signature:
                        seen.add(signature)
                        signature_owners[signature] = (
                            candidate_id,
                            dict(case),
                            shard_id,
                        )
                    if kept[2] == shard_id:
                        shard_unique_count = max(0, shard_unique_count - 1)
                    else:
                        for row in per_shard_counts:
                            if str(row.get("shard_id") or "") == kept[2]:
                                row["unique_case_count"] = max(
                                    0,
                                    int(row.get("unique_case_count") or 0) - 1,
                                )
                                break
                    shard_unique_count += 1
                if len(relation_samples) < 32:
                    relation_samples.append(
                        {
                            "relation": relation,
                            "confidence": float(pair_sample.get("confidence") or 0.0),
                            "reasons": list(pair_sample.get("reasons") or []),
                            "action": action,
                            "case_id": candidate_id,
                            "source_shard_id": shard_id,
                            "related_case_id": kept[0],
                            "related_shard_id": kept[2],
                            "dropped_case_id": dropped_id,
                            "dropped_shard_id": dropped_shard_id,
                            "dropped_fact_ids": sorted(
                                verified_case_fact_ids(
                                    kept[1] if candidate_replaces_kept else case
                                )
                            ),
                            "retained_case_id": retained_id,
                            "retained_shard_id": retained_shard_id,
                            "retained_fact_ids": sorted(
                                verified_case_fact_ids(
                                    case if candidate_replaces_kept else kept[1]
                                )
                            ),
                            "fact_ids": sorted(verified_case_fact_ids(case)),
                        }
                    )
                continue

            for kept, pair_sample in unresolved_containment:
                containment_count += 1
                shard_containment_count += 1
                if len(relation_samples) < 32:
                    relation_samples.append(
                        {
                            "relation": str(pair_sample.get("relation") or ""),
                            "confidence": float(pair_sample.get("confidence") or 0.0),
                            "reasons": list(pair_sample.get("reasons") or []),
                            "action": str(pair_sample.get("action") or ""),
                            "case_id": _case_old_id(case),
                            "source_shard_id": shard_id,
                            "related_case_id": kept[0],
                            "related_shard_id": kept[2],
                        }
                    )
            if signature:
                seen.add(signature)
                signature_owners[signature] = (
                    _case_old_id(case),
                    dict(case),
                    shard_id,
                )
            unique_cases.append((_case_old_id(case), dict(case), shard_id))
            shard_unique_count += 1
        per_shard_counts.append(
            {
                "shard_id": shard_id,
                "input_case_count": int(shard_input_count),
                "unique_case_count": int(shard_unique_count),
                "semantic_duplicate_count": int(shard_semantic_duplicate_count),
                "containment_count": int(shard_containment_count),
            }
        )

    limit = int(expected_count or 0)
    truncated_count = 0
    if limit > 0 and len(unique_cases) > limit:
        truncated_count = int(len(unique_cases) - limit)
        unique_cases = unique_cases[:limit]

    id_map: dict[str, str] = {}
    final_cases: list[dict[str, Any]] = []
    next_id = int(start_id or 1)
    for old_id, case, _source_shard_id in unique_cases:
        new_id = f"TC-{next_id:03d}"
        if old_id:
            id_map[old_id] = new_id
        updated = dict(case)
        updated["id"] = new_id
        if "caseId" in updated:
            updated["caseId"] = new_id
        final_cases.append(updated)
        next_id += 1

    for dropped_id, retained_id in replacement_id_map.items():
        resolved_retained_id = retained_id
        visited: set[str] = set()
        while resolved_retained_id in replacement_id_map and resolved_retained_id not in visited:
            visited.add(resolved_retained_id)
            resolved_retained_id = replacement_id_map[resolved_retained_id]
        if resolved_retained_id in id_map:
            id_map[dropped_id] = id_map[resolved_retained_id]

    for case in final_cases:
        if "depends_on" in case:
            case["depends_on"] = _rewrite_dependency_refs(case.get("depends_on"), id_map)
        if "preconditions" in case:
            case["preconditions"] = _rewrite_dependency_refs(case.get("preconditions"), id_map)

    duplicate_rate = float(duplicate_count) / float(input_case_count) if input_case_count > 0 else 0.0
    return {
        "cases": final_cases,
        "input_case_count": int(input_case_count),
        "unique_case_count": int(len(final_cases)),
        "duplicate_count": int(duplicate_count),
        "exact_duplicate_count": int(exact_duplicate_count),
        "semantic_duplicate_count": int(semantic_duplicate_count),
        "containment_count": int(containment_count),
        "semantic_containment_dropped_count": int(
            semantic_containment_dropped_count
        ),
        "semantic_relation_samples": relation_samples,
        "duplicate_rate": round(float(duplicate_rate), 4),
        "truncated_count": int(truncated_count),
        "per_shard_counts": per_shard_counts,
    }


def merge_public_batch_against_accepted_history(
    shard_results: list[dict[str, Any]],
    *,
    accepted_history_cases: list[dict[str, Any]],
    build_case_signature_fn: Callable[[dict[str, Any]], str],
    start_id: int,
    expected_batch_count: int,
) -> dict[str, Any]:
    """用统一语义口径在批次推进前校验跨批唯一性，并保护已验收批次。"""

    history_cases = [
        dict(case)
        for case in accepted_history_cases
        if isinstance(case, dict)
    ]
    if not history_cases:
        result = merge_parallel_shard_cases(
            shard_results,
            build_case_signature_fn=build_case_signature_fn,
            start_id=start_id,
            expected_count=expected_batch_count,
        )
        result["accepted_history_case_count"] = 0
        result["cumulative_unique_case_count"] = int(
            result.get("unique_case_count") or 0
        )
        result["cross_batch_semantic_drop_count"] = 0
        return result

    history_result = {
        "shard": {
            "shard_id": _ACCEPTED_PUBLIC_BATCH_HISTORY_SHARD_ID,
            "merge_order": -1,
        },
        "cases": history_cases,
    }
    merged = merge_parallel_shard_cases(
        [history_result, *shard_results],
        build_case_signature_fn=build_case_signature_fn,
        start_id=start_id,
        expected_count=len(history_cases) + max(0, int(expected_batch_count or 0)),
        protected_shard_ids={_ACCEPTED_PUBLIC_BATCH_HISTORY_SHARD_ID},
    )
    cumulative_cases = [
        dict(case)
        for case in (merged.get("cases") or [])
        if isinstance(case, dict)
    ]
    current_batch_cases = cumulative_cases[len(history_cases) :]
    current_shard_ids = {
        str((result.get("shard") or {}).get("shard_id") or "")
        for result in shard_results
        if isinstance(result, dict)
    }
    current_input_count = sum(
        int(item.get("input_case_count") or 0)
        for item in (merged.get("per_shard_counts") or [])
        if isinstance(item, dict)
        and str(item.get("shard_id") or "") in current_shard_ids
    )
    relation_samples = [
        dict(sample)
        for sample in (merged.get("semantic_relation_samples") or [])
        if isinstance(sample, dict)
    ]
    cross_batch_drop_count = sum(
        1
        for sample in relation_samples
        if str(sample.get("retained_shard_id") or "")
        == _ACCEPTED_PUBLIC_BATCH_HISTORY_SHARD_ID
        or "accepted_public_batch_history_protected"
        in set(sample.get("reasons") or [])
    )
    output = dict(merged)
    output["cases"] = current_batch_cases
    output["input_case_count"] = int(current_input_count)
    output["unique_case_count"] = int(len(current_batch_cases))
    output["accepted_history_case_count"] = int(len(history_cases))
    output["cumulative_unique_case_count"] = int(len(cumulative_cases))
    output["cross_batch_semantic_drop_count"] = int(cross_batch_drop_count)
    output["per_shard_counts"] = [
        dict(item)
        for item in (merged.get("per_shard_counts") or [])
        if isinstance(item, dict)
        and str(item.get("shard_id") or "") != _ACCEPTED_PUBLIC_BATCH_HISTORY_SHARD_ID
    ]
    output["duplicate_rate"] = (
        round(float(merged.get("duplicate_count") or 0) / float(current_input_count), 4)
        if current_input_count > 0
        else 0.0
    )
    return output


def assign_cross_shard_duplicate_repair_targets(
    shard_results: list[dict[str, Any]],
    merge_result: dict[str, Any],
    *,
    gap_count: int | None = None,
) -> list[dict[str, Any]]:
    """把共享语义判重删除的缺口归还给原分片。"""

    output = [dict(item) for item in shard_results if isinstance(item, dict)]
    for result in output:
        result["repair_target_count"] = 0
        result["public_batch_merge_gap_target"] = 0
    results_by_shard = {
        str((item.get("shard") or {}).get("shard_id") or ""): item
        for item in output
        if isinstance(item.get("shard"), dict)
    }
    duplicate_samples_by_shard: dict[str, list[dict[str, Any]]] = {}
    for sample in merge_result.get("semantic_relation_samples") or []:
        if not isinstance(sample, dict):
            continue
        action = str(sample.get("action") or "")
        if action not in {
            "drop_duplicate",
            "replace_with_richer_duplicate",
            "drop_contained_case",
            "replace_with_containing_case",
            "drop_protected_history_conflict",
        }:
            continue
        shard_id = str(
            sample.get("dropped_shard_id")
            or sample.get("source_shard_id")
            or ""
        )
        if shard_id:
            duplicate_samples_by_shard.setdefault(shard_id, []).append(dict(sample))

    remaining = (
        sum(len(samples) for samples in duplicate_samples_by_shard.values())
        if gap_count is None
        else max(0, int(gap_count or 0))
    )
    for shard_id, samples in duplicate_samples_by_shard.items():
        if remaining <= 0:
            break
        result = results_by_shard.get(shard_id)
        if result is None:
            continue
        allocation = min(remaining, len(samples))
        if allocation <= 0:
            continue
        allocated_samples = samples[:allocation]
        result["repair_target_count"] = int(allocation)
        fact_sets = [
            list(sample.get("dropped_fact_ids") or sample.get("fact_ids") or [])
            for sample in allocated_samples
            if list(sample.get("dropped_fact_ids") or sample.get("fact_ids") or [])
        ]
        result["repair_instruction"] = (
            "The global merge removed these candidates using the same semantic identity policy as "
            "final postprocess because they duplicated or were contained by another accepted case. "
            "Generate different grounded behavior atoms from this shard's assigned active facts; "
            "do not rename, paraphrase, broaden, or narrow the rejected behavior. "
            f"Rejected fact-id sets: {fact_sets}"
        )
        remaining -= allocation
    return output
