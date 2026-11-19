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
