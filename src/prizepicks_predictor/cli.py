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
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from prizepicks_predictor.config import get_settings
from prizepicks_predictor.db import IngestionRun, create_db_engine, get_session_factory, init_db
from prizepicks_predictor.ingestion import CsvGameLogProvider, ProviderError, load_game_logs
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
    print(f"  P(more) = {p.more:.3f}   P(less) = {p.less:.3f}   P(push) = {p.push:.3f}")
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
