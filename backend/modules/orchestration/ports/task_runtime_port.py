"""Task runtime abstraction for background dispatch and status lookup."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TaskRuntimePort(ABC):
    """Port for task queue operations used by routers and domain services."""

    @abstractmethod
    def dispatch(self, *, task_name: str, kwargs: dict[str, Any] | None = None) -> str:
        """Queue a task and return task id."""

    @abstractmethod
    def get_status(self, *, task_id: str) -> dict[str, Any]:
        """Return unified task status payload."""

