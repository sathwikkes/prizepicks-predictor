"""Data-quality report over what is currently stored."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import Game, IngestionRun, Player, PlayerGameStat, Season, Team


class SeasonCoverage(BaseModel):
    season: str
    season_type: str
    games: int
    player_games: int
    players: int
    first_game: date
    last_game: date


class SourceProvenance(BaseModel):
    source: str
    runs: int
    last_run_at: datetime | None
    last_status: str | None
    records_inserted: int
    records_updated: int
    records_rejected: int
    player_games: int


class DataQualityReport(BaseModel):
    generated_at: datetime
    games: int
    player_games: int
    players: int
    teams: int
    seasons: list[SeasonCoverage]
    missing_values: dict[str, int]
    zero_minute_rows: int
    duplicate_player_game_keys: int
    duplicate_games: int
    stat_rows_without_game_date: int
    games_without_scores: int
    players_with_multiple_teams: int
    sources: list[SourceProvenance]
    latest_game_date: date | None


def build_report(session: Session) -> DataQualityReport:
    count = func.count()

    def scalar_int(stmt: Select[Any]) -> int:
        return int(session.scalar(stmt) or 0)

    coverage_stmt = (
        select(
            Season.label,
            Game.season_type,
            func.count(func.distinct(Game.id)),
            func.count(PlayerGameStat.id),
            func.count(func.distinct(PlayerGameStat.player_id)),
            func.min(Game.game_date),
            func.max(Game.game_date),
        )
        .join(Game, Game.season_id == Season.id)
        .outerjoin(PlayerGameStat, PlayerGameStat.game_id == Game.id)
        .group_by(Season.label, Game.season_type)
        .order_by(Season.label, Game.season_type)
    )
    seasons = [
        SeasonCoverage(
            season=label,
            season_type=stype,
            games=games,
            player_games=pg,
            players=players,
            first_game=first,
            last_game=last,
        )
        for label, stype, games, pg, players, first, last in session.execute(coverage_stmt)
    ]

    missing = {
        col: scalar_int(select(count).where(getattr(PlayerGameStat, col).is_(None)))
        for col in ("steals", "blocks", "turnovers", "three_pointers_made", "started")
    }

    dup_pg = (
        select(PlayerGameStat.game_id, PlayerGameStat.player_id)
        .group_by(PlayerGameStat.game_id, PlayerGameStat.player_id)
        .having(count > 1)
    )
    dup_games = (
        select(Game.game_date, Game.home_team_id, Game.away_team_id)
        .group_by(Game.game_date, Game.home_team_id, Game.away_team_id)
        .having(count > 1)
    )
    multi_team = (
        select(PlayerGameStat.player_id)
        .group_by(PlayerGameStat.player_id)
        .having(func.count(func.distinct(PlayerGameStat.team_id)) > 1)
    )

    sources: list[SourceProvenance] = []
    for (source,) in session.execute(
        select(IngestionRun.source).distinct().order_by(IngestionRun.source)
    ):
        runs = session.scalars(
            select(IngestionRun)
            .where(IngestionRun.source == source)
            .order_by(IngestionRun.id.desc())
        ).all()
        pg_count = scalar_int(
            select(count)
            .select_from(PlayerGameStat)
            .join(IngestionRun, PlayerGameStat.ingestion_run_id == IngestionRun.id)
            .where(IngestionRun.source == source)
        )
        sources.append(
            SourceProvenance(
                source=source,
                runs=len(runs),
                last_run_at=runs[0].finished_at or runs[0].started_at,
                last_status=runs[0].status,
                records_inserted=sum(r.records_inserted for r in runs),
                records_updated=sum(r.records_updated for r in runs),
                records_rejected=sum(r.records_rejected for r in runs),
                player_games=pg_count,
            )
        )

    return DataQualityReport(
        generated_at=datetime.now(),
        games=scalar_int(select(count).select_from(Game)),
        player_games=scalar_int(select(count).select_from(PlayerGameStat)),
        players=scalar_int(select(count).select_from(Player)),
        teams=scalar_int(select(count).select_from(Team)),
        seasons=seasons,
        missing_values=missing,
        zero_minute_rows=scalar_int(select(count).where(PlayerGameStat.minutes == 0)),
        duplicate_player_game_keys=scalar_int(select(count).select_from(dup_pg.subquery())),
        duplicate_games=scalar_int(select(count).select_from(dup_games.subquery())),
        stat_rows_without_game_date=scalar_int(
            select(count)
            .select_from(PlayerGameStat)
            .outerjoin(Game)
            .where(Game.game_date.is_(None))
        ),
        games_without_scores=scalar_int(select(count).where(Game.home_score.is_(None))),
        players_with_multiple_teams=scalar_int(select(count).select_from(multi_team.subquery())),
        sources=sources,
        latest_game_date=session.scalar(select(func.max(Game.game_date))),
    )


def render_report(report: DataQualityReport) -> str:
    lines = [
        f"Data quality report ({report.generated_at:%Y-%m-%d %H:%M})",
        f"  games={report.games} player_games={report.player_games} "
        f"players={report.players} teams={report.teams}",
        f"  latest game date: {report.latest_game_date}",
        "  season coverage:",
    ]
    for s in report.seasons:
        lines.append(
            f"    {s.season} {s.season_type:9s} games={s.games:5d} "
            f"player_games={s.player_games:6d} players={s.players:4d} "
            f"{s.first_game} .. {s.last_game}"
        )
    lines.append("  integrity:")
    lines.append(f"    duplicate (game, player) keys: {report.duplicate_player_game_keys}")
    lines.append(f"    duplicate games (date, home, away): {report.duplicate_games}")
    lines.append(f"    stat rows without a game date: {report.stat_rows_without_game_date}")
    lines.append(f"    games without final scores: {report.games_without_scores}")
    lines.append(f"    zero-minute rows stored: {report.zero_minute_rows}")
    lines.append(f"    players appearing for >1 team: {report.players_with_multiple_teams}")
    lines.append(
        "  missing optional values: "
        + ", ".join(f"{k}={v}" for k, v in report.missing_values.items())
    )
    lines.append("  provenance:")
    for src in report.sources:
        lines.append(
            f"    {src.source}: runs={src.runs} last={src.last_run_at:%Y-%m-%d %H:%M} "
            f"({src.last_status}) inserted={src.records_inserted} "
            f"updated={src.records_updated} rejected={src.records_rejected} "
            f"player_games={src.player_games}"
        )
    return "\n".join(lines)
