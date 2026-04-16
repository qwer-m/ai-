from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.authn.auth import get_current_user  # noqa: E402
from core.ai.ai_client import ai_client  # noqa: E402
from core.db.database import SessionLocal  # noqa: E402
from core.db.models import TestGeneration  # noqa: E402
from main import app  # noqa: E402


CLASSIFIER_PROMPT = """你是一个测试用例分类器。

请对每一条测试用例进行分类，只能选择以下三类之一：

1. FLOW（流程类）
- 涉及完整业务流程或多步骤路径
- 包含页面跳转 / 学习路径 / 用户操作链路
- 例如：进入课程 → 学习 → 做题 → 完成 → 返回

2. STATE（状态/数据类）
- 涉及数据加载、状态变化、切换、刷新
- 例如：切换年级、版本、页面状态、缓存、接口数据

3. UI（展示/低风险类）
- 仅涉及UI展示、文案、样式、非空校验
- 不影响核心流程
- 例如：按钮显示、颜色、提示文案、输入框非空

规则：
- 优先判断 FLOW，其次 STATE，最后 UI
- 如果一个case同时涉及流程和UI，归类为 FLOW
- 只输出 JSON 数组，不要解释

格式：
[
  {"case_id": "...", "type": "FLOW|STATE|UI"}
]
"""

FLOW_KW = [
    "流程",
    "路径",
    "进入",
    "返回",
    "跳转",
    "完成",
    "学习",
    "练习",
    "提交",
    "闭环",
    "跨页面",
    "跨模块",
]
STATE_KW = [
    "状态",
    "切换",
    "加载",
    "刷新",
    "缓存",
    "接口",
    "请求",
    "数据",
    "恢复",
    "中断",
    "重试",
    "并发",
    "竞态",
]
UI_KW = [
    "按钮",
    "文案",
    "样式",
    "颜色",
    "展示",
    "提示",
    "弹窗",
    "图标",
    "非空",
    "UI",
    "页面元素",
]


@dataclass
class Seed:
    project_id: int
    user_id: int
    requirement: str
    source_id: int


def _extract_gen_diag(stream_text: str) -> dict[str, Any]:
    by_kind: dict[str, Any] = {}
    marker = "GEN_DIAG:"
    for line in (stream_text or "").splitlines():
        line = line.strip()
        if marker not in line:
            continue
        for part in line.split(marker)[1:]:
            payload = part.strip()
            if not payload:
                continue
            if not payload.startswith("{"):
                brace = payload.find("{")
                if brace < 0:
                    continue
                payload = payload[brace:]
            try:
                obj = json.loads(payload)
            except Exception:
                end = payload.rfind("}")
                if end <= 0:
                    continue
                try:
                    obj = json.loads(payload[: end + 1])
                except Exception:
                    continue
            kind = str(obj.get("kind") or "").strip()
            if kind:
                by_kind[kind] = obj
    return by_kind


def _load_generation_cases(generation_id: int) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(TestGeneration).filter(TestGeneration.id == int(generation_id)).first()
        if not row:
            return []
        data = json.loads(row.generated_result or "[]")
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    except Exception:
        return []
    finally:
        db.close()


def _safe_json_array(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
    except Exception:
        pass
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, list):
                return [x for x in obj if isinstance(x, dict)]
        except Exception:
            return []
    return []


def _heuristic_case_type(case: dict[str, Any]) -> str:
    steps = case.get("steps") or []
    text = " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("test_module") or ""),
            " ".join(str(x or "") for x in steps if x),
            str(case.get("expected_result") or ""),
        ]
    ).lower()
    flow_hit = any(k.lower() in text for k in FLOW_KW) or len([x for x in steps if str(x).strip()]) >= 3
    state_hit = any(k.lower() in text for k in STATE_KW)
    ui_hit = any(k.lower() in text for k in UI_KW)
    if flow_hit:
        return "FLOW"
    if state_hit:
        return "STATE"
    if ui_hit:
        return "UI"
    return "UI"


