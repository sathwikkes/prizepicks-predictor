from __future__ import annotations

import pytest

from prizepicks_predictor.prediction.distributions import (
    MIN_EMPIRICAL_SAMPLE,
    Predictive,
    build_predictive,
    choose_family,
    empirical_pmf,
    outcome_probabilities,
    predictive_variance,
)
from prizepicks_predictor.prediction.schemas import DistributionFamily as F

HIST20 = [18.0, 22.0, 25.0, 19.0, 30.0, 27.0, 21.0, 24.0, 26.0, 20.0] * 2


def _all_families(mean: float = 24.0, var: float = 48.0) -> list[Predictive]:
    return [
        build_predictive(F.POISSON, mean=mean, variance=var, history_values=HIST20),
        build_predictive(F.NEGATIVE_BINOMIAL, mean=mean, variance=var, history_values=HIST20),
        build_predictive(F.NORMAL, mean=mean, variance=var, history_values=HIST20),
        build_predictive(F.EMPIRICAL, mean=mean, variance=var, history_values=HIST20),
    ]


@pytest.mark.parametrize("dist", _all_families(), ids=lambda d: d.family.value)
def test_pmf_sums_to_one_and_matches_cdf(dist: Predictive) -> None:
    total = sum(dist.pmf(k) for k in range(0, 400))
    assert total == pytest.approx(1.0, abs=1e-6)
    running = 0.0
    for k in range(0, 60):
        running += dist.pmf(k)
        assert dist.cdf(k) == pytest.approx(running, abs=1e-9)
    assert dist.pmf(-1) == 0.0 and dist.cdf(-1) == 0.0


@pytest.mark.parametrize("dist", _all_families(), ids=lambda d: d.family.value)
@pytest.mark.parametrize("line", [0.5, 17.5, 23.5, 24.0, 24.5, 30.0, 59.5])
def test_probabilities_valid_and_sum_to_one(dist: Predictive, line: float) -> None:
    p = outcome_probabilities(dist, line)
    assert 0 <= p.more <= 1 and 0 <= p.less <= 1 and 0 <= p.push <= 1
    assert p.more + p.less + p.push == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("dist", _all_families(), ids=lambda d: d.family.value)
def test_half_point_line_never_pushes(dist: Predictive) -> None:
    assert outcome_probabilities(dist, 24.5).push == 0.0


@pytest.mark.parametrize("dist", _all_families()[:3], ids=lambda d: d.family.value)
def test_integer_line_push_equals_pmf(dist: Predictive) -> None:
    p = outcome_probabilities(dist, 24.0)
    assert p.push == pytest.approx(dist.pmf(24), abs=1e-9)
    assert p.push > 0
    assert p.less == pytest.approx(dist.cdf(23), abs=1e-9)


def test_integer_and_half_line_identity() -> None:
    """P(more | 23.5) == P(more | 24) + P(push | 24)."""
    dist = build_predictive(F.NEGATIVE_BINOMIAL, mean=24, variance=50, history_values=HIST20)
    half = outcome_probabilities(dist, 23.5)
    whole = outcome_probabilities(dist, 24.0)
    assert half.more == pytest.approx(whole.more + whole.push, abs=1e-9)
    assert half.less == pytest.approx(whole.less, abs=1e-9)


@pytest.mark.parametrize("dist", _all_families(), ids=lambda d: d.family.value)
def test_p_more_decreases_with_line(dist: Predictive) -> None:
    probs = [outcome_probabilities(dist, line).more for line in (10.5, 20.5, 24.5, 30.5, 40.5)]
    assert probs == sorted(probs, reverse=True)


def test_more_near_half_when_line_at_mean() -> None:
    dist = build_predictive(F.NORMAL, mean=24.5, variance=36, history_values=HIST20)
    p = outcome_probabilities(dist, 24.5)
    assert p.more == pytest.approx(0.5, abs=0.02)


def test_negative_binomial_parameters() -> None:
    dist = build_predictive(F.NEGATIVE_BINOMIAL, mean=25, variance=50, history_values=HIST20)
    assert dist.family is F.NEGATIVE_BINOMIAL
    assert dist.parameters["r"] == pytest.approx(25.0)
    assert dist.parameters["p"] == pytest.approx(0.5)
    implied_mean = sum(k * dist.pmf(k) for k in range(0, 400))
    implied_var = sum((k - 25) ** 2 * dist.pmf(k) for k in range(0, 400))
    assert implied_mean == pytest.approx(25.0, abs=1e-3)
    assert implied_var == pytest.approx(50.0, abs=1e-2)


def test_negative_binomial_falls_back_to_poisson_when_underdispersed() -> None:
    dist = build_predictive(F.NEGATIVE_BINOMIAL, mean=10, variance=8, history_values=HIST20)
    assert dist.family is F.POISSON
    assert any("poisson" in n for n in dist.notes)


def test_poisson_variance_equals_mean() -> None:
    dist = build_predictive(F.POISSON, mean=10, variance=99, history_values=HIST20)
    assert dist.std() == pytest.approx(10**0.5)


def test_auto_selection_rule() -> None:
    assert choose_family(20, 40) is F.NEGATIVE_BINOMIAL
    assert choose_family(20, 20.5) is F.POISSON
    assert (
        build_predictive(F.AUTO, mean=20, variance=40, history_values=[]).family
        is F.NEGATIVE_BINOMIAL
    )
    assert build_predictive(F.AUTO, mean=20, variance=19, history_values=[]).family is F.POISSON


def test_normal_truncation_handles_mean_near_zero() -> None:
    dist = build_predictive(F.NORMAL, mean=1.0, variance=4.0, history_values=[])
    assert sum(dist.pmf(k) for k in range(0, 60)) == pytest.approx(1.0, abs=1e-6)
    assert dist.quantile(0.1) >= 0


def test_empirical_distribution() -> None:
    pmf = empirical_pmf([1, 1, 2, 3])
    assert pmf == {1: 0.5, 2: 0.25, 3: 0.25}
    dist = build_predictive(F.EMPIRICAL, mean=99, variance=1, history_values=HIST20)
    assert dist.family is F.EMPIRICAL
    assert dist.mean == pytest.approx(sum(HIST20) / len(HIST20))  # ignores projection
    assert dist.pmf(25) == pytest.approx(2 / 20)
    assert dist.quantile(0.5) == 22.0  # 10 of 20 values are <= 22
    assert outcome_probabilities(dist, 24.5).more == pytest.approx(
        sum(1 for v in HIST20 if v > 24.5) / 20
    )


def test_empirical_falls_back_when_sample_too_small() -> None:
    small = HIST20[: MIN_EMPIRICAL_SAMPLE - 1]
    dist = build_predictive(F.EMPIRICAL, mean=24, variance=48, history_values=small)
    assert dist.family is not F.EMPIRICAL
    assert any("fell back" in n for n in dist.notes)


def test_predictive_variance_inflation() -> None:
    est = predictive_variance([10.0, 20.0, 30.0], mean=20)
    assert est.sample_size == 3 and est.inflated
    assert est.variance == pytest.approx(100 * (1 + 1 / 3))


def test_predictive_variance_small_samples() -> None:
    one = predictive_variance([20.0], mean=20)
    assert one.variance == pytest.approx(20.0) and not one.inflated
    flat = predictive_variance([5.0, 5.0, 5.0], mean=5)
    assert flat.variance > 0  # floored, never certainty
