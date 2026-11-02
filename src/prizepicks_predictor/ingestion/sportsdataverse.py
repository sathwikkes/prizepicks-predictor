"""SportsDataverse adapter: real NBA player box scores from public GitHub releases.

Source
------
https://github.com/sportsdataverse/sportsdataverse-data (MIT-licensed repository). Two
release assets per season are combined, both scraped by SportsDataverse from NBA.com
stats:

* ``nba_stats_player_boxscores/player_boxscores_{season}.csv`` - one row per player
  per game: official ``person_id``, team tricode, ``minutes`` as ``MM:SS`` (empty for
  DNP/DND/NWT rows, which carry a ``comment``), points, rebounds, assists, ``side``
  (home/away), ``game_id``, ``season_type_id``. It has **no game date or opponent**.
* ``nba_stats_player_game_logs/player_game_logs_{season}.csv`` - despite the tag name
  these are **team** game logs: ``game_id``, ``team_id``, ``game_date``, ``matchup``,
  ``season_type`` and team ``pts``. Joining on ``(game_id, team_id)`` supplies the date,
  opponent and final score; the other team's row in the same game is the opponent.

``season`` follows the SportsDataverse/hoopR convention of the season's END year
(2025 == 2024-25). Our ``season_start_year`` is therefore ``season - 1``.

Coverage notes (verified 2026-11-02 against the 2025 files): regular-season and playoff
games join completely; play-in (game id prefix 005) and NBA Cup final / all-star rows
(006) are absent from the team logs and are rejected with a reason unless excluded by
``season_types``. Minutes are only ever ``MM:SS`` or empty.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
from pydantic import ValidationError

from prizepicks_predictor.ingestion.base import ProviderError, RecordValidationError
from prizepicks_predictor.ingestion.csv_provider import parse_minutes
from prizepicks_predictor.schemas.game_log import NormalizedGameLog

log = logging.getLogger(__name__)

SOURCE_NAME = "sportsdataverse_nba_stats"
RELEASE_BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
BOX_URL = RELEASE_BASE + "/nba_stats_player_boxscores/player_boxscores_{season}.csv"
TEAM_LOG_URL = RELEASE_BASE + "/nba_stats_player_game_logs/player_game_logs_{season}.csv"

#: NBA.com game id prefix -> season type.
SEASON_TYPE_BY_PREFIX = {
    "001": "preseason",
    "002": "regular",
    "003": "allstar",
    "004": "playoffs",
    "005": "playin",
    "006": "other",
}
#: ``season_type_id`` in the box-score file -> season type (used when the prefix is unknown).
SEASON_TYPE_BY_ID = {
    "1": "preseason",
    "2": "regular",
    "3": "allstar",
    "4": "playoffs",
    "5": "playin",
}

BOX_REQUIRED = frozenset(
    {
        "game_id",
        "team_id",
        "team_tricode",
        "person_id",
        "first_name",
        "family_name",
        "minutes",
        "points",
        "rebounds_total",
        "assists",
        "side",
    }
)
TEAM_LOG_REQUIRED = frozenset({"game_id", "team_id", "game_date", "team_abbreviation"})


def season_type_for(game_id: str, season_type_id: str | None = None) -> str:
    by_prefix = SEASON_TYPE_BY_PREFIX.get(game_id[:3])
    if by_prefix:
        return by_prefix
    return SEASON_TYPE_BY_ID.get(season_type_id or "", "other")


def is_dnp(box_row: dict[str, str]) -> bool:
    """A box-score row for a player who did not play: no minutes, with an explanatory comment."""
    return not box_row.get("minutes", "").strip()


def _opt_int(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    return int(float(raw))


@dataclass(frozen=True)
class TeamGame:
    game_id: str
    team_id: str
    team: str
    game_date: date
    season_type: str
    points: int | None


def box_row_to_game_log(
    box: dict[str, str],
    own: TeamGame,
    opp: TeamGame,
    *,
    season: int,
    include_dnp: bool,
) -> NormalizedGameLog:
    """Pure function: one box-score row plus its two team-log rows -> normalized record."""
    dnp = is_dnp(box)
    if dnp and not include_dnp:
        raise ValueError("DNP row excluded")
    minutes = 0.0 if dnp else parse_minutes(box["minutes"])

    def stat(name: str) -> int:
        return 0 if dnp else int(float(box[name]))

    first, family = box.get("first_name", "").strip(), box.get("family_name", "").strip()
    team_city, team_name = box.get("team_city", "").strip(), box.get("team_name", "").strip()
    # NBA.com box scores fill ``position`` for exactly the five starters (verified: 5 per
    # team in every 2024-25 regular-season game), so it doubles as the starter flag.
    position = (box.get("position") or "").strip() or None
    return NormalizedGameLog(
        source=SOURCE_NAME,
        source_player_id=box["person_id"].strip(),
        source_game_id=box["game_id"].strip(),
        player_name=f"{first} {family}".strip(),
        team=box["team_tricode"],
        opponent=opp.team,
        is_home=box["side"].strip().lower() == "home",
        game_date=own.game_date,
        season_start_year=season - 1,
        season_type=own.season_type,
        team_name=f"{team_city} {team_name}".strip() or None,
        position=position,
        started=None if dnp else position is not None,
        minutes=minutes,
        points=stat("points"),
        rebounds=stat("rebounds_total"),
        assists=stat("assists"),
        steals=0 if dnp else _opt_int(box.get("steals")),
        blocks=0 if dnp else _opt_int(box.get("blocks")),
        turnovers=0 if dnp else _opt_int(box.get("turnovers")),
        three_pointers_made=0 if dnp else _opt_int(box.get("three_pointers_made")),
        team_score=own.points,
        opponent_score=opp.points,
    )


@dataclass
class ProviderStats:
    """Counts the provider keeps so the CLI can explain what was skipped and why."""

    box_rows: int = 0
    team_log_rows: int = 0
    dnp_rows: int = 0
    dnp_reasons: dict[str, int] = field(default_factory=dict)
    filtered_season_type: dict[str, int] = field(default_factory=dict)
    missing_team_log: int = 0
    yielded: int = 0


class SportsDataverseProvider:
    source_name = SOURCE_NAME

    def __init__(
        self,
        season: int,
        *,
        cache_dir: str | Path,
        season_types: Sequence[str] = ("regular",),
        include_dnp: bool = False,
        force_download: bool = False,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 180.0,
    ) -> None:
        if season < 1947 or season > 2100:
            raise ProviderError(f"implausible season {season}")
        self.season = season
        self.cache_dir = Path(cache_dir)
        self.season_types = tuple(season_types)
        self.include_dnp = include_dnp
        self.force_download = force_download
        self.stats = ProviderStats()
        self._client = httpx.Client(follow_redirects=True, timeout=timeout, transport=transport)

    # --- public -----------------------------------------------------------
    def describe(self) -> str:
        return (
            f"SportsDataverse nba_stats season={self.season} "
            f"({self.season - 1}-{self.season % 100:02d}) "
            f"season_types={list(self.season_types)} include_dnp={self.include_dnp} "
            f"files={self.box_path.name},{self.team_log_path.name}"
        )

    @property
    def box_path(self) -> Path:
        return self.cache_dir / f"player_boxscores_{self.season}.csv"

    @property
    def team_log_path(self) -> Path:
        return self.cache_dir / f"team_game_logs_{self.season}.csv"

    def iter_game_logs(self) -> Iterator[NormalizedGameLog | RecordValidationError]:
        self._fetch(BOX_URL.format(season=self.season), self.box_path)
        self._fetch(TEAM_LOG_URL.format(season=self.season), self.team_log_path)

        team_games = self._load_team_games()
        by_game: dict[str, list[TeamGame]] = {}
        for tg in team_games.values():
            by_game.setdefault(tg.game_id, []).append(tg)

        for line_no, box in self._read_csv(self.box_path, BOX_REQUIRED):
            self.stats.box_rows += 1
            ref = (
                f"{self.box_path.name}:{line_no} game={box.get('game_id')} "
                f"person={box.get('person_id')}"
            )
            game_id = box["game_id"].strip()
            stype = season_type_for(game_id, box.get("season_type_id"))
            if stype not in self.season_types:
                self.stats.filtered_season_type[stype] = (
                    self.stats.filtered_season_type.get(stype, 0) + 1
                )
                continue
            if is_dnp(box):
                self.stats.dnp_rows += 1
                reason = (box.get("comment") or "unknown").split(" - ")[0].strip()
                self.stats.dnp_reasons[reason] = self.stats.dnp_reasons.get(reason, 0) + 1
                if not self.include_dnp:
                    continue

            own = team_games.get((game_id, box["team_id"].strip()))
            opp = next(
                (t for t in by_game.get(game_id, []) if t.team_id != box["team_id"].strip()), None
            )
            if own is None or opp is None:
                self.stats.missing_team_log += 1
                yield RecordValidationError(
                    f"no team game log for game {game_id} ({stype}); date/opponent unknown",
                    record_ref=ref,
                )
                continue
            try:
                record = box_row_to_game_log(
                    box, own, opp, season=self.season, include_dnp=self.include_dnp
                )
            except (ValueError, ValidationError, KeyError) as exc:
                yield RecordValidationError(str(exc), record_ref=ref)
                continue
            self.stats.yielded += 1
            yield record

    def close(self) -> None:
        self._client.close()

    # --- internals --------------------------------------------------------
    def _fetch(self, url: str, dest: Path) -> None:
        if dest.is_file() and dest.stat().st_size > 0 and not self.force_download:
            log.info("using cached %s", dest)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        log.info("downloading %s", url)
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise ProviderError(f"season {self.season} not published: {url} (404)")
                if response.status_code >= 400:
                    raise ProviderError(f"HTTP {response.status_code} downloading {url}")
                with tmp.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        fh.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise ProviderError(f"download failed for {url}: {exc}") from exc
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise ProviderError(f"empty download for {url}")
        tmp.replace(dest)

    def _read_csv(
        self, path: Path, required: frozenset[str]
    ) -> Iterator[tuple[int, dict[str, str]]]:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ProviderError(f"{path.name} is empty")
            fields = {f.strip().lower() for f in reader.fieldnames}
            missing = required - fields
            if missing:
                raise ProviderError(
                    f"{path.name} missing columns {sorted(missing)}; schema may have changed"
                )
            for line_no, raw in enumerate(reader, start=2):
                yield (
                    line_no,
                    {k.strip().lower(): (v or "") for k, v in raw.items() if k is not None},
                )

    def _load_team_games(self) -> dict[tuple[str, str], TeamGame]:
        games: dict[tuple[str, str], TeamGame] = {}
        for _line_no, row in self._read_csv(self.team_log_path, TEAM_LOG_REQUIRED):
            self.stats.team_log_rows += 1
            game_id = row["game_id"].strip()
            stype = row.get("season_type", "").strip().lower().replace("-season", "")
            if stype not in SEASON_TYPE_BY_PREFIX.values():
                stype = season_type_for(game_id)
            try:
                tg = TeamGame(
                    game_id=game_id,
                    team_id=row["team_id"].strip(),
                    team=row["team_abbreviation"].strip().upper(),
                    game_date=date.fromisoformat(row["game_date"].strip()[:10]),
                    season_type=stype,
                    points=_opt_int(row.get("pts")),
                )
            except ValueError as exc:
                raise ProviderError(
                    f"malformed team game log row for game {game_id}: {exc}"
                ) from exc
            games[(tg.game_id, tg.team_id)] = tg
        if not games:
            raise ProviderError(f"{self.team_log_path.name} contained no games")
        return games
