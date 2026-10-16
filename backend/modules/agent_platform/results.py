"""统一运行输出与测试生成产物的读取边界。"""

from __future__ import annotations

from typing import Any


def persisted_run_output(run: Any) -> dict[str, Any]:
    """运行产物只持久化在上下文，对外读取时组装完整工作流输出。"""
    output = dict(run.output_payload or {})
    if "result" in output and "artifacts" not in output:
        output["artifacts"] = dict((run.run_context or {}).get("artifacts") or {})
    return output


def persisted_test_generation_result(run: Any) -> dict[str, Any] | None:
    """运行上下文为主；历史输出格式只在此处转换。"""
    for payload in (run.run_context, run.output_payload):
        artifact = dict((payload or {}).get("artifacts") or {}).get("test_generation")
        if isinstance(artifact, dict):
            return artifact
    return None
