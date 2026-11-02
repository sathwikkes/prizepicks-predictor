"""BALLDONTLIE adapter tests against a mocked transport.

These prove the adapter parses the documented response shape, paginates, and
maps errors. They do NOT prove compatibility with the live API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from prizepicks_predictor.ingestion import ProviderError, load_game_logs
from prizepicks_predictor.ingestion.balldontlie import BallDontLieProvider, stat_to_game_log
from prizepicks_predictor.ingestion.base import RecordValidationError
from prizepicks_predictor.services import get_player_game_logs

TEAMS = {"data": [{"id": 2, "abbreviation": "BOS"}, {"id": 14, "abbreviation": "LAL"}]}


def _stat(stat_id: int, game_id: int, day: str, pts: int, team_id: int = 2) -> dict[str, Any]:
    return {
        "id": stat_id,
        "min": "34:12",
        "pts": pts,
        "reb": 7,
        "ast": 5,
        "stl": 1,
        "blk": 0,
        "turnover": 2,
        "fg3m": 3,
        "game": {
            "id": game_id,
            "date": day,
            "season": 2024,
            "home_team_id": 2,
            "visitor_team_id": 14,
            "home_team_score": 110,
            "visitor_team_score": 104,
        },
        "player": {"id": 777, "first_name": "Mock", "last_name": "Player"},
        "team": {"id": team_id, "abbreviation": "BOS" if team_id == 2 else "LAL"},
    }


def _transport(pages: list[dict[str, Any]], *, teams_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "test-key"
        if request.url.path.endswith("/teams"):
            return httpx.Response(teams_status, json=TEAMS)
        if request.url.path.endswith("/stats"):
            cursor = request.url.params.get("cursor")
            idx = int(cursor) if cursor else 0
            return httpx.Response(200, json=pages[idx])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_stat_to_game_log_maps_fields() -> None:
    rec = stat_to_game_log(_stat(1, 500, "2024-11-01", 28), {2: "BOS", 14: "LAL"})
    assert rec.source == "balldontlie"
    assert rec.player_name == "Mock Player"
    assert rec.team == "BOS" and rec.opponent == "LAL" and rec.is_home is True
    assert rec.minutes == pytest.approx(34.2)
    assert rec.points == 28 and rec.three_pointers_made == 3
    assert (rec.team_score, rec.opponent_score) == (110, 104)
    assert rec.season_start_year == 2024


def test_stat_to_game_log_away_team_perspective() -> None:
    rec = stat_to_game_log(_stat(1, 500, "2024-11-01", 28, team_id=14), {2: "BOS", 14: "LAL"})
    assert rec.team == "LAL" and rec.opponent == "BOS" and rec.is_home is False
    assert (rec.team_score, rec.opponent_score) == (104, 110)


def test_pagination_and_load(session: Session) -> None:
    pages = [
        {"data": [_stat(1, 500, "2024-11-01", 28)], "meta": {"next_cursor": 1, "per_page": 1}},
        {"data": [_stat(2, 501, "2024-11-03", 31)], "meta": {"next_cursor": None, "per_page": 1}},
    ]
    provider = BallDontLieProvider(
        "test-key", seasons=[2024], player_ids=[777], transport=_transport(pages)
    )
    result = load_game_logs(session, provider)
    assert result.inserted == 2 and result.rejected == 0
    rows = get_player_game_logs(session, "Mock Player")
    assert [r.points for r in rows] == [31, 28]


def test_malformed_item_yields_record_error() -> None:
    bad = _stat(9, 502, "2024-11-05", 10)
    bad["game"]["home_team_id"] = 99  # player's team not in game
    pages = [{"data": [bad], "meta": {"next_cursor": None}}]
    provider = BallDontLieProvider("test-key", transport=_transport(pages))
    items = list(provider.iter_game_logs())
    assert len(items) == 1 and isinstance(items[0], RecordValidationError)


def test_http_401_is_provider_error() -> None:
    provider = BallDontLieProvider("test-key", transport=_transport([], teams_status=401))
    with pytest.raises(ProviderError, match="401"):
        list(provider.iter_game_logs())


def test_network_failure_is_provider_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    provider = BallDontLieProvider("test-key", transport=httpx.MockTransport(boom))
    with pytest.raises(ProviderError, match="failed"):
        list(provider.iter_game_logs())


def test_non_json_is_provider_error() -> None:
    def text(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>oops</html>")

    provider = BallDontLieProvider("test-key", transport=httpx.MockTransport(text))
    with pytest.raises(ProviderError, match="non-JSON"):
        list(provider.iter_game_logs())


def test_missing_api_key_rejected() -> None:
    with pytest.raises(ProviderError, match="API key"):
        BallDontLieProvider("")


def test_payload_without_data_key_rejected() -> None:
    def weird(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"nope": []}).encode())

    provider = BallDontLieProvider("test-key", transport=httpx.MockTransport(weird))
    with pytest.raises(ProviderError, match="unexpected payload"):
        list(provider.iter_game_logs())
