from __future__ import annotations

import numpy as np
import pytest

from prizepicks_predictor.backtest.config import BacktestConfig, parse_season, season_label
from prizepicks_predictor.backtest.lines import base_line, synthetic_lines
from prizepicks_predictor.backtest.metrics import (
    brier,
    expected_calibration_error,
    interval_coverage,
    log_loss,
    paired_bootstrap_mean_diff,
    point_metrics,
    reliability_table,
)


def test_parse_season() -> None:
    assert parse_season("2024-25") == 2024
    assert parse_season("2024") == 2024
    assert parse_season(2023) == 2023
    assert season_label(1999) == "1999-00"
    with pytest.raises(ValueError):
        parse_season("2024-26")
    with pytest.raises(ValueError):
        parse_season("24-25")


def test_config_validation() -> None:
    cfg = BacktestConfig(seasons=[2024])
    assert cfg.models and cfg.families and cfg.season_labels == ["2024-25"]
    with pytest.raises(ValueError, match="unknown models"):
        BacktestConfig(seasons=[2024], models=["xgboost"])
    with pytest.raises(ValueError, match="include_empirical"):
        BacktestConfig(seasons=[2024], families=["empirical"])  # type: ignore[list-item]


def test_synthetic_lines_are_half_point_pregame_and_model_free() -> None:
    history_desc = [24.0, 30.0, 18.0, 22.0, 27.0, 25.0, 19.0, 31.0, 23.0, 26.0, 99.0, 99.0]
    # median of the last 10 (ignores the two older 99s) = 24.5 -> floor 24 + 0.5
    assert base_line(history_desc, window=10) == 24.5
    lines = synthetic_lines(history_desc, window=10, offsets=[-2, -1, 0, 1, 2])
    assert lines == [22.5, 23.5, 24.5, 25.5, 26.5]
    assert all(abs(line * 2 - round(line * 2)) < 1e-9 and line % 1 == 0.5 for line in lines)
    # clipped at 0.5 so low-volume players still get valid thresholds
    assert synthetic_lines([0.0, 1.0, 0.0], window=10, offsets=[-2, 0]) == [0.5, 0.5]
    with pytest.raises(ValueError):
        base_line([], window=10)


def test_point_metrics() -> None:
    m = point_metrics([10, 20, 30], [12, 18, 30])
    assert m.n == 3
    assert m.mae == pytest.approx(4 / 3)
    assert m.rmse == pytest.approx(np.sqrt(8 / 3))
    assert m.bias == pytest.approx(0.0)
    assert np.isnan(point_metrics([], []).mae)


def test_brier_and_log_loss() -> None:
    assert brier([1.0, 0.0], [1, 0]) == 0.0
    assert brier([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(np.log(2))
    assert log_loss([1.0], [0]) < 20  # clipped, finite


def test_reliability_table_and_ece() -> None:
    p = np.array([0.05, 0.15, 0.15, 0.95, 0.95, 0.95])
    y = np.array([0, 0, 1, 1, 1, 0])
    table = reliability_table(p, y, bins=10)
    assert sum(b.n for b in table) == 6
    first = table[0]
    assert (
        first.n == 1
        and first.mean_predicted == pytest.approx(0.05)
        and first.observed_frequency == 0
    )
    last = table[9]
    assert last.n == 3 and last.observed_frequency == pytest.approx(2 / 3)
    assert expected_calibration_error(p, y) == pytest.approx(
        (1 * 0.05 + 2 * abs(0.5 - 0.15) + 3 * abs(2 / 3 - 0.95)) / 6
    )
    assert expected_calibration_error([0.5] * 10, [1, 0] * 5) == pytest.approx(0.0)


def test_interval_coverage() -> None:
    cov, width = interval_coverage([5, 5, 5], [10, 10, 10], [7, 10, 12])
    assert cov == pytest.approx(2 / 3) and width == 5.0


def test_paired_bootstrap_is_deterministic_and_detects_difference() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(1.0, 1.0, 500)
    b = a - 0.5  # b is better by 0.5 everywhere
    ci1 = paired_bootstrap_mean_diff(a, b, resamples=300, seed=7)
    ci2 = paired_bootstrap_mean_diff(a, b, resamples=300, seed=7)
    assert ci1 == ci2
    assert ci1.estimate == pytest.approx(0.5) and ci1.excludes_zero
    same = paired_bootstrap_mean_diff(a, a + rng.normal(0, 0.01, 500), resamples=300, seed=7)
    assert not same.excludes_zero
    with pytest.raises(ValueError):
        paired_bootstrap_mean_diff([1, 2], [1])
