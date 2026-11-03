"""Page functions. Each opens a session, calls dashboard.data, renders, and closes."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from prizepicks_predictor.dashboard import charts, data
from prizepicks_predictor.dashboard.common import backtest_dirs, calibration_dir, db_session, footer
from prizepicks_predictor.prediction.models import DEFAULT_MODEL, MODELS
from prizepicks_predictor.prediction.schemas import DistributionFamily, StatType
from prizepicks_predictor.services.monitoring import build_monitor

STATS = [s.value for s in StatType]


# ----------------------------------------------------------------------------- overview
def overview_page() -> None:
    st.title("PrizePicks Predictor — local research dashboard")
    with db_session() as session:
        status = data.data_status(session, calibration_dir=calibration_dir())
    for w in status.warnings:
        st.warning(w)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latest box score", str(status.latest_game_date or "none"))
    c2.metric("Scheduled games loaded", sum(status.scheduled_by_status.values()))
    c3.metric(
        "Next game date",
        str(status.next_game_date or "n/a"),
        f"{status.next_game_count} games" if status.next_game_date else None,
    )
    c4.metric(
        "Saved forecasts",
        status.snapshots.snapshots,
        f"{status.snapshots.unique_player_game_stat} unique player-game-stat",
    )
    st.subheader("Season coverage (box scores)")
    st.dataframe(
        pd.DataFrame([c.__dict__ for c in status.coverage]), hide_index=True, width="stretch"
    )
    left, right = st.columns(2)
    with left:
        st.subheader("Schedule")
        st.write(
            {
                "by status": status.scheduled_by_status,
                "loaded at (UTC)": str(status.schedule_loaded_at),
                "last final game": str(status.schedule_last_final),
            }
        )
        st.caption(
            "Source: ESPN schedule republished by SportsDataverse. "
            "Statuses update only when the schedule is reloaded."
        )
    with right:
        st.subheader("Calibration artifacts in use")
        if status.artifacts:
            st.dataframe(pd.DataFrame(status.artifacts), hide_index=True, width="stretch")
        else:
            st.info("none loaded")
    st.subheader("Forecast snapshots")
    s = status.snapshots
    st.write(
        {
            "rows": s.snapshots,
            "unique player-game-stat combinations": s.unique_player_game_stat,
            "players": s.players,
            "games": s.games,
            "batches": s.batches,
            "revisions beyond the first": s.revisions_beyond_first,
            "saved before a known tip-off (prospective)": s.prospective,
            "no tip-off to verify against (manual / replay)": s.unverified,
        }
    )
    st.caption(
        "Participants in schedule-driven forecasts are *expected* from recent appearances, "
        "not confirmed "
        "active. Players who end up inactive evaluate as did-not-play."
    )
    footer()


# ------------------------------------------------------------------------------ players
def players_page() -> None:
    st.title("Player exploration")
    query = st.text_input("Search player (accent-insensitive)", placeholder="e.g. jokic")
    with db_session() as session:
        matches = data.search_players(session, query) if query else []
        if query and not matches:
            st.info("No player matches that search in the local database.")
            footer()
            return
        if not matches:
            st.caption(
                "Type a name to start. Data: regular-season box scores loaded via `ppp ingest-sdv`."
            )
            footer()
            return
        labels = {f"{m.name} ({m.team or '?'}, {m.games} games)": m.player_id for m in matches}
        choice = st.selectbox("Player", list(labels))
        pid = labels[choice]
        profile = data.player_profile(session, pid)
        if profile is None:
            st.error("Player not found.")
            return
        s = profile.summary
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Position", s.position or "n/a")
        c2.metric("Latest team", s.team or "n/a")
        c3.metric("Games stored", s.games)
        c4.metric("Last game", str(s.last_game or "n/a"))
        st.subheader("Season averages (regular season)")
        st.dataframe(
            pd.DataFrame([sl.__dict__ for sl in profile.seasons]).round(1),
            hide_index=True,
            width="stretch",
        )
        seasons = ["all"] + [sl.season for sl in profile.seasons]
        col_a, col_b, col_c = st.columns(3)
        season_pick = col_a.selectbox("Season", seasons)
        n_games = col_b.slider("Games", min_value=5, max_value=120, value=25, step=5)
        playoffs = col_c.checkbox("Include playoffs (if loaded)", value=False)
        start_year = (
            None
            if season_pick == "all"
            else next(sl.start_year for sl in profile.seasons if sl.season == season_pick)
        )
        logs = data.player_logs(
            session, pid, season_start_year=start_year, limit=n_games, include_playoffs=playoffs
        )
    if not logs:
        st.info("No game logs for that selection.")
        footer()
        return
    g1, g2 = st.columns(2)
    g1.plotly_chart(charts.game_log_chart(logs, "points"), width="stretch")
    g2.plotly_chart(charts.game_log_chart(logs, "rebounds"), width="stretch")
    g3, g4 = st.columns(2)
    g3.plotly_chart(charts.game_log_chart(logs, "assists"), width="stretch")
    g4.plotly_chart(charts.game_log_chart(logs, "minutes"), width="stretch")
    st.subheader("Game log (newest first)")
    frame = pd.DataFrame([r.model_dump() for r in logs])[
        [
            "game_date",
            "season",
            "season_type",
            "team",
            "opponent",
            "is_home",
            "minutes",
            "points",
            "rebounds",
            "assists",
            "steals",
            "blocks",
            "turnovers",
            "three_pointers_made",
            "started",
        ]
    ]
    st.dataframe(frame.round(1), hide_index=True, width="stretch")
    footer()


# ------------------------------------------------------------------------------ predict
def predict_page() -> None:
    st.title("Prediction analysis")
    st.caption(
        "Calls the same `predict()` service as the CLI. "
        "Thresholds you enter here are illustrative evaluation lines."
    )
    query = st.text_input("Player search", placeholder="e.g. sabonis")
    with db_session() as session:
        matches = data.search_players(session, query) if query else []
        if not matches:
            st.info("Search for a player to begin." if not query else "No match.")
            footer()
            return
        labels = {f"{m.name} ({m.team or '?'})": m.player_id for m in matches}
        pid = labels[st.selectbox("Player", list(labels))]
        c1, c2, c3 = st.columns(3)
        stat = StatType(c1.selectbox("Statistic", STATS))
        target = c2.date_input(
            "Target game date (history strictly before this date is used)",
            value=date.today() + timedelta(days=1),
        )
        line = c3.number_input(
            "Evaluation threshold (illustrative)",
            min_value=0.5,
            max_value=100.0,
            value=20.5,
            step=0.5,
        )
        with st.expander("Model options"):
            m1, m2, m3 = st.columns(3)
            model = m1.selectbox(
                "Projection model", sorted(MODELS), index=sorted(MODELS).index(DEFAULT_MODEL)
            )
            family = DistributionFamily(
                m2.selectbox("Distribution", [f.value for f in DistributionFamily])
            )
            calibrate = m3.checkbox("Apply calibration artifact if available", value=True)
        view = data.run_prediction(
            session,
            player_id=pid,
            stat=stat,
            line=float(line),
            target_date=target,
            model_name=model,
            distribution=family,
            calibrate=calibrate,
        )
    if view.error:
        if view.error_kind == "insufficient_data":
            st.warning(f"Insufficient history: {view.error}")
        else:
            st.error(view.error)
        footer()
        return
    r = view.result
    assert r is not None
    st.subheader(f"{r.player_name} — {r.stat_type.value} vs {r.line:g} on {r.as_of_date}")
    a, b, c, d = st.columns(4)
    a.metric("Projected total", f"{r.model.projection:.2f}")
    b.metric("80% interval", f"{r.interval_80[0]:.0f} - {r.interval_80[1]:.0f}")
    c.metric("Games used", r.sample_size, f"{r.features.season_games} this season")
    d.metric("Model", f"{r.model.name} v{r.model.version}")
    p = r.probabilities
    rows = [{"": "raw", "P(more)": p.more, "P(less)": p.less, "P(push)": p.push}]
    if r.probabilities_calibrated is not None:
        q = r.probabilities_calibrated
        rows.append({"": "calibrated", "P(more)": q.more, "P(less)": q.less, "P(push)": q.push})
    st.dataframe(pd.DataFrame(rows).set_index("").round(3), width="stretch")
    st.write(f"**Calibration status:** {r.calibration_status}")
    if r.calibration is not None:
        held = (
            f"held-out ECE {r.calibration.held_out_ece_uncalibrated:.4f} -> "
            f"{r.calibration.held_out_ece_calibrated:.4f}"
            if r.calibration.held_out_ece_calibrated is not None
            else "no held-out metrics"
        )
        st.caption(
            f"Artifact `{r.calibration.artifact_id}` / fit seasons {r.calibration.fit_seasons} / "
            f"{held}. {r.calibration.line_policy}."
        )
    if r.warnings:
        st.warning("Warnings: " + ", ".join(w.value for w in r.warnings))
    if r.unavailable_features:
        st.caption("Context not available to the model: " + ", ".join(r.unavailable_features))
    st.plotly_chart(charts.distribution_chart(r), width="stretch")
    with st.expander("Features used (all computed from games before the target date)"):
        st.json(r.features.model_dump(mode="json"))
    with st.expander("Model assumptions and distribution notes"):
        for line_ in r.model.assumptions:
            st.write(f"- {line_}")
        for note in r.distribution.notes:
            st.write(f"- {note}")
    st.info(
        "Betting-market performance is unvalidated: no real PrizePicks lines exist in this project."
    )
    footer()


# ---------------------------------------------------------------------------- forecasts
def forecasts_page() -> None:
    st.title("Prospective forecasts")
    with db_session() as session:
        dates = data.snapshot_dates(session)
        if not dates:
            st.info("No saved forecasts. Generate with `ppp forecast --date YYYY-MM-DD --save`.")
            footer()
            return
        date_labels = {f"{d} ({n} rows)": d for d, n in dates}
        c1, c2, c3, c4 = st.columns(4)
        pick = c1.selectbox("Target date", ["all", *date_labels])
        target = None if pick == "all" else date_labels[pick]
        games = data.snapshot_games(session, target) if target else []
        game_labels = {label: gid for gid, label in games}
        game_pick = c2.selectbox("Game", ["all", *game_labels]) if games else "all"
        stat_pick = c3.selectbox("Statistic", ["all", *STATS])
        status_pick = c4.selectbox(
            "Status",
            [
                "all",
                "pending",
                "evaluated",
                "did_not_play",
                "voided",
                "not_scheduled",
                "missing_result",
            ],
        )
        player_q = st.text_input("Player filter", placeholder="name contains…")
        rows = data.list_snapshots(
            session,
            target_date=target,
            scheduled_game_id=None if game_pick == "all" else game_labels[game_pick],
            player_query=player_q or None,
            stat=None if stat_pick == "all" else StatType(stat_pick),
            status=None if status_pick == "all" else status_pick,
        )
        stats_all = data.snapshot_stats(session, target_date=target) if target else None
    if stats_all:
        st.caption(
            f"{stats_all.snapshots} rows = {stats_all.unique_player_game_stat} unique "
            f"player-game-stat combinations ({stats_all.players} players, {stats_all.games} games, "
            f"{stats_all.stats} stats, {stats_all.batches} batch(es), "
            f"{stats_all.revisions_beyond_first} revisions); {stats_all.prospective} saved before "
            f"a known tip-off, {stats_all.unverified} without a tip-off to verify."
        )
    if not rows:
        st.info("No snapshots match the filters.")
        footer()
        return
    kinds = pd.Series([r.kind for r in rows]).value_counts().to_dict()
    statuses = pd.Series([r.status for r in rows]).value_counts().to_dict()
    k1, k2 = st.columns(2)
    k1.write({"kind": kinds})
    k2.write({"status": statuses})
    st.caption(
        "**prospective** = saved before the game's known tip-off (genuine pregame forecast). "
        "**unverified** = manual or replay targets with no tip-off recorded; treat as historical "
        "replay, not a live forecast. **not_scheduled** = the team had no game that day; "
        "**missing_result** = the game is not in the box-score data yet."
    )
    frame = pd.DataFrame([r.__dict__ for r in rows])
    frame["matchup"] = frame.apply(
        lambda x: f"{x.team or '?'} {'vs' if x.is_home else '@'} {x.opponent or '?'}", axis=1
    )
    show = frame[
        [
            "target_date",
            "player_name",
            "matchup",
            "stat",
            "line",
            "line_source",
            "projection",
            "q_lo",
            "q_hi",
            "p_more",
            "p_more_cal",
            "sample_size",
            "kind",
            "participant_status",
            "status",
            "actual",
            "abs_error",
            "covered",
            "outcome_more",
            "cutoff_at",
            "tipoff_utc",
            "model",
            "revision",
        ]
    ]
    st.dataframe(show.round(3), hide_index=True, width="stretch", height=520)
    st.caption(
        "Lines marked synthetic_trailing_median are evaluation thresholds, not market lines."
    )
    footer()


# -------------------------------------------------------------------------- performance
def performance_page() -> None:
    st.title("Model performance")
    runs: list[data.BacktestRun] = []
    for base in backtest_dirs():
        for run in data.load_backtest_runs(base):
            if run.name not in {r.name for r in runs}:
                runs.append(run)
    tab_hist, tab_pro = st.tabs(["Historical backtests (Phase 3)", "Prospective outcomes"])
    with tab_hist:
        if not runs:
            st.info(
                "No backtest runs found under docs/backtests or data/backtests. Run `ppp backtest`."
            )
        else:
            labels = {f"{r.name} — {r.role} ({r.seasons})": r for r in runs}
            run = labels[st.selectbox("Run", list(labels), index=len(labels) - 1)]
            meta = run.meta
            st.caption(
                f"Observations {meta.get('observations')}, candidates {meta.get('candidates')}, "
                f"exclusions {meta.get('exclusions')}. Lines are synthetic evaluation thresholds "
                "(trailing median +/- offsets), identical for every model."
            )
            stat = st.selectbox("Statistic", STATS, key="perf_stat")
            comp = [r for r in run.comparison if r["stat"] == stat]
            if comp:
                frame = pd.DataFrame(comp)[
                    [
                        "model",
                        "family",
                        "n",
                        "mae",
                        "rmse",
                        "bias",
                        "nll",
                        "coverage",
                        "width",
                        "brier",
                        "log_loss",
                        "ece",
                    ]
                ]
                for col in frame.columns[2:]:
                    frame[col] = pd.to_numeric(frame[col])
                st.dataframe(frame.round(4), hide_index=True, width="stretch")
                b1, b2 = st.columns(2)
                b1.plotly_chart(
                    charts.metric_bar_chart(
                        comp, metric="mae", title=f"MAE by model ({stat}, auto family)"
                    ),
                    width="stretch",
                )
                b2.plotly_chart(
                    charts.metric_bar_chart(
                        comp, metric="brier", title=f"Brier by model ({stat}, auto family)"
                    ),
                    width="stretch",
                )
            rel = [r for r in run.reliability if r["stat"] == stat and r["family"] == "auto"]
            if rel:
                st.plotly_chart(
                    charts.reliability_chart(
                        rel, title=f"Reliability: {stat} (auto family, bins n>=30)"
                    ),
                    width="stretch",
                )
            cal = [r for r in run.calibration if r["stat"] == stat]
            if cal:
                st.subheader("Calibration fitted on validation, evaluated on this run")
                st.dataframe(
                    pd.DataFrame(cal)[
                        ["model", "family", "method", "n_fit", "n_eval", "brier", "log_loss", "ece"]
                    ],
                    hide_index=True,
                    width="stretch",
                )
            with st.expander("Full summary (summary.md)"):
                st.markdown(run.summary_md or "_none_")
    with tab_pro:
        with db_session() as session:
            report = build_monitor(session)
        st.write({"snapshots": report.snapshots, "by status": report.by_status})
        if report.overall is None:
            st.info(
                "No evaluated prospective predictions yet. After games complete: "
                "`ppp ingest-sdv --season <END_YEAR> --force-download` then "
                "`ppp evaluate-predictions --through <date>`."
            )
        else:
            m = report.overall
            if report.preliminary:
                st.warning(f"Preliminary: only {m.n} evaluated predictions.")
            a, b, c, d = st.columns(4)
            a.metric("Evaluated", m.n)
            b.metric("MAE", f"{m.mae:.3f}")
            c.metric("RMSE", f"{m.rmse:.3f}")
            d.metric("80% coverage", f"{m.coverage:.3f}")
            prob = {
                "n (lines, pushes excluded)": m.n_prob,
                "Brier raw / calibrated": f"{m.brier_raw:.4f} / {m.brier_cal:.4f}"
                if m.brier_raw is not None and m.brier_cal is not None
                else "n/a",
                "log loss raw / calibrated": f"{m.logloss_raw:.4f} / {m.logloss_cal:.4f}"
                if m.logloss_raw is not None and m.logloss_cal is not None
                else "n/a",
                "ECE raw / calibrated": f"{m.ece_raw:.4f} / {m.ece_cal:.4f}"
                if m.ece_raw is not None and m.ece_cal is not None
                else "n/a",
            }
            st.write(prob)
            if report.brier_diff_ci:
                est, lo, hi = report.brier_diff_ci
                st.caption(
                    f"Brier(calibrated) - Brier(raw) = {est:+.4f} [{lo:+.4f}, {hi:+.4f}] "
                    "(cluster bootstrap by player-game)"
                )
            for name, grp in report.by_group.items():
                if name in ("stat", "date"):
                    st.subheader(f"By {name}")
                    st.dataframe(
                        pd.DataFrame([{name: k, **v.__dict__} for k, v in grp.items()]).round(4),
                        hide_index=True,
                        width="stretch",
                    )
            if report.reliability_raw:
                rel_rows = [
                    {
                        "method": "raw",
                        "n": b.n,
                        "mean_predicted": b.mean_predicted,
                        "observed_frequency": b.observed_frequency,
                    }
                    for b in report.reliability_raw
                ]
                rel_rows += [
                    {
                        "method": "calibrated",
                        "n": b.n,
                        "mean_predicted": b.mean_predicted,
                        "observed_frequency": b.observed_frequency,
                    }
                    for b in report.reliability_cal
                ]
                st.plotly_chart(
                    charts.reliability_chart(
                        rel_rows, min_n=10, title="Prospective reliability (bins n>=10)"
                    ),
                    width="stretch",
                )
        for note in report.notes:
            st.caption(note)
    footer()
