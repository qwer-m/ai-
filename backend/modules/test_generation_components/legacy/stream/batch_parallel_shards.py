from __future__ import annotations

from dataclasses import dataclass
import math
import os
import time
from typing import Any, Callable, Mapping

from modules.orchestration.background_task_governance import run_governed_threadpool_map


TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled"}


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
    return ParallelShardConfig(
        enabled=_env_bool("GENERATION_STREAM_COVERAGE_SHARDS_ENABLED", False, env),
        max_workers=_env_int(
            "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS",
            2,
            min_value=1,
            max_value=4,
            env=env,
        ),
        min_expected_count=_env_int(
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT",
            60,
            min_value=1,
            max_value=500,
            env=env,
        ),
        min_coverage_rules=_env_int(
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES",
            8,
            min_value=1,
            max_value=100,
            env=env,
        ),
        duplicate_rate_abort=_env_float(
            "GENERATION_STREAM_COVERAGE_SHARD_DUPLICATE_RATE_ABORT",
            0.25,
            min_value=0.0,
            max_value=1.0,
            env=env,
        ),
        min_unique_ratio=_env_float(
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_UNIQUE_RATIO",
            0.45,
            min_value=0.0,
            max_value=1.0,
            env=env,
        ),
    )


def parallel_shard_config_from_settings(settings_obj: Any) -> ParallelShardConfig:
    return ParallelShardConfig(
        enabled=bool(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARDS_ENABLED", False)),
        max_workers=max(1, min(4, int(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS", 2) or 2))),
        min_expected_count=max(
            1,
            min(500, int(getattr(settings_obj, "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT", 60) or 60)),
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
    if int(total_batches or 0) < 2:
        return False, "single_batch"
    if int(coverage_rule_count or 0) < int(cfg.min_coverage_rules or 1):
        return False, "insufficient_coverage_rules"
    if int(cfg.max_workers or 0) < 2:
        return False, "max_workers_below_parallel"
    return True, "enabled"


def _normalize_rules(coverage_plan_rules: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(coverage_plan_rules or [], start=1):
        if not isinstance(item, dict):
            continue
        rule_text = str(item.get("rule_text") or "").strip()
        if not rule_text:
            continue
        rule_id = str(item.get("rule_id") or f"RULE-{index:03d}").strip() or f"RULE-{index:03d}"
        normalized.append({"rule_id": rule_id, "rule_text": rule_text})
    return normalized


def build_coverage_shard_plan(
    coverage_plan_rules: list[dict[str, Any]],
    *,
    expected_count: int,
    max_workers: int,
    max_cases_per_worker: int = 25,
) -> list[dict[str, Any]]:
    rules = _normalize_rules(coverage_plan_rules)
    if not rules:
        return []
    by_count = max(1, math.ceil(max(1, int(expected_count or 0)) / max(1, int(max_cases_per_worker or 1))))
    shard_count = min(max(1, int(max_workers or 1)), len(rules), by_count)
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
        shards.append(
            {
                "shard_id": f"SHARD-{shard_index + 1:02d}",
                "shard_index": int(shard_index + 1),
                "total_shards": int(shard_count),
                "target_count": int(base_target + (1 if shard_index < remainder else 0)),
                "rule_ids": rule_ids,
                "rule_texts": [rule["rule_text"] for rule in assigned],
                "excluded_rule_ids": [rule_id for rule_id in all_rule_ids if rule_id not in set(rule_ids)],
                "merge_order": int(shard_index + 1),
            }
        )
    return shards


def build_parallel_shard_instruction(shard: dict[str, Any]) -> str:
    rule_ids = [str(item) for item in (shard.get("rule_ids") or []) if str(item or "").strip()]
    rule_texts = [str(item).strip() for item in (shard.get("rule_texts") or []) if str(item or "").strip()]
    rule_lines = []
    for index, rule_text in enumerate(rule_texts, start=1):
        rule_id = rule_ids[index - 1] if index - 1 < len(rule_ids) else f"RULE-{index:03d}"
        rule_lines.append(f"{index}. {rule_id}: {rule_text[:220]}")
    excluded = ", ".join(str(item) for item in (shard.get("excluded_rule_ids") or []) if str(item or "").strip())
    if not excluded:
        excluded = "(none)"
    return f"""
# --- PARALLEL COVERAGE SHARD (internal planning, do not output this section) ---
Shard: {shard.get("shard_id") or ""} ({int(shard.get("shard_index") or 1)}/{int(shard.get("total_shards") or 1)})
Target count: about {int(shard.get("target_count") or 0)} cases. This is quality-first, not a quota.
Assigned validation rules:
{chr(10).join(rule_lines) if rule_lines else "- No assigned rules; return []"}
Out-of-scope rule IDs for validation goals: {excluded}

Shard contract:
- Generate validation goals only for the assigned rules above.
- Other rule IDs may appear only as setup or prerequisite context, not as the primary validation goal.
- Do not compensate for other shards and do not broaden the module scope to fill count.
- Case IDs are provisional; the merge step will deduplicate, order, and renumber final IDs.
"""


def _parse_shard_cases(
    *,
    content: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
) -> list[dict[str, Any]]:
    parsed = clean_and_parse_json_fn(content)
    parsed = normalize_json_structure_fn(parsed)
    if not isinstance(parsed, list):
        raise ValueError("parallel shard response is not a JSON array")
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _run_single_shard_request(
    *,
    request: dict[str, Any],
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    shard = dict(request.get("shard") or {})
    client = request.get("client")
    system_prompt = str(request.get("system_prompt") or "")
    if client is None or not hasattr(client, "generate_response"):
        return {
            "shard": shard,
            "status": "client_unavailable",
            "error": "parallel shard client does not support generate_response",
            "cases": [],
            "duration_ms": 0,
            "metadata": {},
        }
    try:
        content = client.generate_response(
            requirement,
            system_prompt,
            task_type="generation",
        )
        content_text = str(content or "")
        if content_text.startswith("Error:") or content_text.startswith("Exception"):
            status = "provider_error"
            cases: list[dict[str, Any]] = []
            error = content_text[:300]
        else:
            cases = _parse_shard_cases(
                content=content_text,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
            )
            status = "parsed"
            error = ""
    except Exception as exc:
        status = "exception"
        cases = []
        error = str(exc)[:300]
    return {
        "shard": shard,
        "status": status,
        "error": error,
        "cases": cases,
        "duration_ms": max(0, int(round((time.perf_counter() - started) * 1000))),
        "metadata": dict(getattr(client, "last_response_metadata", {}) or {}),
        "response_case_count": int(len(cases)),
    }


def execute_parallel_shard_requests(
    *,
    requests: list[dict[str, Any]],
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    max_workers: int,
) -> list[dict[str, Any]]:
    if not requests:
        return []
    worker_count = min(max(1, int(max_workers or 1)), len(requests))
    governed_results = run_governed_threadpool_map(
        profile_key="test_generation_coverage_shard_threadpool",
        items=requests,
        worker=lambda request: _run_single_shard_request(
            request=request,
            requirement=requirement,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
        ),
        max_workers=worker_count,
        thread_name_prefix="testgen-shard",
        business_id=str(requests[0].get("request_id") or "") if isinstance(requests[0], dict) else None,
    )
    results: list[dict[str, Any]] = []
    for item in governed_results:
        if item.exception is not None:
            shard = item.item.get("shard") if isinstance(item.item, dict) else {}
            results.append(
                {
                    "shard": shard if isinstance(shard, dict) else {},
                    "status": "exception",
                    "error": str(item.exception)[:300],
                    "cases": [],
                    "duration_ms": 0,
                    "metadata": {},
                }
            )
        else:
            results.append(dict(item.result or {}))
    return sorted(
        results,
        key=lambda item: int((item.get("shard") or {}).get("merge_order") or (item.get("shard") or {}).get("shard_index") or 0),
    )


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
) -> dict[str, Any]:
    ordered_results = sorted(
        [item for item in (shard_results or []) if isinstance(item, dict)],
        key=lambda item: _result_sort_key(item, 0),
    )
    seen: set[str] = set()
    unique_cases: list[tuple[str, dict[str, Any]]] = []
    input_case_count = 0
    duplicate_count = 0
    per_shard_counts: list[dict[str, Any]] = []
    for result_index, result in enumerate(ordered_results):
        cases = result.get("cases") if isinstance(result.get("cases"), list) else []
        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
        shard_input_count = 0
        shard_unique_count = 0
        for case in cases:
            if not isinstance(case, dict):
                continue
            shard_input_count += 1
            input_case_count += 1
            signature = str(build_case_signature_fn(case) or "").strip()
            if signature and signature in seen:
                duplicate_count += 1
                continue
            if signature:
                seen.add(signature)
            unique_cases.append((_case_old_id(case), dict(case)))
            shard_unique_count += 1
        per_shard_counts.append(
            {
                "shard_id": str(shard.get("shard_id") or f"SHARD-{result_index + 1:02d}"),
                "input_case_count": int(shard_input_count),
                "unique_case_count": int(shard_unique_count),
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
    for old_id, case in unique_cases:
        new_id = f"TC-{next_id:03d}"
        if old_id:
            id_map[old_id] = new_id
        updated = dict(case)
        updated["id"] = new_id
        if "caseId" in updated:
            updated["caseId"] = new_id
        final_cases.append(updated)
        next_id += 1

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
        "duplicate_rate": round(float(duplicate_rate), 4),
        "truncated_count": int(truncated_count),
        "per_shard_counts": per_shard_counts,
    }
