"""Compatibility exports for pipeline runtime helpers.

Pipeline background execution lives in modules/orchestration_components. This
module is kept thin so older imports do not reintroduce router-owned workers.
"""

from __future__ import annotations

from modules.orchestration_components.pipeline_runtime.dispatcher import (
    start_pipeline_worker as _start_worker,
)
from modules.orchestration_components.pipeline_runtime.runner import (
    STAGE_WORKFLOW_KIND,
    STAGE_WORKFLOW_STAGE,
    claim_pending_run as _claim_pending_run,
    run_pipeline_worker as _run_pipeline_worker,
)
from modules.orchestration_components.pipeline_runtime.schema_compat import (
    ensure_pipeline_table as _ensure_pipeline_table,
)

__all__ = [
    "STAGE_WORKFLOW_KIND",
    "STAGE_WORKFLOW_STAGE",
    "_claim_pending_run",
    "_ensure_pipeline_table",
    "_run_pipeline_worker",
    "_start_worker",
]
