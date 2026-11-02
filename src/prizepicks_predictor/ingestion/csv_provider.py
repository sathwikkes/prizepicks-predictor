"""CSV game-log provider.

Expected columns (header names are case-insensitive):

    game_date, player_id, player_name, team, opponent, home_away, minutes,
    points, rebounds, assists

Optional columns:

    source_game_id, season, season_type, steals, blocks, turnovers, fg3m, started,
    team_score, opponent_score

``home_away`` accepts ``home``/``away`` or ``H``/``A``. ``minutes`` accepts a
decimal (``34.5``) or ``MM:SS`` (``34:30``).
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from prizepicks_predictor.ingestion.base import ProviderError, RecordValidationError
from prizepicks_predictor.schemas.game_log import NormalizedGameLog

REQUIRED_COLUMNS = frozenset(
    {
        "game_date",
        "player_id",
        "player_name",
        "team",
        "opponent",
        "home_away",
        "minutes",
        "points",
        "rebounds",
        "assists",
    }
)

_TRUE = {"1", "true", "t", "yes", "y"}
_FALSE = {"0", "false", "f", "no", "n"}


def parse_minutes(raw: str) -> float:
    """Convert ``"34:30"`` or ``"34.5"`` to decimal minutes."""
    text = raw.strip()
    if not text:
        raise ValueError("minutes is empty")
    if ":" in text:
        mins, secs = text.split(":", 1)
        return int(mins) + int(secs) / 60.0
    return float(text)


def parse_home_away(raw: str) -> bool:
    text = raw.strip().lower()
    if text in {"home", "h"}:
        return True
    if text in {"away", "a", "@"}:
        return False
    raise ValueError(f"home_away must be home/away, got {raw!r}")


def parse_optional_int(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    return int(raw)


def parse_optional_bool(raw: str | None) -> bool | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"cannot interpret {raw!r} as boolean")


def row_to_game_log(row: dict[str, str], *, source: str) -> NormalizedGameLog:
    """Pure function: one CSV row -> validated record. Raises ``ValueError`` on bad data."""
    season_raw = row.get("season")
    return NormalizedGameLog(
        source=source,
        source_player_id=row["player_id"],
        source_game_id=row.get("source_game_id") or None,
        player_name=row["player_name"],
        team=row["team"],
        opponent=row["opponent"],
        is_home=parse_home_away(row["home_away"]),
        game_date=date.fromisoformat(row["game_date"].strip()),
        season_start_year=int(season_raw) if season_raw and season_raw.strip() else None,
        season_type=row.get("season_type") or "regular",
        minutes=parse_minutes(row["minutes"]),
        points=int(row["points"]),
        rebounds=int(row["rebounds"]),
        assists=int(row["assists"]),
        steals=parse_optional_int(row.get("steals")),
        blocks=parse_optional_int(row.get("blocks")),
        turnovers=parse_optional_int(row.get("turnovers")),
        three_pointers_made=parse_optional_int(row.get("fg3m")),
        started=parse_optional_bool(row.get("started")),
        team_score=parse_optional_int(row.get("team_score")),
        opponent_score=parse_optional_int(row.get("opponent_score")),
    )


class CsvGameLogProvider:
    def __init__(self, path: str | Path, *, source_name: str = "csv") -> None:
        self.path = Path(path)
        self.source_name = source_name

    def describe(self) -> str:
        return f"CSV file {self.path}"

    def iter_game_logs(self) -> Iterator[NormalizedGameLog | RecordValidationError]:
        if not self.path.is_file():
            raise ProviderError(f"CSV file not found: {self.path}")

        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ProviderError(f"CSV file is empty: {self.path}")
            fieldnames = [name.strip().lower() for name in reader.fieldnames]
            missing = REQUIRED_COLUMNS - set(fieldnames)
            if missing:
                raise ProviderError(f"CSV missing required columns: {sorted(missing)}")

            for line_no, raw_row in enumerate(reader, start=2):
                row = {k.strip().lower(): (v or "") for k, v in raw_row.items() if k is not None}
                ref = f"{self.path.name}:{line_no}"
                try:
                    yield row_to_game_log(row, source=self.source_name)
                except (ValueError, ValidationError, KeyError) as exc:
                    yield RecordValidationError(str(exc), record_ref=ref)
