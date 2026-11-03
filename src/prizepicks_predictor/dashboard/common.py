"""Shared Streamlit helpers: cached engine, session access, disclaimers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import streamlit as st
from sqlalchemy.orm import Session, sessionmaker

from prizepicks_predictor.config import get_settings
from prizepicks_predictor.db import create_db_engine, get_session_factory, init_db

RESEARCH_NOTE = (
    "Research tool. Probabilities are calibrated against synthetic thresholds built from each "
    "player's own history, not against PrizePicks or sportsbook lines. Nothing here is a wagering "
    "recommendation. Underlying statistics are NBA.com data republished by SportsDataverse and "
    "used non-commercially; see docs/LICENSING_RISK.md."
)


@st.cache_resource(show_spinner=False)
def _factory(database_url: str) -> sessionmaker[Session]:
    engine = create_db_engine(database_url)
    init_db(engine)
    return get_session_factory(engine)


@contextmanager
def db_session() -> Iterator[Session]:
    with _factory(get_settings().database_url)() as session:
        yield session


def calibration_dir() -> Path:
    return Path(get_settings().calibration_dir)


def backtest_dirs() -> list[Path]:
    """Committed summaries first, then any local full runs."""
    return [Path("docs/backtests"), Path("data/backtests")]


def footer() -> None:
    st.caption(RESEARCH_NOTE)
