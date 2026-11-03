"""Fit versioned calibration artifacts from backtest runs.

Two operational flavours are produced by the CLI (``ppp fit-calibrators``):

* **held-out validated**: fitted on the validation run(s) and evaluated on a *different*
  held-out run; the artifact records those held-out metrics.
* **in-sample only**: fitted on every completed season available (an operational refit
  for the coming season) with no held-out evaluation; the artifact says so.

Nothing here touches the Phase 3 reported results: those runs are read, never rewritten.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np

from prizepicks_predictor.backtest.calibrate import load_pairs
from prizepicks_predictor.backtest.calibration import (
    MIN_FIT_SIZE,
    IsotonicCalibrator,
    PlattCalibrator,
)
from prizepicks_predictor.backtest.metrics import brier, expected_calibration_error, log_loss
from prizepicks_predictor.backtest.report import read_run
from prizepicks_predictor.prediction.calibrators import (
    CalibrationArtifact,
    HeldOutSummary,
    compute_support,
    make_artifact_id,
    utc_now,
)
from prizepicks_predictor.prediction.models import MODELS
from prizepicks_predictor.prediction.schemas import DistributionFamily, StatType


class CalibrationFitError(ValueError):
    pass


def _run_meta(run_dir: Path) -> tuple[list[int], str, date | None]:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    seasons = [int(s) for s in meta["config"]["seasons"]]
    source = str(meta.get("database_url", "unknown"))
    _, observations, _ = read_run(run_dir)
    latest = max((date.fromisoformat(o["game_date"]) for o in observations), default=None)
    return seasons, source, latest


def fit_artifact(
    *,
    fit_runs: list[Path],
    stat: StatType,
    model_name: str,
    family: DistributionFamily = DistributionFamily.AUTO,
    method: Literal["isotonic", "platt"] = "isotonic",
    eval_run: Path | None = None,
    data_source: str = "sportsdataverse_nba_stats",
) -> CalibrationArtifact:
    if model_name not in MODELS:
        raise CalibrationFitError(f"unknown model {model_name!r}")
    if eval_run is not None and any(eval_run.resolve() == r.resolve() for r in fit_runs):
        raise CalibrationFitError("evaluation run must not be one of the fit runs")

    ps: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    seasons: list[int] = []
    latest: date | None = None
    for run in fit_runs:
        p, y = load_pairs(run, stat=stat.value, model=model_name, family=family.value)
        ps.append(p)
        ys.append(y)
        run_seasons, _, run_latest = _run_meta(run)
        seasons.extend(run_seasons)
        if run_latest and (latest is None or run_latest > latest):
            latest = run_latest
    p_fit, y_fit = np.concatenate(ps), np.concatenate(ys)
    if len(p_fit) < MIN_FIT_SIZE:
        raise CalibrationFitError(
            f"only {len(p_fit)} fitting pairs for {stat.value}/{model_name}/{family.value}; "
            f"need {MIN_FIT_SIZE}"
        )

    x: list[float] = []
    yv: list[float] = []
    a = b = None
    if method == "isotonic":
        iso = IsotonicCalibrator.fit(p_fit, y_fit)
        x, yv = iso.x.tolist(), iso.y.tolist()
        apply = iso.apply
    elif method == "platt":
        platt = PlattCalibrator.fit(p_fit, y_fit)
        a, b = platt.a, platt.b
        apply = platt.apply
    else:  # pragma: no cover - Literal guards this
        raise CalibrationFitError(f"unknown method {method!r}")

    held_out = None
    if eval_run is not None:
        p_eval, y_eval = load_pairs(
            eval_run, stat=stat.value, model=model_name, family=family.value
        )
        q = apply(p_eval)
        eval_seasons, _, _ = _run_meta(eval_run)
        held_out = HeldOutSummary(
            run=str(eval_run),
            seasons=eval_seasons,
            n_eval=len(p_eval),
            brier_uncalibrated=brier(p_eval, y_eval),
            brier_calibrated=brier(q, y_eval),
            log_loss_uncalibrated=log_loss(p_eval, y_eval),
            log_loss_calibrated=log_loss(q, y_eval),
            ece_uncalibrated=expected_calibration_error(p_eval, y_eval),
            ece_calibrated=expected_calibration_error(q, y_eval),
        )

    fit_seasons = sorted(set(seasons))
    support = compute_support(p_fit)
    return CalibrationArtifact(
        artifact_id=make_artifact_id(
            method=method,
            stat=stat,
            model_name=model_name,
            model_version=MODELS[model_name].version,
            family=family,
            fit_seasons=fit_seasons,
            x=x,
            y=yv,
            a=a,
            b=b,
        ),
        stat=stat,
        model_name=model_name,
        model_version=MODELS[model_name].version,
        family=family,
        method=method,
        x=x,
        y=yv,
        a=a,
        b=b,
        n_fit=len(p_fit),
        fit_seasons=fit_seasons,
        fit_runs=[str(r) for r in fit_runs],
        data_source=data_source,
        data_latest_game_date=latest,
        fitted_at=utc_now(),
        validation_status="held_out_validated" if held_out else "in_sample_only",
        held_out=held_out,
        notes=[
            "fitted on synthetic evaluation lines; not validated against market lines",
            "applies to P(more | no push); push probability is left to the model",
        ],
        support_lo=support["support_lo"],
        support_hi=support["support_hi"],
        support_bins=support["support_bins"],
    )


def annotate_support(artifact_dir: Path, fit_runs: list[Path]) -> list[tuple[Path, str]]:
    """Add training-domain metadata to existing artifacts from their fit runs.

    The fitted map is untouched: the artifact id (a hash of x/y) must be identical before
    and after, which is asserted. Returns (path, artifact_id) per updated file.
    """
    updated: list[tuple[Path, str]] = []
    for path in sorted(artifact_dir.glob("*.json")):
        art = CalibrationArtifact.load(path)
        ps = [
            load_pairs(run, stat=art.stat.value, model=art.model_name, family=art.family.value)[0]
            for run in fit_runs
        ]
        annotated = art.with_support(np.concatenate(ps))
        assert (
            annotated.artifact_id == art.artifact_id
            and annotated.x == art.x
            and annotated.y == art.y
        )
        annotated.save(artifact_dir)
        updated.append((path, annotated.artifact_id))
    return updated
