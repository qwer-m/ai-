"""记录已应用的人工暂停观测修复，不连接业务数据库或调用模型。"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
paths = ["backend/modules/agent_platform/sdk_adapter.py",
    "backend/modules/agent_platform/runtime.py",
    "backend/tests/agent_platform/test_pause_observability_real_data.py",
    "backend/tests/agent_platform/fixtures/pause_observability_cross_source_real.json.gz"]
files = []
for relative in paths:
    path = ROOT / relative
    raw = path.read_bytes()
    row = {"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if path.suffix == ".py":
        source = path.read_text(encoding="utf-8-sig")
        compile(source, relative, "exec")
        row["normalized_sha256"] = hashlib.sha256(source.encode()).hexdigest()
        row["lines"] = len(source.splitlines())
    files.append(row)

junit = ET.parse(BASE / "pause-observability-applied-tests.xml").getroot()
suites = list(junit.iter("testsuite"))
summary = {key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
           for key in ("tests", "failures", "errors", "skipped")}
summary["suite_seconds"] = sum(float(suite.attrib.get("time", "0")) for suite in suites)
assert summary["tests"] == 92 and summary["failures"] == summary["errors"] == summary["skipped"] == 0
report = {
    "observed_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "applied_after_root_confirmed_run61_terminal",
    "files": files,
    "tests": summary,
    "new_real_metadata_tests": 9,
    "additional_existing_runtime_retry_and_failure_tests": 83,
    "paid_model_requests": 0,
    "business_database_writes": 0,
    "does_not_reconstruct_run61_missing_95_requests": True,
    "data_provenance": {
        "diagnostics": "Run59 Node404 真实SDK保留的最后200请求、182工具调用、32工具观测",
        "pause_control": "Run61 Approval15实际审批items，仅作为异源控制题目",
        "missing_before_pause": "round-02/final-snapshot.json首次waiting_approval不可变快照，95请求总账保留但三组诊断缺失",
        "test_partition": "真实59元数据分成不重叠切片验证暂停/恢复透传，不宣称是61的各执行阶段",
    },
    "source_review": {
        "sdk_top_level_boundary": "run_agent_async每次顶层调用新建ModelRequestDiagnostics与AutonomousToolLoop；tool_call_start只取本次上下文区间",
        "nested_sessions": "专业子会话共享当前顶层diagnostics，但只有最外层运行时落库一次，嵌套异常附件覆盖同一批记录，不触发多次追加",
        "same_node_human_resume": "_prepare_node_execution对waiting_approval复用同Node；暂停和成功分支追加当前调用局部观测",
        "new_node_retry": "既有restorable_sdk_state排除三组诊断；新Node只保存本次阶段，原Node不修改",
        "budget": "只复制观测，不执行record_failure/interrupted_requests/record_agent_usage或修改action_checkpoint、action_usage、usage",
    },
    "preserved_first_failure": "pause-observability-first-source-assumption-failure.log及pause-fixture-resumed-live-node-before-first-pause-source-fix.json.gz",
}
(BASE / "pause-observability-applied-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"tests": summary, "files": files}, ensure_ascii=False, indent=2))
