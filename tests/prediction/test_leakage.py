"""Mandatory no-leakage tests.

Each test builds two databases that differ only in information that must be
invisible to a prediction, and asserts the prediction is identical.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from prizepicks_predictor.db import create_db_engine, get_session_factory, init_db
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction import PredictionRequest, PredictionResult, StatType, predict
from prizepicks_predictor.prediction.models import MODELS
from prizepicks_predictor.services import find_players
from tests.conftest import BASE_HEADER, write_csv

HISTORY = [
    "2024-11-01,P1,Leak Test,BOS,LAL,home,30:00,20,5,4",
    "2024-11-03,P1,Leak Test,BOS,MIA,away,32:00,22,6,5",
    "2024-11-05,P1,Leak Test,BOS,NYK,home,28:00,18,4,3",
    "2024-11-08,P1,Leak Test,BOS,DEN,away,34:00,26,7,6",
    "2024-11-10,P1,Leak Test,BOS,PHX,home,31:00,21,5,4",
    "2024-11-12,P1,Leak Test,BOS,GSW,away,33:00,24,6,5",
]
TARGET_DATE = date(2024, 11, 14)


def _db_with(tmp_path: Path, name: str, rows: list[str]) -> Session:
    engine = create_db_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
    init_db(engine)
    session = get_session_factory(engine)()
    load_game_logs(
        session, CsvGameLogProvider(write_csv(tmp_path / f"{name}.csv", BASE_HEADER, *rows))
    )
    return session


def _predict_all(session: Session, as_of: date = TARGET_DATE) -> dict[str, PredictionResult]:
    pid = find_players(session, "Leak Test")[0].id
    return {
        name: predict(
            session,
            PredictionRequest(
                player_id=pid,
                stat_type=StatType.POINTS,
                line=22.5,
                as_of_date=as_of,
                model_name=name,
            ),
        )
        for name in MODELS
    }


def _assert_identical(a: dict[str, PredictionResult], b: dict[str, PredictionResult]) -> None:
    for name in MODELS:
        assert a[name].model == b[name].model, name
        assert a[name].probabilities == b[name].probabilities, name
        assert a[name].distribution == b[name].distribution, name
        assert a[name].features == b[name].features, name


def test_1_target_game_stats_never_enter_its_own_prediction(tmp_path: Path) -> None:
    with_huge = _db_with(
        tmp_path, "huge", [*HISTORY, "2024-11-14,P1,Leak Test,BOS,DAL,home,40:00,80,20,15"]
    )
    with_zero = _db_with(
        tmp_path, "zero", [*HISTORY, "2024-11-14,P1,Leak Test,BOS,DAL,home,30:00,0,0,0"]
    )
    without = _db_with(tmp_path, "none", HISTORY)

    a, b, c = _predict_all(with_huge), _predict_all(with_zero), _predict_all(without)
    _assert_identical(a, b)
    _assert_identical(a, c)
    assert all(r.sample_size == 6 for r in a.values())
    assert all(r.features.cutoff_last_game_date == date(2024, 11, 12) for r in a.values())


def test_2_future_games_do_not_change_earlier_predictions(tmp_path: Path) -> None:
    base = _db_with(tmp_path, "base", HISTORY)
    future = _db_with(
        tmp_path,
        "future",
        [
            *HISTORY,
            "2024-11-16,P1,Leak Test,BOS,DAL,home,36:00,45,12,9",
            "2024-11-18,P1,Leak Test,BOS,CHI,away,35:00,41,10,8",
        ],
    )
    _assert_identical(_predict_all(base), _predict_all(future))
    # but the future DB *does* see them once the cutoff moves past
    later = _predict_all(future, as_of=date(2024, 11, 20))
    assert all(r.sample_size == 8 for r in later.values())


def test_3_rolling_statistics_use_chronological_order_not_insertion_order(tmp_path: Path) -> None:
    shuffled = [HISTORY[3], HISTORY[0], HISTORY[5], HISTORY[1], HISTORY[4], HISTORY[2]]
    ordered = _predict_all(_db_with(tmp_path, "ordered", HISTORY))
    scrambled = _predict_all(_db_with(tmp_path, "scrambled", shuffled))
    _assert_identical(ordered, scrambled)
    f = ordered["rolling_5"].features
    # last five by date: 11-03..11-12 -> (22+18+26+21+24)/5
    assert f.last5_mean == pytest.approx(22.2)
    assert ordered["rolling_5"].model.projection == pytest.approx(22.2)


def test_4_features_respect_cutoff_exactly(tmp_path: Path) -> None:
    session = _db_with(tmp_path, "cutoff", HISTORY)
    pid = find_players(session, "Leak Test")[0].id
    # Cutoff ON a game date excludes that game.
    on_game_day = predict(
        session,
        PredictionRequest(
            player_id=pid, stat_type=StatType.POINTS, line=22.5, as_of_date=date(2024, 11, 12)
        ),
    )
    assert on_game_day.sample_size == 5
    assert on_game_day.features.cutoff_last_game_date == date(2024, 11, 10)
    assert on_game_day.features.season_mean == pytest.approx((20 + 22 + 18 + 26 + 21) / 5)
    # Day after includes it.
    day_after = predict(
        session,
        PredictionRequest(
            player_id=pid, stat_type=StatType.POINTS, line=22.5, as_of_date=date(2024, 11, 13)
        ),
    )
    assert day_after.sample_size == 6


def test_5_previous_season_handling_does_not_leak_future_seasons(tmp_path: Path) -> None:
    last_season = [
        "2024-03-01,P1,Leak Test,BOS,LAL,home,30:00,30,8,7",
        "2024-03-03,P1,Leak Test,BOS,MIA,away,30:00,32,8,7",
        "2024-03-05,P1,Leak Test,BOS,NYK,home,30:00,34,8,7",
    ]
    next_season = [
        "2025-10-25,P1,Leak Test,BOS,LAL,home,30:00,50,15,12",
        "2025-10-27,P1,Leak Test,BOS,MIA,away,30:00,52,15,12",
    ]
    two_seasons = _db_with(tmp_path, "two", [*last_season, *HISTORY])
    three_seasons = _db_with(tmp_path, "three", [*last_season, *HISTORY, *next_season])

    a, b = _predict_all(two_seasons), _predict_all(three_seasons)
    _assert_identical(a, b)

    f = a["season_average"].features
    assert (
        f.season_start_year == 2024 and f.season_games == 6
    )  # 2023-24 games excluded from season mean
    assert f.season_mean == pytest.approx((20 + 22 + 18 + 26 + 21 + 24) / 6)
    assert f.games_total == 9  # but prior-season games are legitimate history

    # Predicting before the 2024-25 season starts falls back to 2023-24 only.
    pid = find_players(three_seasons, "Leak Test")[0].id
    pre = predict(
        three_seasons,
        PredictionRequest(
            player_id=pid,
            stat_type=StatType.POINTS,
            line=30.5,
            as_of_date=date(2024, 10, 20),
            model_name="season_average",
        ),
    )
    assert pre.features.season_start_year == 2023 and pre.sample_size == 3
    assert pre.model.projection == pytest.approx(32.0)
