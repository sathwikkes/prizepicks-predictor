"""End-to-end calibration across two fabricated run directories.

Builds two synthetic runs whose P(more) values are systematically overconfident, fits on
the first and evaluates on the second, and checks the held-out scores improve.
"""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from prizepicks_predictor.backtest.calibrate import (
    calibrate,
    render_calibration,
    write_calibration_report,
)

OFFSETS = [-2, -1, 0, 1, 2]


def _fake_run(path: Path, *, seed: int, n_obs: int = 600) -> None:
    rng = np.random.default_rng(seed)
    path.mkdir(parents=True)
    obs_rows = []
    pred_rows = []
    for obs_id in range(1, n_obs + 1):
        mean = rng.uniform(8, 30)
        actual = int(rng.poisson(mean))
        base = np.floor(mean) + 0.5
        lines = [max(0.5, base + off) for off in OFFSETS]
        obs_rows.append(
            {
                "obs_id": obs_id,
                "season": 2024,
                "stat": "points",
                "actual": actual,
                **{f"line_{off:+d}": line for off, line in zip(OFFSETS, lines, strict=True)},
            }
        )
        # true P(more) from the generating Poisson, then sharpened => overconfident
        from scipy import stats

        true_p = [float(1 - stats.poisson.cdf(np.floor(line), mean)) for line in lines]
        over = [
            float(1 / (1 + np.exp(-2.5 * np.log(p / (1 - p)))))
            for p in np.clip(true_p, 1e-4, 1 - 1e-4)
        ]
        pred_rows.append(
            {
                "obs_id": obs_id,
                "model": "ewma",
                "requested_family": "auto",
                "family": "poisson",
                "projection": mean,
                **{f"p_more_{off:+d}": p for off, p in zip(OFFSETS, over, strict=True)},
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
    (path / "run.json").write_text(json.dumps({"config": {"line_offsets": OFFSETS}}))


def test_calibrate_fits_on_validation_and_improves_heldout(tmp_path: Path) -> None:
    fit_run, eval_run = tmp_path / "val", tmp_path / "test"
    _fake_run(fit_run, seed=1)
    _fake_run(eval_run, seed=2)
    report = calibrate(fit_run, eval_run, stat="points", model="ewma", family="auto")
    assert report.supported, report.reason
    by = {r.method: r for r in report.rows}
    assert by["uncalibrated"].n_fit == 0 and by["platt"].n_fit == 600 * len(OFFSETS)
    assert by["platt"].n_eval == by["uncalibrated"].n_eval == 600 * len(OFFSETS)
    assert by["platt"].log_loss < by["uncalibrated"].log_loss
    assert by["platt"].ece < by["uncalibrated"].ece
    assert by["isotonic"].ece < by["uncalibrated"].ece
    assert "a<1" in report.calibrators["platt"]
    out = write_calibration_report(report, eval_run, chart=False)
    assert out.is_file() and (eval_run / "calibration_points_ewma_auto.json").is_file()
    text = render_calibration(report)
    assert "platt" in text and "isotonic" in text


def test_calibrate_unsupported_when_too_few_pairs(tmp_path: Path) -> None:
    fit_run, eval_run = tmp_path / "val", tmp_path / "test"
    _fake_run(fit_run, seed=1, n_obs=50)
    _fake_run(eval_run, seed=2, n_obs=50)
    report = calibrate(fit_run, eval_run, stat="points", model="ewma", family="auto")
    assert not report.supported and "need >=" in report.reason
    assert [r.method for r in report.rows] == ["uncalibrated"]


def test_calibrate_rejects_same_run(tmp_path: Path) -> None:
    run = tmp_path / "one"
    _fake_run(run, seed=1, n_obs=20)
    with pytest.raises(ValueError, match="must differ"):
        calibrate(run, run, stat="points", model="ewma", family="auto")
