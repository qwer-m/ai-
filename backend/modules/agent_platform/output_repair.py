from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable


class OutputValidationError(ValueError):
    """明确的业务输出差异，可反馈给 Agent，但不要求绑定外层重试策略。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = deepcopy(details or {})


class OutputRepairError(OutputValidationError):
    """业务校验在错误源头提供修复策略及结构化差异，不以文案作为协议。"""

    def __init__(
        self, message: str, *, strategy_key: str, details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.strategy_key = strategy_key


@dataclass(frozen=True)
class OutputRepairStrategy:
    build_context: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
    feedback: Callable[[dict[str, Any], dict[str, Any]], str | None]


def build_output_repair_guidance(
    *,
    strategy: OutputRepairStrategy,
    error: OutputRepairError,
    input_payload: dict[str, Any],
    candidate_output: dict[str, Any] | None,
) -> dict[str, Any]:
    """同会话反馈与外层重试共用原始事实修复策略，不改动输入或候选。"""

    details = deepcopy(error.details)
    # 已生成的说明不是新的领域差异，避免再次解释时嵌套旧反馈。
    details.pop("repair_guidance", None)
    feedback = strategy.feedback(deepcopy(input_payload), deepcopy(details))
    context = (
        strategy.build_context(deepcopy(input_payload), deepcopy(candidate_output), deepcopy(details))
        if candidate_output is not None else {}
    )
    if feedback is not None and not isinstance(feedback, str):
        raise TypeError("修复策略反馈必须是字符串或空值")
    if not isinstance(context, dict):
        raise TypeError("修复策略上下文必须是对象")
    return {"strategy_key": error.strategy_key, "feedback": feedback, "context": deepcopy(context)}


def repairable_output(strategy_key: str) -> Callable:
    """在既有后处理边界绑定策略，业务层已提供的详细差异原样保留。"""

    def decorate(handler: Callable) -> Callable:
        @wraps(handler)
        def checked(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return handler(*args, **kwargs)
            except OutputRepairError:
                raise
            except OutputValidationError as exc:
                raise OutputRepairError(str(exc), strategy_key=strategy_key, details=exc.details) from exc
            except ValueError as exc:
                raise OutputRepairError(str(exc), strategy_key=strategy_key) from exc
        return checked
    return decorate
