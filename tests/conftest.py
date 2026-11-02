from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from prizepicks_predictor.db import create_db_engine, get_session_factory, init_db

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"
SYNTHETIC_CSV = FIXTURE_DIR / "synthetic_game_logs.csv"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = get_session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture
def synthetic_csv() -> Path:
    assert SYNTHETIC_CSV.is_file(), "run scripts/generate_synthetic_fixture.py"
    return SYNTHETIC_CSV


def write_csv(path: Path, header: str, *rows: str) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


BASE_HEADER = (
    "game_date,player_id,player_name,team,opponent,home_away,minutes,points,rebounds,assists"
)
