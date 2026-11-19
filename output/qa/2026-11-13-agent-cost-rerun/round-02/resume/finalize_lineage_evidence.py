"""汇总来源继承收口证据，不执行模型或数据库写入。"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
paths = ["backend/modules/agent_platform/test_generation_source_context.py",
    "backend/tests/agent_platform/test_source_context_reuse_real_data.py",
    "backend/tests/agent_platform/test_source_context_lineage_real_data.py",
    "backend/tests/agent_platform/fixtures/source_context_lineage_run61_real.json.gz"]
files = []
for name in paths:
    path = ROOT / name
    raw = path.read_bytes()
    row = {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if path.suffix == ".py":
        source = path.read_text(encoding="utf-8-sig")
        compile(source, name, "exec")
        row["normalized_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    files.append(row)
suites = list(ET.parse(BASE / "source-lineage-regression.xml").getroot().iter("testsuite"))
tests = {key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
         for key in ("tests", "errors", "failures", "skipped")}
assert tests == {"tests": 24, "errors": 0, "failures": 0, "skipped": 0}
integrity = json.loads((BASE / "integrity-audit.json").read_text(encoding="utf-8"))
authority = json.loads((BASE / "authority-baseline-after-lineage-fix.json").read_text(encoding="utf-8"))
assert integrity["passed"] and authority["status"] == "accepted"
answers = integrity["source_context_reuse_current_extractor"]
assert answers["current_extractor_status"] == "accepted" and answers["count"] == 3
report = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), "status": "production_frozen_after_targeted_regression",
    "files": files, "tests": tests, "business_database_writes": 0, "paid_model_requests": 0,
    "actual_source_lineage": [61, 53], "cpu_revalidated_source_raw_count": 17,
    "actual_approved_answer_count": 3, "approval_origins": {"13": {"run_id": 53, "answers": 2},
        "15": {"run_id": 61, "answers": 1}},
    "contract": ["同一来源链统一验证同用户项目、完整source snapshot、来源专家冻结定义、不可变选定state及逐项raw provenance",
        "原始专业请求身份按其真实父行动pre-state的用户口径重建，未尝试多个hash或复用旧执行协议",
        "逐真实已批准表单核验payload及原话，外层字典与继承来源必须完全匹配；防环、越权、缺答和被改写回执均拒绝",
        "来源raw与归一治理result逐字段复验；历史planning_scopes派生view用当前编译器重建，CPU重验不作为新模型请求",
        "61原缺95细时序不补造；旧32回执和79专业回执规范hash、18历史Run SQLhash全未变"],
    "authority_recompile": {key: authority[key] for key in ("old_scope_fact_count", "current_scope_fact_count",
        "added_scope_fact_ids", "removed_scope_fact_ids", "baseline_result_matches_old")},
    "resume_integrity": {"checks_passed": len(integrity["checks"]), "budget_identity": integrity["budget"]["identity"],
        "actual_wait_seconds": integrity["wait"]["stored_confirmation_wait_seconds"],
        "approval_processing_seconds_still_count_as_execution": integrity["wait"]["approval_processing_time_included_in_execution_seconds"]},
    "preserved_before_fix": ["integrity-audit-before-approval-lineage-fix.json", "authority-baseline-before-lineage-fix.json",
        "integrity-audit-first-hash-serialization-mismatch.json"],
    "qa_corrections": ["首次审计节点JSON排序触发MySQL1038，改为先查轻ID再按PK取JSON，事务只读回滚",
        "行动回执baseline使用规范JSON，历史Run baseline使用SQL CAST JSON字节；分别按原口径核验，不改baseline",
        "首次fixture捕获在datetime序列化前计算摘要报TypeError，改为真实ISO序列化后计算摘要；未产生业务数据"]}
(BASE / "source-context-lineage-closure.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"tests": tests, "files": files}, ensure_ascii=False, indent=2))
