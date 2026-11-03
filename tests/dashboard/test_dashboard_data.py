from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from prizepicks_predictor.dashboard import charts, data
from prizepicks_predictor.prediction.schemas import DistributionFamily, StatType
from prizepicks_predictor.services.evaluation import evaluate_predictions

DOCS_BACKTESTS = Path(__file__).resolve().parents[2] / "docs" / "backtests"


def test_search_and_profile(loaded: Session) -> None:
    assert data.search_players(loaded, "") == []
    assert data.search_players(loaded, "nobody") == []
    hits = data.search_players(loaded, "riv")
    assert [h.name for h in hits] == ["Alex Rivera"]
    assert hits[0].games == 12 and hits[0].team == "BOS" and hits[0].last_game == date(2024, 11, 27)
    profile = data.player_profile(loaded, hits[0].player_id)
    assert profile is not None and len(profile.seasons) == 1
    season = profile.seasons[0]
    assert season.season == "2024-25" and season.games == 12 and season.teams == "BOS"
    assert 15 < season.points < 35
    assert data.player_profile(loaded, 9999) is None


def test_player_logs_filters(loaded: Session) -> None:
    pid = data.search_players(loaded, "okafor")[0].player_id
    assert len(data.player_logs(loaded, pid, limit=5)) == 5
    assert len(data.player_logs(loaded, pid, season_start_year=2024, limit=None)) == 12
    assert data.player_logs(loaded, pid, season_start_year=2019, limit=None) == []


def test_run_prediction_success_and_errors(loaded: Session) -> None:
    pid = data.search_players(loaded, "rivera")[0].player_id
    view = data.run_prediction(
        loaded, player_id=pid, stat=StatType.POINTS, line=24.5, target_date=date(2024, 12, 1)
    )
    assert view.error is None and view.result is not None
    assert view.result.sample_size == 12
    bad_line = data.run_prediction(
        loaded, player_id=pid, stat=StatType.POINTS, line=24.3, target_date=date(2024, 12, 1)
    )
    assert bad_line.error_kind == "invalid" and "0.5" in (bad_line.error or "")
    early = data.run_prediction(
        loaded, player_id=pid, stat=StatType.POINTS, line=20.5, target_date=date(2024, 10, 27)
    )
    assert early.error_kind == "insufficient_data"
    missing = data.run_prediction(
        loaded, player_id=9999, stat=StatType.POINTS, line=20.5, target_date=date(2024, 12, 1)
    )
    assert missing.error_kind == "not_found"
    normal = data.run_prediction(
        loaded,
        player_id=pid,
        stat=StatType.POINTS,
        line=24.5,
        target_date=date(2024, 12, 1),
        distribution=DistributionFamily.NORMAL,
    )
    assert (
        normal.result is not None and normal.result.distribution.family is DistributionFamily.NORMAL
    )


def test_snapshot_listing_filters_and_kinds(with_forecasts: Session) -> None:
    dates = data.snapshot_dates(with_forecasts)
    assert [(d, n) for d, n in dates] == [(date(2024, 12, 1), 12), (date(2024, 11, 20), 1)]
    games = data.snapshot_games(with_forecasts, date(2024, 12, 1))
    assert len(games) == 1 and "LAL @ BOS" in games[0][1]
    all_rows = data.list_snapshots(with_forecasts)
    assert len(all_rows) == 13
    kinds = {r.kind for r in all_rows if r.target_date == date(2024, 12, 1)}
    assert kinds == {"prospective"}
    replay = [r for r in all_rows if r.target_date == date(2024, 11, 20)]
    assert (
        replay[0].kind == "unverified"
        and replay[0].participant_status == "manual"
        and replay[0].line_source == "manual"
    )
    assert all(r.status == "pending" for r in all_rows)
    only_pts = data.list_snapshots(
        with_forecasts, target_date=date(2024, 12, 1), stat=StatType.POINTS
    )
    assert len(only_pts) == 4
    by_game = data.list_snapshots(with_forecasts, scheduled_game_id=games[0][0])
    assert len(by_game) == 12
    by_player = data.list_snapshots(with_forecasts, player_query="okafor")
    assert {r.player_name for r in by_player} == {"Sam Okafor"}
    assert data.list_snapshots(with_forecasts, player_query="zzz") == []
    assert data.list_snapshots(with_forecasts, status="evaluated") == []


