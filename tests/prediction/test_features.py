from __future__ import annotations

from datetime import date

import pytest

from prizepicks_predictor.prediction.features import (
    build_features,
    ewma,
    mean,
    per_minute_rate,
    sample_std,
)
from prizepicks_predictor.prediction.schemas import StatType, WarningCode
from tests.prediction.conftest import row, series

NOV = date(2024, 11, 1)


def test_rolling_means_and_season_mean(as_of: date) -> None:
    hist = series(NOV, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    assert f.games_total == 12
    assert f.last5_mean == pytest.approx(100.0)  # 80..120
    assert f.last10_mean == pytest.approx(75.0)  # 30..120
    assert f.season_mean == pytest.approx(65.0)
    assert f.season_start_year == 2024 and f.season_games == 12
    assert f.last5_games == 5 and f.last10_games == 10


def test_fewer_games_than_window_uses_available(as_of: date) -> None:
    f = build_features(series(NOV, [10, 20, 30]), stat=StatType.POINTS, as_of_date=as_of)
    assert f.last5_games == 3 and f.last5_mean == pytest.approx(20.0)
    assert f.last10_games == 3


def test_ewma_pure_function() -> None:
    assert ewma([], 5) is None
    assert ewma([7.0], 5) == pytest.approx(7.0)
    assert ewma([4.0, 4.0, 4.0], 2) == pytest.approx(4.0)
    # halflife 1: weights 0.5 (older), 1.0 (newest)
    assert ewma([10.0, 20.0], 1) == pytest.approx((5 + 20) / 1.5)
    with pytest.raises(ValueError):
        ewma([1.0], 0)


def test_ewma_weights_recent_more(as_of: date) -> None:
    rising = build_features(
        series(NOV, [10, 10, 10, 30, 30, 30]), stat=StatType.POINTS, as_of_date=as_of
    )
    falling = build_features(
        series(NOV, [30, 30, 30, 10, 10, 10]), stat=StatType.POINTS, as_of_date=as_of
    )
    assert rising.ewma_mean is not None and falling.ewma_mean is not None
    assert rising.ewma_mean > 20 > falling.ewma_mean
    assert rising.season_mean == pytest.approx(20.0)


def test_std_and_per_minute() -> None:
    assert sample_std([10.0, 20.0, 30.0]) == pytest.approx(10.0)
    assert sample_std([5.0]) is None
    assert mean([]) is None
    assert per_minute_rate(30, 60) == pytest.approx(0.5)
    assert per_minute_rate(30, 0) is None


def test_per_minute_features(as_of: date) -> None:
    hist = [
        row(date(2024, 11, 10), points=20, minutes=40),
        row(date(2024, 11, 8), points=10, minutes=20),
    ]
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    assert f.per_minute_recent == pytest.approx(0.5)
    assert f.per_minute_season == pytest.approx(0.5)
    assert f.minutes_recent_mean == pytest.approx(30.0)
    assert f.rolling10_std == pytest.approx(sample_std([20.0, 10.0]))


def test_zero_minute_games_excluded(as_of: date) -> None:
    hist = [
        row(date(2024, 11, 10), points=0, minutes=0),  # DNP
        row(date(2024, 11, 8), points=20),
        row(date(2024, 11, 6), points=30),
    ]
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    assert f.games_total == 2 and f.dnp_games == 1
    assert f.season_mean == pytest.approx(25.0)
    assert WarningCode.DNP_GAMES_EXCLUDED in f.warnings
    # rest days still count from the DNP date (it was a scheduled game)
    assert f.rest_days == 21


def test_empty_history(as_of: date) -> None:
    f = build_features([], stat=StatType.POINTS, as_of_date=as_of)
    assert f.games_total == 0
    assert f.season_mean is None and f.ewma_mean is None and f.last5_mean is None
    assert f.rest_days is None and f.back_to_back is None
    assert f.season_start_year is None


def test_season_fallback_to_previous_season() -> None:
    hist = series(date(2024, 3, 1), [20, 22, 24])  # 2023-24 season
    f = build_features(hist, stat=StatType.POINTS, as_of_date=date(2024, 10, 25))
    assert f.season_start_year == 2023 and f.season_games == 3
    assert WarningCode.SEASON_FALLBACK in f.warnings
    assert WarningCode.LONG_LAYOFF in f.warnings


def test_season_mean_excludes_previous_season(as_of: date) -> None:
    old = series(date(2024, 3, 1), [50, 50, 50])
    new = series(NOV, [10, 10])
    f = build_features(new + old, stat=StatType.POINTS, as_of_date=as_of)
    assert f.season_games == 2 and f.season_mean == pytest.approx(10.0)
    assert f.games_total == 5
    assert WarningCode.WINDOW_CROSSES_SEASON in f.warnings


def test_team_change_warning(as_of: date) -> None:
    hist = [row(date(2024, 11, 10), team="MIA"), row(date(2024, 11, 8), team="BOS")]
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    assert f.latest_team == "MIA"
    assert f.teams_in_last10 == ["BOS", "MIA"]
    assert WarningCode.TEAM_CHANGE_IN_WINDOW in f.warnings


def test_rest_days_and_back_to_back() -> None:
    f = build_features(
        [row(date(2024, 11, 30))], stat=StatType.POINTS, as_of_date=date(2024, 12, 1)
    )
    assert f.rest_days == 1 and f.back_to_back is True
    g = build_features(
        [row(date(2024, 11, 28))], stat=StatType.POINTS, as_of_date=date(2024, 12, 1)
    )
    assert g.rest_days == 3 and g.back_to_back is False


def test_home_away_split(as_of: date) -> None:
    hist = [
        row(date(2024, 11, 10), points=30, is_home=True),
        row(date(2024, 11, 8), points=10, is_home=False),
        row(date(2024, 11, 6), points=20, is_home=True),
    ]
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    assert f.home_mean == pytest.approx(25.0) and f.home_games == 2
    assert f.away_mean == pytest.approx(10.0) and f.away_games == 1


def test_minutes_volatility_warning(as_of: date) -> None:
    hist = [row(date(2024, 11, 10), minutes=40), row(date(2024, 11, 8), minutes=12)]
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    assert WarningCode.MINUTES_VOLATILE in f.warnings


def test_other_stat_types(as_of: date) -> None:
    hist = [
        row(date(2024, 11, 10), rebounds=12, assists=2),
        row(date(2024, 11, 8), rebounds=8, assists=6),
    ]
    reb = build_features(hist, stat=StatType.REBOUNDS, as_of_date=as_of)
    ast = build_features(hist, stat=StatType.ASSISTS, as_of_date=as_of)
    assert reb.season_mean == pytest.approx(10.0)
    assert ast.season_mean == pytest.approx(4.0)


def test_unavailable_features_listed(as_of: date) -> None:
    f = build_features([row(date(2024, 11, 10))], stat=StatType.POINTS, as_of_date=as_of)
    assert "opponent_defensive_rating" in f.unavailable
    assert "injury_report" in f.unavailable
    assert WarningCode.MATCHUP_CONTEXT_UNAVAILABLE in f.warnings


def test_build_features_rejects_game_on_or_after_cutoff(as_of: date) -> None:
    with pytest.raises(ValueError, match="leakage"):
        build_features([row(as_of)], stat=StatType.POINTS, as_of_date=as_of)
    with pytest.raises(ValueError, match="leakage"):
        build_features([row(date(2024, 12, 5))], stat=StatType.POINTS, as_of_date=as_of)
