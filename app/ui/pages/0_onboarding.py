# What: the "Get started" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 2 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("👋 Get started")
coming_soon(
    phase=2,
    what=(
        "A 3-step wizard: your profile, your documents (or the demo persona) and your "
        "writing preferences."
    ),
)
