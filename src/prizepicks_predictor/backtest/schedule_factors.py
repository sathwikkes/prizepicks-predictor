"""Fit the schedule-adjustment factors from a backtest run (development season only).

factor[stat][bucket] = (sum actual / sum ewma projection in bucket) / (same ratio in the
"normal" rest bucket). Venue factors use the same ratio for home vs away relative to all
games. Ratios of sums are robust to the stat's scale and need no optimiser.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from prizepicks_predictor.backtest.report import read_run

MIN_BUCKET_N = 200


def fit_schedule_factors(
    run_dir: Path, *, model: str = "ewma", family: str = "auto"
) -> dict[str, object]:
    meta, observations, predictions = read_run(run_dir)
    obs_by_id = {int(o["obs_id"]): o for o in observations}
    sums: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0, 0])
    )
    for p in predictions:
        if p["model"] != model or p["requested_family"] != family:
            continue
        o = obs_by_id[int(p["obs_id"])]
        actual, proj = float(o["actual"]), float(p["projection"])
        keys = [
            o.get("rest_bucket", "unknown"),
            "home" if o["is_home"] == "True" else "away",
            "all",
        ]
        for k in keys:
            cell = sums[o["stat"]][k]
            cell[0] += actual
            cell[1] += proj
            cell[2] += 1
    factors: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}
    for stat, cells in sums.items():
        ratio = {k: (v[0] / v[1] if v[1] > 0 else 1.0) for k, v in cells.items()}
        ref = ratio.get("normal", 1.0)
        allr = ratio.get("all", 1.0)
        factors[stat] = {}
        counts[stat] = {k: int(v[2]) for k, v in cells.items()}
        for bucket in ("back_to_back", "three_in_four", "long_rest", "normal"):
            n = cells[bucket][2]
            factors[stat][bucket] = (
                round(ratio[bucket] / ref, 5) if n >= MIN_BUCKET_N and ref > 0 else 1.0
            )
        for venue in ("home", "away"):
            n = cells[venue][2]
            factors[stat][venue] = (
                round(ratio[venue] / allr, 5) if n >= MIN_BUCKET_N and allr > 0 else 1.0
            )
    return {
        "fitted_at": datetime.now(UTC).isoformat(),
        "fit_run": str(run_dir),
        "fit_seasons": meta["config"]["seasons"],
        "base_model": f"{model}/{family}",
        "method": "ratio of sums per bucket, normalised to normal rest (venue: to all games)",
        "min_bucket_n": MIN_BUCKET_N,
        "counts": counts,
        "factors": factors,
    }


def write_schedule_factors(payload: dict[str, object], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest
