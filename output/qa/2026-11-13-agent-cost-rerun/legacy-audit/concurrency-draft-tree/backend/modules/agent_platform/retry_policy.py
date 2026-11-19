"""与执行方式无关的 Agent 重试预算和断点状态。"""

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError


TRANSIENT_ATTEMPT_BUDGET = 4
CONCURRENCY_PRESSURE_FAILURE_KINDS = frozenset(
    {"timeout", "connection", "rate_limit", "upstream_transient", "upstream_server"}
)
MODEL_ROUTE_HEALTH_FAILURE_KINDS = frozenset(
    {*CONCURRENCY_PRESSURE_FAILURE_KINDS, "empty_output", "json_syntax", "output_degeneration"}
)


@dataclass
class AdaptiveConcurrency:
    """DAG 与受管会话共享压力控制；只决定准入上限，不执行重试。"""

    max_concurrency: int
    min_concurrency: int
    pressure_threshold: int
    recovery_successes: int
    effective_concurrency: int
    pressure_failures: int = 0
    successful_since_adjustment: int = 0

    @classmethod
    def restore(cls, *, max_concurrency: int, min_concurrency: int,
                pressure_threshold: int, recovery_successes: int,
                checkpoint: dict[str, Any] | None = None) -> "AdaptiveConcurrency":
        if any(type(value) is not int or value < 1 for value in
               (max_concurrency, min_concurrency, pressure_threshold, recovery_successes)):
            raise ValueError("并发控制参数必须是正整数")
        floor = min(max_concurrency, min_concurrency)
        saved = checkpoint or {}
        effective = saved.get("effective_concurrency", max_concurrency)
        pressure = saved.get("pressure_failures", 0)
        successes = saved.get("successful_since_adjustment", 0)
        if any(type(value) is not int or value < 0 for value in (effective, pressure, successes)):
            raise ValueError("并发控制检查点必须是非负整数")
        return cls(max_concurrency, floor, pressure_threshold, recovery_successes,
                   min(max_concurrency, max(floor, effective)), pressure, successes)

    def snapshot(self) -> dict[str, int]:
        return {"max_concurrency": self.max_concurrency,
                "min_concurrency": self.min_concurrency,
                "effective_concurrency": self.effective_concurrency,
                "pressure_failures": self.pressure_failures,
                "successful_since_adjustment": self.successful_since_adjustment}

    def _adjust(self, target: int, reason: str, failure_kind: str = "") -> dict[str, Any] | None:
        target = min(self.max_concurrency, max(self.min_concurrency, target))
        if target == self.effective_concurrency:
            return None
        previous = self.effective_concurrency
        self.effective_concurrency = target
        self.pressure_failures = self.successful_since_adjustment = 0
        return {"from": previous, "to": target, "reason": reason, "failure_kind": failure_kind}

    def record_success(self) -> dict[str, Any] | None:
        self.successful_since_adjustment += 1
        if self.successful_since_adjustment >= self.recovery_successes:
            # 一个成功不能抹掉窗口内限流；稳定成功窗口结束才解除压力。
            self.pressure_failures = 0
            if self.effective_concurrency < self.max_concurrency:
                return self._adjust(self.effective_concurrency + 1, "success_recovery")
            self.successful_since_adjustment = 0
        return None

    def record_failure(self, failure_kind: str | None, *, retryable: bool = True) -> dict[str, Any] | None:
        if not retryable or failure_kind not in CONCURRENCY_PRESSURE_FAILURE_KINDS:
            return None
        self.successful_since_adjustment = 0
        self.pressure_failures += 1
        if self.pressure_failures >= self.pressure_threshold:
            return self._adjust(self.effective_concurrency - 1, "upstream_pressure", failure_kind)
        return None


def restorable_sdk_state(node_run: Any, *, destination_run_id: int | None = None) -> dict[str, Any]:
    """同运行重试与跨运行续跑共用恢复边界；历史大诊断只在原节点保存。"""
    state = {key: value for key, value in dict(node_run.sdk_state or {}).items()
             if key not in {"model_requests", "tool_calls", "tool_observations", "error_diagnostic", "checkpoint_restore"}}
    if isinstance(state.get("action_checkpoint"), dict):
        from .checkpoint_storage import compact_checkpoint

        state["action_checkpoint"] = compact_checkpoint(node_run)
        if destination_run_id is not None and destination_run_id != node_run.run_id:
            # 新 Run 重新绑定消费游标；历史预算和已用消费仍完整继承为基线。
            state.pop("action_usage_recorded", None)
    # map 的工作项和修复预算仍是可变控制数据，不能与旧尝试共享 Python 对象。
    return deepcopy(state)


def model_transport_failure_kind(error: Exception) -> str | None:
    """批次收敛与节点重试共用同一份上游暂态故障分类。"""

    if isinstance(error, (TimeoutError, APITimeoutError)):
        return "timeout"
    if isinstance(error, (ConnectionError, APIConnectionError)):
        return "connection"
    if isinstance(error, RateLimitError):
        return "rate_limit"
    if isinstance(error, InternalServerError):
        return "upstream_server"
    status_code = getattr(error, "status_code", None)
    if status_code == 429:
        return "rate_limit"
    if isinstance(status_code, int) and status_code >= 500:
        return "upstream_server"
    return None


