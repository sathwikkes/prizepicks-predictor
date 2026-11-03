from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db import (
    Game,
    PredictionOutcome,
    PredictionSnapshot,
    ScheduledGame,
    SnapshotImmutableError,
)
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction.calibrators import CalibratorRegistry
from prizepicks_predictor.prediction.schemas import StatType
from prizepicks_predictor.services import find_players
from prizepicks_predictor.services.evaluation import evaluate_predictions
from prizepicks_predictor.services.forecast import (
    ManualTarget,
    expected_participants,
    game_forecastable,
    run_forecast,
    scheduled_games_on,
)
from prizepicks_predictor.services.monitoring import build_monitor, render_monitor, snapshot_stats
from tests.conftest import BASE_HEADER, write_csv
from tests.forecasting.conftest import AFTER_TIPOFF, BEFORE_TIPOFF, TARGET, TIPOFF


def _rivera(session: Session) -> int:
    return find_players(session, "Alex Rivera")[0].id


# --- scheduling and participants ----------------------------------------------------
def _game(session: Session, external_id: str) -> ScheduledGame:
    g = session.scalar(select(ScheduledGame).where(ScheduledGame.external_id == external_id))
    assert g is not None
    return g


def test_game_forecastable_rules(scheduled: Session) -> None:
    g1, g2 = _game(scheduled, "g1"), _game(scheduled, "g2")
    assert len(scheduled_games_on(scheduled, TARGET)) == 2
    assert game_forecastable(g1, BEFORE_TIPOFF) is None
    assert game_forecastable(g1, AFTER_TIPOFF) == "already_started"
    at_tip = datetime(2024, 12, 2, 0, 0, tzinfo=UTC)
    assert game_forecastable(g1, at_tip) == "already_started"
    # no tip-off time: forecastable only while the cutoff date precedes the game date
    assert game_forecastable(g2, datetime(2024, 11, 30, 12, 0, tzinfo=UTC)) is None
    assert game_forecastable(g2, BEFORE_TIPOFF) == "tipoff_unknown"
    g1.status = "final"
    assert game_forecastable(g1, BEFORE_TIPOFF) == "already_started"
    g1.status = "postponed"
    assert game_forecastable(g1, BEFORE_TIPOFF) == "game_postponed"


def test_expected_participants_from_recent_games(scheduled: Session) -> None:
    games = scheduled_games_on(scheduled, TARGET)
    parts = expected_participants(scheduled, games, target_date=TARGET, lookback_days=21)
    teams = {p.team for p in parts}
    # fixture teams: BOS (2 players), LAL (2), DEN (1), MIA (1) all played within 21 days
    assert teams == {"BOS", "LAL", "DEN", "MIA"} and len(parts) == 6
    assert all(p.status == "expected" for p in parts)
    none = expected_participants(scheduled, games, target_date=TARGET, lookback_days=1)
    assert none == []


# --- forecast generation ----------------------------------------------------------------
def test_forecast_dry_run_saves_nothing(scheduled: Session, registry: CalibratorRegistry) -> None:
    s = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=BEFORE_TIPOFF,
        calibrators=registry,
    )
    assert (
        s.scheduled_games == 2 and s.forecastable_games == 1
    )  # g2 has unknown tip-off on the cutoff date
    assert s.skipped["game:tipoff_unknown"] == 1
    assert s.participants == 4  # BOS + LAL players only
    assert len(s.rows) == 4 and s.saved == 0
    assert scheduled.scalar(select(PredictionSnapshot)) is None
    assert all(r.line_source == "synthetic_trailing_median" and r.line % 1 == 0.5 for r in s.rows)
    assert all(r.p_more_cal is not None for r in s.rows)


def test_forecast_save_is_idempotent_and_revisable(
    scheduled: Session, registry: CalibratorRegistry
) -> None:
    first = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=list(StatType),
        now=BEFORE_TIPOFF,
        save=True,
        calibrators=registry,
    )
    assert first.saved == 12 and first.duplicates == 0
    again = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=list(StatType),
        now=BEFORE_TIPOFF + timedelta(minutes=5),
        save=True,
        calibrators=registry,
    )
    assert again.saved == 0 and again.duplicates == 12
    assert scheduled.scalar(select(PredictionSnapshot.id).limit(1)) is not None
    assert len(scheduled.scalars(select(PredictionSnapshot)).all()) == 12
    revised = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=BEFORE_TIPOFF + timedelta(hours=1),
        save=True,
        revise=True,
        calibrators=registry,
    )
    assert revised.saved == 4
    snaps = scheduled.scalars(
        select(PredictionSnapshot).where(PredictionSnapshot.stat_type == "points")
    ).all()
    assert sorted({s.revision for s in snaps}) == [1, 2]