def test_snapshot_listing_after_evaluation(with_forecasts: Session) -> None:
    evaluate_predictions(
        with_forecasts, through=date(2024, 12, 1), now=datetime(2024, 12, 3, tzinfo=UTC)
    )
    rows = data.list_snapshots(with_forecasts)
    statuses = {r.target_date: r.status for r in rows}
    # 2024-12-01: schedule loaded but the game is not in the box data -> missing_result;
    # 2024-11-20: the fixture holds Rivera's real game that day -> evaluated
    assert statuses[date(2024, 12, 1)] == "missing_result"
    assert statuses[date(2024, 11, 20)] == "evaluated"
    evaluated = data.list_snapshots(with_forecasts, status="evaluated")
    assert len(evaluated) == 1 and evaluated[0].actual is not None
    assert data.list_snapshots(with_forecasts, status="missing_result")
    assert data.snapshot_kind(datetime(2024, 12, 1, 18), datetime(2024, 12, 2)) == "prospective"
    assert data.snapshot_kind(datetime(2024, 12, 2, 1), datetime(2024, 12, 2)) == "unverified"
    assert data.snapshot_kind(datetime(2024, 12, 1, 18), None) == "unverified"


def test_data_status(with_forecasts: Session, tmp_path: Path) -> None:
    status = data.data_status(
        with_forecasts, calibration_dir=tmp_path / "none", now=datetime(2024, 12, 5, tzinfo=UTC)
    )
    assert status.latest_game_date == date(2024, 11, 29)
    assert status.coverage[0].season == "2024-25" and status.coverage[0].player_games == 72
    assert status.scheduled_by_status == {"scheduled": 1}
    assert status.snapshots.snapshots == 13 and status.snapshots.unique_player_game_stat == 13
    assert status.snapshots.prospective == 12 and status.snapshots.unverified == 1
    assert any("No calibration artifacts" in w for w in status.warnings)
    assert any("Schedule last loaded" in w for w in status.warnings)
    assert status.artifacts == []


def test_data_status_reports_box_score_lag(with_forecasts: Session, tmp_path: Path) -> None:
    from sqlalchemy import select

    from prizepicks_predictor.db import ScheduledGame

    g = with_forecasts.scalar(select(ScheduledGame))
    assert g is not None
    g.status = "final"
    with_forecasts.commit()
    status = data.data_status(
        with_forecasts, calibration_dir=tmp_path, now=datetime(2024, 12, 2, tzinfo=UTC)
    )
    assert status.box_score_lag_days == 2  # 2024-11-29 -> 2024-12-01
    assert any("behind" in w for w in status.warnings)


def test_load_backtest_runs_from_committed_docs() -> None:
    runs = data.load_backtest_runs(DOCS_BACKTESTS)
    names = [r.name for r in runs]
    assert names[:3] == ["dev_2023-24", "val_2024-25", "test_2025-26"]  # Phase 3 partitions first
    test_run = next(r for r in runs if r.role == "held-out test")
    assert test_run.role == "held-out test" and test_run.seasons == "2025-26"
    assert any(r["model"] == "ewma" and r["stat"] == "points" for r in test_run.comparison)
    assert test_run.reliability and test_run.strata and test_run.calibration
    assert {c["method"] for c in test_run.calibration} == {"uncalibrated", "platt", "isotonic"}
    assert "synthetic evaluation thresholds" in test_run.summary_md
    assert data.load_backtest_runs(Path("/nonexistent")) == []


def test_charts_build(loaded: Session) -> None:
    pid = data.search_players(loaded, "rivera")[0].player_id
    logs = data.player_logs(loaded, pid, limit=None)
    for stat in ("points", "rebounds", "assists", "minutes"):
        fig = charts.game_log_chart(logs, stat)
        assert len(fig.data) == 2  # bars + rolling mean
    view = data.run_prediction(
        loaded, player_id=pid, stat=StatType.POINTS, line=24.5, target_date=date(2024, 12, 1)
    )
    assert view.result is not None
    dist = charts.distribution_chart(view.result)
    ys = dist.data[0].y
    assert abs(sum(ys) - 1.0) < 1e-3
    runs = data.load_backtest_runs(DOCS_BACKTESTS)
    held_out = next(r for r in runs if r.role == "held-out test")
    rel = [r for r in held_out.reliability if r["stat"] == "points" and r["family"] == "auto"]
    rfig = charts.reliability_chart(rel)
    assert len(rfig.data) >= 2
    comp = [r for r in held_out.comparison if r["stat"] == "points"]
    bfig = charts.metric_bar_chart(comp, metric="mae")
    assert next(iter(bfig.data[0].x)) == "ewma"  # best MAE first
    empty = charts.game_log_chart([], "points")
    assert len(empty.data) == 1
    assert json.loads(dist.to_json())  # serialisable