def content_attempt_budget(*, failure_kind: str, configured_max_attempts: int) -> int:
    """结构性退化保留三次生成预算，其余内容错误使用节点配置。"""

    if failure_kind in {"json_syntax", "output_degeneration", "tool_arguments_validation"}:
        return max(configured_max_attempts, 3)
    return configured_max_attempts


@dataclass(frozen=True)
class RetryDecision:
    failure_kind: str
    retryable: bool
    can_retry: bool
    attempt_limit: int
    capability_changed: bool = False

    def repeated_output_action(self, previous_repair_mode: str) -> str:
        """最小修补未改变候选时仅升级一次，完整重生成仍重复则停止。"""

        if self.can_retry and previous_repair_mode != "full_regeneration":
            return "full_regeneration"
        return "stop"


@dataclass
class RetryAttemptState:
    attempt: int = 0
    content_failure_counts: dict[str, int] = field(default_factory=dict)
    transient_failure_count: int = 0
    route_health_failure_count: int = 0
    capability_fallback_count: int = 0
    server_output_schema_disabled: bool = False
    last_failure_kind: str = ""
    retry_exhausted: bool = False

    @classmethod
    def restore(cls, checkpoint: dict[str, Any]) -> "RetryAttemptState":
        if not checkpoint:
            return cls()
        counts = {
            str(key): max(0, int(value))
            for key, value in dict(checkpoint.get("content_failure_counts") or {}).items()
        }
        transient_count = max(0, int(checkpoint.get("transient_failure_count") or 0))
        route_count = checkpoint.get("route_health_failure_count")
        if route_count is None:
            raise ValueError("重试检查点缺少 route_health_failure_count")
        return cls(
            attempt=max(0, int(checkpoint.get("item_attempt") or 0)),
            content_failure_counts=counts,
            transient_failure_count=transient_count,
            route_health_failure_count=max(0, int(route_count)),
            capability_fallback_count=max(0, int(checkpoint.get("capability_fallback_count") or 0)),
            server_output_schema_disabled=bool(checkpoint.get("server_output_schema_disabled")),
            last_failure_kind=str(checkpoint.get("last_failure_kind") or checkpoint.get("failure_kind") or ""),
            retry_exhausted=bool(checkpoint.get("retry_exhausted")),
        )

    def checkpoint(self) -> dict[str, Any]:
        return {
            "content_failure_counts": dict(self.content_failure_counts),
            "content_failure_count": sum(self.content_failure_counts.values()),
            "transient_failure_count": self.transient_failure_count,
            "route_health_failure_count": self.route_health_failure_count,
            "capability_fallback_count": self.capability_fallback_count,
            "server_output_schema_disabled": self.server_output_schema_disabled,
            "last_failure_kind": self.last_failure_kind,
            "retry_exhausted": self.retry_exhausted,
        }

    def require_retry_budget(self, *, configured_max_attempts: int) -> None:
        """续跑沿用原候选预算，只有重新生成才开始新预算。"""

        exhausted = self.retry_exhausted
        if self.last_failure_kind in self.content_failure_counts:
            exhausted = exhausted or self.content_failure_counts[self.last_failure_kind] >= content_attempt_budget(
                failure_kind=self.last_failure_kind, configured_max_attempts=configured_max_attempts,
            )
        if self.last_failure_kind in CONCURRENCY_PRESSURE_FAILURE_KINDS:
            exhausted = exhausted or self.transient_failure_count >= max(configured_max_attempts, TRANSIENT_ATTEMPT_BUDGET)
        if exhausted:
            raise RuntimeError("该候选已耗尽重试预算；续跑保留原预算，重新生成才开始新预算")

    def record_failure(
        self,
        *,
        failure_kind: str,
        retryable: bool,
        is_content_error: bool,
        configured_max_attempts: int,
        stop_on_failure: bool = False,
    ) -> RetryDecision:
        self.last_failure_kind = failure_kind
        capability_changed = False
        if (not stop_on_failure and failure_kind == "server_schema_unsupported"
                and not self.server_output_schema_disabled):
            self.server_output_schema_disabled = True
            capability_changed = True

        if retryable and failure_kind in MODEL_ROUTE_HEALTH_FAILURE_KINDS:
            self.route_health_failure_count += 1
        if capability_changed:
            self.capability_fallback_count += 1
            remaining = 1
        elif is_content_error:
            count = self.content_failure_counts.get(failure_kind, 0) + 1
            self.content_failure_counts[failure_kind] = count
            remaining = max(0, content_attempt_budget(
                failure_kind=failure_kind, configured_max_attempts=configured_max_attempts,
            ) - count)
        elif retryable and failure_kind != "server_schema_unsupported":
            self.transient_failure_count += 1
            remaining = max(0, max(configured_max_attempts, TRANSIENT_ATTEMPT_BUDGET) - self.transient_failure_count)
        else:
            remaining = 0
        if stop_on_failure:
            remaining = 0
        self.retry_exhausted = not (retryable and remaining > 0)
        return RetryDecision(
            failure_kind=failure_kind,
            retryable=retryable,
            can_retry=retryable and remaining > 0,
            attempt_limit=self.attempt + remaining,
            capability_changed=capability_changed,
        )