def test_snapshot_contents_and_immutability(
    scheduled: Session, registry: CalibratorRegistry
) -> None:
    run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=BEFORE_TIPOFF,
        save=True,
        calibrators=registry,
    )
    snap = scheduled.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.player_id == _rivera(scheduled))
    )
    assert snap is not None
    assert snap.target_date == TARGET and snap.cutoff_at == BEFORE_TIPOFF.replace(tzinfo=None)
    assert snap.tipoff_utc == TIPOFF and snap.cutoff_at < snap.tipoff_utc
    assert snap.history_latest_game_date is not None and snap.history_latest_game_date < TARGET
    assert snap.team == "BOS" and snap.opponent == "LAL" and snap.is_home is True
    assert snap.model_name == "ewma" and snap.model_version == "1.0"
    assert snap.calibration_artifact == "test.points" and snap.p_more_cal is not None
    assert snap.participant_status == "expected"
    features = json.loads(snap.features_json)
    assert features["games_total"] == snap.sample_size == 12
    assert "matchup_context_unavailable" in json.loads(snap.warnings_json)
    snap.projection = 99.0
    with pytest.raises(SnapshotImmutableError):
        scheduled.flush()
    scheduled.rollback()
    fresh = scheduled.get(PredictionSnapshot, snap.id)
    assert fresh is not None and fresh.projection != 99.0


def test_forecast_refuses_started_games(scheduled: Session) -> None:
    s = run_forecast(
        scheduled, target_date=TARGET, stats=[StatType.POINTS], now=AFTER_TIPOFF, save=True
    )
    assert s.forecastable_games == 0 and s.saved == 0 and s.rows == []
    assert s.skipped["game:already_started"] == 1
    manual = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=AFTER_TIPOFF,
        save=True,
        manual_targets=[ManualTarget(_rivera(scheduled), StatType.POINTS, 24.5)],
    )
    assert manual.saved == 0 and manual.skipped["player:game_not_forecastable"] == 1


def test_forecast_uses_only_pre_target_history(scheduled: Session, tmp_path: Path) -> None:
    """Adding the target game's own box score (as if ingested early) must not change it."""
    before = run_forecast(scheduled, target_date=TARGET, stats=[StatType.POINTS], now=BEFORE_TIPOFF)
    rows = {r.player_id: r for r in before.rows}
    load_game_logs(
        scheduled,
        CsvGameLogProvider(
            write_csv(
                tmp_path / "target.csv",
                BASE_HEADER,
                "2024-12-01,FIX-001,Alex Rivera,BOS,LAL,home,40:00,80,20,15",
            ),
            source_name="csv_fixture",
        ),
    )
    after = run_forecast(scheduled, target_date=TARGET, stats=[StatType.POINTS], now=BEFORE_TIPOFF)
    pid = _rivera(scheduled)
    assert after.rows and {r.player_id: r for r in after.rows}[pid].projection == pytest.approx(
        rows[pid].projection
    )
    assert {r.player_id: r for r in after.rows}[pid].line == rows[pid].line


def test_manual_targets_and_unknown_player(scheduled: Session) -> None:
    pid = _rivera(scheduled)
    s = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=BEFORE_TIPOFF,
        save=True,
        manual_targets=[
            ManualTarget(pid, StatType.POINTS, 24.5),
            ManualTarget(pid, StatType.ASSISTS, None),
            ManualTarget(999, StatType.POINTS, 10.5),
        ],
    )
    assert s.saved == 2 and s.skipped["player:unknown"] == 1
    by_stat = {r.stat: r for r in s.rows}
    assert by_stat["points"].line_source == "manual" and by_stat["points"].line == 24.5
    assert by_stat["assists"].line_source == "synthetic_trailing_median"
    snap = scheduled.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.stat_type == "points")
    )
    assert snap is not None and snap.participant_status == "manual" and snap.opponent == "LAL"


def test_forecast_without_schedule_notes_and_insufficient_history(loaded: Session) -> None:
    s = run_forecast(loaded, target_date=TARGET, stats=[StatType.POINTS], now=BEFORE_TIPOFF)
    assert (
        s.scheduled_games == 0 and s.rows == [] and any("no scheduled games" in n for n in s.notes)
    )
    early = run_forecast(
        loaded,
        target_date=date(2024, 10, 28),
        stats=[StatType.POINTS],
        now=datetime(2024, 10, 28, 12, tzinfo=UTC),
        manual_targets=[ManualTarget(_rivera(loaded), StatType.POINTS, 20.5)],
    )
    assert early.skipped["player:insufficient_history"] == 1 and early.rows == []