def classify_cases(cases: list[dict[str, Any]]) -> dict[str, str]:
    payload = []
    for c in cases:
        payload.append(
            {
                "case_id": str(c.get("id") or ""),
                "description": str(c.get("description") or ""),
                "test_module": str(c.get("test_module") or ""),
                "steps": [str(x or "") for x in (c.get("steps") or []) if str(x or "").strip()],
                "expected_result": str(c.get("expected_result") or ""),
            }
        )
    user_input = f"{CLASSIFIER_PROMPT}\n\n待分类测试用例：\n{json.dumps(payload, ensure_ascii=False)}"
    raw = ai_client.generate_response(
        user_input=user_input,
        system_prompt="你是严格的测试用例分类器，只输出 JSON 数组。",
        task_type="general",
    )
    parsed = _safe_json_array(raw)
    out: dict[str, str] = {}
    for item in parsed:
        cid = str(item.get("case_id") or "").strip()
        typ = str(item.get("type") or "").strip().upper()
        if cid and typ in {"FLOW", "STATE", "UI"}:
            out[cid] = typ
    for c in cases:
        cid = str(c.get("id") or "").strip()
        if not cid:
            continue
        if cid not in out:
            out[cid] = _heuristic_case_type(c)
    return out


def _select_seeds(seed_count: int) -> list[Seed]:
    db = SessionLocal()
    try:
        rows = (
            db.query(
                TestGeneration.id,
                TestGeneration.project_id,
                TestGeneration.user_id,
                TestGeneration.requirement_text,
            )
            .filter(TestGeneration.requirement_text.isnot(None))
            .filter(TestGeneration.project_id.isnot(None))
            .filter(TestGeneration.user_id.isnot(None))
            .order_by(TestGeneration.id.desc())
            .limit(600)
            .all()
        )
        selected: list[Seed] = []
        seen: set[str] = set()
        for row in rows:
            requirement = str(row.requirement_text or "").strip()
            if len(requirement) < 80:
                continue
            pid = int(row.project_id or 0)
            uid = int(row.user_id or 0)
            if pid <= 0 or uid <= 0:
                continue
            key = hashlib.md5(requirement.encode("utf-8", errors="ignore")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            selected.append(Seed(project_id=pid, user_id=uid, requirement=requirement, source_id=int(row.id)))
            if len(selected) >= max(1, int(seed_count)):
                break
        return selected
    finally:
        db.close()


def _ratio(n: int, d: int) -> float:
    return round(float(n) / float(max(1, d)), 4)


def _structure_signals(cases: list[dict[str, Any]]) -> dict[str, int]:
    cross_page_kw = ["跳转", "返回", "进入", "跨页面", "跨模块", "导航", "切换页面"]
    state_kw = ["状态", "切换", "加载", "刷新", "缓存", "请求", "数据", "恢复", "中断", "重试", "并发", "竞态"]
    cross_page = 0
    multi_step = 0
    state_transition = 0
    for c in cases:
        steps = [str(x or "") for x in (c.get("steps") or []) if str(x or "").strip()]
        text = " ".join(
            [
                str(c.get("description") or ""),
                str(c.get("test_module") or ""),
                " ".join(steps),
                str(c.get("expected_result") or ""),
            ]
        ).lower()
        if len(steps) >= 3:
            multi_step += 1
        if any(k.lower() in text for k in cross_page_kw):
            cross_page += 1
        if any(k.lower() in text for k in state_kw):
            state_transition += 1
    return {
        "cross_page_case_count": cross_page,
        "multi_step_case_count": multi_step,
        "state_transition_case_count": state_transition,
    }


def run_once(seed: Seed, expected_count: int) -> dict[str, Any]:
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": int(seed.user_id)})()
    try:
        with TestClient(app) as client:
            payload = {
                "project_id": str(seed.project_id),
                "doc_type": "requirement",
                "compress": "false",
                "expected_count": str(int(expected_count)),
                "enable_sample_pool_feedback": "true",
                "force": "false",
                "append": "false",
                "current_biz_key": "",
                "only_current_biz": "false",
                "multi_pass": "false",
                "generation_mode": "single_pass",
                "requirement_text": seed.requirement,
            }
            resp = client.post("/api/generate-tests-stream", data=payload, headers={"Host": "localhost"})
            body = resp.text or ""
            diag = _extract_gen_diag(body)
            persisted = dict(diag.get("generation_persisted") or {})
            generation_id = int(persisted.get("generation_id") or 0)
            cases = _load_generation_cases(generation_id) if generation_id else []
            mapping = classify_cases(cases) if cases else {}

            total = len(cases)
            p1_count = sum(1 for c in cases if str(c.get("priority") or "").upper().strip() == "P1")
            p2_count = sum(1 for c in cases if str(c.get("priority") or "").upper().strip() == "P2")
            flow_count = 0
            state_count = 0
            ui_count = 0
            for c in cases:
                cid = str(c.get("id") or "").strip()
                typ = mapping.get(cid) or _heuristic_case_type(c)
                if typ == "FLOW":
                    flow_count += 1
                elif typ == "STATE":
                    state_count += 1
                else:
                    ui_count += 1
            structure = _structure_signals(cases)
            control_state = dict(diag.get("feedback_control_state") or {})
            return {
                "seed": {
                    "source_id": int(seed.source_id),
                    "project_id": int(seed.project_id),
                    "user_id": int(seed.user_id),
                    "requirement_length": len(seed.requirement),
                    "requirement_preview": seed.requirement[:220],
                },
                "http_status": int(resp.status_code),
                "generation_id": generation_id,
                "total": total,
                "priority": {"P1": p1_count, "P2": p2_count},
                "type_count": {"FLOW": flow_count, "STATE": state_count, "UI": ui_count},
                "ratios": {
                    "p1_ratio": _ratio(p1_count, total),
                    "ui_ratio": _ratio(ui_count, total),
                    "flow_ratio": _ratio(flow_count, total),
                },
                "structure": structure,
                "control_signature": {
                    "must_cover_rules_count": len(control_state.get("must_cover_rules") or []),
                    "must_have_scenarios_count": len(control_state.get("must_have_scenarios") or []),
                    "forbidden_patterns_count": len(control_state.get("forbidden_patterns") or []),
                    "soft_constraints_count": len(control_state.get("soft_constraints") or []),
                    "quality_fix_hints_count": len(control_state.get("quality_fix_hints") or []),
                    "rule_quota_keys_count": len((control_state.get("rule_quota") or {}).keys()),
                },
                "errors": [ln.strip() for ln in body.splitlines() if "Error:" in ln or "Exception occurred:" in ln],
            }
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = sum(int(r.get("total") or 0) for r in rows)
    p1 = sum(int((r.get("priority") or {}).get("P1") or 0) for r in rows)
    ui = sum(int((r.get("type_count") or {}).get("UI") or 0) for r in rows)
    flow = sum(int((r.get("type_count") or {}).get("FLOW") or 0) for r in rows)
    state = sum(int((r.get("type_count") or {}).get("STATE") or 0) for r in rows)
    cross_page = sum(int((r.get("structure") or {}).get("cross_page_case_count") or 0) for r in rows)
    multi_step = sum(int((r.get("structure") or {}).get("multi_step_case_count") or 0) for r in rows)
    state_transition = sum(int((r.get("structure") or {}).get("state_transition_case_count") or 0) for r in rows)
    return {
        "runs": len(rows),
        "total_cases": total_cases,
        "p1_count": p1,
        "flow_count": flow,
        "state_count": state,
        "ui_count": ui,
        "p1_ratio": _ratio(p1, total_cases),
        "ui_ratio": _ratio(ui, total_cases),
        "flow_ratio": _ratio(flow, total_cases),
        "structure": {
            "cross_page_case_count": cross_page,
            "multi_step_case_count": multi_step,
            "state_transition_case_count": state_transition,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", type=str, required=True)
    parser.add_argument("--seed-count", type=int, default=3)
    parser.add_argument("--runs", type=int, default=0)
    parser.add_argument("--project-id", type=int, default=0)
    parser.add_argument("--user-id", type=int, default=0)
    parser.add_argument("--requirement-file", type=str, default="")
    parser.add_argument("--expected-count", type=int, default=12)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    os.environ["AI_TEMPERATURE"] = "0"
    seeds: list[Seed]
    req_file = str(args.requirement_file or "").strip()
    if int(args.project_id) > 0 and int(args.user_id) > 0 and req_file:
        requirement = Path(req_file).read_text(encoding="utf-8").strip()
        repeat = max(1, int(args.runs or 1))
        seeds = [
            Seed(
                project_id=int(args.project_id),
                user_id=int(args.user_id),
                requirement=requirement,
                source_id=-(idx + 1),
            )
            for idx in range(repeat)
        ]
    else:
        seeds = _select_seeds(seed_count=max(1, int(args.seed_count)))
    rows = [run_once(seed, expected_count=max(1, int(args.expected_count))) for seed in seeds]
    payload = {
        "label": str(args.label),
        "run_at": datetime.now().isoformat(),
        "config": {
            "temperature": 0,
            "generation_mode": "single_pass",
            "expected_count": max(1, int(args.expected_count)),
            "seed_count": len(seeds),
        },
        "results": rows,
        "summary": summarize(rows),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out))
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
