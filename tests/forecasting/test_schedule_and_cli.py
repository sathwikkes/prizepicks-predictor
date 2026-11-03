from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.cli import main
from prizepicks_predictor.db import ScheduledGame
from prizepicks_predictor.ingestion import ProviderError
from prizepicks_predictor.ingestion.schedules import (
    EspnScheduleProvider,
    load_schedule,
    nba_tricode,
    parse_tipoff,
    row_to_record,
)

HEADER = (
    "game_id,date,game_date,home_abbreviation,away_abbreviation,status_type_name,"
    "season,season_type,home_score,away_score"
)
ROWS = [
    "401909906,2026-11-02T02:00Z,2026-11-01,LAL,TOR,STATUS_FINAL,2027,2,131,125",
    "401910001,2026-11-05T00:30Z,2026-11-04,GS,NO,STATUS_SCHEDULED,2027,2,0,0",
    "401910002,2026-11-05T01:00Z,2026-11-04,SA,UTAH,STATUS_POSTPONED,2027,2,0,0",
    "401910003,,2026-11-06,TBD,TBD,STATUS_SCHEDULED,2027,2,0,0",
]


def _transport(rows: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "nba_schedule_2027.csv" in request.url.path:
            return httpx.Response(200, content=("\n".join([HEADER, *rows]) + "\n").encode())
        return httpx.Response(404, text="Not Found")

    return httpx.MockTransport(handler)


def test_schedule_parsing_helpers() -> None:
    assert (
        nba_tricode("GS") == "GSW" and nba_tricode("UTAH") == "UTA" and nba_tricode("BOS") == "BOS"
    )
    assert parse_tipoff("2026-11-02T02:00Z") == datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
    assert parse_tipoff("") is None
    rec = row_to_record(dict(zip(HEADER.split(","), ROWS[0].split(","), strict=True)))
    assert rec.status == "final" and rec.home_score == 131 and rec.season_start_year == 2026
    assert rec.game_date == date(2026, 11, 1) and rec.home == "LAL" and rec.season_type == "regular"
    sched = row_to_record(dict(zip(HEADER.split(","), ROWS[1].split(","), strict=True)))
    assert (
        sched.status == "scheduled"
        and sched.home_score is None
        and sched.home == "GSW"
        and sched.away == "NOP"
    )


def test_schedule_load_is_idempotent_and_updates_status(session: Session, tmp_path: Path) -> None:
    provider = EspnScheduleProvider(2027, cache_dir=tmp_path, transport=_transport(ROWS))
    first = load_schedule(session, provider)
    assert first.inserted == 3 and provider.stats.skipped_tbd == 1
    assert provider.stats.statuses == {"final": 1, "scheduled": 1, "postponed": 1}
    again = load_schedule(
        session, EspnScheduleProvider(2027, cache_dir=tmp_path, transport=_transport(ROWS))
    )
    assert again.inserted == 0 and again.unchanged == 3
    # status change on refresh (forced re-download)
    updated_rows = [
        ROWS[0],
        ROWS[1].replace("STATUS_SCHEDULED", "STATUS_FINAL").replace(",0,0", ",110,100"),
        ROWS[2],
        ROWS[3],
    ]
    third = load_schedule(
        session,
        EspnScheduleProvider(
            2027, cache_dir=tmp_path, force_download=True, transport=_transport(updated_rows)
        ),
    )
    assert third.updated == 1 and third.unchanged == 2
    g = session.scalar(select(ScheduledGame).where(ScheduledGame.external_id == "401910001"))
    assert g is not None and g.status == "final" and (g.home_score, g.away_score) == (110, 100)
    assert g.tipoff_utc == datetime(2026, 11, 5, 0, 30)


def test_schedule_errors(session: Session, tmp_path: Path) -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(ProviderError, match="not published"):
        load_schedule(
            session,
            EspnScheduleProvider(2030, cache_dir=tmp_path, transport=httpx.MockTransport(missing)),
        )

    def drift(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"game_id,foo\n1,2\n")

    with pytest.raises(ProviderError, match="missing columns"):
        load_schedule(
            session,
            EspnScheduleProvider(
                2027, cache_dir=tmp_path / "d", transport=httpx.MockTransport(drift)
            ),
        )


def test_cli_forecast_evaluate_monitor_end_to_end(
    db_url: str, synthetic_csv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    main(["--database-url", db_url, "ingest-csv", str(synthetic_csv), "--source", "fx"])
    targets = tmp_path / "targets.csv"
    targets.write_text("player_id,stat,line\n1,points,24.5\n1,rebounds,\n")
    capsys.readouterr()
    # Historical replay: target date is in the past relative to the wall clock, so with no
    # schedule row the forecast cannot verify tip-off; it still runs for manual targets and says so.
    assert (
        main(
            [
                "--database-url",
                db_url,
                "forecast",
                "--date",
                "2024-12-01",
                "--targets-file",
                str(targets),
                "--save",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "saved=2" in out and "Alex Rivera" in out and "synthetic trailing-median" in out
    assert (
        main(
            [
                "--database-url",
                db_url,
                "forecast",
                "--date",
                "2024-12-01",
                "--targets-file",
                str(targets),
                "--save",
            ]
        )
        == 0
    )
    assert "duplicates=2" in capsys.readouterr().out
    assert main(["--database-url", db_url, "evaluate-predictions", "--through", "2024-12-01"]) == 0
    assert "missing_result" in capsys.readouterr().out
    assert main(["--database-url", db_url, "monitor"]) == 0
    out = capsys.readouterr().out
    assert "snapshots=2" in out and "missing_result" in out and "preliminary" in out


def test_cli_fit_calibrators_from_backtest_runs(
    db_url: str, synthetic_csv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    main(["--database-url", db_url, "ingest-csv", str(synthetic_csv), "--source", "fx"])
    bt = tmp_path / "bt"
    for name, seed in (("fit", 0), ("eval", 1)):
        assert (
            main(
                [
                    "--database-url",
                    db_url,
                    "backtest",
                    "--season",
                    "2024-25",
                    "--min-prior-games",
                    "3",
                    "--max-observations",
                    "20",
                    "--seed",
                    str(seed),
                    "--output-dir",
                    str(bt),
                    "--run-name",
                    name,
                    "--no-charts",
                ]
            )
            == 0
        )
    capsys.readouterr()
    # far too few pairs: every stat is skipped and the command reports failure
    assert (
        main(
            [
                "--database-url",
                db_url,
                "fit-calibrators",
                "--fit-run",
                str(bt / "fit"),
                "--eval-run",
                str(bt / "eval"),
                "--out",
                str(tmp_path / "art"),
            ]
        )
        == 2
    )
    err = capsys.readouterr().err
    assert "need 2000" in err
    assert not list((tmp_path / "art").glob("*.json")) if (tmp_path / "art").exists() else True
