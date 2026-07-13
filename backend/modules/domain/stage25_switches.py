"""RAG 阶段2.5策略开关矩阵。"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        return int(default)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name, default)
    return str(value).strip() if value is not None else str(default)


@dataclass(frozen=True)
class Stage25SwitchMatrix:
    """
    阶段2.5增量能力开关。

    设计目标：
    1. 所有增强能力都可独立回退，默认兼容现有链路；
    2. 每项开关可通过环境变量覆盖，便于灰度发布；
    3. 开关值可序列化到调试输出，便于问题定位。
    """

    final_context_source_log_enabled: bool = _env_bool(
        "RAG_STAGE25_CONTEXT_SOURCE_LOG_ENABLED", True
    )
    snapshot_versioning_enabled: bool = _env_bool(
        "RAG_STAGE25_SNAPSHOT_VERSIONING_ENABLED", True
    )
    guard_reason_chain_enabled: bool = _env_bool(
        "RAG_STAGE25_GUARD_REASON_CHAIN_ENABLED", True
    )
    index_audit_enabled: bool = _env_bool(
        "RAG_STAGE25_INDEX_AUDIT_ENABLED", True
    )
    coverage_diagnostics_enabled: bool = _env_bool(
        "RAG_STAGE25_COVERAGE_DIAGNOSTICS_ENABLED", True
    )
    retrieval_profile_enabled: bool = _env_bool(
        "RAG_STAGE25_RETRIEVAL_PROFILE_ENABLED", True
    )
    compression_fidelity_enabled: bool = _env_bool(
        "RAG_STAGE25_COMPRESSION_FIDELITY_ENABLED", True
    )
    include_switches_in_debug: bool = _env_bool(
        "RAG_STAGE25_INCLUDE_SWITCHES_IN_DEBUG", True
    )
    # 压缩保真阈值（0-1），低于阈值触发 warning/回退策略。
    fidelity_min_retention: float = _env_float(
        "RAG_STAGE25_FIDELITY_MIN_RETENTION", 0.7
    )
    # warn | fallback_light | fallback_raw
    fidelity_fallback_mode: str = _env_str(
        "RAG_STAGE25_FIDELITY_FALLBACK_MODE", "warn"
    ).lower()
    # fallback_light 模式下放宽预算倍率。
    fidelity_light_budget_factor: float = _env_float(
        "RAG_STAGE25_FIDELITY_LIGHT_BUDGET_FACTOR", 1.25
    )
    # 覆盖诊断关键词覆盖率告警阈值。
    coverage_keyword_warn_threshold: float = _env_float(
        "RAG_STAGE25_COVERAGE_KEYWORD_WARN_THRESHOLD", 0.45
    )
    # 低于该值且 expected_count>generated_count 时，推断生成不足。
    coverage_min_count_ratio: float = _env_float(
        "RAG_STAGE25_COVERAGE_MIN_COUNT_RATIO", 0.8
    )
    # 检索画像默认输出的 top-k 分数数量。
    retrieval_profile_topk: int = _env_int(
        "RAG_STAGE25_RETRIEVAL_PROFILE_TOPK", 10
    )

    def to_dict(self) -> dict:
        return asdict(self)


STAGE25_SWITCHES = Stage25SwitchMatrix()
