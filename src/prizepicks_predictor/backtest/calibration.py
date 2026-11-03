"""Lightweight probability calibrators fitted on validation predictions only.

* ``PlattCalibrator``: logistic regression on logit(p), two parameters.
* ``IsotonicCalibrator``: pool-adjacent-violators, monotone step function.

Both are fitted on (predicted P(more), outcome) pairs from the validation season and
applied unchanged to the held-out season. The uncalibrated probabilities stay available
for comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import expit, logit

from prizepicks_predictor.backtest.metrics import EPS

Array = NDArray[np.float64]

#: Below this many fitting pairs a calibrator is considered unsupported.
MIN_FIT_SIZE = 2000


def _clip(p: object) -> Array:
    return np.clip(np.asarray(p, dtype=np.float64), EPS, 1 - EPS)


@dataclass(frozen=True)
class PlattCalibrator:
    a: float
    b: float
    n_fit: int

    @classmethod
    def fit(cls, p: object, y: object) -> PlattCalibrator:
        z = logit(_clip(p))
        yy = np.asarray(y, dtype=np.float64)

        def loss(theta: Array) -> float:
            q = np.clip(expit(theta[0] * z + theta[1]), EPS, 1 - EPS)
            return float(-np.mean(yy * np.log(q) + (1 - yy) * np.log(1 - q)))

        res = minimize(loss, x0=np.array([1.0, 0.0]), method="L-BFGS-B")
        return cls(a=float(res.x[0]), b=float(res.x[1]), n_fit=len(yy))

    def apply(self, p: object) -> Array:
        return np.asarray(expit(self.a * logit(_clip(p)) + self.b), dtype=np.float64)

    def describe(self) -> str:
        return f"Platt(a={self.a:.3f}, b={self.b:.3f}; a<1 means the inputs were overconfident)"


@dataclass(frozen=True)
class IsotonicCalibrator:
    x: Array  # block right edges (sorted predicted probabilities)
    y: Array  # block values (observed frequencies, non-decreasing)
    n_fit: int

    @classmethod
    def fit(cls, p: object, y: object) -> IsotonicCalibrator:
        pp = np.asarray(p, dtype=np.float64)
        yy = np.asarray(y, dtype=np.float64)
        order = np.argsort(pp, kind="stable")
        pp, yy = pp[order], yy[order]
        # PAV with weights; blocks hold (sum, count, right_x)
        sums: list[float] = []
        counts: list[int] = []
        rights: list[float] = []
        for xi, yi in zip(pp, yy, strict=True):
            sums.append(float(yi))
            counts.append(1)
            rights.append(float(xi))
            while len(sums) > 1 and sums[-2] / counts[-2] > sums[-1] / counts[-1]:
                sums[-2] += sums[-1]
                counts[-2] += counts[-1]
                rights[-2] = rights[-1]
                sums.pop()
                counts.pop()
                rights.pop()
        values = np.array([s / c for s, c in zip(sums, counts, strict=True)])
        return cls(x=np.array(rights), y=values, n_fit=len(pp))

    def apply(self, p: object) -> Array:
        pp = np.asarray(p, dtype=np.float64)
        idx = np.clip(np.searchsorted(self.x, pp, side="left"), 0, len(self.y) - 1)
        return np.asarray(self.y[idx], dtype=np.float64)

    def describe(self) -> str:
        return f"Isotonic({len(self.y)} blocks)"
