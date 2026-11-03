"""Headless Streamlit runs of the app entry point and each page against a temporary database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from prizepicks_predictor.cli import main

APP = Path(__file__).resolve().parents[2] / "src" / "prizepicks_predictor" / "dashboard" / "app.py"


def _page_script(name: str) -> str:
    return f"from prizepicks_predictor.dashboard.pages import {name}\n{name}()\n"


@pytest.fixture
def app_db(tmp_path: Path, synthetic_csv: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'app.db'}"
    main(["--database-url", url, "init-db"])
    main(["--database-url", url, "ingest-csv", str(synthetic_csv), "--source", "fx"])
    monkeypatch.setenv("PPP_DATABASE_URL", url)
    from prizepicks_predictor.config import get_settings
    from prizepicks_predictor.dashboard.common import _factory

    get_settings.cache_clear()
    _factory.clear()
    return url


def test_app_entrypoint_renders_overview(app_db: str) -> None:
    at = AppTest.from_file(str(APP), default_timeout=90).run()
    assert not at.exception
    assert any("PrizePicks Predictor" in t.value for t in at.title)
    assert any("Latest completed game" in m.label for m in at.metric)
    assert any("2024-11-29" in m.value for m in at.metric)


def test_players_page_search_and_charts(app_db: str) -> None:
    at = AppTest.from_string(_page_script("players_page"), default_timeout=90).run()
    assert not at.exception
    at.text_input[0].input("riv").run()
    assert not at.exception
    assert at.selectbox[0].value.startswith("Alex Rivera")
    assert any("Games stored" in m.label for m in at.metric)
    assert len(at.get("plotly_chart")) == 4
    at.text_input[0].input("nobody").run()
    assert any("No player matches" in i.value for i in at.info)


def test_predict_page_runs_prediction(app_db: str) -> None:
    at = AppTest.from_string(_page_script("predict_page"), default_timeout=90).run()
    assert not at.exception
    at.text_input[0].input("rivera").run()
    at.date_input[0].set_value(date(2024, 12, 1)).run()
    at.number_input[0].set_value(24.5).run()
    assert not at.exception
    assert any("Projected total" in m.label for m in at.metric)
    # no artifacts in the test environment -> explicit uncalibrated headline, never a bare number
    assert any("Uncalibrated estimate shown" in i.value for i in at.info)
    assert any("unvalidated" in i.value for i in at.info)
    at.date_input[0].set_value(date(2024, 10, 27)).run()  # insufficient history -> warning
    assert not at.exception
    assert any("Insufficient history" in w.value for w in at.warning)


def test_forecasts_page_without_snapshots(app_db: str) -> None:
    at = AppTest.from_string(_page_script("forecasts_page"), default_timeout=90).run()
    assert not at.exception
    assert any("No saved forecasts" in i.value for i in at.info)


def test_performance_page_lists_committed_backtests(app_db: str) -> None:
    at = AppTest.from_string(_page_script("performance_page"), default_timeout=90).run()
    assert not at.exception
    assert any("test_2025-26" in o for o in at.selectbox[0].options)
    assert any("No evaluated prospective predictions" in i.value for i in at.info)
