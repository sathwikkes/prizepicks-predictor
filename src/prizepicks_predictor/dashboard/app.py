"""Streamlit entry point.

Run locally with:  uv run streamlit run src/prizepicks_predictor/dashboard/app.py
"""

from __future__ import annotations

import streamlit as st

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
        st.Page(overview_page, title="Overview & data status", icon="📋", default=True),
        st.Page(players_page, title="Player exploration", icon="🔍"),
        st.Page(predict_page, title="Prediction analysis", icon="📈"),
        st.Page(forecasts_page, title="Prospective forecasts", icon="🗓️"),
        st.Page(performance_page, title="Model performance", icon="📊"),
    ]
)
NAV.run()