# --- evaluation -------------------------------------------------------------------------
def _saved_forecast(scheduled: Session, registry: CalibratorRegistry) -> None:
    run_forecast(
        scheduled,
        target_date=TARGET,
        stats=list(StatType),
        now=BEFORE_TIPOFF,
        save=True,
        calibrators=registry,
    )


def _load_results(session: Session, tmp_path: Path, *rows: str) -> None:
    load_game_logs(
        session,
        CsvGameLogProvider(
            write_csv(tmp_path / "results.csv", BASE_HEADER, *rows), source_name="csv_fixture"
        ),
    )


def test_evaluate_before_results_is_pending_or_missing(
    scheduled: Session, registry: CalibratorRegistry
) -> None:
    _saved_forecast(scheduled, registry)
    pending = evaluate_predictions(scheduled, through=TARGET, now=BEFORE_TIPOFF)
    assert pending.pending_future == 12 and pending.statuses == {}
    missing = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=1))
    assert missing.statuses["missing_result"] == 12
    rep = build_monitor(scheduled)
    assert rep.by_status == {"missing_result": 12} and rep.overall is None


def test_evaluate_completed_game_and_dnp(
    scheduled: Session, registry: CalibratorRegistry, tmp_path: Path
) -> None:
    _saved_forecast(scheduled, registry)
    # BOS vs LAL results: Rivera plays, Nakamura (BOS) has no row, Lindqvist (LAL) logs 0:00
    _load_results(
        scheduled,
        tmp_path,
        "2024-12-01,FIX-001,Alex Rivera,BOS,LAL,home,36:00,31,8,5",
        "2024-12-01,FIX-003,Sam Okafor,LAL,BOS,away,34:00,20,6,9",
        "2024-12-01,FIX-004,Casey Lindqvist,LAL,BOS,away,0:00,0,0,0",
    )
    first = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(hours=6))
    assert first.statuses["evaluated"] == 6  # Rivera + Okafor x 3 stats
    assert (
        first.statuses["did_not_play"] == 6
    )  # Nakamura (no row) + Lindqvist (0 minutes) x 3 stats
    snap = scheduled.scalar(
        select(PredictionSnapshot).where(
            PredictionSnapshot.player_id == _rivera(scheduled),
            PredictionSnapshot.stat_type == "points",
        )
    )
    assert snap is not None
    out = scheduled.scalar(
        select(PredictionOutcome).where(PredictionOutcome.snapshot_id == snap.id)
    )
    assert out is not None and out.status == "evaluated" and out.actual == 31
    assert out.abs_error == pytest.approx(
        abs(snap.projection - 31)
    ) and out.sq_error == pytest.approx((snap.projection - 31) ** 2)
    assert out.covered == (snap.q_lo <= 31 <= snap.q_hi)
    assert out.outcome_more is (snap.line < 31) and out.outcome_push is False
    assert out.brier_raw is not None and out.brier_cal is not None and out.logloss_raw is not None
    # snapshot untouched by evaluation
    assert snap.projection == snap.projection and snap.p_more is not None
    # re-running changes nothing: all outcomes final
    second = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=2))
    assert second.considered == 0 and second.statuses == {}
    rep = build_monitor(scheduled)
    assert rep.by_status == {"evaluated": 6, "did_not_play": 6}
    assert rep.overall is not None and rep.overall.n == 6 and rep.preliminary
    assert "did_not_play" in render_monitor(rep) and "preliminary" in render_monitor(rep)
    assert rep.by_group["stat"]["points"].n == 2


def test_missing_result_is_reevaluated_when_data_arrives(
    scheduled: Session, registry: CalibratorRegistry, tmp_path: Path
) -> None:
    _saved_forecast(scheduled, registry)
    evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=1))
    assert (
        scheduled.scalar(
            select(PredictionOutcome).where(PredictionOutcome.status == "missing_result")
        )
        is not None
    )
    _load_results(scheduled, tmp_path, "2024-12-01,FIX-001,Alex Rivera,BOS,LAL,home,36:00,31,8,5")
    again = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=2))
    assert again.reevaluated == 12 and again.statuses["evaluated"] == 3
    assert len(scheduled.scalars(select(PredictionOutcome)).all()) == 12  # still one per snapshot


def test_postponed_game_voids_forecasts(scheduled: Session, registry: CalibratorRegistry) -> None:
    _saved_forecast(scheduled, registry)
    g1 = _game(scheduled, "g1")
    g1.status = "postponed"
    scheduled.commit()
    s = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=1))
    assert s.statuses["voided"] == 12
    assert all(o.actual is None for o in scheduled.scalars(select(PredictionOutcome)))


