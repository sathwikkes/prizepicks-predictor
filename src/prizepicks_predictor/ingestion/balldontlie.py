"""BALLDONTLIE adapter (https://www.balldontlie.io).

Status: implemented against the documented v1 response shape and covered by
unit tests using a mocked HTTP transport. It has NOT been exercised against the
live API in this repository because no API key is configured, and the
``/stats`` endpoint is only available on paid tiers.

Endpoints used:
* ``GET /teams``  -> id -> abbreviation map (needed to resolve the opponent)
* ``GET /stats``  -> per-player box scores, cursor paginated
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import httpx
from pydantic import ValidationError

from prizepicks_predictor.ingestion.base import ProviderError, RecordValidationError
from prizepicks_predictor.schemas.game_log import NormalizedGameLog

SOURCE_NAME = "balldontlie"


def _parse_bdl_minutes(raw: Any) -> float:
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text:
        return 0.0
    if ":" in text:
        mins, secs = text.split(":", 1)
        return int(mins) + int(secs) / 60.0
    return float(text)


def stat_to_game_log(stat: dict[str, Any], team_abbr_by_id: dict[int, str]) -> NormalizedGameLog:
    """Pure function: one ``/stats`` item -> normalized record."""
    game = stat["game"]
    player = stat["player"]
    team = stat["team"]

    team_id = int(team["id"])
    home_id = int(game["home_team_id"])
    away_id = int(game["visitor_team_id"])
    if team_id not in (home_id, away_id):
        raise ValueError(f"team {team_id} is neither home nor away in game {game.get('id')}")
    is_home = team_id == home_id
    opponent_id = away_id if is_home else home_id
    try:
        opponent_abbr = team_abbr_by_id[opponent_id]
    except KeyError as exc:
        raise ValueError(f"unknown opponent team id {opponent_id}") from exc

    home_score = game.get("home_team_score")
    away_score = game.get("visitor_team_score")
    team_score = home_score if is_home else away_score
    opp_score = away_score if is_home else home_score

    return NormalizedGameLog(
        source=SOURCE_NAME,
        source_player_id=str(player["id"]),
        source_game_id=str(game["id"]),
        player_name=f"{player['first_name']} {player['last_name']}".strip(),
        team=team["abbreviation"],
        opponent=opponent_abbr,
        is_home=is_home,
        game_date=date.fromisoformat(str(game["date"])[:10]),
        season_start_year=int(game["season"]) if game.get("season") is not None else None,
        minutes=_parse_bdl_minutes(stat.get("min")),
        points=int(stat.get("pts") or 0),
        rebounds=int(stat.get("reb") or 0),
        assists=int(stat.get("ast") or 0),
        steals=stat.get("stl"),
        blocks=stat.get("blk"),
        turnovers=stat.get("turnover"),
        three_pointers_made=stat.get("fg3m"),
        team_score=team_score,
        opponent_score=opp_score,
    )


class BallDontLieProvider:
    source_name = SOURCE_NAME

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.balldontlie.io/v1",
        seasons: Sequence[int] = (),
        player_ids: Sequence[int] = (),
        per_page: int = 100,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ProviderError("BALLDONTLIE API key is required (PPP_BALLDONTLIE_API_KEY)")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": api_key},
            timeout=timeout,
            transport=transport,
        )
        self.seasons = list(seasons)
        self.player_ids = list(player_ids)
        self.per_page = per_page

    def describe(self) -> str:
        return f"BALLDONTLIE /stats seasons={self.seasons} player_ids={self.player_ids}"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {path} failed: {exc}") from exc
        if response.status_code == 401:
            raise ProviderError("BALLDONTLIE rejected the API key (401)")
        if response.status_code == 429:
            raise ProviderError("BALLDONTLIE rate limit hit (429); retry later")
        if response.status_code >= 400:
            raise ProviderError(f"BALLDONTLIE returned HTTP {response.status_code} for {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"non-JSON response from {path}") from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise ProviderError(f"unexpected payload shape from {path}")
        return payload

    def _team_map(self) -> dict[int, str]:
        payload = self._get("/teams", {})
        return {int(t["id"]): str(t["abbreviation"]) for t in payload["data"]}

    def iter_game_logs(self) -> Iterator[NormalizedGameLog | RecordValidationError]:
        teams = self._team_map()
        params: dict[str, Any] = {"per_page": self.per_page}
        if self.seasons:
            params["seasons[]"] = self.seasons
        if self.player_ids:
            params["player_ids[]"] = self.player_ids

        cursor: int | None = None
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            payload = self._get("/stats", page_params)
            for item in payload["data"]:
                ref = f"stat:{item.get('id')}"
                try:
                    yield stat_to_game_log(item, teams)
                except (KeyError, ValueError, TypeError, ValidationError) as exc:
                    yield RecordValidationError(str(exc), record_ref=ref)
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if cursor is None:
                break

    def close(self) -> None:
        self._client.close()
