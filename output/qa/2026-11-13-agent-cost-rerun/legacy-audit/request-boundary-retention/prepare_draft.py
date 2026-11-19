"""只在QA目录生成取消轻边界裁剪的小补丁，在线63代码保持不变。"""

import difflib
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]


def replace_once(source, before, after):
    assert source.count(before) == 1, before
    return source.replace(before, after)


changes = {}
path = "backend/modules/agent_platform/request_diagnostics.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
after = replace_once(before, "父子 Agent 共享有限长度的请求记录，失败时复用现有节点诊断落库。",
                     "父子 Agent 共享本次执行全部轻量请求边界；不保存输入输出正文。")
after = replace_once(after, "def __init__(self, *, journal: Any = None, max_records: int = 100,",
                     "def __init__(self, *, journal: Any = None,")
after = replace_once(after, "        self._max_records = max(1, min(int(max_records), 200))\n", "")
helper = '''    def _record_started(self, context: Any, item: dict[str, Any]) -> None:
        """活动请求与持久轻边界共用同一对象，后续回执更新不能丢失早期行。"""
        self._active[context] = (item, perf_counter())
        self._records.append(item)
        self._emit("request_started", item)

'''
after = replace_once(after, "    def start(self, context: Any, agent: Any, system_prompt: str | None, input_items: list[Any]) -> None:\n",
                     helper + "    def start(self, context: Any, agent: Any, system_prompt: str | None, input_items: list[Any]) -> None:\n")
after = replace_once(after, '''        self._active[context] = (item, perf_counter())
        self._records.append(item)
        self._records = self._records[-self._max_records:]
        self._emit("request_started", item)
''', "        self._record_started(context, item)\n")
changes[path] = (before, after)
path = "backend/modules/agent_platform/sdk_adapter.py"
before = (ROOT / path).read_text(encoding="utf-8-sig")
after = replace_once(before, '''    request_diagnostics = request_diagnostics or ModelRequestDiagnostics(
        journal=journal, max_records=(request_budget or {}).get("attempted_requests", 100),
    )
''', "    request_diagnostics = request_diagnostics or ModelRequestDiagnostics(journal=journal)\n")
changes[path] = (before, after)
changes["backend/tests/agent_platform/test_request_boundary_retention_real_data.py"] = (
    "", (BASE / "retention_tests_draft.py").read_text(encoding="utf-8"))
patch, files = [], []
for path, (before, after) in changes.items():
    compile(after, path, "exec")
    target = BASE / "draft-tree" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8", newline="\n")
    patch.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/" + path if before else "/dev/null", tofile="b/" + path))
    files.append({"path": path, "before_normalized_sha256": hashlib.sha256(before.encode()).hexdigest(),
                  "after_normalized_sha256": hashlib.sha256(after.encode()).hexdigest()})
(BASE / "request-boundary-retention-draft.patch").write_text("".join(patch), encoding="utf-8", newline="\n")
(BASE / "draft-manifest.json").write_text(json.dumps({"status": "draft_only_not_applied",
    "production_files_edited": 0, "files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(files, ensure_ascii=False, indent=2))
