"""Load team box-score totals from the cached SportsDataverse team game logs.

The team log file (release ``nba_stats_player_game_logs``, cached as
``team_game_logs_{season}.csv``) has one row per team per game with fga, fta, oreb, dreb,
tov, ast, pts and minutes. Rows attach to existing ``games`` through the NBA game id in
``external_ids``; games not yet loaded as box scores are skipped and counted.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import ExternalId, IngestionRun, Team, TeamGameStat
from prizepicks_predictor.ingestion.base import ProviderError
from prizepicks_predictor.ingestion.sportsdataverse import SOURCE_NAME

REQUIRED = frozenset(
    {"game_id", "team_abbreviation", "min", "pts", "fga", "fta", "oreb", "dreb", "tov", "ast"}
)


@dataclass
class TeamStatsLoad:
    rows: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_no_game: int = 0
    skipped_bad: int = 0


def load_team_stats(session: Session, season: int, *, cache_dir: str | Path) -> TeamStatsLoad:
    path = Path(cache_dir) / f"team_game_logs_{season}.csv"
    if not path.is_file():
        raise ProviderError(f"{path} not found; run `ppp ingest-sdv --season {season}` first")
    game_ids = {
        x: gid
        for gid, x in session.execute(
            select(ExternalId.entity_id, ExternalId.external_id).where(
                ExternalId.source == SOURCE_NAME, ExternalId.entity_type == "game"
            )
        )
    }
    teams = {t.abbreviation: t.id for t in session.scalars(select(Team))}
    run = IngestionRun(
        source=f"{SOURCE_NAME}_team_stats",
        source_detail=path.name,
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(run)
    session.flush()
    result = TeamStatsLoad()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or REQUIRED - {f.lower() for f in reader.fieldnames}:
            raise ProviderError(
                f"{path.name} missing columns {sorted(REQUIRED - set(reader.fieldnames or []))}"
            )
        for raw in reader:
            row = {k.lower(): (v or "") for k, v in raw.items() if k}
            result.rows += 1
            gid = game_ids.get(row["game_id"].strip())
            tid = teams.get(row["team_abbreviation"].strip().upper())
            if gid is None or tid is None:
                result.skipped_no_game += 1
                continue
            try:
                values = {
                    "minutes": float(row["min"]),
                    "points": int(float(row["pts"])),
                    "fga": int(float(row["fga"])),
                    "fta": int(float(row["fta"])),
                    "oreb": int(float(row["oreb"])),
                    "dreb": int(float(row["dreb"])),
                    "tov": int(float(row["tov"])),
                    "ast": int(float(row["ast"])),
                }
            except ValueError:
                result.skipped_bad += 1
                continue
            existing = session.scalar(
                select(TeamGameStat).where(TeamGameStat.game_id == gid, TeamGameStat.team_id == tid)
            )
            if existing is None:
                session.add(
                    TeamGameStat(game_id=gid, team_id=tid, ingestion_run_id=run.id, **values)
                )
                result.inserted += 1
            elif any(getattr(existing, k) != v for k, v in values.items()):
                for k, v in values.items():
                    setattr(existing, k, v)
                existing.ingestion_run_id = run.id
                result.updated += 1
            else:
                result.unchanged += 1
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    run.records_received = result.rows
    run.records_inserted = result.inserted
    run.records_updated = result.updated
    run.records_unchanged = result.unchanged
    session.commit()
    return result
