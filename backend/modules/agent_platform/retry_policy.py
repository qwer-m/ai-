"""与执行方式无关的 Agent 重试预算和断点状态。"""

from dataclasses import dataclass, field
from typing import Any


TRANSIENT_ATTEMPT_BUDGET = 4
CONCURRENCY_PRESSURE_FAILURE_KINDS = frozenset(
    {"timeout", "connection", "rate_limit", "upstream_transient", "upstream_server"}
)
MODEL_ROUTE_HEALTH_FAILURE_KINDS = frozenset(
    {*CONCURRENCY_PRESSURE_FAILURE_KINDS, "empty_output", "json_syntax", "output_degeneration"}
)


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
    model_thinking_disabled: bool = False
    last_failure_kind: str = ""
    retry_exhausted: bool = False

    @classmethod
    def restore(cls, checkpoint: dict[str, Any]) -> "RetryAttemptState":
        counts = {
            str(key): max(0, int(value))
            for key, value in dict(checkpoint.get("content_failure_counts") or {}).items()
        }
        transient_count = max(0, int(checkpoint.get("transient_failure_count") or 0))
        route_count = checkpoint.get("route_health_failure_count")
        if route_count is None:
            # 旧检查点未单列路由计数，历史失败仍计入同一预算。
            route_count = transient_count + sum(
                count for kind, count in counts.items()
                if kind in MODEL_ROUTE_HEALTH_FAILURE_KINDS
            )
        return cls(
            attempt=max(0, int(checkpoint.get("item_attempt") or 0)),
            content_failure_counts=counts,
            transient_failure_count=transient_count,
            route_health_failure_count=max(0, int(route_count)),
            capability_fallback_count=max(0, int(checkpoint.get("capability_fallback_count") or 0)),
            server_output_schema_disabled=bool(checkpoint.get("server_output_schema_disabled")),
            model_thinking_disabled=bool(checkpoint.get("model_thinking_disabled")),
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
            "model_thinking_disabled": self.model_thinking_disabled,
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
    ) -> RetryDecision:
        self.last_failure_kind = failure_kind
        capability_changed = False
        if failure_kind == "server_schema_unsupported" and not self.server_output_schema_disabled:
            self.server_output_schema_disabled = True
            capability_changed = True
        elif failure_kind == "empty_output" and not self.model_thinking_disabled:
            self.model_thinking_disabled = True
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
        self.retry_exhausted = not (retryable and remaining > 0)
        return RetryDecision(
            failure_kind=failure_kind,
            retryable=retryable,
            can_retry=retryable and remaining > 0,
            attempt_limit=self.attempt + remaining,
            capability_changed=capability_changed,
        )
