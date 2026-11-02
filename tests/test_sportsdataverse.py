"""SportsDataverse adapter tests against the real-schema excerpt in tests/fixtures/sdv_sample.

The fixture is served through an httpx MockTransport so no network is used.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prizepicks_predictor.db import ExternalId, Game, Player, PlayerGameStat, Team
from prizepicks_predictor.ingestion import ProviderError, load_game_logs
from prizepicks_predictor.ingestion.sportsdataverse import (
    SOURCE_NAME,
    SportsDataverseProvider,
    TeamGame,
    box_row_to_game_log,
    is_dnp,
    season_type_for,
)
from prizepicks_predictor.prediction import (
    InsufficientDataError,
    PredictionRequest,
    StatType,
    WarningCode,
    predict,
)
from prizepicks_predictor.services import find_players, get_player_game_logs
from prizepicks_predictor.services.data_quality import build_report, render_report

SAMPLE = Path(__file__).parent / "fixtures" / "sdv_sample"


def _transport(sample_dir: Path = SAMPLE, *, box_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name.startswith("player_boxscores_"):
            if box_status != 200:
                return httpx.Response(box_status, text="Not Found")
            return httpx.Response(
                200, content=(sample_dir / "player_boxscores_2025.csv").read_bytes()
            )
        if name.startswith("player_game_logs_"):
            return httpx.Response(
                200, content=(sample_dir / "player_game_logs_2025.csv").read_bytes()
            )
        return httpx.Response(404, text="Not Found")

    return httpx.MockTransport(handler)


def _provider(tmp_path: Path, **kwargs: object) -> SportsDataverseProvider:
    kwargs.setdefault("transport", _transport())
    return SportsDataverseProvider(2025, cache_dir=tmp_path / "cache", **kwargs)  # type: ignore[arg-type]


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


# --- pure functions ------------------------------------------------------------


def test_season_type_from_game_id_prefix() -> None:
    assert season_type_for("0022400001") == "regular"
    assert season_type_for("0042400171") == "playoffs"
    assert season_type_for("0052400101") == "playin"
    assert season_type_for("0012400001") == "preseason"
    assert season_type_for("9992400001", "2") == "regular"
    assert season_type_for("9992400001", None) == "other"


def test_is_dnp() -> None:
    assert is_dnp({"minutes": "", "comment": "DNP - Coach's Decision"})
    assert not is_dnp({"minutes": "12:30", "comment": ""})


def test_box_row_to_game_log_maps_fields() -> None:
    own = TeamGame("0022400001", "1610612738", "BOS", date(2024, 11, 12), "regular", 116)
    opp = TeamGame("0022400001", "1610612737", "ATL", date(2024, 11, 12), "regular", 117)
    box = {
        "game_id": "0022400001",
        "team_id": "1610612738",
        "team_tricode": "BOS",
        "team_city": "Boston",
        "team_name": "Celtics",
        "person_id": "1627759",
        "first_name": "Jaylen",
        "family_name": "Brown",
        "position": "F",
        "side": "home",
        "minutes": "35:56",
        "points": "37",
        "rebounds_total": "5",
        "assists": "2",
        "steals": "1",
        "blocks": "0",
        "turnovers": "6",
        "three_pointers_made": "3",
    }
    rec = box_row_to_game_log(box, own, opp, season=2025, include_dnp=False)
    assert rec.source == SOURCE_NAME and rec.source_player_id == "1627759"
    assert rec.player_name == "Jaylen Brown" and rec.position == "F"
    assert rec.team == "BOS" and rec.opponent == "ATL" and rec.is_home is True
    assert rec.team_name == "Boston Celtics"
    assert rec.game_date == date(2024, 11, 12) and rec.season_start_year == 2024
    assert rec.minutes == pytest.approx(35 + 56 / 60)
    assert (rec.points, rec.rebounds, rec.assists) == (37, 5, 2)
    assert (rec.team_score, rec.opponent_score) == (116, 117)
    assert rec.started is True  # position filled => starter
    bench = box_row_to_game_log({**box, "position": ""}, own, opp, season=2025, include_dnp=False)
    assert bench.started is False and bench.position is None


def test_box_row_dnp_handling() -> None:
    own = TeamGame("g", "1", "BOS", date(2024, 11, 12), "regular", None)
    opp = TeamGame("g", "2", "ATL", date(2024, 11, 12), "regular", None)
    box = {
        "game_id": "g",
        "team_id": "1",
        "team_tricode": "BOS",
        "person_id": "9",
        "first_name": "A",
        "family_name": "B",
        "side": "away",
        "minutes": "",
        "comment": "DNP - Coach's Decision",
        "points": "",
        "rebounds_total": "",
        "assists": "",
    }
    with pytest.raises(ValueError, match="DNP"):
        box_row_to_game_log(box, own, opp, season=2025, include_dnp=False)
    rec = box_row_to_game_log(box, own, opp, season=2025, include_dnp=True)
    assert rec.minutes == 0 and rec.points == 0 and rec.is_home is False
    assert rec.started is None


# --- provider against the real-schema excerpt ------------------------------------


def test_regular_season_import(session: Session, tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    result = load_game_logs(session, provider)
    st = provider.stats

    assert st.box_rows == 129
    assert st.filtered_season_type == {"playoffs": 29, "playin": 25}
    assert st.dnp_rows > 0 and st.missing_team_log == 0
    assert result.rejected == 0
    assert result.inserted == st.yielded == 75 - st.dnp_rows + 0 or result.inserted == st.yielded
    assert _count(session, Game) == 3  # three regular-season games
    assert _count(session, PlayerGameStat) == result.inserted
    assert _count(session, Team) == 6
    assert {t.abbreviation for t in session.scalars(select(Team))} == {
        "BOS",
        "ATL",
        "LAC",
        "HOU",
        "CHI",
        "PHI",
    }
    bos = session.scalar(select(Team).where(Team.abbreviation == "BOS"))
    assert bos is not None and bos.name == "Boston Celtics"
    assert all(g.season_type == "regular" for g in session.scalars(select(Game)))
    # provider ids recorded for players and games
    assert (
        session.scalar(
            select(func.count())
            .select_from(ExternalId)
            .where(ExternalId.source == SOURCE_NAME, ExternalId.entity_type == "game")
        )
        == 3
    )
    # cached files exist for repeat runs
    assert provider.box_path.is_file() and provider.team_log_path.is_file()


def test_game_rows_are_consistent(session: Session, tmp_path: Path) -> None:
    load_game_logs(session, _provider(tmp_path))
    game = session.scalar(select(Game).where(Game.game_date == date(2024, 11, 12)))
    assert game is not None
    assert (game.home_team.abbreviation, game.away_team.abbreviation) == ("BOS", "ATL")
    assert (game.home_score, game.away_score) == (116, 117)
    rows = session.scalars(select(PlayerGameStat).where(PlayerGameStat.game_id == game.id)).all()
    assert all(r.minutes > 0 for r in rows)
    assert sum(1 for r in rows if r.started) == 10  # five starters per team
    # both teams' players share the one game row
    assert {r.team.abbreviation for r in rows} == {"BOS", "ATL"}


def test_traded_player_keeps_one_identity(session: Session, tmp_path: Path) -> None:
    load_game_logs(session, _provider(tmp_path))
    roddy = find_players(session, "David Roddy")
    assert len(roddy) == 1  # one person_id -> one player across ATL, PHI and HOU
    logs = get_player_game_logs(session, roddy[0].id)
    assert [log.team for log in logs] == ["HOU", "PHI", "ATL"]  # newest first
    assert logs[0].game_date > logs[1].game_date > logs[2].game_date


def test_reimport_is_idempotent(session: Session, tmp_path: Path) -> None:
    first = load_game_logs(session, _provider(tmp_path))
    second = load_game_logs(session, _provider(tmp_path))
    assert second.inserted == 0 and second.updated == 0
    assert second.unchanged == first.inserted
    assert _count(session, PlayerGameStat) == first.inserted
    assert _count(session, Player) == len(set(session.scalars(select(PlayerGameStat.player_id))))


def test_include_playoffs_and_dnp(session: Session, tmp_path: Path) -> None:
    provider = _provider(tmp_path, season_types=("regular", "playoffs"), include_dnp=True)
    result = load_game_logs(session, provider)
    assert provider.stats.filtered_season_type == {"playin": 25}
    assert result.inserted == 129 - 25
    assert _count(session, Game) == 4
    assert (
        session.scalar(select(func.count()).where(PlayerGameStat.minutes == 0))
        == provider.stats.dnp_rows
    )
    assert set(session.scalars(select(Game.season_type).distinct())) == {"regular", "playoffs"}
    # default query hides playoff games; explicit opt-in shows them
    jokic = find_players(session, "Jokic")
    if jokic:
        assert get_player_game_logs(session, jokic[0].id) == []
        assert len(get_player_game_logs(session, jokic[0].id, season_types=None)) == 1


def test_playin_without_team_log_is_rejected_with_reason(session: Session, tmp_path: Path) -> None:
    provider = _provider(tmp_path, season_types=("playin",))
    result = load_game_logs(session, provider)
    assert result.inserted == 0
    assert result.rejected == provider.stats.missing_team_log > 0
    assert all("no team game log" in r for r in result.rejections)


def test_missing_season_is_provider_error(session: Session, tmp_path: Path) -> None:
    provider = _provider(tmp_path, transport=_transport(box_status=404))
    with pytest.raises(ProviderError, match="not published"):
        load_game_logs(session, provider)
    assert not provider.box_path.exists()


def test_schema_drift_is_provider_error(session: Session, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "player_boxscores_2025.csv").write_text("game_id,team_id\n1,2\n")
    (broken / "player_game_logs_2025.csv").write_text(
        (SAMPLE / "player_game_logs_2025.csv").read_text()
    )
    provider = _provider(tmp_path, transport=_transport(broken))
    with pytest.raises(ProviderError, match="missing columns"):
        load_game_logs(session, provider)


def test_network_error_is_provider_error(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    provider = _provider(tmp_path, transport=httpx.MockTransport(boom))
    with pytest.raises(ProviderError, match="download failed"):
        list(provider.iter_game_logs())


def test_cache_is_reused_without_network(session: Session, tmp_path: Path) -> None:
    load_game_logs(session, _provider(tmp_path))
    calls = 0

    def counting(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = _provider(tmp_path, transport=httpx.MockTransport(counting))
    load_game_logs(session, provider)
    assert calls == 0


def test_multi_season_import_keeps_seasons_separate(session: Session, tmp_path: Path) -> None:
    first = load_game_logs(session, _provider(tmp_path))
    # Fabricate a "2025-26" copy of the excerpt: same players, game ids and dates one season later.
    shifted = tmp_path / "shifted"
    shifted.mkdir()
    box_text = (SAMPLE / "player_boxscores_2025.csv").read_text().replace("00224", "00225")
    log_text = (
        (SAMPLE / "player_game_logs_2025.csv")
        .read_text()
        .replace("00224", "00225")
        .replace("2025-", "2026-")
        .replace("2024-", "2025-")
    )
    (shifted / "player_boxscores_2025.csv").write_text(box_text)
    (shifted / "player_game_logs_2025.csv").write_text(log_text)
    other = SportsDataverseProvider(
        2026, cache_dir=tmp_path / "cache", transport=_transport(shifted)
    )
    second = load_game_logs(session, other)

    assert second.inserted == first.inserted
    assert len(set(session.scalars(select(Game.season_id).distinct()))) == 2
    assert _count(session, Game) == 6
    # Same person_id namespace -> the same Player rows, now with two seasons of history.
    assert _count(session, Player) == len(set(session.scalars(select(PlayerGameStat.player_id))))
    roddy = find_players(session, "David Roddy")[0]
    assert len(get_player_game_logs(session, roddy.id)) == 6
    assert len(get_player_game_logs(session, roddy.id, season_start_year=2025)) == 3


# --- downstream: data quality and predictions on real rows -------------------------


def test_data_quality_report(session: Session, tmp_path: Path) -> None:
    load_game_logs(session, _provider(tmp_path))
    report = build_report(session)
    assert report.games == 3 and report.player_games > 0
    assert report.duplicate_player_game_keys == 0 and report.duplicate_games == 0
    assert report.stat_rows_without_game_date == 0 and report.games_without_scores == 0
    assert report.players_with_multiple_teams == 1  # David Roddy
    assert [s.season for s in report.seasons] == ["2024-25"]
    assert report.sources[0].source == SOURCE_NAME and report.sources[0].last_status == "succeeded"
    text = render_report(report)
    assert "2024-25 regular" in text and "duplicate (game, player) keys: 0" in text


def test_prediction_runs_on_real_rows_with_point_in_time_cutoff(
    session: Session, tmp_path: Path
) -> None:
    load_game_logs(session, _provider(tmp_path, include_dnp=True))
    roddy = find_players(session, "David Roddy")[0]

    # Cutoff before his second game: one prior game, so the engine refuses rather than guesses.
    with pytest.raises(InsufficientDataError):
        predict(
            session,
            PredictionRequest(
                player_id=roddy.id,
                stat_type=StatType.POINTS,
                line=8.5,
                as_of_date=date(2025, 2, 24),
            ),
        )
    logs = get_player_game_logs(session, roddy.id, before=date(2025, 2, 24))
    assert len(logs) == 1 and logs[0].game_date == date(2024, 11, 12)

    # After all three games: a prediction carrying sample-size and team-change warnings.
    result = predict(
        session,
        PredictionRequest(
            player_id=roddy.id, stat_type=StatType.POINTS, line=8.5, as_of_date=date(2025, 4, 10)
        ),
    )
    assert result.sample_size == 3
    assert result.features.cutoff_last_game_date == date(2025, 4, 9)
    assert WarningCode.SMALL_SAMPLE in result.warnings
    assert WarningCode.TEAM_CHANGE_IN_WINDOW in result.warnings
    assert result.probabilities.more + result.probabilities.less == pytest.approx(1.0)
