"""Predictive distributions and More/Less/Push probabilities.

Given a projected mean and an estimate of outcome variance, build a predictive
distribution over the integer stat value and integrate it against the offered
line.

Families
--------
* **Poisson**: variance == mean. Appropriate when observed dispersion is close
  to the mean (often rebounds/assists for low-usage players).
* **Negative Binomial**: variance > mean. Parameterised by (mean, variance):
  ``r = mean^2 / (var - mean)``, ``p = r / (r + mean)``. Falls back to Poisson
  when the sample variance does not exceed the mean. Default for points.
* **Normal**: continuous benchmark with continuity correction at +/- 0.5.
* **Empirical**: relative frequencies of historical values. Needs a reasonable
  sample; falls back to ``auto`` otherwise.

Variance estimate
-----------------
The sample variance of the stat is inflated by ``(1 + 1/n)`` to reflect that the
mean itself is estimated from ``n`` games (predictive variance for a new
observation under a normal-mean model). Minutes variability is already embedded
in the observed stat variance and is not added again.

None of this is calibrated. Phase 3 evaluates these choices against real data.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from scipy import stats

from prizepicks_predictor.prediction.schemas import (
    DistributionFamily,
    DistributionSummary,
    OutcomeProbabilities,
)

MIN_EMPIRICAL_SAMPLE = 15
MIN_STD = 0.75  # floor so a flat history does not collapse into certainty
MAX_NB_R = 1_000.0  # beyond this NB is indistinguishable from Poisson


@dataclass(frozen=True)
class VarianceEstimate:
    variance: float
    sample_size: int
    inflated: bool


def predictive_variance(values: Sequence[float], *, mean: float) -> VarianceEstimate:
    """Sample variance inflated by (1 + 1/n). Uses a Poisson-like floor if n < 2."""
    n = len(values)
    if n < 2:
        return VarianceEstimate(variance=max(mean, MIN_STD**2), sample_size=n, inflated=False)
    mu = sum(values) / n
    sample_var = sum((v - mu) ** 2 for v in values) / (n - 1)
    var = max(sample_var * (1 + 1 / n), MIN_STD**2)
    return VarianceEstimate(variance=var, sample_size=n, inflated=True)


class Predictive:
    """Thin wrapper over a scipy frozen distribution (or empirical pmf) on integers."""

    def __init__(
        self,
        family: DistributionFamily,
        *,
        mean: float,
        variance: float,
        pmf_table: dict[int, float] | None = None,
        notes: Sequence[str] = (),
    ) -> None:
        self.family = family
        self.mean = mean
        self.variance = variance
        self.notes = list(notes)
        self.parameters: dict[str, float] = {}
        self._pmf_table = pmf_table
        self._dist: Any = None
        self._zero_mass = 0.0  # normal only: mass below -0.5 folded into zero

        if family is DistributionFamily.POISSON:
            self.variance = mean
            self.parameters = {"mu": mean}
            self._dist = stats.poisson(mu=mean)
        elif family is DistributionFamily.NEGATIVE_BINOMIAL:
            r = mean**2 / (variance - mean)
            r = min(r, MAX_NB_R)
            p = r / (r + mean)
            self.parameters = {"r": r, "p": p}
            self._dist = stats.nbinom(n=r, p=p)
        elif family is DistributionFamily.NORMAL:
            sigma = math.sqrt(variance)
            self.parameters = {"mu": mean, "sigma": sigma}
            self._dist = stats.norm(loc=mean, scale=sigma)
            self._zero_mass = float(self._dist.cdf(-0.5))
        elif family is DistributionFamily.EMPIRICAL:
            if not pmf_table:
                raise ValueError("empirical distribution requires a pmf table")
            self.parameters = {"support_size": float(len(pmf_table))}
        else:  # pragma: no cover - AUTO is resolved before construction
            raise ValueError(f"unsupported family {family}")

    # --- integer-outcome primitives --------------------------------------
    def pmf(self, k: int) -> float:
        if k < 0:
            return 0.0
        if self.family is DistributionFamily.EMPIRICAL:
            assert self._pmf_table is not None
            return self._pmf_table.get(k, 0.0)
        if self.family is DistributionFamily.NORMAL:
            return float(self._cdf_cont(k + 0.5) - self._cdf_cont(k - 0.5))
        return float(self._dist.pmf(k))

    def cdf(self, k: int) -> float:
        """P(X <= k) for integer k."""
        if k < 0:
            return 0.0
        if self.family is DistributionFamily.EMPIRICAL:
            assert self._pmf_table is not None
            return sum(p for v, p in self._pmf_table.items() if v <= k)
        if self.family is DistributionFamily.NORMAL:
            return float(self._cdf_cont(k + 0.5))
        return float(self._dist.cdf(k))

    def _cdf_cont(self, x: float) -> float:
        """Normal CDF truncated at -0.5 so the integer pmf over k >= 0 sums to 1."""
        if x < -0.5:
            return 0.0
        return (float(self._dist.cdf(x)) - self._zero_mass) / (1.0 - self._zero_mass)

    def quantile(self, q: float) -> float:
        if self.family is DistributionFamily.EMPIRICAL:
            assert self._pmf_table is not None
            acc = 0.0
            for v in sorted(self._pmf_table):
                acc += self._pmf_table[v]
                if acc >= q - 1e-12:
                    return float(v)
            return float(max(self._pmf_table))
        if self.family is DistributionFamily.NORMAL:
            return max(0.0, float(self._dist.ppf(q)))
        return float(self._dist.ppf(q))

    def std(self) -> float:
        return math.sqrt(self.variance)

    def summary(self, variance_sample_size: int) -> DistributionSummary:
        return DistributionSummary(
            family=self.family,
            mean=self.mean,
            std=self.std(),
            parameters=self.parameters,
            variance_sample_size=variance_sample_size,
            notes=self.notes,
        )


def outcome_probabilities(dist: Predictive, line: float) -> OutcomeProbabilities:
    """P(more), P(less), P(push) for an integer-valued stat against ``line``.

    Integer line L: push = P(X = L), more = P(X > L), less = P(X < L).
    Half-point line L: push = 0, more = P(X >= ceil(L)), less = P(X <= floor(L)).
    """
    if math.isclose(line, round(line), abs_tol=1e-9):
        k = round(line)
        push = dist.pmf(k)
        less = dist.cdf(k - 1)
        more = max(0.0, 1.0 - less - push)
    else:
        k = math.floor(line)
        push = 0.0
        less = dist.cdf(k)
        more = max(0.0, 1.0 - less)
    total = more + less + push
    if total <= 0:  # pragma: no cover - defensive
        raise ValueError("degenerate distribution")
    return OutcomeProbabilities(more=more / total, less=less / total, push=push / total)


def choose_family(mean: float, variance: float) -> DistributionFamily:
    """AUTO rule: Negative Binomial when overdispersed, Poisson otherwise."""
    return (
        DistributionFamily.NEGATIVE_BINOMIAL
        if variance > mean * 1.05 and mean > 0
        else DistributionFamily.POISSON
    )


def empirical_pmf(values: Sequence[float]) -> dict[int, float]:
    counts: dict[int, int] = {}
    for v in values:
        counts[round(v)] = counts.get(round(v), 0) + 1
    n = len(values)
    return {k: c / n for k, c in sorted(counts.items())}


def build_predictive(
    family: DistributionFamily,
    *,
    mean: float,
    variance: float,
    history_values: Sequence[float],
) -> Predictive:
    """Resolve ``family`` (including AUTO and fallbacks) into a ``Predictive``."""
    notes: list[str] = []
    mean = max(mean, 1e-6)

    if family is DistributionFamily.EMPIRICAL:
        if len(history_values) >= MIN_EMPIRICAL_SAMPLE:
            emp_mean = sum(history_values) / len(history_values)
            return Predictive(
                DistributionFamily.EMPIRICAL,
                mean=emp_mean,
                variance=variance,
                pmf_table=empirical_pmf(history_values),
                notes=["empirical: relative frequencies of historical values; ignores projection"],
            )
        notes.append(
            f"empirical needs >= {MIN_EMPIRICAL_SAMPLE} games, have {len(history_values)}; "
            "fell back to auto"
        )
        family = DistributionFamily.AUTO

    if family is DistributionFamily.AUTO:
        family = choose_family(mean, variance)
        notes.append(f"auto selected {family.value} (mean={mean:.2f}, var={variance:.2f})")

    if family is DistributionFamily.NEGATIVE_BINOMIAL and variance <= mean * 1.0001:
        notes.append("variance <= mean; negative binomial undefined, using poisson")
        family = DistributionFamily.POISSON

    if family is DistributionFamily.NORMAL:
        notes.append("normal approximation with +/-0.5 continuity correction, truncated at 0")

    return Predictive(family, mean=mean, variance=variance, notes=notes)
