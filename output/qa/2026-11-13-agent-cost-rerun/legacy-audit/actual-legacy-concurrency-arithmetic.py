def legacy_replay(events):
    max_concurrency, min_concurrency = 6, 2
    effective_concurrency = 6
    successful_since_concurrency_adjustment = 0
    pressure_failures_since_concurrency_adjustment = 0
    concurrency_adjustments = []
    completed_by_index = {}
    def adjust_concurrency(*, target: int, reason: str, failure_kind: str='') -> None:
        """记录实际并发变化，使重试调度与诊断共享同一事实。"""
        nonlocal effective_concurrency, successful_since_concurrency_adjustment
        nonlocal pressure_failures_since_concurrency_adjustment
        normalized_target = min(max_concurrency, max(min_concurrency, int(target)))
        if normalized_target == effective_concurrency:
            return
        previous_concurrency = effective_concurrency
        effective_concurrency = normalized_target
        successful_since_concurrency_adjustment = 0
        pressure_failures_since_concurrency_adjustment = 0
        adjustment = {'from': previous_concurrency, 'to': effective_concurrency, 'reason': reason, 'failure_kind': failure_kind, 'completed_instances': len(completed_by_index)}
        concurrency_adjustments.append(adjustment)
        del concurrency_adjustments[:-20]
    for event in events:
        if event["status"] == "validated":
            successful_since_concurrency_adjustment += 1
            pressure_failures_since_concurrency_adjustment = max(
                0,
                pressure_failures_since_concurrency_adjustment - 1,
            )
            recovery_successes = int(
                settings.AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES
            )
            if (
                effective_concurrency < max_concurrency
                and successful_since_concurrency_adjustment >= recovery_successes
            ):
                adjust_concurrency(
                    target=effective_concurrency + 1,
                    reason="success_recovery",
                )

        else:
            retryable, failure_kind = True, event["failure_kind"]
            if retryable and _is_concurrency_pressure_failure(failure_kind):
                pressure_failures_since_concurrency_adjustment += 1
                pressure_threshold = int(
                    settings.AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES
                )
                if (
                    pressure_failures_since_concurrency_adjustment
                    >= pressure_threshold
                ):
                    adjust_concurrency(
                        target=effective_concurrency - 1,
                        reason="upstream_pressure",
                        failure_kind=failure_kind,
                    )

    return concurrency_adjustments
