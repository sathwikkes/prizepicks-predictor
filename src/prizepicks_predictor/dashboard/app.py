"""Streamlit entry point.

Run locally with:  uv run streamlit run src/prizepicks_predictor/dashboard/app.py
"""

from __future__ import annotations

import streamlit as st

from prizepicks_predictor.dashboard.common import guarded
from prizepicks_predictor.dashboard.pages import (
    forecasts_page,
    overview_page,
    performance_page,
    players_page,
    predict_page,
)

st.set_page_config(page_title="PrizePicks Predictor", page_icon="🏀", layout="wide")

NAV = st.navigation(
    [
        st.Page(guarded(overview_page), title="Overview & data status", icon="📋", default=True),
        st.Page(guarded(players_page), title="Player exploration", icon="🔍"),
        st.Page(guarded(predict_page), title="Prediction analysis", icon="📈"),
        st.Page(guarded(forecasts_page), title="Prospective forecasts", icon="🗓️"),
        st.Page(guarded(performance_page), title="Model performance", icon="📊"),
    ]
)
NAV.run()
