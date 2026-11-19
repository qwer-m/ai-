"""从旧 Runtime 原代码提取算术，与共享策略按真实交错序列做 CPU 对照。"""

import ast
from collections import defaultdict
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
# 旧算术在生产整合前已提取归档；比较基线固定，不从新 Runtime 反推旧策略。
baseline_report = json.loads((BASE / "concurrency-policy-real-sequence-replay.json").read_text(encoding="utf-8"))
source_runtime_sha256 = baseline_report["source_runtime_sha256"]
reference = (BASE / "actual-legacy-concurrency-arithmetic.py").read_text(encoding="utf-8")

policy_path = BASE / "concurrency-draft-tree/backend/modules/agent_platform/retry_policy.py"
spec = importlib.util.spec_from_file_location("concurrency_policy_draft", policy_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
settings = SimpleNamespace(AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES=6, AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES=2)
namespace = {"Any": object, "settings": settings,
    "_is_concurrency_pressure_failure": lambda kind: kind in module.CONCURRENCY_PRESSURE_FAILURE_KINDS}
exec(compile(reference, "actual_legacy_runtime_arithmetic", "exec"), namespace)

fixture = BASE / "concurrency-draft-tree/backend/tests/agent_platform/fixtures/run60_concurrency_signals_real.json.gz"
with gzip.open(fixture, "rt", encoding="utf-8") as stream:
    real = json.load(stream)
groups = defaultdict(list)
for event in real["interleaved_session_signals"]:
    groups[event["scope_key"]].append(event)
old = {key: namespace["legacy_replay"](events) for key, events in groups.items()}
new = []
for key, events in groups.items():
    policy = module.AdaptiveConcurrency.restore(max_concurrency=6, min_concurrency=2,
                                               pressure_threshold=2, recovery_successes=6)
    for event in events:
        adjustment = policy.record_success() if event["status"] == "validated" else policy.record_failure(event["failure_kind"])
        if adjustment:
            new.append({"scope_key": key, "node_id": event["node_id"], "source_sequence": event["source_sequence"],
                        "completed_at": event["completed_at"], **adjustment})
report = {"run_id": 60, "model_requests_sent": 0, "database_writes": 0, "production_files_changed": 0,
          "scope": "真实交错信号的策略CPU重放；不是新模型响应，不宣称在线减少429",
          "source_runtime_sha256": source_runtime_sha256,
          "actual_signal_count": len(real["interleaved_session_signals"]), "actual_429": len(real["rate_limit_signals"]),
          "legacy_adjustments": old, "draft_adjustments": new,
          "timestamp_precision_provenance": real["provenance"]["run60_actual_trace"],
          "run59_events_in_interleaved_replay": 0,
          "separate_run59_auxiliary_test_sessions": len(real["validated_success_signals"]),
          "legacy_arithmetic_sha256": hashlib.sha256(reference.encode()).hexdigest(),
          "legacy_adjustment_count": sum(len(v) for v in old.values()),
          "draft_adjustment_count": len(new)}
assert report["legacy_adjustment_count"] == 0
assert len([item for item in new if item["reason"] == "upstream_pressure"]) == 1
assert len([item for item in new if item["reason"] == "success_recovery"]) == 1
(BASE / "concurrency-policy-real-sequence-replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
(BASE / "actual-legacy-concurrency-arithmetic.py").write_text(reference, encoding="utf-8")
print(json.dumps({"legacy_adjustment_count": report["legacy_adjustment_count"], "draft_adjustments": new}, ensure_ascii=False, indent=2))
