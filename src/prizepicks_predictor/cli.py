"""Command-line interface.

ppp init-db
ppp ingest-csv data/samples/synthetic_game_logs.csv --source csv_fixture
ppp players --search "riv"
ppp game-logs "Alex Rivera" --season 2024 --limit 10
ppp runs
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.config import BacktestConfig, parse_season
from prizepicks_predictor.config import get_settings
from prizepicks_predictor.db import IngestionRun, create_db_engine, get_session_factory, init_db
from prizepicks_predictor.ingestion import CsvGameLogProvider, ProviderError, load_game_logs
from prizepicks_predictor.ingestion.schedules import EspnScheduleProvider, load_schedule
from prizepicks_predictor.ingestion.sportsdataverse import SportsDataverseProvider
from prizepicks_predictor.prediction import (
    DistributionFamily,
    PredictionError,
    PredictionRequest,
    PredictionResult,
    StatType,
    predict,
)
from prizepicks_predictor.prediction.models import DEFAULT_MODEL, MODELS
from prizepicks_predictor.services import PlayerNotFoundError, find_players, get_player_game_logs
from prizepicks_predictor.services.data_quality import build_report, render_report
from prizepicks_predictor.services.evaluation import evaluate_predictions
from prizepicks_predictor.services.forecast import read_targets_file, run_forecast
from prizepicks_predictor.services.monitoring import build_monitor, render_monitor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppp", description="PrizePicks Predictor CLI")
    parser.add_argument("--database-url", help="Override PPP_DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database tables (idempotent)")

    ingest = sub.add_parser("ingest-csv", help="Load player game logs from a CSV file")
    ingest.add_argument("path")
    ingest.add_argument("--source", default="csv", help="Source label stored for provenance")

    sdv = sub.add_parser(
        "ingest-sdv",
        help="Download and load real NBA box scores from SportsDataverse GitHub releases",
    )
    sdv.add_argument(
        "--season",
        type=int,
        action="append",
        required=True,
        help="Season END year (2025 = 2024-25). Repeat for several seasons.",
    )
    sdv.add_argument(
        "--season-types",
        default="regular",
        help="Comma list of regular,playoffs,playin,preseason (default: regular)",
    )
    sdv.add_argument("--include-dnp", action="store_true", help="Store DNP rows as 0-minute games")
    sdv.add_argument("--cache-dir", default="data/raw/sportsdataverse")
    sdv.add_argument("--force-download", action="store_true")

    sub.add_parser("data-quality", help="Summarize what is stored: coverage, integrity, provenance")

    players = sub.add_parser("players", help="Search players by name")
    players.add_argument("--search", required=True)

    logs = sub.add_parser("game-logs", help="Show a player's historical game logs")
    logs.add_argument("player", help="Player name or internal id")
    logs.add_argument("--season", type=int, help="Season start year, e.g. 2024")
    logs.add_argument("--limit", type=int)

    sub.add_parser("runs", help="List ingestion runs")

    bt = sub.add_parser("backtest", help="Walk-forward evaluation of the baseline models")
    bt.add_argument("--season", action="append", required=True, help="e.g. 2024-25; repeatable")
    bt.add_argument(
        "--stat",
        action="append",
        choices=[s.value for s in StatType],
        help="repeatable; default all",
    )
    bt.add_argument("--models", default="all", help="'all' or comma list of model names")
    bt.add_argument("--families", default="auto,normal", help="comma list of distribution families")
    bt.add_argument(
        "--no-empirical", action="store_true", help="skip the empirical-history reference"
    )
    bt.add_argument("--min-prior-games", type=int, default=10)
    bt.add_argument("--max-observations", type=int, help="random subset of eligible player-games")
    bt.add_argument("--seed", type=int, default=0)
    bt.add_argument("--date-from", help="YYYY-MM-DD lower bound on target dates")
    bt.add_argument("--date-to", help="YYYY-MM-DD upper bound on target dates")
    bt.add_argument("--output-dir", default="data/backtests")
    bt.add_argument("--run-name", help="directory name under output-dir (default: timestamp)")
    bt.add_argument("--no-charts", action="store_true")

    cal = sub.add_parser("calibrate", help="Fit calibrators on one run, evaluate on another")
    cal.add_argument("--fit-run", required=True, help="validation run directory")
    cal.add_argument("--eval-run", required=True, help="held-out run directory")
    cal.add_argument("--stat", required=True, choices=[s.value for s in StatType])
    cal.add_argument("--model", default=DEFAULT_MODEL)
    cal.add_argument("--family", default=DistributionFamily.AUTO.value)
    cal.add_argument("--no-charts", action="store_true")

    fit = sub.add_parser(
        "fit-calibrators", help="Fit versioned calibration artifacts from backtest runs"
    )
    fit.add_argument("--fit-run", action="append", required=True, help="run directory; repeatable")
    fit.add_argument("--eval-run", help="held-out run directory for validation metrics")
    fit.add_argument("--out", default="artifacts/calibration")
    fit.add_argument("--models", default="ewma", help="comma list of model names")
    fit.add_argument("--family", default=DistributionFamily.AUTO.value)
    fit.add_argument("--method", default="isotonic", choices=["isotonic", "platt"])

    sched = sub.add_parser(
        "ingest-schedule", help="Load the ESPN season schedule (SportsDataverse)"
    )
    sched.add_argument(
        "--season", type=int, action="append", required=True, help="END year, e.g. 2027"
    )
    sched.add_argument("--cache-dir", default="data/raw/sportsdataverse")
    sched.add_argument("--force-download", action="store_true")

    fc = sub.add_parser(
        "forecast", help="Generate pregame forecasts for a date (immutable snapshots)"
    )
    fc.add_argument("--date", required=True, help="target game date YYYY-MM-DD (US game date)")
    fc.add_argument(
        "--stat", action="append", help="points|rebounds|assists|all; repeatable; default all"
    )
    fc.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    fc.add_argument(
        "--distribution",
        default=DistributionFamily.AUTO.value,
        choices=[d.value for d in DistributionFamily],
    )
    fc.add_argument(
        "--targets-file", help="CSV player_id,stat[,line] instead of schedule-derived participants"
    )
    fc.add_argument("--save", action="store_true", help="persist snapshots (otherwise dry run)")
    fc.add_argument(
        "--revise",
        action="store_true",
        help="save a new revision when an identical forecast exists",
    )
    fc.add_argument("--lookback-days", type=int, default=21)

    ev = sub.add_parser(
        "evaluate-predictions", help="Score saved snapshots against completed games"
    )
    ev.add_argument("--through", required=True, help="evaluate targets on or before YYYY-MM-DD")

    mon = sub.add_parser("monitor", help="Prospective performance report")
    mon.add_argument("--since")
    mon.add_argument("--through")

    sub.add_parser(
        "check",
        help="Readiness checks: schema, data coverage, schedule, artifacts, smoke prediction",
    )

    bk = sub.add_parser("backup", help="Write a consistent copy of the SQLite database")
    bk.add_argument(
        "--out", help="destination file (default data/backups/prizepicks-<UTC timestamp>.sqlite)"
    )

    rs = sub.add_parser("restore", help="Replace the database with a backup file")
    rs.add_argument("--from", dest="source", required=True)
    rs.add_argument(
        "--force", action="store_true", help="overwrite a populated database (safety copy kept)"
    )

    ex = sub.add_parser(
        "export-snapshots", help="Export prediction snapshots and outcomes to gzip CSV"
    )
    ex.add_argument("--out", required=True)

    im = sub.add_parser(
        "import-snapshots", help="Insert exported snapshots that are not already present"
    )
    im.add_argument("--from", dest="source", required=True)

    pred = sub.add_parser("predict", help="Predict More/Less probabilities for a prop line")
    pred.add_argument("--player-id", type=int, required=True, help="Internal player id")
    pred.add_argument("--stat", required=True, choices=[s.value for s in StatType])
    pred.add_argument("--line", type=float, required=True, help="Offered line, e.g. 24.5")
    pred.add_argument("--date", required=True, help="Target game date (YYYY-MM-DD)")
    pred.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    pred.add_argument(
        "--distribution",
        default=DistributionFamily.AUTO.value,
        choices=[d.value for d in DistributionFamily],
    )
    return parser


def _print_prediction(result: PredictionResult) -> None:
    f = result.features
    p = result.probabilities
    print(f"{result.player_name} (id={result.player_id}, team={result.team or '?'})")
    print(f"  {result.stat_type.value} line {result.line:g}  |  game date {result.as_of_date}")
    print(f"  model: {result.model.name} v{result.model.version}")
    if result.model.projected_minutes is not None:
        print(f"  projected minutes: {result.model.projected_minutes:.1f}")
    print(f"  projected {result.stat_type.value}: {result.model.projection:.2f}")
    print(
        f"  distribution: {result.distribution.family.value} "
        f"(mean {result.distribution.mean:.2f}, sd {result.distribution.std:.2f}, "
        f"variance from {result.distribution.variance_sample_size} games)"
    )
    lo, hi = result.interval_80
    print(f"  80% interval: {lo:.0f} - {hi:.0f}")
    print(f"  P(more) = {p.more:.3f}   P(less) = {p.less:.3f}   P(push) = {p.push:.3f}   [raw]")
    c = result.probabilities_calibrated
    if c is not None and result.calibration is not None:
        print(
            f"  P(more) = {c.more:.3f}   P(less) = {c.less:.3f}   P(push) = {c.push:.3f}   "
            f"[calibrated: {result.calibration.method}, fit {result.calibration.fit_seasons}, "
            f"{result.calibration.validation_status}]"
        )
        print(f"  calibration artifact: {result.calibration.artifact_id}")
    print(
        f"  sample: {result.sample_size} games with minutes before cutoff "
        f"(season {f.season_start_year}: {f.season_games} games; dnp excluded: {f.dnp_games})"
    )
    print(
        "  features: "
        f"season {_fmt(f.season_mean)}, last5 {_fmt(f.last5_mean)}, last10 {_fmt(f.last10_mean)}, "
        f"ewma {_fmt(f.ewma_mean)}, per-min(recent) {_fmt(f.per_minute_recent, 3)}, "
        f"min(recent) {_fmt(f.minutes_recent_mean, 1)}, rest {f.rest_days}d"
    )
    print(f"  calibration: {result.calibration_status}")
    if result.warnings:
        print("  warnings: " + ", ".join(w.value for w in result.warnings))
    for note in result.distribution.notes:
        print(f"  note: {note}")
    for assumption in result.model.assumptions:
        print(f"  assumes: {assumption}")


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _fit_calibrators(args: argparse.Namespace) -> int:
    from prizepicks_predictor.backtest.fit_calibration import CalibrationFitError, fit_artifact

    out = Path(args.out)
    written = 0
    for model_name in [m.strip() for m in args.models.split(",")]:
        for stat in StatType:
            try:
                artifact = fit_artifact(
                    fit_runs=[Path(r) for r in args.fit_run],
                    stat=stat,
                    model_name=model_name,
                    family=DistributionFamily(args.family),
                    method=args.method,
                    eval_run=Path(args.eval_run) if args.eval_run else None,
                )
            except (CalibrationFitError, FileNotFoundError, KeyError) as exc:
                print(f"skip {stat.value}/{model_name}: {exc}", file=sys.stderr)
                continue
            path = artifact.save(out)
            written += 1
            held = artifact.held_out
            extra = (
                f" held-out ECE {held.ece_uncalibrated:.4f} -> {held.ece_calibrated:.4f} "
                f"(n={held.n_eval})"
                if held
                else " (in-sample only)"
            )
            print(f"wrote {path}  {artifact.artifact_id}  n_fit={artifact.n_fit}{extra}")
    print(f"{written} artifacts in {out}")
    return 0 if written else 2


def _forecast(session: Session, args: argparse.Namespace) -> int:
    stats = (
        list(StatType) if not args.stat or "all" in args.stat else [StatType(s) for s in args.stat]
    )
    targets = read_targets_file(Path(args.targets_file)) if args.targets_file else None
    summary = run_forecast(
        session,
        target_date=date.fromisoformat(args.date),
        stats=stats,
        model_name=args.model,
        distribution=DistributionFamily(args.distribution),
        manual_targets=targets,
        save=args.save,
        revise=args.revise,
        lookback_days=args.lookback_days,
    )
    print(
        f"forecast {summary.target_date} batch={summary.batch_id} "
        f"cutoff={summary.cutoff_at:%Y-%m-%dT%H:%M:%SZ} "
        f"scheduled_games={summary.scheduled_games} forecastable={summary.forecastable_games} "
        f"participants={summary.participants} rows={len(summary.rows)} saved={summary.saved} "
        f"duplicates={summary.duplicates} {'(dry run)' if not args.save else ''}"
    )
    if summary.skipped:
        print(f"  skipped: {dict(summary.skipped)}")
    for note in summary.notes:
        print(f"  note: {note}")
    _print_table(
        [
            "player",
            "team",
            "opp",
            "stat",
            "line",
            "src",
            "proj",
            "P(more) raw",
            "cal",
            "n",
            "saved",
        ],
        [
            (
                r.player_name,
                r.team or "?",
                r.opponent or "?",
                r.stat,
                f"{r.line:g}",
                "syn" if r.line_source == "synthetic_trailing_median" else "man",
                f"{r.projection:.2f}",
                f"{r.p_more:.3f}",
                "n/a" if r.p_more_cal is None else f"{r.p_more_cal:.3f}",
                r.sample_size,
                "dup" if r.duplicate else ("yes" if r.saved else "no"),
            )
            for r in summary.rows[:60]
        ],
    )
    if len(summary.rows) > 60:
        print(f"  ... {len(summary.rows) - 60} more rows")
    print("  lines marked 'syn' are synthetic trailing-median thresholds, not market lines")
    return 0


def _run_backtest(session: Session, args: argparse.Namespace, database_url: str) -> int:
    from prizepicks_predictor.backtest.engine import run_backtest
    from prizepicks_predictor.backtest.report import build_report, write_run

    try:
        config = BacktestConfig(
            seasons=[parse_season(s) for s in args.season],
            stats=[StatType(s) for s in args.stat] if args.stat else list(StatType),
            models=sorted(MODELS)
            if args.models == "all"
            else [m.strip() for m in args.models.split(",")],
            families=[DistributionFamily(f.strip()) for f in args.families.split(",")],
            include_empirical=not args.no_empirical,
            min_prior_games=args.min_prior_games,
            max_observations=args.max_observations,
            seed=args.seed,
            date_from=date.fromisoformat(args.date_from) if args.date_from else None,
            date_to=date.fromisoformat(args.date_to) if args.date_to else None,
        )
    except (ValidationError, ValueError) as exc:
        print(f"error: invalid backtest configuration: {exc}", file=sys.stderr)
        return 2

    run_name = args.run_name or datetime.now(UTC).strftime("bt_%Y%m%d_%H%M%S")
    run_dir = Path(args.output_dir) / run_name
    print(f"backtest {config.season_labels} stats={[s.value for s in config.stats]} -> {run_dir}")

    def progress(done: int, total: int) -> None:
        print(f"  {done}/{total} player-games", file=sys.stderr, flush=True)

    result = run_backtest(session, config, progress=progress)
    write_run(result, run_dir, database_url=database_url)
    print(
        f"candidates={result.candidates} observations={len(result.observations)} "
        f"predictions={len(result.predictions)} elapsed={result.elapsed_seconds:.1f}s"
    )
    print(f"exclusions: {dict(result.exclusions)}")
    if result.missing:
        print(f"missing predictions: {dict(result.missing)}")
    if not result.observations:
        print("no eligible observations; nothing to report")
        return 0
    cmp = build_report(run_dir, charts=not args.no_charts)
    _print_table(
        [
            "stat",
            "model",
            "family",
            "n",
            "MAE",
            "RMSE",
            "bias",
            "NLL",
            "cov80",
            "Brier",
            "logloss",
            "ECE",
        ],
        [
            (
                r.stat,
                r.model,
                r.family,
                r.n,
                f"{r.mae:.3f}",
                f"{r.rmse:.3f}",
                f"{r.bias:+.3f}",
                f"{r.nll:.3f}",
                f"{r.coverage:.3f}",
                f"{r.brier:.4f}",
                f"{r.log_loss:.4f}",
                f"{r.ece:.4f}",
            )
            for r in sorted(cmp.rows, key=lambda r: (r.stat, r.model, r.family))
        ],
    )
    print(f"summary: {run_dir / 'summary.md'}")
    return 0


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    widths = [len(h) for h in headers]
    text_rows = [[str(c) for c in row] for row in rows]
    for row in text_rows:
        widths = [max(w, len(c)) for w, c in zip(widths, row, strict=True)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in text_rows:
        print(fmt.format(*row))


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # signed download URLs are noise
    args = _build_parser().parse_args(argv)

    engine = create_db_engine(args.database_url or settings.database_url)
    session_factory = get_session_factory(engine)

    if args.command == "init-db":
        init_db(engine)
        print(f"Database initialized at {engine.url}")
        return 0
    if args.command in ("backup", "restore"):
        from prizepicks_predictor.services.portability import (
            PortabilityError,
            backup_database,
            restore_database,
        )

        try:
            if args.command == "backup":
                out = (
                    Path(args.out)
                    if args.out
                    else Path("data/backups")
                    / (f"prizepicks-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sqlite")
                )
                written = backup_database(engine, out)
                print(f"backup written: {written} ({written.stat().st_size / 1e6:.1f} MB)")
            else:
                target = restore_database(engine, Path(args.source), force=args.force)
                print(f"restored {args.source} -> {target}")
        except PortabilityError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    init_db(engine)  # idempotent: creates tables added since the database was first built

    with session_factory() as session:
        if args.command == "ingest-csv":
            provider = CsvGameLogProvider(args.path, source_name=args.source)
            try:
                result = load_game_logs(session, provider)
            except ProviderError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(
                f"run #{result.run_id} [{result.source}] received={result.received} "
                f"inserted={result.inserted} updated={result.updated} "
                f"unchanged={result.unchanged} rejected={result.rejected} "
                f"dup_in_batch={result.duplicates_in_batch}"
            )
            for rejection in result.rejections[:20]:
                print(f"  rejected {rejection}")
            return 0

        if args.command == "ingest-sdv":
            season_types = tuple(t.strip() for t in args.season_types.split(",") if t.strip())
            for season in args.season:
                sdv_provider = SportsDataverseProvider(
                    season,
                    cache_dir=args.cache_dir,
                    season_types=season_types,
                    include_dnp=args.include_dnp,
                    force_download=args.force_download,
                )
                try:
                    result = load_game_logs(session, sdv_provider)
                except ProviderError as exc:
                    print(f"error: season {season}: {exc}", file=sys.stderr)
                    return 2
                finally:
                    sdv_provider.close()
                st = sdv_provider.stats
                print(
                    f"season {season} ({season - 1}-{season % 100:02d}) run #{result.run_id}: "
                    f"box_rows={st.box_rows} yielded={st.yielded} inserted={result.inserted} "
                    f"updated={result.updated} unchanged={result.unchanged} "
                    f"rejected={result.rejected}"
                )
                print(
                    f"  skipped: dnp={st.dnp_rows} {dict(st.dnp_reasons)} "
                    f"season_type={dict(st.filtered_season_type)} no_team_log={st.missing_team_log}"
                )
                for rejection in result.rejections[:5]:
                    print(f"  rejected {rejection}")
            return 0

        if args.command == "data-quality":
            print(render_report(build_report(session)))
            return 0

        if args.command == "players":
            found = find_players(session, args.search)
            _print_table(["id", "name"], [(p.id, p.full_name) for p in found])
            return 0

        if args.command == "game-logs":
            player: int | str = int(args.player) if args.player.isdigit() else args.player
            try:
                rows = get_player_game_logs(
                    session, player, season_start_year=args.season, limit=args.limit
                )
            except PlayerNotFoundError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if not rows:
                print("no game logs found")
                return 0
            print(f"{rows[0].player_name} (id={rows[0].player_id}) - {len(rows)} games")
            _print_table(
                ["date", "season", "team", "opp", "loc", "min", "pts", "reb", "ast"],
                [
                    (
                        r.game_date.isoformat(),
                        r.season,
                        r.team,
                        r.opponent,
                        "home" if r.is_home else "away",
                        f"{r.minutes:.1f}",
                        r.points,
                        r.rebounds,
                        r.assists,
                    )
                    for r in rows
                ],
            )
            return 0

        if args.command == "fit-calibrators":
            return _fit_calibrators(args)

        if args.command == "ingest-schedule":
            for season in args.season:
                sched_provider = EspnScheduleProvider(
                    season, cache_dir=args.cache_dir, force_download=args.force_download
                )
                try:
                    loaded = load_schedule(session, sched_provider)
                except ProviderError as exc:
                    print(f"error: season {season}: {exc}", file=sys.stderr)
                    return 2
                finally:
                    sched_provider.close()
                sst = sched_provider.stats
                print(
                    f"schedule {season} ({season - 1}-{season % 100:02d}): rows={sst.rows} "
                    f"loaded={sst.yielded} inserted={loaded.inserted} updated={loaded.updated} "
                    f"unchanged={loaded.unchanged} skipped_tbd={sst.skipped_tbd} "
                    f"statuses={sst.statuses}"
                )
            return 0

        if args.command == "forecast":
            return _forecast(session, args)

        if args.command == "evaluate-predictions":
            summary = evaluate_predictions(session, through=date.fromisoformat(args.through))
            print(
                f"evaluated through {summary.through}: considered={summary.considered} "
                f"statuses={dict(summary.statuses)} pending_future={summary.pending_future} "
                f"reevaluated={summary.reevaluated}"
            )
            return 0

        if args.command == "check":
            from prizepicks_predictor.prediction.service import resolve_calibration_dir
            from prizepicks_predictor.services.readiness import render_checks, run_checks

            checks = run_checks(
                session, calibration_dir=resolve_calibration_dir(settings.calibration_dir)
            )
            print(render_checks(checks))
            return 0 if all(c.status != "FAIL" for c in checks) else 1

        if args.command == "export-snapshots":
            from prizepicks_predictor.services.portability import export_snapshots

            res = export_snapshots(session, Path(args.out))
            print(f"exported {res.exported} snapshots to {args.out}")
            return 0

        if args.command == "import-snapshots":
            from prizepicks_predictor.services.portability import import_snapshots, snapshot_counts

            try:
                res = import_snapshots(session, Path(args.source))
            except (FileNotFoundError, OSError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            total, outcomes = snapshot_counts(session)
            print(
                f"imported {res.imported} snapshots ({res.outcomes_imported} outcomes); "
                f"skipped existing {res.skipped_existing}, "
                f"unknown player {res.skipped_unknown_player}, "
                f"schedule link missing {res.unlinked_schedule}; "
                f"database now holds {total} snapshots, "
                f"{outcomes} outcomes"
            )
            return 0

        if args.command == "monitor":
            monitor = build_monitor(
                session,
                since=date.fromisoformat(args.since) if args.since else None,
                through=date.fromisoformat(args.through) if args.through else None,
            )
            print(render_monitor(monitor))
            return 0

        if args.command == "backtest":
            return _run_backtest(session, args, engine.url.render_as_string(hide_password=True))

        if args.command == "calibrate":
            from prizepicks_predictor.backtest.calibrate import (
                calibrate,
                render_calibration,
                write_calibration_report,
            )

            try:
                report = calibrate(
                    Path(args.fit_run),
                    Path(args.eval_run),
                    stat=args.stat,
                    model=args.model,
                    family=args.family,
                )
            except (FileNotFoundError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            path = write_calibration_report(report, Path(args.eval_run), chart=not args.no_charts)
            print(render_calibration(report))
            print(f"written {path}")
            return 0

        if args.command == "predict":
            try:
                request = PredictionRequest(
                    player_id=args.player_id,
                    stat_type=StatType(args.stat),
                    line=args.line,
                    as_of_date=date.fromisoformat(args.date),
                    model_name=args.model,
                    distribution=DistributionFamily(args.distribution),
                )
            except (ValidationError, ValueError) as exc:
                print(f"error: invalid request: {exc}", file=sys.stderr)
                return 2
            try:
                prediction = predict(session, request)
            except PlayerNotFoundError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            except PredictionError as exc:
                print(f"insufficient data / prediction error: {exc}", file=sys.stderr)
                return 3
            except SQLAlchemyError as exc:
                print(f"error: database failure: {exc}", file=sys.stderr)
                return 4
            _print_prediction(prediction)
            return 0

        if args.command == "runs":
            runs = session.scalars(select(IngestionRun).order_by(IngestionRun.id.desc())).all()
            _print_table(
                ["id", "source", "status", "recv", "ins", "upd", "same", "rej", "started"],
                [
                    (
                        r.id,
                        r.source,
                        r.status,
                        r.records_received,
                        r.records_inserted,
                        r.records_updated,
                        r.records_unchanged,
                        r.records_rejected,
                        r.started_at.strftime("%Y-%m-%d %H:%M"),
                    )
                    for r in runs
                ],
            )
            return 0

    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
