"""Stylesheet injection for ClearWay.

The base dark theme lives in .streamlit/config.toml. This module adds the
custom polish from assets/theme.css on top of it.

Run from the repository root:

    streamlit run app.py
"""

from pathlib import Path

import streamlit as st

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
THEME_CSS = ASSETS_DIR / "theme.css"


def load_theme():
    """Inject the ClearWay stylesheet into the current Streamlit page.

    Called once, immediately after set_page_config. A missing stylesheet is
    non-fatal: the app still runs with Streamlit's default theme.
    """
    try:
        css = THEME_CSS.read_text(encoding="utf-8")
    except OSError:
        return

    if not css.strip():
        return

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
