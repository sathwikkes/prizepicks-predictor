"""Fit per-stat opponent elasticities from a development backtest run.

log((actual + 0.5) / (ewma + 0.5)) = a * log(pace_rel) + b * log(def_rel) + c, by least squares,
on observations with opponent context. Elasticities below 1e-3 in absolute value are kept as
fitted; the intercept is discarded (EWMA level is not re-centred).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from prizepicks_predictor.backtest.report import read_run

MIN_N = 2000


def fit_opponent_factors(
    run_dir: Path, *, model: str = "ewma", family: str = "auto"
) -> dict[str, object]:
    meta, observations, predictions = read_run(run_dir)
    obs_by_id = {int(o["obs_id"]): o for o in observations}
    rows: dict[str, list[tuple[float, float, float]]] = {}
    for p in predictions:
        if p["model"] != model or p["requested_family"] != family:
            continue
        o = obs_by_id[int(p["obs_id"])]
        if not o.get("opp_pace_rel") or not o.get("opp_def_rel"):
            continue
        y = float(np.log((float(o["actual"]) + 0.5) / (float(p["projection"]) + 0.5)))
        rows.setdefault(o["stat"], []).append(
            (np.log(float(o["opp_pace_rel"])), np.log(float(o["opp_def_rel"])), y)
        )
    factors: dict[str, dict[str, float]] = {}
    diagnostics: dict[str, dict[str, float]] = {}
    for stat, data in rows.items():
        arr = np.array(data)
        n = len(arr)
        if n < MIN_N:
            factors[stat] = {"pace": 0.0, "defense": 0.0}
            diagnostics[stat] = {"n": float(n), "r2": 0.0}
            continue
        x = np.column_stack([arr[:, 0], arr[:, 1], np.ones(n)])
        coef, *_ = np.linalg.lstsq(x, arr[:, 2], rcond=None)
        resid = arr[:, 2] - x @ coef
        ss_tot = float(((arr[:, 2] - arr[:, 2].mean()) ** 2).sum()) or 1.0
        factors[stat] = {"pace": round(float(coef[0]), 4), "defense": round(float(coef[1]), 4)}
        diagnostics[stat] = {
            "n": float(n),
            "r2": round(1.0 - float((resid**2).sum()) / ss_tot, 5),
            "intercept": round(float(coef[2]), 4),
        }
    return {
        "fitted_at": datetime.now(UTC).isoformat(),
        "fit_run": str(run_dir),
        "fit_seasons": meta["config"]["seasons"],
        "base_model": f"{model}/{family}",
        "method": "least squares of log((actual+0.5)/(ewma+0.5)) on log pace_rel, log def_rel",
        "min_n": MIN_N,
        "diagnostics": diagnostics,
        "factors": factors,
        "status": "research_only_not_default",
    }


def write_opponent_factors(payload: dict[str, object], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest
