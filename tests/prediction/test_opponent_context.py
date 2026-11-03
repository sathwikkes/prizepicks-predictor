from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.config import BacktestConfig
from prizepicks_predictor.backtest.engine import run_backtest
from prizepicks_predictor.db import Game, Team, TeamGameStat
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction import PredictionRequest, StatType, predict
from prizepicks_predictor.prediction.features import build_features
from prizepicks_predictor.prediction.models import (
    MODELS,
    OpponentAdjustedEwmaModel,
    load_opponent_factors,
)
from prizepicks_predictor.prediction.opponent_context import (
    TeamGameLine,
    build_opponent_context,
    league_averages,
    pace48,
    possessions,
)
from prizepicks_predictor.services import find_players
from prizepicks_predictor.services.team_context import load_team_lines
from tests.prediction.conftest import series

D = date(2024, 11, 20)


def _line(
    d: date, *, pace: float = 100.0, allowed: float = 110.0, season: int = 2024
) -> TeamGameLine:
    return TeamGameLine(
        game_date=d,
        season_start_year=season,
        possessions=pace,
        pace48=pace,
        points_allowed=allowed,
        opp_possessions=100.0,
    )


def test_possessions_and_pace() -> None:
    assert possessions(90, 10, 15, 20) == pytest.approx(90 - 10 + 15 + 8.8)
    assert pace48(100, 240) == pytest.approx(100.0)
    assert pace48(100, 265) == pytest.approx(100 * 48 / 53)  # overtime normalised
    assert possessions(0, 50, 0, 0) == 1.0  # floor


def test_league_averages_window() -> None:
    lines = [
        _line(date(2024, 11, 1), pace=98, allowed=100),
        _line(date(2024, 11, 15), pace=102, allowed=120),
        _line(date(2024, 11, 20), pace=200, allowed=200),
    ]
    pace, dr = league_averages(lines, D)
    assert pace == pytest.approx(100.0) and dr == pytest.approx(
        110.0
    )  # the target-day line is excluded
    assert league_averages(lines, date(2024, 10, 1)) == (None, None)


def test_opponent_context_shrinkage_and_leakage() -> None:
    league = (100.0, 110.0)
    none = build_opponent_context("BOS", [], league=league, target_date=D, season_start_year=2024)
    assert (
        none.available
        and none.games_used == 0
        and none.opp_pace_rel == 1.0
        and none.opp_def_rel == 1.0
    )
    fast = [_line(date(2024, 11, i), pace=110, allowed=121) for i in range(1, 11)]
    ctx = build_opponent_context("BOS", fast, league=league, target_date=D, season_start_year=2024)
    assert ctx.games_used == 10
    assert ctx.opp_pace_rel == pytest.approx((10 * 110 + 5 * 100) / (15 * 100))
    assert ctx.opp_def_rel == pytest.approx((10 * 121 + 5 * 110) / (15 * 110))
    one = build_opponent_context(
        "BOS", fast[:1], league=league, target_date=D, season_start_year=2024
    )
    assert 1.0 < one.opp_pace_rel < ctx.opp_pace_rel  # less history -> closer to league average
    prev = [_line(date(2024, 3, 1), pace=150, allowed=150, season=2023)]
    assert (
        build_opponent_context(
            "BOS", prev, league=league, target_date=D, season_start_year=2024
        ).games_used
        == 0
    )
    with pytest.raises(ValueError, match="leakage"):
        build_opponent_context(
            "BOS", [_line(D, pace=110)], league=league, target_date=D, season_start_year=2024
        )
    assert not build_opponent_context(
        None, fast, league=league, target_date=D, season_start_year=2024
    ).available
    assert not build_opponent_context(
        "BOS", fast, league=(None, None), target_date=D, season_start_year=2024
    ).available


def _features(ctx):  # type: ignore[no-untyped-def]
    f = build_features(
        series(date(2024, 10, 1), [20, 22, 24, 26, 28, 30]), stat=StatType.POINTS, as_of_date=D
    )
    return f.model_copy(update={"opponent": ctx})


def test_opponent_model_elasticities_and_fallback() -> None:
    base = MODELS["ewma"].project(_features(None)).projection
    model = OpponentAdjustedEwmaModel({"points": {"pace": 1.0, "defense": 0.5}})
    ctx = build_opponent_context(
        "BOS",
        [_line(date(2024, 11, i), pace=110, allowed=121) for i in range(1, 11)],
        league=(100.0, 110.0),
        target_date=D,
        season_start_year=2024,
    )
    out = model.project(_features(ctx))
    assert out.projection == pytest.approx(base * ctx.opp_pace_rel**1.0 * ctx.opp_def_rel**0.5)
    assert out.projection == model.project(_features(ctx)).projection  # reproducible
    assert model.project(_features(None)).projection == pytest.approx(base)
    neutral = OpponentAdjustedEwmaModel({})
    assert neutral.project(_features(ctx)).projection == pytest.approx(base)
    load_opponent_factors.cache_clear()
    assert load_opponent_factors(Path("/nonexistent.json")) == {}
    load_opponent_factors.cache_clear()
    assert "ewma_opponent" in MODELS


@pytest.fixture
def with_team_stats(session: Session, synthetic_csv: Path) -> Session:
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    # synthetic team totals: every game gets both teams' rows with fixed box totals
    teams = {t.abbreviation: t.id for t in session.scalars(select(Team))}
    for g in session.scalars(select(Game)):
        for tid, pts in (
            (g.home_team_id, g.home_score or 100),
            (g.away_team_id, g.away_score or 100),
        ):
            session.add(
                TeamGameStat(
                    game_id=g.id,
                    team_id=tid,
                    minutes=240,
                    points=pts,
                    fga=90,
                    fta=20,
                    oreb=10,
                    dreb=35,
                    tov=14,
                    ast=25,
                )
            )
    session.commit()
    assert teams
    return session


def test_team_lines_and_prediction_with_opponent(with_team_stats: Session) -> None:
    lines = load_team_lines(with_team_stats)
    assert "BOS" in lines and all(
        a.game_date <= b.game_date for a, b in zip(lines["BOS"], lines["BOS"][1:], strict=False)
    )
    before = load_team_lines(with_team_stats, before=date(2024, 11, 10))
    assert all(ln.game_date < date(2024, 11, 10) for rows in before.values() for ln in rows)
    pid = find_players(with_team_stats, "Alex Rivera")[0].id
    res = predict(
        with_team_stats,
        PredictionRequest(
            player_id=pid,
            stat_type=StatType.POINTS,
            line=24.5,
            as_of_date=date(2024, 11, 28),
            opponent="LAL",
        ),
    )
    oc = res.features.opponent
    assert oc is not None and oc.available and oc.opponent == "LAL" and oc.games_used > 0
    assert oc.opp_pace_rel > 0 and oc.opp_def_rel > 0
    no_opp = predict(
        with_team_stats,
        PredictionRequest(
            player_id=pid, stat_type=StatType.POINTS, line=24.5, as_of_date=date(2024, 11, 28)
        ),
    )
    assert no_opp.features.opponent is None
    adj = predict(
        with_team_stats,
        PredictionRequest(
            player_id=pid,
            stat_type=StatType.POINTS,
            line=24.5,
            as_of_date=date(2024, 11, 28),
            opponent="LAL",
            model_name="ewma_opponent",
        ),
    )
    assert adj.model.name == "ewma_opponent" and adj.model.projection > 0


def test_backtest_records_opponent_fields(with_team_stats: Session) -> None:
    cfg = BacktestConfig(
        seasons=[2024],
        stats=[StatType.POINTS],
        models=["ewma", "ewma_opponent"],
        min_prior_games=3,
        include_empirical=False,
    )
    result = run_backtest(with_team_stats, cfg)
    assert result.observations and any(o.opp_games is not None for o in result.observations)
    n = len(result.observations)
    assert (
        sum(
            1
            for p in result.predictions
            if p.model == "ewma_opponent" and p.requested_family == "auto"
        )
        == n
    )
