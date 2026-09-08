"""统一测试生成产物的读取边界。"""

from __future__ import annotations

from typing import Any


def persisted_test_generation_result(run: Any) -> dict[str, Any] | None:
    """运行上下文为主；历史输出格式只在此处转换。"""
    for payload in (run.run_context, run.output_payload):
        artifact = dict((payload or {}).get("artifacts") or {}).get("test_generation")
        if isinstance(artifact, dict):
            return artifact
    return None
