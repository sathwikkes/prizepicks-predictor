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

from sqlalchemy import select

from prizepicks_predictor.config import get_settings
from prizepicks_predictor.db import IngestionRun, create_db_engine, get_session_factory, init_db
from prizepicks_predictor.ingestion import CsvGameLogProvider, ProviderError, load_game_logs
from prizepicks_predictor.services import PlayerNotFoundError, find_players, get_player_game_logs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppp", description="PrizePicks Predictor CLI")
    parser.add_argument("--database-url", help="Override PPP_DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database tables (idempotent)")

    ingest = sub.add_parser("ingest-csv", help="Load player game logs from a CSV file")
    ingest.add_argument("path")
    ingest.add_argument("--source", default="csv", help="Source label stored for provenance")

    players = sub.add_parser("players", help="Search players by name")
    players.add_argument("--search", required=True)

    logs = sub.add_parser("game-logs", help="Show a player's historical game logs")
    logs.add_argument("player", help="Player name or internal id")
    logs.add_argument("--season", type=int, help="Season start year, e.g. 2024")
    logs.add_argument("--limit", type=int)

    sub.add_parser("runs", help="List ingestion runs")
    return parser


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
