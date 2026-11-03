from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.config import BacktestConfig
from prizepicks_predictor.backtest.engine import run_backtest
from prizepicks_predictor.db import ScheduledGame, Team
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction import PredictionRequest, StatType, WarningCode, predict
from prizepicks_predictor.prediction.features import build_features
from prizepicks_predictor.prediction.models import (
    MODELS,
    ScheduleAdjustedEwmaModel,
    load_schedule_factors,
)
from prizepicks_predictor.prediction.schedule_context import (
    ScheduleContext,
    build_schedule_context,
    games_in_window,
    rest_days,
)
from prizepicks_predictor.services import find_players
from prizepicks_predictor.services.team_schedule import team_game_dates_before
from tests.conftest import BASE_HEADER, write_csv
from tests.prediction.conftest import series

D = date(2024, 11, 10)


# --- pure functions ------------------------------------------------------------------------
def test_rest_days_and_windows() -> None:
    dates = [date(2024, 11, 1), date(2024, 11, 3), date(2024, 11, 9)]
    assert rest_days(dates, D) == 1
    assert rest_days(dates, date(2024, 11, 9)) == 6  # strictly before: the 9th itself is excluded
    assert rest_days(dates, date(2024, 11, 1)) is None
    assert games_in_window(dates, D, 3) == 1  # 7,8,9 -> only the 9th
    assert games_in_window(dates, D, 7) == 2
    assert games_in_window([], D, 7) == 0


def test_build_schedule_context_buckets() -> None:
    b2b = build_schedule_context([date(2024, 11, 9)], target_date=D, is_home=True)
    assert b2b.rest_days == 1 and b2b.back_to_back and b2b.rest_bucket == "back_to_back"
    three = build_schedule_context(
        [date(2024, 11, 7), date(2024, 11, 8)], target_date=D, is_home=False
    )
    assert three.rest_days == 2 and three.three_in_four and not three.back_to_back
    assert three.rest_bucket == "three_in_four"
    normal = build_schedule_context([date(2024, 11, 8)], target_date=D, is_home=None)
    assert normal.rest_bucket == "normal" and normal.is_home is None
    long = build_schedule_context([date(2024, 11, 5)], target_date=D, is_home=True)
    assert long.rest_days == 5 and long.rest_bucket == "long_rest"
    none = build_schedule_context([], target_date=D, is_home=True)
    assert none.rest_days is None and none.rest_bucket == "unknown" and none.source == "none"
    # date boundary: the day before counts as 1 day of rest; the same day is leakage
    assert (
        build_schedule_context([D - timedelta(days=1)], target_date=D, is_home=True).rest_days == 1
    )
    with pytest.raises(ValueError, match="leakage"):
        build_schedule_context([D], target_date=D, is_home=True)
    with pytest.raises(ValueError, match="leakage"):
        build_schedule_context([D + timedelta(days=1)], target_date=D, is_home=True)
    # three-in-four requires two games on the three preceding days, not just two in a week
    assert not build_schedule_context(
        [date(2024, 11, 4), date(2024, 11, 8)], target_date=D, is_home=True
    ).three_in_four


# --- model -------------------------------------------------------------------------------
def _features_with(ctx: ScheduleContext | None):  # type: ignore[no-untyped-def]
    f = build_features(
        series(date(2024, 10, 1), [20, 22, 24, 26, 28, 30]), stat=StatType.POINTS, as_of_date=D
    )
    return f.model_copy(update={"schedule": ctx})


def test_schedule_model_applies_factors_and_is_reproducible() -> None:
    factors = {"points": {"back_to_back": 0.95, "normal": 1.0, "home": 1.02, "away": 0.98}}
    model = ScheduleAdjustedEwmaModel(factors)
    base = MODELS["ewma"].project(_features_with(None)).projection
    b2b_home = model.project(
        _features_with(build_schedule_context([D - timedelta(days=1)], target_date=D, is_home=True))
    )
    assert b2b_home.projection == pytest.approx(base * 0.95 * 1.02)
    assert any("back_to_back" in a and "0.9500" in a for a in b2b_home.assumptions)
    again = model.project(
        _features_with(build_schedule_context([D - timedelta(days=1)], target_date=D, is_home=True))
    )
    assert again.projection == b2b_home.projection
    normal_away = model.project(
        _features_with(
            build_schedule_context([D - timedelta(days=2)], target_date=D, is_home=False)
        )
    )
    assert normal_away.projection == pytest.approx(base * 1.0 * 0.98)
    unknown = model.project(_features_with(None))
    assert unknown.projection == pytest.approx(base)
    assert any("no schedule context" in a for a in unknown.assumptions)
    venue_unknown = model.project(
        _features_with(build_schedule_context([D - timedelta(days=1)], target_date=D, is_home=None))
    )
    assert venue_unknown.projection == pytest.approx(base * 0.95)


