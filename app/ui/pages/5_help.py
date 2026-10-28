# What: the "Help" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 8 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("❓ Help")
coming_soon(phase=8, what="How the app works in 4 steps, FAQ, privacy note and a small help-bot.")