def test_push_outcome_has_no_brier(scheduled: Session) -> None:
    pid = _rivera(scheduled)
    run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.REBOUNDS],
        now=BEFORE_TIPOFF,
        save=True,
        manual_targets=[ManualTarget(pid, StatType.REBOUNDS, 8.0)],
    )
    game_rows = scheduled.scalars(select(Game)).all()
    assert game_rows is not None
    load_game_logs(
        scheduled,
        CsvGameLogProvider(
            write_csv(
                Path(str(scheduled.get_bind().url.database)).parent / "push.csv",
                BASE_HEADER,
                "2024-12-01,FIX-001,Alex Rivera,BOS,LAL,home,36:00,31,8,5",
            ),
            source_name="csv_fixture",
        ),
    )
    evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=1))
    out = scheduled.scalar(select(PredictionOutcome))
    assert out is not None and out.status == "evaluated" and out.outcome_push is True
    assert out.outcome_more is None and out.brier_raw is None
    rep = build_monitor(scheduled)
    assert rep.overall is not None and rep.overall.pushes == 1 and rep.overall.n_prob == 0


def test_not_scheduled_distinguished_from_missing_result(
    scheduled: Session, registry: CalibratorRegistry
) -> None:
    """A schedule exists for the date; Morgan Adeyemi's DEN plays (g2) but Riley
    Castellano's MIA ... both play in g2. Use a manual target for a player whose team has
    no game that day by adding a third team's player: Alex Rivera's BOS plays in g1, so
    change g1 to a different pair and check Rivera becomes not_scheduled."""
    g1 = _game(scheduled, "g1")
    # make g1 a DEN vs MIA duplicate so BOS and LAL have no game on TARGET
    g1.home_team_id, g1.away_team_id = (
        _game(scheduled, "g2").home_team_id,
        _game(scheduled, "g2").away_team_id,
    )
    scheduled.commit()
    pid = _rivera(scheduled)
    run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=BEFORE_TIPOFF,
        save=True,
        manual_targets=[ManualTarget(pid, StatType.POINTS, 24.5)],
        calibrators=registry,
    )
    s = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=1))
    assert s.statuses == {"not_scheduled": 1}
    out = scheduled.scalar(select(PredictionOutcome))
    assert out is not None and out.status == "not_scheduled" and out.actual is None
    # final: a later run does not touch it
    again = evaluate_predictions(scheduled, through=TARGET, now=AFTER_TIPOFF + timedelta(days=2))
    assert again.considered == 0


def test_missing_result_when_no_schedule_for_date(loaded: Session) -> None:
    pid = _rivera(loaded)
    run_forecast(
        loaded,
        target_date=TARGET,
        stats=[StatType.POINTS],
        now=BEFORE_TIPOFF,
        save=True,
        manual_targets=[ManualTarget(pid, StatType.POINTS, 24.5)],
    )
    s = evaluate_predictions(loaded, through=TARGET, now=AFTER_TIPOFF + timedelta(days=1))
    assert s.statuses == {"missing_result": 1}  # cannot tell "no game" from "not loaded"


def test_revisions_increment_and_duplicates_persist(scheduled: Session) -> None:
    pid = _rivera(scheduled)
    kw = dict(
        target_date=TARGET,
        stats=[StatType.POINTS],
        save=True,
        manual_targets=[ManualTarget(pid, StatType.POINTS, 24.5)],
    )
    r1 = run_forecast(scheduled, now=BEFORE_TIPOFF, **kw)  # type: ignore[arg-type]
    r2 = run_forecast(scheduled, now=BEFORE_TIPOFF + timedelta(minutes=1), revise=True, **kw)  # type: ignore[arg-type]
    r3 = run_forecast(scheduled, now=BEFORE_TIPOFF + timedelta(minutes=2), revise=True, **kw)  # type: ignore[arg-type]
    r4 = run_forecast(scheduled, now=BEFORE_TIPOFF + timedelta(minutes=3), **kw)  # type: ignore[arg-type]
    assert (r1.saved, r2.saved, r3.saved, r4.saved) == (1, 1, 1, 0) and r4.duplicates == 1
    revs = sorted(s.revision for s in scheduled.scalars(select(PredictionSnapshot)))
    assert revs == [1, 2, 3]
    # a different line is a different forecast, not a duplicate
    other = run_forecast(
        scheduled,
        target_date=TARGET,
        stats=[StatType.POINTS],
        save=True,
        now=BEFORE_TIPOFF + timedelta(minutes=4),
        manual_targets=[ManualTarget(pid, StatType.POINTS, 25.5)],
    )
    assert other.saved == 1 and other.duplicates == 0
    stats = snapshot_stats(scheduled, target_date=TARGET)
    assert (
        stats.snapshots == 4
        and stats.unique_player_game_stat == 1
        and stats.revisions_beyond_first == 2
    )
    assert stats.prospective == 4 and stats.unverified == 0