def test_schedule_model_neutral_without_artifact(tmp_path: Path) -> None:
    load_schedule_factors.cache_clear()
    assert load_schedule_factors(tmp_path / "missing.json") == {}
    model = ScheduleAdjustedEwmaModel({})
    base = MODELS["ewma"].project(_features_with(None)).projection
    ctx = build_schedule_context([D - timedelta(days=1)], target_date=D, is_home=True)
    assert model.project(_features_with(ctx)).projection == pytest.approx(base)
    load_schedule_factors.cache_clear()


def test_registry_has_schedule_model() -> None:
    assert "ewma_schedule" in MODELS and MODELS["ewma_schedule"].version == "1.0"
    assert BacktestConfig(seasons=[2024], models=["ewma", "ewma_schedule"]).models == [
        "ewma",
        "ewma_schedule",
    ]


# --- service integration -------------------------------------------------------------------
@pytest.fixture
def loaded(session: Session, synthetic_csv: Path) -> Session:
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    return session


def test_team_dates_from_games_and_schedule(loaded: Session) -> None:
    dates, source = team_game_dates_before(loaded, "BOS", date(2024, 12, 1))
    assert source == "games" and dates and dates[-1] < date(2024, 12, 1)
    assert all(a < b for a, b in pairwise(dates))
    # a schedule row dated before the target that is not yet in box scores fills the gap
    bos = loaded.scalar(select(Team).where(Team.abbreviation == "BOS"))
    lal = loaded.scalar(select(Team).where(Team.abbreviation == "LAL"))
    assert bos and lal
    from datetime import datetime

    loaded.add(
        ScheduledGame(
            source="t",
            external_id="x",
            season_start_year=2024,
            game_date=date(2024, 11, 30),
            tipoff_utc=None,
            home_team_id=bos.id,
            away_team_id=lal.id,
            status="final",
            loaded_at=datetime(2024, 11, 30),
        )
    )
    loaded.commit()
    dates2, source2 = team_game_dates_before(loaded, "BOS", date(2024, 12, 1))
    assert source2 == "schedule" and dates2[-1] == date(2024, 11, 30)
    assert team_game_dates_before(loaded, "ZZZ", date(2024, 12, 1)) == ([], "none")


def test_predict_populates_schedule_context_and_warning(loaded: Session) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    res = predict(
        loaded,
        PredictionRequest(
            player_id=pid,
            stat_type=StatType.POINTS,
            line=24.5,
            as_of_date=date(2024, 11, 28),
            is_home=True,
        ),
    )
    sc = res.features.schedule
    assert (
        sc is not None and sc.rest_days == 1 and sc.back_to_back and sc.is_home is True
    )  # fixture: BOS played 11-27
    assert WarningCode.SCHEDULE_CONTEXT_UNAVAILABLE not in res.warnings
    sched = predict(
        loaded,
        PredictionRequest(
            player_id=pid,
            stat_type=StatType.POINTS,
            line=24.5,
            as_of_date=date(2024, 11, 28),
            model_name="ewma_schedule",
        ),
    )
    assert sched.model.name == "ewma_schedule" and sched.model.projection > 0


def test_schedule_context_never_uses_target_or_future_games(
    tmp_path: Path, session: Session
) -> None:
    rows = [f"2024-11-{d:02d},P1,Rest Test,BOS,LAL,home,30:00,20,5,4" for d in (1, 3, 5, 7, 9, 10)]
    load_game_logs(session, CsvGameLogProvider(write_csv(tmp_path / "a.csv", BASE_HEADER, *rows)))
    pid = find_players(session, "Rest Test")[0].id
    r = predict(
        session,
        PredictionRequest(
            player_id=pid, stat_type=StatType.POINTS, line=19.5, as_of_date=date(2024, 11, 10)
        ),
    )
    assert (
        r.features.schedule is not None and r.features.schedule.rest_days == 1
    )  # 11-09 -> 11-10; the 11-10 game itself excluded
    # adding future games changes nothing for an earlier target
    load_game_logs(
        session,
        CsvGameLogProvider(
            write_csv(
                tmp_path / "b.csv", BASE_HEADER, "2024-11-11,P1,Rest Test,BOS,LAL,home,30:00,50,5,4"
            )
        ),
    )
    r2 = predict(
        session,
        PredictionRequest(
            player_id=pid, stat_type=StatType.POINTS, line=19.5, as_of_date=date(2024, 11, 10)
        ),
    )
    assert r2.features.schedule == r.features.schedule and r2.model.projection == r.model.projection


def test_backtest_records_rest_buckets_and_compares_models(loaded: Session) -> None:
    cfg = BacktestConfig(
        seasons=[2024],
        stats=[StatType.POINTS],
        models=["ewma", "ewma_schedule"],
        min_prior_games=3,
        include_empirical=False,
    )
    result = run_backtest(loaded, cfg)
    assert result.observations and all(
        o.rest_bucket in {"back_to_back", "three_in_four", "normal", "long_rest", "unknown"}
        for o in result.observations
    )
    assert any(o.rest_days is not None for o in result.observations)
    n_obs = len(result.observations)
    for m in ("ewma", "ewma_schedule"):
        assert (
            sum(1 for p in result.predictions if p.model == m and p.requested_family == "auto")
            == n_obs
        )
