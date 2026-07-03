from __future__ import annotations

import logging
import os
import re


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using default=%s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Env %s=%r below minimum=%s; using minimum", name, raw, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("Env %s=%r above maximum=%s; using maximum", name, raw, maximum)
        return maximum
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("Invalid float env %s=%r; using default=%s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Env %s=%r below minimum=%s; using minimum", name, raw, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("Env %s=%r above maximum=%s; using maximum", name, raw, maximum)
        return maximum
    return value


_MAX_MUST_COVER_RULES = 12
_MAX_SCENARIOS = 8
_MAX_FORBIDDEN_PATTERNS = 8
_MAX_PREFERRED_PATTERNS = 10
_MAX_SOFT_CONSTRAINTS = 14
_MAX_QUALITY_HINTS = 12
_MAX_EVAL_REPORT_DOCS = 6
_MAX_AGENT_LEARNING_DOCS = 3
_MAX_DATASET_SAMPLES = 500
_MAX_PRIORITY_POOL_SAMPLES = 400
_MAX_PRIORITY_POOL_HINTS = 14
_MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS = 8
_MAX_PRIORITY_POOL_SOFT_CONSTRAINTS = 14
_MAX_PRIORITY_POOL_SCENARIOS = 8
_MAX_WORKFLOW_BLUEPRINTS = 5
_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K = _env_int(
    "TESTGEN_PRIORITY_POOL_RETRIEVAL_TOP_K",
    5,
    minimum=1,
    maximum=_MAX_PRIORITY_POOL_SAMPLES,
)
_MAX_PRIORITY_POOL_CLUSTER_CAP = _env_int(
    "TESTGEN_PRIORITY_POOL_CLUSTER_CAP",
    2,
    minimum=1,
    maximum=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
)
_PRIORITY_POOL_MIN_POSITIVE_TOP_K = _env_int(
    "TESTGEN_PRIORITY_POOL_MIN_POSITIVE_TOP_K",
    2,
    minimum=0,
    maximum=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
)
_PRIORITY_POOL_MAX_NEGATIVE_TOP_K = _env_int(
    "TESTGEN_PRIORITY_POOL_MAX_NEGATIVE_TOP_K",
    3,
    minimum=0,
    maximum=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
)
_SYNC_PRIORITY_INDEX_ON_READ = str(
    os.getenv("TESTGEN_PRIORITY_POOL_INDEX_SYNC_ON_READ", "false")
).strip().lower() in {"1", "true", "yes", "on"}
_MIN_PRIORITY_POOL_PATTERN_CONFIDENCE = _env_float(
    "TESTGEN_PRIORITY_POOL_MIN_PATTERN_CONFIDENCE",
    0.6,
    minimum=0.0,
    maximum=1.0,
)
_ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
