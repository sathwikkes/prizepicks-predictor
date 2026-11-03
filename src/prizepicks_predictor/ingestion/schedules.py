"""ESPN schedule adapter (SportsDataverse ``espn_nba_schedules`` release).

Why ESPN and not NBA.com stats: the ``nba_stats_schedules`` release stopped updating in
May 2026 and has no 2026-27 file; the ESPN release is refreshed daily and lists every
remaining game of the season with a UTC tip-off and an explicit status.

Rows become ``scheduled_games``. Unlike box scores, schedule rows are *mutable facts*
(status, scores and times change), so reloads update them in place.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import ScheduledGame, Team
from prizepicks_predictor.ingestion.base import ProviderError
from prizepicks_predictor.ingestion.download import download_file
from prizepicks_predictor.ingestion.sportsdataverse import RELEASE_BASE

SOURCE_NAME = "sportsdataverse_espn_schedule"
SCHEDULE_URL = RELEASE_BASE + "/espn_nba_schedules/nba_schedule_{season}.csv"

#: ESPN abbreviations that differ from NBA.com tricodes.
ESPN_TO_NBA = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_DELAYED": "scheduled",
    "STATUS_IN_PROGRESS": "in_progress",
    "STATUS_HALFTIME": "in_progress",
    "STATUS_END_PERIOD": "in_progress",
    "STATUS_FINAL": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "canceled",
}
SEASON_TYPE_BY_ID = {"1": "preseason", "2": "regular", "3": "playoffs", "5": "playin"}
REQUIRED = frozenset(
    {
        "game_id",
        "date",
        "game_date",
        "home_abbreviation",
        "away_abbreviation",
        "status_type_name",
        "season",
    }
)


def nba_tricode(espn_abbr: str) -> str:
    code = espn_abbr.strip().upper()
    return ESPN_TO_NBA.get(code, code)


def parse_tipoff(raw: str) -> datetime | None:
    """'2026-11-02T02:00Z' -> aware UTC datetime; empty -> None."""
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)


@dataclass(frozen=True)
class ScheduledGameRecord:
    external_id: str
    game_date: date
    tipoff_utc: datetime | None
    home: str
    away: str
    status: str
    season_start_year: int
    season_type: str
    home_score: int | None
    away_score: int | None


def row_to_record(row: dict[str, str]) -> ScheduledGameRecord:
    status = STATUS_MAP.get(row["status_type_name"].strip(), "unknown")
    final = status == "final"
    return ScheduledGameRecord(
        external_id=row["game_id"].strip(),
        game_date=date.fromisoformat(row["game_date"].strip()[:10]),
        tipoff_utc=parse_tipoff(row["date"]),
        home=nba_tricode(row["home_abbreviation"]),
        away=nba_tricode(row["away_abbreviation"]),
        status=status,
        season_start_year=int(row["season"]) - 1,
        season_type=SEASON_TYPE_BY_ID.get(row.get("season_type", "").strip(), "other"),
        home_score=int(float(row["home_score"])) if final and row.get("home_score") else None,
        away_score=int(float(row["away_score"])) if final and row.get("away_score") else None,
    )


@dataclass
class ScheduleStats:
    rows: int = 0
    skipped_tbd: int = 0
    skipped_bad: int = 0
    yielded: int = 0
    statuses: dict[str, int] = field(default_factory=dict)


class EspnScheduleProvider:
    source_name = SOURCE_NAME

    def __init__(
        self,
        season: int,
        *,
        cache_dir: str | Path,
        force_download: bool = False,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.season = season
        self.cache_dir = Path(cache_dir)
        self.force_download = force_download
        self.stats = ScheduleStats()
        self._client = httpx.Client(follow_redirects=True, timeout=timeout, transport=transport)

    @property
    def path(self) -> Path:
        return self.cache_dir / f"espn_schedule_{self.season}.csv"

    def describe(self) -> str:
        return f"ESPN schedule season={self.season} file={self.path.name}"

    def iter_games(self) -> Iterator[ScheduledGameRecord]:
        download_file(
            self._client,
            SCHEDULE_URL.format(season=self.season),
            self.path,
            force=self.force_download,
        )
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ProviderError(f"{self.path.name} is empty")
            missing = REQUIRED - {f.strip().lower() for f in reader.fieldnames}
            if missing:
                raise ProviderError(f"{self.path.name} missing columns {sorted(missing)}")
            for raw in reader:
                row = {k.strip().lower(): (v or "") for k, v in raw.items() if k is not None}
                self.stats.rows += 1
                if "TBD" in (row["home_abbreviation"].upper(), row["away_abbreviation"].upper()):
                    self.stats.skipped_tbd += 1
                    continue
                try:
                    rec = row_to_record(row)
                except (KeyError, ValueError):
                    self.stats.skipped_bad += 1
                    continue
                self.stats.yielded += 1
                self.stats.statuses[rec.status] = self.stats.statuses.get(rec.status, 0) + 1
                yield rec

    def close(self) -> None:
        self._client.close()


@dataclass
class ScheduleLoadResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def load_schedule(session: Session, provider: EspnScheduleProvider) -> ScheduleLoadResult:
    """Upsert schedule rows by (source, external_id). Commits."""
    result = ScheduleLoadResult()
    teams: dict[str, Team] = {}

    def team(abbr: str) -> Team:
        if abbr not in teams:
            found = session.scalar(select(Team).where(Team.abbreviation == abbr))
            if found is None:
                found = Team(abbreviation=abbr)
                session.add(found)
                session.flush()
            teams[abbr] = found
        return teams[abbr]

    now = datetime.now(UTC).replace(tzinfo=None)
    for rec in provider.iter_games():
        home, away = team(rec.home), team(rec.away)
        tip = rec.tipoff_utc.replace(tzinfo=None) if rec.tipoff_utc else None
        existing = session.scalar(
            select(ScheduledGame).where(
                ScheduledGame.source == provider.source_name,
                ScheduledGame.external_id == rec.external_id,
            )
        )
        if existing is None:
            session.add(
                ScheduledGame(
                    source=provider.source_name,
                    external_id=rec.external_id,
                    season_start_year=rec.season_start_year,
                    season_type=rec.season_type,
                    game_date=rec.game_date,
                    tipoff_utc=tip,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    status=rec.status,
                    home_score=rec.home_score,
                    away_score=rec.away_score,
                    loaded_at=now,
                )
            )
            result.inserted += 1
            continue
        changed = False
        for attr, value in (
            ("game_date", rec.game_date),
            ("tipoff_utc", tip),
            ("home_team_id", home.id),
            ("away_team_id", away.id),
            ("status", rec.status),
            ("home_score", rec.home_score),
            ("away_score", rec.away_score),
            ("season_type", rec.season_type),
        ):
            if getattr(existing, attr) != value:
                setattr(existing, attr, value)
                changed = True
        if changed:
            existing.loaded_at = now
            result.updated += 1
        else:
            result.unchanged += 1
    session.commit()
    return result
