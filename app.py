"""SPHEREx Time Machine — Streamlit entry point.

Run locally:  streamlit run app.py
Deploy:       Streamlit Community Cloud, main file ``app.py``.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="SPHEREx Time Machine — blink the infrared sky",
    page_icon="🔭",
    layout="wide",
    initial_sidebar_state="expanded",
)

from spherex.ui import render  # noqa: E402  (after set_page_config by design)

render()
