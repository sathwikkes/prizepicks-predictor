"""Evaluation metrics as pure functions on numpy arrays."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
EPS = 1e-6


def _f(x: object) -> Array:
    return np.asarray(x, dtype=np.float64)


@dataclass(frozen=True)
class PointMetrics:
    n: int
    mae: float
    rmse: float
    bias: float  # mean(prediction - actual); positive = over-forecast


def point_metrics(pred: object, actual: object) -> PointMetrics:
    p, a = _f(pred), _f(actual)
    err = p - a
    return PointMetrics(
        n=len(err),
        mae=float(np.mean(np.abs(err))) if len(err) else float("nan"),
        rmse=float(np.sqrt(np.mean(err**2))) if len(err) else float("nan"),
        bias=float(np.mean(err)) if len(err) else float("nan"),
    )


def brier(p: object, y: object) -> float:
    pp, yy = _f(p), _f(y)
    return float(np.mean((pp - yy) ** 2)) if len(pp) else float("nan")


def log_loss(p: object, y: object) -> float:
    pp, yy = np.clip(_f(p), EPS, 1 - EPS), _f(y)
    return float(-np.mean(yy * np.log(pp) + (1 - yy) * np.log(1 - pp))) if len(pp) else float("nan")


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    n: int
    mean_predicted: float
    observed_frequency: float


def reliability_table(p: object, y: object, bins: int = 10) -> list[ReliabilityBin]:
    """Observed frequency of the event per predicted-probability bin."""
    pp, yy = _f(p), _f(y)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(pp, edges[1:-1], right=False), 0, bins - 1)
    out: list[ReliabilityBin] = []
    for b in range(bins):
        mask = idx == b
        n = int(mask.sum())
        out.append(
            ReliabilityBin(
                lo=float(edges[b]),
                hi=float(edges[b + 1]),
                n=n,
                mean_predicted=float(pp[mask].mean()) if n else float("nan"),
                observed_frequency=float(yy[mask].mean()) if n else float("nan"),
            )
        )
    return out


def expected_calibration_error(p: object, y: object, bins: int = 10) -> float:
    """Weighted mean |observed - predicted| across bins (lower is better)."""
    table = reliability_table(p, y, bins)
    total = sum(b.n for b in table)
    if not total:
        return float("nan")
    return float(
        sum(b.n * abs(b.observed_frequency - b.mean_predicted) for b in table if b.n) / total
    )


def interval_coverage(lo: object, hi: object, actual: object) -> tuple[float, float]:
    """(fraction of actuals inside [lo, hi], mean width)."""
    l_, h_, a = _f(lo), _f(hi), _f(actual)
    if not len(a):
        return float("nan"), float("nan")
    return float(np.mean((a >= l_) & (a <= h_))), float(np.mean(h_ - l_))


@dataclass(frozen=True)
class BootstrapCI:
    estimate: float
    lo: float
    hi: float
    n: int
    resamples: int

    @property
    def excludes_zero(self) -> bool:
        return self.lo > 0 or self.hi < 0


def paired_bootstrap_mean_diff(
    a: object, b: object, *, resamples: int = 1000, seed: int = 0, level: float = 0.95
) -> BootstrapCI:
    """CI for mean(a - b) over paired observations (e.g. per-observation |error| of two models).

    Observations are resampled with replacement; pairing is preserved so model noise
    common to both cancels.
    """
    aa, bb = _f(a), _f(b)
    if len(aa) != len(bb):
        raise ValueError("paired arrays must have equal length")
    n = len(aa)
    diff = aa - bb
    if n == 0:
        return BootstrapCI(float("nan"), float("nan"), float("nan"), 0, resamples)
    rng = np.random.default_rng(seed)
    # Chunked so a 70k-observation run does not allocate a 70k x 1000 index matrix.
    means = np.empty(resamples)
    chunk = max(1, min(resamples, 20_000_000 // max(n, 1)))
    for start in range(0, resamples, chunk):
        size = min(chunk, resamples - start)
        idx = rng.integers(0, n, size=(size, n))
        means[start : start + size] = diff[idx].mean(axis=1)
    alpha = (1 - level) / 2
    return BootstrapCI(
        estimate=float(diff.mean()),
        lo=float(np.quantile(means, alpha)),
        hi=float(np.quantile(means, 1 - alpha)),
        n=n,
        resamples=resamples,
    )
