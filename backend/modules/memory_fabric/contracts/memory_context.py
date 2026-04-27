from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryContext:
    """
    Scope for every memory access.

    user_id + project_id are required to avoid cross-project/cross-user leakage.
    """

    user_id: str
    project_id: str
    run_id: str
    request_id: str

    @classmethod
    def from_runtime(
        cls,
        *,
        user_id: int | str | None,
        project_id: int | str | None,
        run_id: str | None,
        request_id: str | None,
    ) -> "MemoryContext":
        return cls(
            user_id=str(user_id or "0"),
            project_id=str(project_id or "0"),
            run_id=str(run_id or request_id or "legacy-run"),
            request_id=str(request_id or run_id or "legacy-request"),
        )

    def scoped_key(self, raw_key: str) -> str:
        return (
            f"u:{self.user_id}:p:{self.project_id}:"
            f"run:{self.run_id}:req:{self.request_id}:k:{str(raw_key or '').strip()}"
        )

