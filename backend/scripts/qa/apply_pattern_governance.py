from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.db.database import SessionLocal
from modules.testing.priority_sample_pool_store import (
    load_priority_sample_pool,
    upsert_priority_sample_pool,
)


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def _safe_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return float(default)


def _pattern_key(raw: Any) -> str:
    return str(raw or "").strip().lower()[:120]


def _iter_result_rows(campaign_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, category_body in (campaign_payload.get("categories") or {}).items():
        for item in (category_body.get("results") or []):
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _collect_pattern_stats(
    rows: list[dict[str, Any]],
    *,
    strong_neg_threshold: float,
) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "hits": 0.0,
            "pos_hits": 0.0,
            "neg_hits": 0.0,
            "strong_neg_hits": 0.0,
            "delta_sum": 0.0,
        }
    )
    for row in rows:
        delta = _safe_float(row.get("p1_ratio_delta"), default=0.0)
        meta = (
            ((row.get("seed_meta") or {}).get("treatment_pred") or {}).get(
                "on_control_meta"
            )
            or {}
        )
        dist = meta.get("pattern_hit_distribution") or {}
        if not isinstance(dist, dict):
            continue
        for raw_key, raw_count in dist.items():
            key = _pattern_key(raw_key)
            if not key:
                continue
            count = max(1, _safe_int(raw_count, default=1))
            stat = stats[key]
            stat["hits"] += float(count)
            stat["delta_sum"] += float(delta) * float(count)
            if delta < 0:
                stat["neg_hits"] += float(count)
                if delta <= float(strong_neg_threshold):
                    stat["strong_neg_hits"] += float(count)
            elif delta > 0:
                stat["pos_hits"] += float(count)
    return stats


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply pattern governance (downweight/disable) from campaign results."
    )
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--campaign-json", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--strong-neg-threshold", type=float, default=-0.2)
    parser.add_argument("--disable-min-hits", type=int, default=3)
    parser.add_argument("--disable-neg-rate", type=float, default=0.8)
    parser.add_argument("--disable-avg-delta", type=float, default=-0.15)
    parser.add_argument("--strong-neg-disable-hits", type=int, default=2)
    args = parser.parse_args()

    campaign_path = Path(args.campaign_json)
    payload = json.loads(campaign_path.read_text(encoding="utf-8"))
    rows = _iter_result_rows(payload)
    stats = _collect_pattern_stats(rows, strong_neg_threshold=float(args.strong_neg_threshold))

    db = SessionLocal()
    try:
        pool = load_priority_sample_pool(
            db=db,
            project_id=int(args.project_id),
            user_id=int(args.user_id),
        )
        if not isinstance(pool, dict):
            raise RuntimeError("priority sample pool not found")
        samples = list(pool.get("samples") or [])
        if not samples:
            raise RuntimeError("priority sample pool has no samples")

        touched = 0
        disabled = 0
        utc_now = datetime.now(timezone.utc).isoformat()
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            key = _pattern_key(
                sample.get("pattern_canonical")
                or sample.get("pattern_summary")
                or sample.get("title")
                or sample.get("case_id")
            )
            if not key:
                continue
            stat = stats.get(key)
            if not stat:
                continue

            hits = float(stat["hits"])
            if hits <= 0:
                continue
            pos_hits = float(stat["pos_hits"])
            neg_hits = float(stat["neg_hits"])
            strong_neg_hits = float(stat["strong_neg_hits"])
            avg_delta = float(stat["delta_sum"]) / hits
            neg_rate = float(neg_hits) / float(hits)

            current_adj = _safe_float(sample.get("pattern_weight_adjustment"), default=1.0)
            mult = 1.0
            if strong_neg_hits > 0:
                mult *= 0.82 ** strong_neg_hits
            if neg_hits > strong_neg_hits:
                mult *= 0.94 ** max(0.0, neg_hits - strong_neg_hits)
            if pos_hits > 0:
                mult *= 1.02 ** min(6.0, pos_hits)
            next_adj = _clamp(current_adj * mult, 0.25, 1.5)

            should_disable = False
            if (
                hits >= float(args.disable_min_hits)
                and neg_rate >= float(args.disable_neg_rate)
                and avg_delta <= float(args.disable_avg_delta)
                and pos_hits <= 0
            ):
                should_disable = True
            if strong_neg_hits >= float(args.strong_neg_disable_hits) and pos_hits <= 0:
                should_disable = True

            sample["pattern_weight_adjustment"] = round(next_adj, 4)
            sample["pattern_feedback_total_hits"] = int(hits)
            sample["pattern_feedback_negative_hits"] = int(neg_hits)
            sample["pattern_feedback_positive_hits"] = int(pos_hits)
            sample["pattern_feedback_strong_negative_hits"] = int(strong_neg_hits)
            sample["pattern_feedback_avg_delta"] = round(avg_delta, 6)
            sample["governance_status"] = "disabled" if should_disable else "active"
            sample["governance_updated_at"] = utc_now
            sample["governance_source"] = str(campaign_path.name)
            touched += 1
            if should_disable:
                disabled += 1

        print(
            json.dumps(
                {
                    "rows": len(rows),
                    "stats_patterns": len(stats),
                    "pool_samples": len(samples),
                    "touched_samples": touched,
                    "disabled_samples": disabled,
                    "apply": bool(args.apply),
                },
                ensure_ascii=False,
            )
        )
        if not args.apply:
            return

        generation_id = pool.get("generation_id")
        try:
            generation_id = int(generation_id) if generation_id is not None else None
        except Exception:
            generation_id = None
        upsert_priority_sample_pool(
            db=db,
            project_id=int(args.project_id),
            user_id=int(args.user_id),
            generation_id=generation_id,
            samples=samples,
        )
        print(
            json.dumps(
                {
                    "status": "applied",
                    "project_id": int(args.project_id),
                    "user_id": int(args.user_id),
                    "touched_samples": touched,
                    "disabled_samples": disabled,
                },
                ensure_ascii=False,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
