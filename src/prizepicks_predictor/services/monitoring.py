"""Prospective monitoring report over saved snapshots and their outcomes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.metrics import (
    ReliabilityBin,
    brier,
    expected_calibration_error,
    log_loss,
    reliability_table,
)
from prizepicks_predictor.db.models import PredictionOutcome, PredictionSnapshot

PRELIMINARY_BELOW = 200


@dataclass
class Metrics:
    n: int
    mae: float
    rmse: float
    bias: float
    coverage: float
    n_prob: int
    pushes: int
    brier_raw: float | None
    brier_cal: float | None
    logloss_raw: float | None
    logloss_cal: float | None
    ece_raw: float | None
    ece_cal: float | None


@dataclass
class MonitorReport:
    since: date | None
    through: date | None
    snapshots: int
    by_status: dict[str, int]
    overall: Metrics | None
    by_group: dict[str, dict[str, Metrics]] = field(default_factory=dict)
    reliability_raw: list[ReliabilityBin] = field(default_factory=list)
    reliability_cal: list[ReliabilityBin] = field(default_factory=list)
    brier_diff_ci: tuple[float, float, float] | None = None  # cal - raw, cluster bootstrap
    preliminary: bool = True
    notes: list[str] = field(default_factory=list)


def _metrics(rows: list[tuple[PredictionSnapshot, PredictionOutcome]]) -> Metrics | None:
    ev = [(s, o) for s, o in rows if o.status == "evaluated" and o.actual is not None]
    if not ev:
        return None
    err = np.asarray([s.projection - float(o.actual or 0) for s, o in ev], dtype=np.float64)
    cov = np.asarray([1.0 if o.covered else 0.0 for _, o in ev], dtype=np.float64)
    prob = [(s, o) for s, o in ev if o.outcome_more is not None and o.brier_raw is not None]
    pushes = sum(1 for _, o in ev if o.outcome_push)

    def cond(p_more: float | None, p_less: float | None) -> float:
        assert p_more is not None
        denom = p_more + (p_less or 0.0)
        return p_more / denom if denom > 0 else p_more

    p_raw = np.array([cond(s.p_more, s.p_less) for s, _ in prob])
    y = np.array([1.0 if o.outcome_more else 0.0 for _, o in prob])
    cal_rows = [(s, o) for s, o in prob if s.p_more_cal is not None]
    p_cal = np.array([cond(s.p_more_cal, s.p_less_cal) for s, _ in cal_rows])
    y_cal = np.array([1.0 if o.outcome_more else 0.0 for _, o in cal_rows])
    return Metrics(
        n=len(ev),
        mae=float(np.mean(np.abs(err))),
        rmse=float(np.sqrt(np.mean(err**2))),
        bias=float(np.mean(err)),
        coverage=float(np.mean(cov)),
        n_prob=len(prob),
        pushes=pushes,
        brier_raw=brier(p_raw, y) if len(prob) else None,
        brier_cal=brier(p_cal, y_cal) if len(cal_rows) else None,
        logloss_raw=log_loss(p_raw, y) if len(prob) else None,
        logloss_cal=log_loss(p_cal, y_cal) if len(cal_rows) else None,
        ece_raw=expected_calibration_error(p_raw, y) if len(prob) else None,
        ece_cal=expected_calibration_error(p_cal, y_cal) if len(cal_rows) else None,
    )


def _cluster_bootstrap_brier_diff(
    rows: list[tuple[PredictionSnapshot, PredictionOutcome]],
    *,
    resamples: int = 1000,
    seed: int = 0,
) -> tuple[float, float, float] | None:
    """CI for mean(brier_cal - brier_raw) resampling player-game clusters, not rows."""
    usable = [(s, o) for s, o in rows if o.brier_cal is not None and o.brier_raw is not None]
    if len(usable) < 2:
        return None
    clusters: dict[tuple[int, date], list[float]] = defaultdict(list)
    for s, o in usable:
        assert o.brier_cal is not None and o.brier_raw is not None
        clusters[(s.player_id, s.target_date)].append(o.brier_cal - o.brier_raw)
    groups = [np.array(v) for v in clusters.values()]
    if len(groups) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    for i in range(resamples):
        idx = rng.integers(0, len(groups), size=len(groups))
        picked = np.concatenate([groups[j] for j in idx])
        means[i] = picked.mean()
    est = float(np.concatenate(groups).mean())
    return est, float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _sample_bucket(n: int) -> str:
    return "10-29" if n < 30 else "30-79" if n < 80 else "80+"


def build_monitor(
    session: Session, *, since: date | None = None, through: date | None = None
) -> MonitorReport:
    stmt = select(PredictionSnapshot, PredictionOutcome).outerjoin(
        PredictionOutcome, PredictionOutcome.snapshot_id == PredictionSnapshot.id
    )
    if since is not None:
        stmt = stmt.where(PredictionSnapshot.target_date >= since)
    if through is not None:
        stmt = stmt.where(PredictionSnapshot.target_date <= through)
    pairs = [(s, o) for s, o in session.execute(stmt)]
    by_status: dict[str, int] = defaultdict(int)
    for _, o in pairs:
        by_status["pending" if o is None else o.status] += 1
    rows = [(s, o) for s, o in pairs if o is not None]
    overall = _metrics(rows)
    report = MonitorReport(
        since=since,
        through=through,
        snapshots=len(pairs),
        by_status=dict(by_status),
        overall=overall,
        preliminary=(overall.n if overall else 0) < PRELIMINARY_BELOW,
    )
    groups: dict[str, dict[str, list[tuple[PredictionSnapshot, PredictionOutcome]]]] = {
        "date": defaultdict(list),
        "stat": defaultdict(list),
        "model_version": defaultdict(list),
        "sample_size": defaultdict(list),
        "line_source": defaultdict(list),
    }
    for s, o in rows:
        groups["date"][s.target_date.isoformat()].append((s, o))
        groups["stat"][s.stat_type].append((s, o))
        groups["model_version"][f"{s.model_name}-{s.model_version}"].append((s, o))
        groups["sample_size"][_sample_bucket(s.sample_size)].append((s, o))
        groups["line_source"][s.line_source].append((s, o))
    for name, g in groups.items():
        report.by_group[name] = {
            k: m for k, v in sorted(g.items()) if (m := _metrics(v)) is not None
        }
    prob_rows = [(s, o) for s, o in rows if o.status == "evaluated" and o.outcome_more is not None]
    if prob_rows:
        p_raw = np.array(
            [
                s.p_more / (s.p_more + s.p_less)
                for s, _ in prob_rows
                if s.p_more is not None and s.p_less is not None
            ]
        )
        y = np.array([1.0 if o.outcome_more else 0.0 for _, o in prob_rows])
        report.reliability_raw = reliability_table(p_raw, y)
        cal = [
            (s, o) for s, o in prob_rows if s.p_more_cal is not None and s.p_less_cal is not None
        ]
        if cal:
            p_cal = np.array([s.p_more_cal / (s.p_more_cal + s.p_less_cal) for s, _ in cal])  # type: ignore[operator]
            report.reliability_cal = reliability_table(
                p_cal, np.array([1.0 if o.outcome_more else 0.0 for _, o in cal])
            )
        report.brier_diff_ci = _cluster_bootstrap_brier_diff(rows)
    report.notes.append(
        "lines are synthetic (or manual) thresholds; hit rates are not market results"
    )
    if report.preliminary:
        report.notes.append(f"preliminary: fewer than {PRELIMINARY_BELOW} evaluated predictions")
    return report


def _fmt(x: float | None, d: int = 3) -> str:
    return "n/a" if x is None or x != x else f"{x:.{d}f}"


def render_monitor(report: MonitorReport) -> str:
    lines = [
        f"Prospective monitoring (since={report.since or 'start'} "
        f"through={report.through or 'latest'})",
        f"  snapshots={report.snapshots} by status: {report.by_status}",
    ]
    m = report.overall
    if m is None:
        lines.append("  no evaluated predictions yet")
    else:
        lines.append(
            f"  evaluated n={m.n}  MAE={m.mae:.3f} RMSE={m.rmse:.3f} bias={m.bias:+.3f} "
            f"80%cov={m.coverage:.3f}"
        )
        lines.append(
            f"  probabilistic n={m.n_prob} (pushes excluded: {m.pushes})  "
            f"Brier raw={_fmt(m.brier_raw, 4)} cal={_fmt(m.brier_cal, 4)}  "
            f"logloss raw={_fmt(m.logloss_raw, 4)} cal={_fmt(m.logloss_cal, 4)}  "
            f"ECE raw={_fmt(m.ece_raw, 4)} cal={_fmt(m.ece_cal, 4)}"
        )
        if report.brier_diff_ci:
            est, lo, hi = report.brier_diff_ci
            lines.append(
                f"  Brier(cal) - Brier(raw) = {est:+.4f} [{lo:+.4f}, {hi:+.4f}] "
                "(cluster bootstrap by player-game)"
            )
        for name, grp in report.by_group.items():
            lines.append(f"  by {name}:")
            for key, gm in grp.items():
                lines.append(
                    f"    {key:14s} n={gm.n:5d} MAE={gm.mae:.3f} bias={gm.bias:+.3f} "
                    f"cov={gm.coverage:.3f} Brier raw={_fmt(gm.brier_raw, 4)} "
                    f"cal={_fmt(gm.brier_cal, 4)}"
                )
        if report.reliability_raw:
            lines.append("  reliability (raw -> cal), bins with n>=20:")
            cal_by_bin = {round(b.lo, 2): b for b in report.reliability_cal}
            for b in report.reliability_raw:
                if b.n >= 20:
                    c = cal_by_bin.get(round(b.lo, 2))
                    cal_txt = (
                        f" | cal pred={c.mean_predicted:.3f} obs={c.observed_frequency:.3f}"
                        if c and c.n
                        else ""
                    )
                    lines.append(
                        f"    [{b.lo:.1f},{b.hi:.1f}) n={b.n:4d} pred={b.mean_predicted:.3f} "
                        f"obs={b.observed_frequency:.3f}{cal_txt}"
                    )
    for note in report.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)
