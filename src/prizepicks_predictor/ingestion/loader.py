"""Persist normalized game logs into the database.

The loader is idempotent: re-running the same input produces no duplicate rows.
Entities are resolved on natural keys (team abbreviation, (date, home, away)
for games) or on provider IDs via ``external_ids`` (players). Existing stat rows
are updated only when a stat value actually changed, so unchanged re-imports
are cheap and reported honestly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import (
    ExternalId,
    Game,
    IngestionRun,
    Player,
    PlayerGameStat,
    Season,
    Team,
)
from prizepicks_predictor.ingestion.base import GameLogProvider, RecordValidationError
from prizepicks_predictor.schemas.game_log import NormalizedGameLog

log = logging.getLogger(__name__)


@dataclass
class LoadResult:
    run_id: int
    source: str
    received: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    duplicates_in_batch: int = 0
    rejections: list[str] = field(default_factory=list)

    @property
    def persisted(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _EntityResolver:
    """Resolves/creates seasons, teams, players and games with per-run caching."""

    def __init__(self, session: Session, source: str) -> None:
        self.session = session
        self.source = source
        self._seasons: dict[int, Season] = {}
        self._teams: dict[str, Team] = {}
        self._players: dict[str, Player] = {}
        self._games: dict[tuple[object, int, int], Game] = {}

    def season(self, start_year: int) -> Season:
        if start_year not in self._seasons:
            season = self.session.scalar(select(Season).where(Season.start_year == start_year))
            if season is None:
                season = Season(start_year=start_year, label=Season.label_for(start_year))
                self.session.add(season)
                self.session.flush()
            self._seasons[start_year] = season
        return self._seasons[start_year]

    def team(self, abbreviation: str) -> Team:
        if abbreviation not in self._teams:
            team = self.session.scalar(select(Team).where(Team.abbreviation == abbreviation))
            if team is None:
                team = Team(abbreviation=abbreviation)
                self.session.add(team)
                self.session.flush()
            self._teams[abbreviation] = team
        return self._teams[abbreviation]

    def player(self, external_id: str, full_name: str) -> Player:
        if external_id not in self._players:
            mapping = self.session.scalar(
                select(ExternalId).where(
                    ExternalId.source == self.source,
                    ExternalId.entity_type == "player",
                    ExternalId.external_id == external_id,
                )
            )
            if mapping is not None:
                player = self.session.get(Player, mapping.entity_id)
                if player is None:  # pragma: no cover - FK prevents this
                    raise RuntimeError(f"dangling external id {mapping.id}")
            else:
                player = Player(full_name=full_name)
                self.session.add(player)
                self.session.flush()
                self.session.add(
                    ExternalId(
                        source=self.source,
                        entity_type="player",
                        entity_id=player.id,
                        external_id=external_id,
                    )
                )
            self._players[external_id] = player
        return self._players[external_id]

    def game(self, record: NormalizedGameLog, season: Season, home: Team, away: Team) -> Game:
        key = (record.game_date, home.id, away.id)
        if key not in self._games:
            game = self.session.scalar(
                select(Game).where(
                    Game.game_date == record.game_date,
                    Game.home_team_id == home.id,
                    Game.away_team_id == away.id,
                )
            )
            if game is None:
                game = Game(
                    season_id=season.id,
                    game_date=record.game_date,
                    home_team_id=home.id,
                    away_team_id=away.id,
                )
                self.session.add(game)
                self.session.flush()
                if record.source_game_id:
                    self.session.add(
                        ExternalId(
                            source=self.source,
                            entity_type="game",
                            entity_id=game.id,
                            external_id=record.source_game_id,
                        )
                    )
            self._games[key] = game

        game = self._games[key]
        home_score = record.team_score if record.is_home else record.opponent_score
        away_score = record.opponent_score if record.is_home else record.team_score
        if home_score is not None and game.home_score is None:
            game.home_score = home_score
        if away_score is not None and game.away_score is None:
            game.away_score = away_score
        return game


def _apply_stats(row: PlayerGameStat, record: NormalizedGameLog) -> bool:
    """Copy stat values onto ``row``. Returns True if anything changed."""
    changed = False
    for name in PlayerGameStat.STAT_FIELDS:
        new_value = getattr(record, name)
        if getattr(row, name) != new_value:
            setattr(row, name, new_value)
            changed = True
    return changed


def load_game_logs(session: Session, provider: GameLogProvider) -> LoadResult:
    """Run ``provider`` and persist its records. Commits on success.

    A ``ProviderError`` raised by the provider marks the run as failed, rolls back
    all entity changes from this run, and re-raises.
    """
    run = IngestionRun(
        source=provider.source_name,
        source_detail=provider.describe(),
        started_at=_now(),
    )
    session.add(run)
    session.flush()
    run_id = run.id
    session.commit()  # the run row survives even if the load fails

    result = LoadResult(run_id=run_id, source=provider.source_name)
    resolver = _EntityResolver(session, provider.source_name)
    seen: set[tuple[object, ...]] = set()

    try:
        for item in provider.iter_game_logs():
            result.received += 1
            if isinstance(item, RecordValidationError):
                result.rejected += 1
                result.rejections.append(f"{item.record_ref}: {item}")
                log.warning("rejected record %s: %s", item.record_ref, item)
                continue

            if item.dedupe_key in seen:
                result.duplicates_in_batch += 1
                continue
            seen.add(item.dedupe_key)

            season_year = item.season_start_year
            assert season_year is not None  # populated by schema validator
            season = resolver.season(season_year)
            team = resolver.team(item.team)
            opponent = resolver.team(item.opponent)
            home, away = (team, opponent) if item.is_home else (opponent, team)
            game = resolver.game(item, season, home, away)
            player = resolver.player(item.source_player_id, item.player_name)

            existing = session.scalar(
                select(PlayerGameStat).where(
                    PlayerGameStat.game_id == game.id,
                    PlayerGameStat.player_id == player.id,
                )
            )
            if existing is None:
                row = PlayerGameStat(
                    game_id=game.id,
                    player_id=player.id,
                    team_id=team.id,
                    ingestion_run_id=run_id,
                    updated_at=_now(),
                    minutes=item.minutes,
                    points=item.points,
                    rebounds=item.rebounds,
                    assists=item.assists,
                )
                _apply_stats(row, item)
                session.add(row)
                result.inserted += 1
            elif _apply_stats(existing, item):
                existing.team_id = team.id
                existing.ingestion_run_id = run_id
                existing.updated_at = _now()
                result.updated += 1
            else:
                result.unchanged += 1

        session.flush()
    except Exception as exc:
        session.rollback()
        failed = session.get(IngestionRun, run_id)
        if failed is not None:
            failed.status = "failed"
            failed.finished_at = _now()
            failed.error_message = str(exc)[:2000]
            failed.records_received = result.received
            session.commit()
        raise

    finished = session.get(IngestionRun, run_id)
    assert finished is not None
    finished.status = "succeeded"
    finished.finished_at = _now()
    finished.records_received = result.received
    finished.records_inserted = result.inserted
    finished.records_updated = result.updated
    finished.records_unchanged = result.unchanged
    finished.records_rejected = result.rejected
    session.commit()
    return result
