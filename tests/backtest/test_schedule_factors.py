from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from prizepicks_predictor.backtest.report import analyse
from prizepicks_predictor.backtest.schedule_factors import (
    fit_schedule_factors,
    write_schedule_factors,
)
from prizepicks_predictor.prediction.models import ScheduleAdjustedEwmaModel, load_schedule_factors

OFFSETS = [-2, -1, 0, 1, 2]


def _fake_run(path: Path, *, n_per_bucket: int = 250) -> None:
    """ewma projections of 20; actuals 18 on back-to-backs, 20 normal, 21 at home (else 19.5)."""
    path.mkdir(parents=True)
    obs_rows, pred_rows = [], []
    obs_id = 0
    for bucket, actual in (("back_to_back", 18.0), ("normal", 20.0), ("long_rest", 20.0)):
        for i in range(n_per_bucket):
            obs_id += 1
            home = i % 2 == 0
            a = actual + (1.0 if home else -0.5)
            obs_rows.append(
                {
                    "obs_id": obs_id,
                    "season": 2023,
                    "stat": "points",
                    "actual": a,
                    "is_home": str(home),
                    "rest_bucket": bucket,
                    "prior_games": 50,
                    "minutes_recent": 30,
                    "prior_start_rate": 0.5,
                    **{f"line_{o:+d}": 19.5 + o for o in OFFSETS},
                }
            )
            pred_rows.append(
                {
                    "obs_id": obs_id,
                    "model": "ewma",
                    "requested_family": "auto",
                    "family": "poisson",
                    "projection": 20.0,
                    "mean": 20.0,
                    "std": 4.0,
                    "q_lo": 15,
                    "q_hi": 25,
                    "nll": 2.0,
                    "covered": True,
                    **{f"p_more_{o:+d}": 0.5 for o in OFFSETS},
                }
            )
    with gzip.open(path / "observations.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(obs_rows[0]))
        w.writeheader()
        w.writerows(obs_rows)
    with gzip.open(path / "predictions.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(pred_rows[0]))
        w.writeheader()
        w.writerows(pred_rows)
    (path / "run.json").write_text(
        json.dumps({"config": {"seasons": [2023], "line_offsets": OFFSETS}})
    )


def test_fit_schedule_factors_recovers_known_ratios(tmp_path: Path) -> None:
    run = tmp_path / "dev"
    _fake_run(run)
    payload = fit_schedule_factors(run)
    f = payload["factors"]["points"]  # type: ignore[index]
    assert f["normal"] == pytest.approx(1.0)
    assert f["back_to_back"] == pytest.approx(
        18.25 / 20.25, abs=1e-4
    )  # (18+0.25 avg venue) / (20+0.25)
    assert f["long_rest"] == pytest.approx(1.0, abs=1e-4)
    assert f["three_in_four"] == 1.0  # no observations -> neutral, not NaN
    assert f["home"] > 1.0 > f["away"]
    assert payload["counts"]["points"]["back_to_back"] == 250  # type: ignore[index]
    out = write_schedule_factors(payload, tmp_path / "factors.json")
    load_schedule_factors.cache_clear()
    loaded = load_schedule_factors(out)
    assert loaded["points"]["back_to_back"] == f["back_to_back"]
    load_schedule_factors.cache_clear()
    model = ScheduleAdjustedEwmaModel(loaded)
    assert model.factors_for("points")["home"] == f["home"]
    assert model.factors_for("rebounds") == {k: 1.0 for k in model.factors_for("rebounds")}


def test_small_buckets_stay_neutral(tmp_path: Path) -> None:
    run = tmp_path / "small"
    _fake_run(run, n_per_bucket=50)
    f = fit_schedule_factors(run)["factors"]["points"]  # type: ignore[index]
    assert all(v == 1.0 for v in f.values())


def test_report_strata_include_rest_buckets(tmp_path: Path) -> None:
    run = tmp_path / "r"
    _fake_run(run, n_per_bucket=30)
    from prizepicks_predictor.backtest.report import read_run

    _, observations, predictions = read_run(run)
    cmp = analyse(observations, predictions, OFFSETS, resamples=20)
    rest_rows = {(bucket, r.n) for stratum, bucket, r in cmp.strata if stratum == "rest"}
    assert rest_rows == {("back_to_back", 30), ("normal", 30), ("long_rest", 30)}
