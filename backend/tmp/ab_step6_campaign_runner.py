from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import statistics

from fastapi.testclient import TestClient

import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.authn.auth import get_current_user  # noqa: E402
from core.db.database import SessionLocal  # noqa: E402
from core.db.models import KnowledgeDocument, TestGeneration  # noqa: E402
from main import app  # noqa: E402
from modules.test_generation_components.control.build_feedback_control_state import (  # noqa: E402
    build_feedback_control_state,
)


UI_WORDS = ["按钮", "页面", "展示", "文案", "弹窗", "布局", "样式", "颜色", "空状态", "入口", "界面", "UI", "page", "button"]
FLOW_WORDS = ["流程", "状态", "异常", "同步", "回滚", "重试", "并发", "事务", "恢复", "一致性", "权限", "鉴权", "超时", "幂等", "state", "retry"]

UNSTABLE_DELTA_THRESHOLD = -0.3


def text_features(text: str) -> dict[str, Any]:
    raw = text or ""
    low = raw.lower()
    return {
        "length": len(raw),
        "line_count": raw.count("\n") + 1,
        "ui_hits": sum(low.count(k.lower()) for k in UI_WORDS),
        "flow_hits": sum(low.count(k.lower()) for k in FLOW_WORDS),
        "hash": hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest(),
    }


def classify_category(feat: dict[str, Any]) -> str:
    if int(feat.get("ui_hits", 0)) >= 3:
        return "noise_ui"
    if int(feat.get("length", 0)) >= 2500 or int(feat.get("line_count", 0)) >= 120 or int(feat.get("flow_hits", 0)) >= 6:
        return "complex"
    return "normal"


def collect_seed_docs(limit: int = 6000) -> dict[str, list[dict[str, Any]]]:
    db = SessionLocal()
    try:
        kd_rows = (
            db.query(
                KnowledgeDocument.id,
                KnowledgeDocument.project_id,
                KnowledgeDocument.user_id,
                KnowledgeDocument.doc_type,
                KnowledgeDocument.filename,
                KnowledgeDocument.content,
                KnowledgeDocument.created_at,
            )
            .filter(KnowledgeDocument.content.isnot(None))
            .order_by(KnowledgeDocument.id.desc())
            .limit(limit)
            .all()
        )
        tg_rows = (
            db.query(
                TestGeneration.id,
                TestGeneration.project_id,
                TestGeneration.user_id,
                TestGeneration.requirement_text,
                TestGeneration.created_at,
            )
            .filter(TestGeneration.requirement_text.isnot(None))
            .order_by(TestGeneration.id.desc())
            .limit(limit)
            .all()
        )
        grouped: dict[str, list[dict[str, Any]]] = {"normal": [], "complex": [], "noise_ui": []}
        seen_hash: set[str] = set()
        for row in kd_rows:
            text = (row.content or "").strip()
            if len(text) < 120:
                continue
            feat = text_features(text)
            h = str(feat["hash"])
            if h in seen_hash:
                continue
            seen_hash.add(h)
            category = classify_category(feat)
            grouped[category].append(
                {
                    "source": "knowledge_document",
                    "doc_id": int(row.id),
                    "project_id": int(row.project_id or 0),
                    "user_id": int(row.user_id or 0),
                    "doc_type": str(row.doc_type or ""),
                    "filename": str(row.filename or ""),
                    "requirement": text,
                    "created_at": str(row.created_at),
                    "features": feat,
                }
            )
        for row in tg_rows:
            text = (row.requirement_text or "").strip()
            if len(text) < 80:
                continue
            feat = text_features(text)
            h = str(feat["hash"])
            if h in seen_hash:
                continue
            seen_hash.add(h)
            category = classify_category(feat)
            grouped[category].append(
                {
                    "source": "test_generation",
                    "doc_id": int(row.id),
                    "project_id": int(row.project_id or 0),
                    "user_id": int(row.user_id or 0),
                    "doc_type": "requirement_text",
                    "filename": f"test_generation_{int(row.id)}.txt",
                    "requirement": text,
                    "created_at": str(row.created_at),
                    "features": feat,
                }
            )
        grouped["normal"].sort(key=lambda x: (x["features"]["length"], x["features"]["ui_hits"]))
        grouped["complex"].sort(key=lambda x: (-x["features"]["flow_hits"], -x["features"]["length"]))
        grouped["noise_ui"].sort(key=lambda x: (-x["features"]["ui_hits"], x["features"]["length"]))
        return grouped
    finally:
        db.close()


def _slice_complex_requirement(text: str) -> list[str]:
    raw = str(text or "").strip()
    if len(raw) < 1200:
        return []
    slices: list[str] = []
    window = 2200
    head = raw[:window].strip()
    if len(head) >= 900:
        slices.append(head)
    mid = len(raw) // 2
    start = max(0, mid - (window // 2))
    end = min(len(raw), start + window)
    middle = raw[start:end].strip()
    if len(middle) >= 900:
        slices.append(middle)
    tail = raw[-window:].strip()
    if len(tail) >= 900:
        slices.append(tail)
    uniq: list[str] = []
    seen: set[str] = set()
    for item in slices:
        h = hashlib.md5(item.encode("utf-8", errors="ignore")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        uniq.append(item)
    return uniq


def augment_complex_pool(
    grouped: dict[str, list[dict[str, Any]]],
    *,
    target_size: int,
) -> dict[str, int]:
    complex_pool = list(grouped.get("complex") or [])
    if len(complex_pool) >= int(target_size):
        grouped["complex"] = complex_pool
        return {"before": len(complex_pool), "after": len(complex_pool), "added": 0}

    seen_hash = {str((item.get("features") or {}).get("hash") or "") for item in complex_pool}
    candidates = [*(grouped.get("complex") or []), *(grouped.get("noise_ui") or []), *(grouped.get("normal") or [])]
    candidates.sort(
        key=lambda x: (
            int((x.get("features") or {}).get("flow_hits") or 0),
            int((x.get("features") or {}).get("length") or 0),
        ),
        reverse=True,
    )
    before = len(complex_pool)
    for seed in candidates:
        if len(complex_pool) >= int(target_size):
            break
        for idx, chunk in enumerate(_slice_complex_requirement(str(seed.get("requirement") or "")), start=1):
            feat = text_features(chunk)
            h = str(feat.get("hash") or "")
            if not h or h in seen_hash:
                continue
            if int(feat.get("flow_hits") or 0) < 3 and int(feat.get("length") or 0) < 1400:
                continue
            derived = dict(seed)
            derived["requirement"] = chunk
            derived["features"] = feat
            derived["source"] = f"{seed.get('source')}_complex_slice"
            derived["filename"] = f"{seed.get('filename')}#slice-{idx}"
            derived["derived_complex"] = True
            complex_pool.append(derived)
            seen_hash.add(h)
            if len(complex_pool) >= int(target_size):
                break
    grouped["complex"] = complex_pool
    return {"before": before, "after": len(complex_pool), "added": max(0, len(complex_pool) - before)}


def _state_signature(state: Any) -> dict[str, Any]:
    obj = state.to_dict() if hasattr(state, "to_dict") else (state if isinstance(state, dict) else {})
    if not isinstance(obj, dict):
        obj = {}
    return {
        "must_cover_rules_count": len(obj.get("must_cover_rules") or []),
        "must_have_scenarios_count": len(obj.get("must_have_scenarios") or []),
        "forbidden_patterns_count": len(obj.get("forbidden_patterns") or []),
        "soft_constraints_count": len(obj.get("soft_constraints") or []),
        "quality_fix_hints_count": len(obj.get("quality_fix_hints") or []),
        "rule_quota_keys_count": len((obj.get("rule_quota") or {}).keys()),
    }


def eval_treatment(seed: dict[str, Any], db: Any) -> dict[str, Any]:
    project_id = int(seed.get("project_id") or 0)
    user_id = int(seed.get("user_id") or 0)
    requirement = str(seed.get("requirement") or "")
    off_state = build_feedback_control_state(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=requirement,
        enable_priority_sample_pool=False,
    )
    on_state = build_feedback_control_state(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=requirement,
        enable_priority_sample_pool=True,
    )
    off_sig = _state_signature(off_state)
    on_sig = _state_signature(on_state)
    off_meta = dict((off_state.to_dict() if hasattr(off_state, "to_dict") else {}).get("source_meta") or {})
    on_meta = dict((on_state.to_dict() if hasattr(on_state, "to_dict") else {}).get("source_meta") or {})
    changed = any(int(on_sig.get(k, 0)) != int(off_sig.get(k, 0)) for k in on_sig.keys())
    return {
        "treated_pred": bool(changed),
        "off_control_sig": off_sig,
        "on_control_sig": on_sig,
        "off_control_meta": off_meta,
        "on_control_meta": on_meta,
    }


def _extract_gen_diag(stream_text: str) -> dict[str, Any]:
    by_kind: dict[str, Any] = {}
    review_tables: list[dict[str, Any]] = []
    marker = "GEN_DIAG:"
    for line in stream_text.splitlines():
        line = line.strip()
        if marker not in line:
            continue
        for part in line.split(marker)[1:]:
            payload = part.strip()
            if not payload:
                continue
            # Best-effort extraction when GEN_DIAG is concatenated after non-JSON text.
            if not payload.startswith("{"):
                brace = payload.find("{")
                if brace < 0:
                    continue
                payload = payload[brace:]
            try:
                obj = json.loads(payload)
            except Exception:
                # Try to trim trailing noise after last JSON object brace.
                end = payload.rfind("}")
                if end > 0:
                    try:
                        obj = json.loads(payload[: end + 1])
                    except Exception:
                        continue
                else:
                    continue
            kind = str(obj.get("kind") or "")
            if not kind:
                continue
            if kind == "review_decision_table":
                review_tables.append(obj)
                continue
            by_kind[kind] = obj
    if review_tables:
        by_kind["review_decision_table"] = review_tables[-1]
    return by_kind


def _tail_status(stream_text: str, n: int = 8) -> list[str]:
    lines = [ln.strip() for ln in stream_text.splitlines() if ln.strip().startswith("@@STATUS@@:")]
    return lines[-n:]


def _load_generation_cases(generation_id: int) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(TestGeneration).filter(TestGeneration.id == int(generation_id)).first()
        if not row:
            return []
        raw = row.generated_result or "[]"
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
    except Exception:
        return []
    finally:
        db.close()


def _priority_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    out = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for c in cases:
        p = str(c.get("priority") or "").upper().strip()
        if p in out:
            out[p] += 1
    return out


def _simple_shape_stats(cases: list[dict[str, Any]]) -> dict[str, Any]:
    ui_words = {"ui", "页面", "按钮", "文案", "样式", "布局", "弹窗", "展示"}
    behavior_words = {"状态", "断网", "恢复", "同步", "一致性", "重试", "异常", "流程", "校验", "回滚"}
    ui_like = 0
    behavior_like = 0
    for c in cases:
        text = " ".join(
            [
                str(c.get("id") or ""),
                str(c.get("description") or ""),
                str(c.get("test_module") or ""),
                " ".join(str(s or "") for s in (c.get("steps") or []) if s),
            ]
        ).lower()
        if any(k in text for k in ui_words):
            ui_like += 1
        if any(k in text for k in behavior_words):
            behavior_like += 1
    total = max(1, len(cases))
    return {
        "count": len(cases),
        "priority": _priority_counts(cases),
        "ui_like_count": ui_like,
        "behavior_like_count": behavior_like,
        "ui_like_ratio": round(ui_like / total, 4),
        "behavior_like_ratio": round(behavior_like / total, 4),
    }


def run_stream_generation(
    client: TestClient,
    *,
    project_id: int,
    user_id: int,
    requirement: str,
    enable_sample_pool_feedback: bool,
    expected_count: int,
) -> dict[str, Any]:
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=int(user_id))
    try:
        payload = {
            "project_id": str(project_id),
            "doc_type": "requirement",
            "compress": "false",
            "expected_count": str(int(expected_count)),
            "enable_sample_pool_feedback": "true" if enable_sample_pool_feedback else "false",
            "force": "false",
            "append": "false",
            "current_biz_key": "",
            "only_current_biz": "false",
            "multi_pass": "false",
            "generation_mode": "single_pass",
            "requirement_text": requirement,
        }
        resp = client.post(
            "/api/generate-tests-stream",
            data=payload,
            headers={"Host": "localhost"},
        )
        body = resp.text or ""
        error_lines = []
        for ln in body.splitlines():
            s = ln.strip()
            if "Error:" in s or "Exception occurred:" in s or "[额度耗尽]" in s:
                error_lines.append(s[:400])
        diag = _extract_gen_diag(body)
        persisted = dict(diag.get("generation_persisted") or {})
        generation_id = int(persisted.get("generation_id") or 0)
        cases = _load_generation_cases(generation_id) if generation_id else []
        stats = _simple_shape_stats(cases)
        control = dict(diag.get("feedback_control_state") or {})
        review = dict(diag.get("review_decision_summary") or {})
        generation_summary = dict(diag.get("generation_summary") or {})
        return {
            "status_code": int(resp.status_code),
            "generation_id": generation_id,
            "chunk_count": len(body.splitlines()),
            "status_tail": _tail_status(body),
            "errors": ([] if resp.status_code == 200 else [f"http_{resp.status_code}"]) + error_lines,
            "stats": stats,
            "feedback_control_state": control,
            "review_decision_summary": review,
            "generation_summary": generation_summary,
        }
    except Exception as exc:
        return {
            "status_code": 0,
            "generation_id": 0,
            "chunk_count": 0,
            "status_tail": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
            "stats": {"count": 0, "priority": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}, "ui_like_count": 0, "behavior_like_count": 0, "ui_like_ratio": 0.0, "behavior_like_ratio": 0.0},
            "feedback_control_state": {},
            "review_decision_summary": {},
            "generation_summary": {},
        }
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def summarize_category(results: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(r["delta"]["p1_ratio_delta_on_minus_off"]) for r in results]
    neg = [x for x in deltas if x < 0]
    unstable = [x for x in deltas if x < float(UNSTABLE_DELTA_THRESHOLD)]
    variance = statistics.pvariance(deltas) if len(deltas) >= 2 else 0.0
    return {
        "runs": len(results),
        "p1_ratio_delta_avg": round(sum(deltas) / max(1, len(deltas)), 4),
        "p1_ratio_delta_min": round(min(deltas) if deltas else 0.0, 4),
        "p1_ratio_delta_max": round(max(deltas) if deltas else 0.0, 4),
        "positive_p1_delta_runs": sum(1 for x in deltas if x > 0),
        "non_negative_p1_delta_runs": sum(1 for x in deltas if x >= 0),
        "negative_rate": round((len(neg) / max(1, len(deltas))), 4),
        "worst_delta": round(min(deltas) if deltas else 0.0, 4),
        "unstable_case_count": int(len(unstable)),
        "unstable_case_rate": round((len(unstable) / max(1, len(deltas))), 4),
        "delta_variance": round(float(variance), 6),
    }


def run_campaign(
    *,
    per_category: int,
    expected_count: int,
    seed_limit: int,
    scan_multiplier: int,
    categories: list[str],
    treated_only: bool,
    strong_treated_only: bool,
    max_requirement_length: int,
) -> dict[str, Any]:
    grouped = collect_seed_docs(limit=max(200, int(seed_limit)))
    complex_augmentation = {"before": len(grouped.get("complex") or []), "after": len(grouped.get("complex") or []), "added": 0}
    if "complex" in [c for c in categories if c in {"normal", "complex", "noise_ui"}]:
        complex_augmentation = augment_complex_pool(
            grouped,
            target_size=max(int(per_category) * 4, 24),
        )
    selected: dict[str, list[dict[str, Any]]] = {"normal": [], "complex": [], "noise_ui": []}
    treatment_audit: dict[str, dict[str, int]] = {}
    active_categories = [c for c in categories if c in {"normal", "complex", "noise_ui"}]
    if not active_categories:
        active_categories = ["normal", "complex", "noise_ui"]

    db = SessionLocal()
    treatment_cache: dict[tuple[int, int, str], dict[str, Any]] = {}
    try:
        for cat in ("normal", "complex", "noise_ui"):
            if cat not in active_categories:
                treatment_audit[cat] = {
                    "pool_total": 0,
                    "pool_treated_pred": 0,
                    "pool_strong_treated_pred": 0,
                    "pool_untreated_pred": 0,
                    "scan_cap": 0,
                    "selected_total": 0,
                    "selected_treated_pred": 0,
                    "selected_strong_treated_pred": 0,
                }
                selected[cat] = []
                continue
            pool = grouped.get(cat, [])
            scan_cap = max(int(per_category), int(per_category) * max(1, int(scan_multiplier)))
            pool = pool[:scan_cap]
            treated_pool: list[dict[str, Any]] = []
            strong_treated_pool: list[dict[str, Any]] = []
            untreated_pool: list[dict[str, Any]] = []
            for seed in pool:
                # Skip invalid ownership seeds; they cannot load project-level sample-pool controls reliably.
                if int(seed.get("project_id") or 0) <= 0 or int(seed.get("user_id") or 0) <= 0:
                    continue
                if int(seed.get("features", {}).get("length") or 0) > int(max_requirement_length):
                    continue
                req_hash = hashlib.md5(str(seed.get("requirement") or "").encode("utf-8", errors="ignore")).hexdigest()
                key = (int(seed.get("project_id") or 0), int(seed.get("user_id") or 0), req_hash)
                treatment = treatment_cache.get(key)
                if treatment is None:
                    treatment = eval_treatment(seed, db)
                    treatment_cache[key] = treatment
                seed["treatment_pred"] = treatment
                on_sig = dict(treatment.get("on_control_sig") or {})
                strong_treated = bool(
                    int(on_sig.get("soft_constraints_count") or 0) > 0
                    or int(on_sig.get("must_have_scenarios_count") or 0) > 0
                )
                seed["strong_treated_pred"] = strong_treated
                if bool(treatment["treated_pred"]):
                    treated_pool.append(seed)
                    if strong_treated:
                        strong_treated_pool.append(seed)
                else:
                    untreated_pool.append(seed)
            if strong_treated_only:
                chosen = strong_treated_pool[: max(0, int(per_category))]
            elif treated_only:
                chosen = treated_pool[: max(0, int(per_category))]
            else:
                chosen = treated_pool[: max(0, int(per_category))]
                if len(chosen) < int(per_category):
                    chosen.extend(untreated_pool[: int(per_category) - len(chosen)])
            selected[cat] = chosen
            treatment_audit[cat] = {
                "pool_total": len(pool),
                "pool_treated_pred": len(treated_pool),
                "pool_strong_treated_pred": len(strong_treated_pool),
                "pool_untreated_pred": len(untreated_pool),
                "scan_cap": int(scan_cap),
                "selected_total": len(chosen),
                "selected_treated_pred": sum(1 for x in chosen if bool((x.get("treatment_pred") or {}).get("treated_pred"))),
                "selected_strong_treated_pred": sum(1 for x in chosen if bool(x.get("strong_treated_pred"))),
            }
    finally:
        db.close()

    now = datetime.now(timezone.utc).astimezone()
    result: dict[str, Any] = {
        "mode": "step6_stability_campaign",
        "run_at": now.isoformat(),
        "params": {
            "per_category": int(per_category),
            "expected_count": int(expected_count),
            "seed_limit": int(seed_limit),
            "scan_multiplier": int(scan_multiplier),
            "categories": active_categories,
            "treated_only": bool(treated_only),
            "strong_treated_only": bool(strong_treated_only),
            "generation_mode": "single_pass",
            "multi_pass": False,
            "max_requirement_length": int(max_requirement_length),
        },
        "seed_pool_counts": {k: len(v) for k, v in grouped.items()},
        "complex_augmentation": complex_augmentation,
        "selected_counts": {k: len(v) for k, v in selected.items()},
        "treatment_audit": treatment_audit,
        "categories": {},
        "overall_summary": {},
    }

    all_runs: list[dict[str, Any]] = []
    with TestClient(app) as client:
        for cat in ("normal", "complex", "noise_ui"):
            if cat not in active_categories:
                result["categories"][cat] = {"results": [], "summary": summarize_category([])}
                continue
            cat_rows: list[dict[str, Any]] = []
            seeds = selected.get(cat) or []
            for idx, seed in enumerate(seeds, start=1):
                off = run_stream_generation(
                    client,
                    project_id=int(seed["project_id"]),
                    user_id=int(seed["user_id"]),
                    requirement=str(seed["requirement"]),
                    enable_sample_pool_feedback=False,
                    expected_count=int(expected_count),
                )
                on = run_stream_generation(
                    client,
                    project_id=int(seed["project_id"]),
                    user_id=int(seed["user_id"]),
                    requirement=str(seed["requirement"]),
                    enable_sample_pool_feedback=True,
                    expected_count=int(expected_count),
                )
                off_count = int(off["stats"]["count"])
                on_count = int(on["stats"]["count"])
                off_p1 = int(off["stats"]["priority"].get("P1", 0))
                on_p1 = int(on["stats"]["priority"].get("P1", 0))
                off_ratio = round(off_p1 / max(1, off_count), 4)
                on_ratio = round(on_p1 / max(1, on_count), 4)
                row = {
                    "category": cat,
                    "run": idx,
                    "seed_meta": {
                        "doc_id": int(seed["doc_id"]),
                        "project_id": int(seed["project_id"]),
                        "user_id": int(seed["user_id"]),
                        "filename": str(seed["filename"]),
                        "doc_type": str(seed["doc_type"]),
                        "created_at": str(seed["created_at"]),
                        "features": dict(seed["features"]),
                        "treatment_pred": dict(seed.get("treatment_pred") or {}),
                    },
                    "off": off,
                    "on": on,
                    "delta": {
                        "p1_ratio_off": off_ratio,
                        "p1_ratio_on": on_ratio,
                        "p1_ratio_delta_on_minus_off": round(on_ratio - off_ratio, 4),
                        "count_off": off_count,
                        "count_on": on_count,
                        "count_delta_on_minus_off": int(on_count - off_count),
                        "ui_like_ratio_off": float(off["stats"].get("ui_like_ratio", 0.0)),
                        "ui_like_ratio_on": float(on["stats"].get("ui_like_ratio", 0.0)),
                        "ui_like_ratio_delta_on_minus_off": round(
                            float(on["stats"].get("ui_like_ratio", 0.0))
                            - float(off["stats"].get("ui_like_ratio", 0.0)),
                            4,
                        ),
                    },
                }
                cat_rows.append(row)
                all_runs.append(row)
            result["categories"][cat] = {
                "results": cat_rows,
                "summary": summarize_category(cat_rows),
            }

    overall_summary = summarize_category(all_runs)
    result["overall_summary"] = overall_summary
    treated_rows = [r for r in all_runs if bool((r.get("seed_meta", {}).get("treatment_pred") or {}).get("treated_pred"))]
    untreated_rows = [r for r in all_runs if not bool((r.get("seed_meta", {}).get("treatment_pred") or {}).get("treated_pred"))]
    result["treated_summary"] = summarize_category(treated_rows)
    result["untreated_summary"] = summarize_category(untreated_rows)
    result["treatment_rate"] = round(len(treated_rows) / max(1, len(all_runs)), 4)
    pattern_hits: dict[str, int] = {}
    for row in treated_rows:
        pred = dict((row.get("seed_meta", {}).get("treatment_pred") or {}))
        on_meta = dict(pred.get("on_control_meta") or {})
        distribution = dict(on_meta.get("pattern_hit_distribution") or {})
        for key, value in distribution.items():
            name = str(key or "").strip()
            if not name:
                continue
            try:
                count = int(value)
            except Exception:
                count = 0
            if count <= 0:
                continue
            pattern_hits[name] = int(pattern_hits.get(name, 0)) + count
    result["pattern_hit_distribution"] = {
        key: int(value)
        for key, value in sorted(pattern_hits.items(), key=lambda x: x[1], reverse=True)[:20]
    }

    negative_rows = [r for r in all_runs if float(r["delta"]["p1_ratio_delta_on_minus_off"]) < 0]
    unstable_rows = [
        r
        for r in all_runs
        if float(r["delta"]["p1_ratio_delta_on_minus_off"]) < float(UNSTABLE_DELTA_THRESHOLD)
    ]
    result["unstable_case_count"] = int(len(unstable_rows))
    result["unstable_case_threshold"] = float(UNSTABLE_DELTA_THRESHOLD)
    result["negative_slice"] = [
        {
            "category": r["category"],
            "run": int(r["run"]),
            "delta": float(r["delta"]["p1_ratio_delta_on_minus_off"]),
            "seed_meta": r["seed_meta"],
            "off_priority": r["off"]["stats"]["priority"],
            "on_priority": r["on"]["stats"]["priority"],
            "off_control": r["off"].get("feedback_control_state") or {},
            "on_control": r["on"].get("feedback_control_state") or {},
        }
        for r in negative_rows
    ]
    result["unstable_slice"] = [
        {
            "category": r["category"],
            "run": int(r["run"]),
            "delta": float(r["delta"]["p1_ratio_delta_on_minus_off"]),
            "seed_meta": r["seed_meta"],
            "off_priority": r["off"]["stats"]["priority"],
            "on_priority": r["on"]["stats"]["priority"],
            "off_control": r["off"].get("feedback_control_state") or {},
            "on_control": r["on"].get("feedback_control_state") or {},
        }
        for r in unstable_rows
    ]
    treated_summary = dict(result.get("treated_summary") or {})
    result["stability_gate"] = {
        "treated_avg_gt_0": bool(float(treated_summary.get("p1_ratio_delta_avg") or 0.0) > 0.0),
        "treated_negative_rate_lt_0_3": bool(float(treated_summary.get("negative_rate") or 1.0) < 0.3),
        "treated_worst_delta_gt_minus_0_2": bool(float(treated_summary.get("worst_delta") or -1.0) > -0.2),
        "treatment_rate_gt_0_6": bool(float(result.get("treatment_rate") or 0.0) > 0.6),
        "unstable_case_count_eq_0": bool(int(result.get("unstable_case_count") or 0) == 0),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--expected-count", type=int, default=12)
    parser.add_argument("--seed-limit", type=int, default=6000)
    parser.add_argument("--scan-multiplier", type=int, default=4)
    parser.add_argument("--categories", type=str, default="normal,complex,noise_ui")
    parser.add_argument("--treated-only", action="store_true")
    parser.add_argument("--strong-treated-only", action="store_true")
    parser.add_argument("--max-requirement-length", type=int, default=4000)
    parser.add_argument("--output", type=str, default="")
    args = parser.parse_args()
    categories = [x.strip() for x in str(args.categories or "").split(",") if x.strip()]

    payload = run_campaign(
        per_category=max(1, int(args.per_category)),
        expected_count=max(1, int(args.expected_count)),
        seed_limit=max(200, int(args.seed_limit)),
        scan_multiplier=max(1, int(args.scan_multiplier)),
        categories=categories,
        treated_only=bool(args.treated_only),
        strong_treated_only=bool(args.strong_treated_only),
        max_requirement_length=max(200, int(args.max_requirement_length)),
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = Path(args.output) if args.output else Path("backend/tmp") / f"ab_step6_campaign_{ts}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(output))
    print(json.dumps(payload.get("overall_summary") or {}, ensure_ascii=False))


if __name__ == "__main__":
    main()
