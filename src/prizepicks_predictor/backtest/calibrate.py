"""Fit calibrators on one run (validation) and evaluate them on another (held-out test)."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from prizepicks_predictor.backtest.calibration import (
    MIN_FIT_SIZE,
    IsotonicCalibrator,
    PlattCalibrator,
)
from prizepicks_predictor.backtest.metrics import (
    ReliabilityBin,
    brier,
    expected_calibration_error,
    log_loss,
    reliability_table,
)
from prizepicks_predictor.backtest.report import read_run

Array = NDArray[np.float64]


def _pairs(run_dir: Path, *, stat: str, model: str, family: str) -> tuple[Array, Array]:
    """All (P(more), outcome) pairs for one configuration in a run, pooled over lines."""
    meta, observations, predictions = read_run(run_dir)
    offsets = [int(x) for x in meta["config"]["line_offsets"]]
    obs_by_id = {int(o["obs_id"]): o for o in observations}
    ps: list[float] = []
    ys: list[float] = []
    for p in predictions:
        o = obs_by_id[int(p["obs_id"])]
        if o["stat"] != stat or p["model"] != model or p["requested_family"] != family:
            continue
        actual = float(o["actual"])
        for off in offsets:
            ps.append(float(p[f"p_more_{off:+d}"]))
            ys.append(float(actual > float(o[f"line_{off:+d}"])))
    return np.array(ps), np.array(ys)


@dataclass
class CalibrationRow:
    method: str
    n_fit: int
    n_eval: int
    brier: float
    log_loss: float
    ece: float


@dataclass
class CalibrationReport:
    stat: str
    model: str
    family: str
    fit_run: str
    eval_run: str
    supported: bool
    reason: str
    rows: list[CalibrationRow]
    reliability: dict[str, list[ReliabilityBin]]
    calibrators: dict[str, str]


def calibrate(
    fit_run: Path, eval_run: Path, *, stat: str, model: str, family: str
) -> CalibrationReport:
    p_fit, y_fit = _pairs(fit_run, stat=stat, model=model, family=family)
    p_eval, y_eval = _pairs(eval_run, stat=stat, model=model, family=family)
    if fit_run.resolve() == eval_run.resolve():
        raise ValueError("fit and evaluation runs must differ (no in-sample calibration)")
    rows = [
        CalibrationRow(
            "uncalibrated",
            0,
            len(p_eval),
            brier(p_eval, y_eval),
            log_loss(p_eval, y_eval),
            expected_calibration_error(p_eval, y_eval),
        )
    ]
    reliability = {"uncalibrated": reliability_table(p_eval, y_eval)}
    calibrators: dict[str, str] = {}
    supported = len(p_fit) >= MIN_FIT_SIZE and len(p_eval) > 0
    reason = (
        ""
        if supported
        else (
            f"need >= {MIN_FIT_SIZE} fitting pairs and some evaluation pairs; "
            f"have {len(p_fit)} / {len(p_eval)}"
        )
    )
    if supported:
        platt = PlattCalibrator.fit(p_fit, y_fit)
        iso = IsotonicCalibrator.fit(p_fit, y_fit)
        for name, cal in (("platt", platt), ("isotonic", iso)):
            q = cal.apply(p_eval)
            rows.append(
                CalibrationRow(
                    name,
                    cal.n_fit,
                    len(q),
                    brier(q, y_eval),
                    log_loss(q, y_eval),
                    expected_calibration_error(q, y_eval),
                )
            )
            reliability[name] = reliability_table(q, y_eval)
            calibrators[name] = cal.describe()
    return CalibrationReport(
        stat,
        model,
        family,
        str(fit_run),
        str(eval_run),
        supported,
        reason,
        rows,
        reliability,
        calibrators,
    )


def write_calibration_report(
    report: CalibrationReport, out_dir: Path, *, chart: bool = True
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"calibration_{report.stat}_{report.model}_{report.family}"
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(CalibrationRow.__dataclass_fields__))
        for r in report.rows:
            w.writerow(list(asdict(r).values()))
    payload: dict[str, Any] = {
        **{k: v for k, v in asdict(report).items() if k not in ("rows", "reliability")},
        "rows": [asdict(r) for r in report.rows],
        "reliability": {k: [asdict(b) for b in bins] for k, bins in report.reliability.items()},
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if chart and report.supported:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], "k--", lw=1)
        for name, bins in report.reliability.items():
            xs = [b.mean_predicted for b in bins if b.n >= 30]
            ys = [b.observed_frequency for b in bins if b.n >= 30]
            ax.plot(xs, ys, marker="o", label=name)
        ax.set_title(f"Calibration on held-out run: {report.stat} {report.model}/{report.family}")
        ax.set_xlabel("predicted P(more)")
        ax.set_ylabel("observed frequency")
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_dir / f"{stem}.png", dpi=110)
        plt.close(fig)
    return out_dir / f"{stem}.csv"


def render_calibration(report: CalibrationReport) -> str:
    lines = [
        f"Calibration: {report.stat} {report.model}/{report.family}",
        f"  fit on {report.fit_run}, evaluated on {report.eval_run}",
    ]
    if not report.supported:
        lines.append(f"  unsupported: {report.reason}")
    for r in report.rows:
        lines.append(
            f"  {r.method:12s} n_fit={r.n_fit:6d} n_eval={r.n_eval:6d} "
            f"Brier={r.brier:.4f} logloss={r.log_loss:.4f} ECE={r.ece:.4f}"
        )
    for name, desc in report.calibrators.items():
        lines.append(f"  {name}: {desc}")
    return "\n".join(lines)
